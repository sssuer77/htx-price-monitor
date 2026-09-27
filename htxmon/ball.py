"""桌面悬浮球：常驻置顶、可拖动、显示实时行情，有提醒时弹出气泡。

实现要点：
- Tkinter 必须独占一个线程：本模块在独立线程里创建 Tk 并跑 mainloop，
  外部（行情线程 / HTTP 线程）只通过线程安全队列与它通信，绝不跨线程操作控件。
- Windows 下用 -transparentcolor 抠出真正的圆形；其它平台退化为半透明方块。
- 位置会记忆到 config.json，下次启动回到原处。
"""

from __future__ import annotations

import queue
import threading
import time
import webbrowser
from typing import Any, Callable

BG_KEY = "#ff00ff"          # 透明色键，选一个不会被用到的洋红
BALL_BG = "#161b26"
BALL_RING = "#2c3648"
RING_ALERT = "#f0b90b"
TXT_MAIN = "#eaecef"
TXT_SUB = "#848e9c"
UP = "#16c784"
DOWN = "#ea3943"
FLAT = "#8a94a6"

FONT = "Microsoft YaHei UI"


def _fmt_price(v: float) -> str:
    if v >= 1000:
        return f"{v:,.1f}"
    if v >= 1:
        return f"{v:.3f}".rstrip("0").rstrip(".")
    return f"{v:.6f}".rstrip("0").rstrip(".")


def _symbol_from_body(body: str) -> str | None:
    """从提醒正文里认出币种，用来决定悬浮球接下来盯谁。"""
    head = (body or "").split("｜")[0].replace("⚡", " ")
    for token in head.split():
        if token.endswith("-USDT"):
            return token
    return None


class FloatingBall:
    """一个常驻桌面的圆形悬浮球。"""

    def __init__(self, app: Any, on_quit: Callable[[], None] | None = None):
        self.app = app
        self.on_quit = on_quit
        cfg = app.cfg.get("ball") or {}
        self.size = max(48, min(160, int(cfg.get("size", 72))))
        self.bubble_sec = max(2.0, float(cfg.get("bubble_sec", 8)))
        self.alpha = float(cfg.get("alpha", 0.95))

        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._unread = 0
        self._focus: str | None = None
        self._alert_until = 0.0
        self._drag: dict[str, Any] | None = None
        self._next_redraw = 0.0

        # 以下只在 Tk 线程里使用
        self._root: Any = None
        self._canvas: Any = None
        self._bubble: Any = None
        self._bubble_label: Any = None
        self._bubble_hide_at = 0.0
        self._x = 0
        self._y = 0

    # ------------------------------------------------------------ 对外接口
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ball", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._q.put(("stop", None, None))

    def notify_alert(self, title: str, body: str) -> None:
        """线程安全：任何线程都可以调用。"""
        self._q.put(("alert", title, body))

    # ------------------------------------------------------------ Tk 线程
    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception as exc:
            print(f"[ball] 无法加载 tkinter，悬浮球已跳过: {exc}")
            return
        try:
            self._root = tk.Tk()
        except Exception as exc:
            print(f"[ball] 无法创建悬浮球窗口: {exc}")
            return

        root = self._root
        root.title("HTX 监控")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-transparentcolor", BG_KEY)
            bg = BG_KEY
        except Exception:
            bg = BALL_BG
            try:
                root.attributes("-alpha", self.alpha)
            except Exception:
                pass
        root.config(bg=bg)

        self._canvas = tk.Canvas(root, width=self.size, height=self.size,
                                 bg=bg, highlightthickness=0, bd=0)
        self._canvas.pack()
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_motion)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._canvas.bind("<Button-3>", self._on_right)

        self._x, self._y = self._initial_pos()
        root.geometry(f"{self.size}x{self.size}+{self._x}+{self._y}")

        self._draw()
        self._tick()
        root.mainloop()

    # ------------------------------------------------------------ 绘制
    def _draw(self) -> None:
        c = self._canvas
        if c is None:
            return
        c.delete("all")
        s = self.size
        hot = time.time() < self._alert_until
        ring = RING_ALERT if hot else BALL_RING
        c.create_oval(2, 2, s - 2, s - 2, fill=BALL_BG, outline=ring,
                      width=3 if hot else 2)

        sym, price, chg = self._read_market()
        label = self._short_symbol(sym) if sym else "HTX"
        if chg is None:
            shown, color = "-", FLAT
        else:
            shown, color = f"{chg:+.2f}%", (UP if chg >= 0 else DOWN)

        c.create_text(s / 2, s * 0.33, text=label, fill=TXT_SUB,
                      font=(FONT, max(8, int(s * 0.13)), "bold"))
        c.create_text(s / 2, s * 0.58, text=shown, fill=color,
                      font=(FONT, max(10, int(s * 0.185)), "bold"))
        if price is not None and s >= 64:
            c.create_text(s / 2, s * 0.79, text=_fmt_price(price), fill=TXT_SUB,
                          font=(FONT, max(7, int(s * 0.105))))

        if self._unread:
            r = max(9, int(s * 0.17))
            cx, cy = s - r - 2, r + 2
            c.create_oval(cx - r, cy - r, cx + r, cy + r,
                          fill=DOWN, outline=BALL_BG, width=2)
            c.create_text(cx, cy, text=str(min(self._unread, 99)), fill="#ffffff",
                          font=(FONT, max(7, int(s * 0.12)), "bold"))

    def _read_market(self):
        feed = self.app.feed
        sym = self._focus or (feed.symbols[0] if feed.symbols else None)
        if not sym:
            return None, None, None
        tick = feed.tick(sym)
        if not tick:
            return sym, None, None
        return sym, tick.price, tick.change_pct24

    @staticmethod
    def _short_symbol(sym: str) -> str:
        return (sym or "").split("-")[0][:5]

    # ------------------------------------------------------------ 主循环
    def _tick(self) -> None:
        if self._root is None:
            return
        dirty = False
        try:
            while True:
                kind, a, b = self._q.get_nowait()
                if kind == "stop":
                    self._root.destroy()
                    return
                if kind == "alert":
                    self._unread += 1
                    self._focus = _symbol_from_body(b) or self._focus
                    self._alert_until = time.time() + 6
                    self._show_bubble(a, b)
                    dirty = True
        except queue.Empty:
            pass

        now = time.time()
        if self._bubble_hide_at and now >= self._bubble_hide_at:
            self._hide_bubble()
        if dirty or now >= self._next_redraw:
            self._next_redraw = now + 0.5
            self._draw()
        self._root.after(120, self._tick)

    # ------------------------------------------------------------ 气泡
    def _show_bubble(self, title: str, body: str) -> None:
        import tkinter as tk

        if self._bubble is None:
            b = tk.Toplevel(self._root)
            b.overrideredirect(True)
            b.attributes("-topmost", True)
            try:
                b.attributes("-alpha", 0.96)
            except Exception:
                pass
            b.config(bg="#1b2230")
            self._bubble_label = tk.Label(
                b, text="", bg="#1b2230", fg=TXT_MAIN, justify="left",
                font=(FONT, 9), wraplength=280, padx=10, pady=8)
            self._bubble_label.pack()
            self._bubble_label.bind("<Button-1>", lambda e: self._open_console())
            b.bind("<Button-1>", lambda e: self._open_console())
            self._bubble = b

        text = f"{title}\n{body}" if title else body
        self._bubble_label.config(text=text[:400])
        self._place_bubble()
        self._bubble.deiconify()
        self._bubble.lift()
        self._bubble_hide_at = time.time() + self.bubble_sec

    def _place_bubble(self) -> None:
        if self._bubble is None:
            return
        self._bubble.update_idletasks()
        w = self._bubble.winfo_reqwidth()
        h = self._bubble.winfo_reqheight()
        sw = self._bubble.winfo_screenwidth()
        sh = self._bubble.winfo_screenheight()
        x = self._x - w - 10
        if x < 0:
            x = self._x + self.size + 10
        x = max(0, min(x, sw - w))
        y = max(0, min(self._y + (self.size - h) // 2, sh - h))
        self._bubble.geometry(f"+{int(x)}+{int(y)}")

    def _hide_bubble(self) -> None:
        self._bubble_hide_at = 0.0
        if self._bubble is not None:
            self._bubble.withdraw()

    # ------------------------------------------------------------ 交互
    def _initial_pos(self):
        cfg = self.app.cfg.get("ball") or {}
        x, y = cfg.get("x"), cfg.get("y")
        if isinstance(x, int) and isinstance(y, int):
            return x, y
        root = self._root
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        return sw - self.size - 40, int(sh * 0.32)

    def _place(self, x: int, y: int) -> None:
        self._x, self._y = int(x), int(y)
        self._root.geometry(f"{self.size}x{self.size}+{self._x}+{self._y}")
        if self._bubble is not None and self._bubble_hide_at:
            self._place_bubble()

    def _on_press(self, e) -> None:
        self._drag = {"dx": e.x_root - self._x, "dy": e.y_root - self._y, "moved": False}

    def _on_motion(self, e) -> None:
        if not self._drag:
            return
        if abs(e.x_root - self._x - self._drag["dx"]) > 2 or \
           abs(e.y_root - self._y - self._drag["dy"]) > 2:
            self._drag["moved"] = True
        self._place(e.x_root - self._drag["dx"], e.y_root - self._drag["dy"])

    def _on_release(self, e) -> None:
        drag, self._drag = self._drag, None
        if not drag:
            return
        if drag["moved"]:
            self._save_pos()
        elif self._bubble_hide_at:
            self._hide_bubble()
            self._unread = 0
            self._draw()
        else:
            self._open_console()

    def _on_right(self, e) -> None:
        import tkinter as tk

        menu = tk.Menu(self._root, tearoff=0)
        menu.add_command(label="打开控制台", command=self._open_console)
        menu.add_command(label="清除提醒角标", command=self._clear_unread)
        sound_on = bool(self.app.cfg.get("notify", {}).get("sound"))
        menu.add_command(label="声音提醒：开" if sound_on else "声音提醒：关",
                         command=self._toggle_sound)
        menu.add_separator()
        menu.add_command(label="退出程序", command=self._quit)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _clear_unread(self) -> None:
        self._unread = 0
        self._alert_until = 0.0
        self._draw()

    def _toggle_sound(self) -> None:
        from .config import save_config

        ncfg = self.app.cfg.setdefault("notify", {})
        ncfg["sound"] = not bool(ncfg.get("sound"))
        self.app.notifier.ncfg = ncfg
        save_config(self.app.cfg)
        self.app.events.add("info", f"声音提醒已{'开启' if ncfg['sound'] else '关闭'}")

    def _open_console(self) -> None:
        cfg = self.app.cfg
        url = f"http://{cfg.get('host', '127.0.0.1')}:{cfg.get('port', 8971)}/"
        self._clear_unread()
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def _save_pos(self) -> None:
        from .config import save_config

        bcfg = self.app.cfg.setdefault("ball", {})
        bcfg["x"], bcfg["y"] = self._x, self._y
        try:
            save_config(self.app.cfg)
        except Exception:
            pass

    def _quit(self) -> None:
        if self.on_quit:
            try:
                self.on_quit()
            except Exception:
                pass

