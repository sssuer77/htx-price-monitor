"""本地 HTTP 服务：静态页面 + JSON API + SSE 实时推送。"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import netutil
from .config import ROOT, save_config
from .engine import RuleEngine
from .feed import PriceFeed, feed_status
from .llm import parse_with_llm
from .models import Rule
from .notify import Notifier
from .parser import parse
from .store import EventLog, RuleStore

WEB_DIR = Path(__file__).resolve().parent / "web"


_SYMBOL_BASE_RE = re.compile(r"^[A-Z0-9]{1,15}$")


def _normalize_symbol(raw: Any) -> str | None:
    """把用户输入的各种写法统一成 BTC-USDT。不合法返回 None。

    只接受 ASCII 字母数字。中文（如「牛来-USDT」）必须挡在这里：
    Python 的 str.isalnum() 对汉字也返回 True，放过去会让 REST 请求抛
    "'ascii' codec can't encode" 编码错误，表现成「加进去了但永远没行情」。
    """
    # 空格 / 斜杠 / 下划线都当分隔符，避免「BTC USDT」被拼成「BTCUSDT-USDT」
    sym = re.sub(r"[\s/_]+", "-", str(raw or "").upper().strip())
    sym = re.sub(r"-+", "-", sym).strip("-")
    if not sym:
        return None
    if "-" not in sym:
        sym += "-USDT"
    base, _, quote_ = sym.partition("-")
    if quote_ in ("", "USD"):
        quote_ = "USDT"
    if quote_ != "USDT" or not _SYMBOL_BASE_RE.match(base):
        return None
    if base == "USDT":          # 「-USDT」这种写法会退化成 USDT-USDT，直接拒绝
        return None
    return f"{base}-USDT"


class App:
    """把各模块组装成一个应用对象。"""

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.events = EventLog(cfg.get("notify", {}).get("log_file", "data/events.jsonl"))
        self.store = RuleStore()
        self.notifier = Notifier(cfg, logger=self._log)
        self.feed = PriceFeed(cfg, on_tick=self._on_tick)
        self.engine: RuleEngine = RuleEngine(self.store, self.feed, self.notifier, self.events)
        self.started_at = time.time()
        self.ball = None            # 由 __main__ 注入 FloatingBall 实例

    def _log(self, level: str, message: str) -> None:
        self.events.add(level, message)

    def _on_tick(self, tick) -> None:
        self.engine.on_tick(tick)

    def start(self) -> None:
        self.feed.start()
        self.events.add("info", "监控服务已启动")

    def stop(self) -> None:
        self.feed.stop()

    # ---- 规则操作（统一入口，保证 prime + 落盘） ----
    def add_rule(self, rule: Rule) -> str:
        self.store.add(rule)                     # 先入库，prime 里可能触发提醒
        hint = self.engine.prime_rule(rule)
        self.store.save()
        self.events.add("rule", f"新增监控：{rule.describe()}", rule_id=rule.id,
                        symbol=rule.symbol, hint=hint)
        return hint

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            rules = [r.to_dict() for r in self.store.all()]
            rules.sort(key=lambda r: (r["status"] != "active", -r["created_at"]))
            return {
                "now": time.time(),
                "uptime_sec": time.time() - self.started_at,
                "feed": feed_status(self.feed),
                "rules": rules,
                "events": self.events.tail(200),
                "cert": netutil.cert_info(),
                "config": {
                    "symbols": self.cfg.get("symbols", []),
                    "poll_interval_sec": self.cfg.get("poll_interval_sec"),
                    "enable_ws": self.cfg.get("enable_ws"),
                    "notify": {
                        "ui": bool(self.cfg["notify"].get("ui")),
                        "sound": bool(self.cfg["notify"].get("sound")),
                        "toast": bool(self.cfg["notify"].get("toast")),
                        "webhook": self.cfg["notify"].get("webhook", ""),
                        "webhook_kind": self.cfg["notify"].get("webhook_kind", "generic"),
                    },
                    "llm": {
                        "enabled": bool(self.cfg["llm"].get("enabled")),
                        "model": self.cfg["llm"].get("model", ""),
                        "has_key": bool(self.cfg["llm"].get("api_key")),
                    },
                    "ball": {
                        "enabled": bool(self.cfg.get("ball", {}).get("enabled", True)),
                        "size": self.cfg.get("ball", {}).get("size", 72),
                        "active": self.ball is not None,
                    },
                },
                "stats": {"fired_total": self.engine.fired_total,
                          "rules_active": sum(1 for r in self.store.all() if r.status == "active")},
            }


class Handler(BaseHTTPRequestHandler):
    server_version = "htxmon/0.1"
    app: App = None  # type: ignore[assignment]

    # ---- 基础工具 ----
    def log_message(self, fmt: str, *args: Any) -> None:
        if "/api/" in (self.path or "") and "stream" not in (self.path or ""):
            print(f"[http] {self.address_string()} {fmt % args}")

    def _json(self, obj: Any, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    # ---- GET ----
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            return self._json(self.app.snapshot())
        if path == "/api/stream":
            return self._stream()
        return self._static(path)

    # ---- POST ----
    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_json()
        try:
            if path == "/api/parse":
                return self._json(self._handle_parse(body))
            if path == "/api/rule/delete":
                ok = self.app.store.remove(str(body.get("id", "")))
                self.app.events.add("rule", "已删除规则" if ok else "删除失败：规则不存在")
                return self._json({"ok": ok})
            if path == "/api/rule/toggle":
                return self._json(self._handle_toggle(body))
            if path == "/api/rules/clear":
                n = self.app.store.clear(body.get("status") or None)
                self.app.events.add("rule", f"已清理 {n} 条规则")
                return self._json({"ok": True, "removed": n})
            if path == "/api/config":
                return self._json(self._handle_config(body))
            if path == "/api/test-notify":
                res = self.app.notifier.send("🔔 测试提醒", "如果你看到这条消息，说明通道配置成功。", {"test": True})
                self.app.events.add("info", f"测试提醒已发送：{res}")
                return self._json({"ok": True, "channels": res})
            if path == "/api/symbol/add":
                return self._json(self._handle_add_symbol(body))
            if path == "/api/symbol/remove":
                return self._json(self._handle_remove_symbol(body))
            return self._json({"ok": False, "error": "未知接口"}, 404)
        except Exception as exc:
            self.app.events.add("error", f"接口 {path} 异常: {exc}")
            return self._json({"ok": False, "error": str(exc)}, 500)

    # ---- 业务处理 ----
    def _handle_parse(self, body: dict) -> dict:
        text = str(body.get("text") or "").strip()
        res = parse(text)
        used_llm = False
        if not res.ok:
            llm_rules, err = parse_with_llm(text, self.app.cfg)
            if llm_rules:
                res.rules, used_llm = llm_rules, True
                res.warnings.append("本地解析失败，已由 LLM 兜底解析")
            elif err:
                res.warnings.append(err)

        out = []
        for rule in res.rules:
            hint = self.app.add_rule(rule)
            out.append({"rule": rule.to_dict(), "describe": rule.describe(), "hint": hint})
        return {"ok": bool(res.rules), "rules": out, "warnings": res.warnings, "used_llm": used_llm}

    def _handle_toggle(self, body: dict) -> dict:
        rule = self.app.store.get(str(body.get("id", "")))
        if not rule:
            return {"ok": False, "error": "规则不存在"}
        if rule.status == "active":
            rule.status = "disabled"
        else:
            rule.status = "active"
            rule.state.pop("last_fire_ts", None)
            hint = self.app.engine.prime_rule(rule)
            self.app.store.update(rule)
            return {"ok": True, "status": rule.status, "hint": hint}
        self.app.store.update(rule)
        return {"ok": True, "status": rule.status}

    def _handle_config(self, body: dict) -> dict:
        cfg = self.app.cfg
        if "poll_interval_sec" in body:
            cfg["poll_interval_sec"] = max(1.0, float(body["poll_interval_sec"]))
        if "enable_ws" in body:
            cfg["enable_ws"] = bool(body["enable_ws"])
        if "notify" in body and isinstance(body["notify"], dict):
            for k in ("ui", "ball", "sound", "toast", "webhook", "webhook_kind"):
                if k in body["notify"]:
                    cfg["notify"][k] = body["notify"][k]
        if "ball" in body and isinstance(body["ball"], dict):
            bcfg = cfg.setdefault("ball", {})
            for k in ("enabled", "size", "alpha", "bubble_sec"):
                if k in body["ball"]:
                    bcfg[k] = body["ball"][k]
        if "llm" in body and isinstance(body["llm"], dict):
            for k in ("enabled", "api_key", "base_url", "model"):
                if k in body["llm"]:
                    cfg["llm"][k] = body["llm"][k]
        save_config(cfg)
        self.app.feed.poll_interval = float(cfg.get("poll_interval_sec", 3.0))
        self.app.notifier.cfg = cfg
        self.app.notifier.ncfg = cfg.get("notify", {})
        self.app.events.add("info", "配置已保存（重启后部分设置才会完全生效）")
        return {"ok": True, "config": cfg.get("notify")}

    def _handle_add_symbol(self, body: dict) -> dict:
        raw = body.get("symbol")
        sym = _normalize_symbol(raw)
        if not sym:
            return {"ok": False,
                    "error": f"「{raw}」不是合法的合约代码。只能填英文字母和数字，"
                             f"正确写法例如 DOGE-USDT"}
        feed = self.app.feed
        if feed.has_symbol(sym):
            return {"ok": True, "added": False, "symbol": sym, "symbols": list(feed.symbols)}

        # 先问 HTX 有没有这个合约，避免「加成功了却永远收不到行情」
        verdict, info = feed.fetch_contract_info(sym)
        if verdict == "none":
            return {"ok": False,
                    "error": f"HTX 没有 {sym} 这个永续合约（{info.get('error')}）"}
        warn = None
        if verdict == "err":
            warn = (f"没能连上 HTX 校验合约（{info.get('error')}），已先加入，"
                    f"若长时间没有行情请核对代码")

        feed.add_symbol(sym)
        self.app.cfg["symbols"] = list(feed.symbols)
        save_config(self.app.cfg)
        msg = f"已加入监控合约 {sym}（即时生效）"
        if warn:
            msg += f"｜{warn}"
        self.app.events.add("info", msg, symbol=sym)
        return {"ok": True, "added": True, "symbol": sym,
                "symbols": list(feed.symbols), "warning": warn}

    def _handle_remove_symbol(self, body: dict) -> dict:
        sym = _normalize_symbol(body.get("symbol"))
        if not sym:
            return {"ok": False, "error": "合约代码不合法"}
        feed = self.app.feed
        if not feed.has_symbol(sym):
            return {"ok": False, "error": f"{sym} 不在监控列表中"}
        if len(feed.symbols) <= 1:
            return {"ok": False, "error": "至少要保留一个监控合约"}

        removed_rules = 0
        if body.get("purge_rules", True):
            for rule in list(self.app.store.all()):
                if rule.symbol == sym:
                    self.app.store.remove(rule.id)
                    removed_rules += 1
        feed.remove_symbol(sym)
        self.app.cfg["symbols"] = list(feed.symbols)
        save_config(self.app.cfg)
        msg = f"已移出监控合约 {sym}"
        if removed_rules:
            msg += f"，并删除 {removed_rules} 条相关规则"
        self.app.events.add("info", msg, symbol=sym)
        return {"ok": True, "symbol": sym, "symbols": list(feed.symbols),
                "removed_rules": removed_rules}

    # ---- SSE ----
    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last_seq = 0
        last_rules = -1
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while True:
                payload: dict[str, Any] = {"type": "tick", "ts": time.time(),
                                           "ticks": {s: t.to_dict() for s, t in self.app.feed.all_ticks().items()}}
                self._sse(payload)
                new_events = [e for e in self.app.events.since(last_seq) if e["kind"] in ("alert", "rule", "warn", "error")]
                if new_events:
                    last_seq = new_events[-1]["seq"]
                    self._sse({"type": "events", "events": new_events})
                rules = self.app.store.all()
                if len(rules) != last_rules:
                    last_rules = len(rules)
                    self._sse({"type": "rules", "rules": [r.to_dict() for r in rules]})
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except Exception:
            return

    def _sse(self, obj: dict) -> None:
        self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    # ---- 静态文件 ----
    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            self.send_error(404, "Not Found")
            return
        data = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def build_server(app: App, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd
