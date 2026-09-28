"""行情馈送：WebSocket 主通道 + REST 轮询兜底。

设计要点：
* WS 负责低延迟推送；REST 定时轮询始终并行运行，保证 WS 被墙/掉线时监控不中断。
* 每个合约维护一条 (时间, 价格) 环形历史，供「5分钟内涨2%」这类区间规则取基准价。
* 启动时用 1 分钟 K 线回补历史，避免刚启动时区间规则无法判定。
* 用交易所返回的服务器时间做时钟偏移估计（EWMA），从而给出可信的单程延迟估计。
"""

from __future__ import annotations

import json
import threading
import time
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any, Callable
from urllib.parse import quote

from .netutil import http_json
from .wsclient import WebSocketClient, WebSocketError

MAX_HISTORY_SEC = 26 * 3600


@dataclass
class Tick:
    symbol: str
    price: float
    local_ts: float
    src: str                      # ws | rest | kline
    exch_ts: float | None = None
    delivery_ms: float | None = None
    open24: float | None = None
    high24: float | None = None
    low24: float | None = None
    turnover24: float | None = None
    count24: int | None = None

    @property
    def change_pct24(self) -> float | None:
        if self.open24 and self.open24 > 0:
            return (self.price / self.open24 - 1) * 100
        return None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["change_pct24"] = self.change_pct24
        return d


class PriceFeed:
    def __init__(self, cfg: dict[str, Any], on_tick: Callable[[Tick], None] | None = None):
        self.cfg = cfg
        self.on_tick = on_tick
        self.symbols: list[str] = list(cfg.get("symbols", []))
        self.rest_base: str = cfg["rest_base"].rstrip("/")
        self.ws_url: str = cfg["ws_url"]
        self.poll_interval: float = float(cfg.get("poll_interval_sec", 3.0))
        self.verify: bool = bool(cfg.get("tls_verify", True))

        self._ticks: dict[str, Tick] = {}
        self._history: dict[str, deque[tuple[float, float]]] = {}
        self._lock = threading.RLock()
        self._ws: WebSocketClient | None = None
        self._ws_lock = threading.RLock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._clock_offset_ms: float | None = None
        self._seq = 0

        self.status: dict[str, Any] = {
            "ws": {"connected": False, "last_msg_ts": None, "reconnects": 0,
                   "last_error": None, "subscribed": []},
            "rest": {"last_ok_ts": None, "last_error": None, "latency_ms": None, "calls": 0},
            "bad": {},
            "clock_offset_ms": None,
            "started_at": time.time(),
        }

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        for sym in self.symbols:
            self._history.setdefault(sym, deque())
        self._threads.append(threading.Thread(target=self._bootstrap_history, daemon=True, name="bootstrap"))
        self._threads.append(threading.Thread(target=self._rest_loop, daemon=True, name="rest-poll"))
        if self.cfg.get("enable_ws", True):
            self._threads.append(threading.Thread(target=self._ws_loop, daemon=True, name="ws"))
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ 监控列表增删
    def has_symbol(self, symbol: str) -> bool:
        return symbol.upper().strip() in self.symbols

    def add_symbol(self, symbol: str) -> bool:
        """加入监控。返回 True 表示新增，False 表示本来就在。立即对 WS 生效。"""
        sym = symbol.upper().strip()
        with self._lock:
            if sym in self.symbols:
                return False
            self.symbols.append(sym)
            self._history.setdefault(sym, deque())
        self._sync_subscriptions()
        return True

    def remove_symbol(self, symbol: str) -> bool:
        """移出监控并清掉它的行情与历史。返回 True 表示确实删掉了。"""
        sym = symbol.upper().strip()
        with self._lock:
            if sym not in self.symbols:
                return False
            self.symbols.remove(sym)
            self._ticks.pop(sym, None)
            self._history.pop(sym, None)
        with self._ws_lock:
            ws = self._ws
            if ws and ws.connected:
                for ch in ("trade.detail", "detail"):
                    try:
                        ws.send_text(json.dumps(
                            {"unsub": f"market.{sym}.{ch}", "id": f"u-{ch}-{sym}"}))
                    except Exception:
                        pass
        self._sync_subscriptions()
        return True

    def _sync_subscriptions(self) -> None:
        """把当前 symbols 同步到已连接的 WS；未连接时下次重连会自动带上。"""
        with self._ws_lock:
            ws = self._ws
            if not ws or not ws.connected:
                return
            try:
                for sym in list(self.symbols):
                    for ch in ("trade.detail", "detail"):
                        ws.send_text(json.dumps(
                            {"sub": f"market.{sym}.{ch}", "id": f"t-{ch}-{sym}"}))
                self.status["ws"]["subscribed"] = list(self.symbols)
            except Exception as exc:
                self.status["ws"]["last_error"] = f"订阅同步失败: {exc}"

    # ------------------------------------------------------------ 合约校验
    CONTRACT_INFO_PATH = "/linear-swap-api/v1/swap_contract_info"

    def fetch_contract_info(self, symbol: str, timeout: float = 8.0) -> tuple[str, dict[str, Any]]:
        """向 HTX 查证合约是否存在。

        返回 (结果, 详情)：
          "ok"   合约存在，详情带 contract_size / price_tick 等
          "none" HTX 明确回答「不存在」
          "err"  网络或接口异常，无法判断（调用方应放行并提示，而不是拒绝）
        """
        url = f"{self.rest_base}{self.CONTRACT_INFO_PATH}?contract_code={quote(symbol, safe='')}"
        try:
            data = http_json(url, timeout=timeout, verify=self.verify)
        except Exception as exc:
            return "err", {"error": str(exc)}
        rows = data.get("data")
        if data.get("status") == "ok" and isinstance(rows, list) and rows:
            return "ok", rows[0]
        return "none", {"error": data.get("err_msg") or "合约不存在"}

    # ------------------------------------------------------------ 单个合约的拉取状态
    def _mark_bad(self, symbol: str, err: str) -> None:
        with self._lock:
            self.status.setdefault("bad", {})[symbol] = err[:160]

    def _mark_ok(self, symbol: str) -> None:
        with self._lock:
            self.status.setdefault("bad", {}).pop(symbol, None)

    # ------------------------------------------------------------ 取数
    def tick(self, symbol: str) -> Tick | None:
        with self._lock:
            return self._ticks.get(symbol)

    def all_ticks(self) -> dict[str, Tick]:
        with self._lock:
            return dict(self._ticks)

    def price_ago(self, symbol: str, seconds: int) -> float | None:
        """取 seconds 秒之前的价格（用于区间涨跌幅规则）。"""
        target = time.time() - seconds
        with self._lock:
            hist = self._history.get(symbol)
            if not hist:
                return None
            times = [h[0] for h in hist]
        idx = bisect_right(times, target) - 1
        if idx < 0:
            idx = 0
        return hist[idx][1]

    # ------------------------------------------------------------ 内部
    def _remember(self, symbol: str, ts: float, price: float) -> None:
        hist = self._history.setdefault(symbol, deque())
        if hist and ts <= hist[-1][0]:
            return
        hist.append((ts, price))
        cutoff = ts - MAX_HISTORY_SEC
        while hist and hist[0][0] < cutoff:
            hist.popleft()

    def _emit(self, tick: Tick) -> None:
        with self._lock:
            self._seq += 1
            self._ticks[tick.symbol] = tick
            self._remember(tick.symbol, tick.local_ts, tick.price)
        if self.on_tick:
            try:
                self.on_tick(tick)
            except Exception as exc:
                print(f"[feed] on_tick 回调异常: {exc}")

    def _update_clock_offset(self, server_ms: float, local_recv: float, rtt_ms: float) -> None:
        offset = (local_recv * 1000 - server_ms) - rtt_ms / 2
        with self._lock:
            if self._clock_offset_ms is None:
                self._clock_offset_ms = offset
            else:
                self._clock_offset_ms = self._clock_offset_ms * 0.8 + offset * 0.2
            self.status["clock_offset_ms"] = round(self._clock_offset_ms, 1)

    # ------------------------------------------------------------ REST
    def _fetch_rest(self, symbol: str) -> Tick | None:
        url = (f"{self.rest_base}/linear-swap-ex/market/detail/merged"
               f"?contract_code={quote(symbol, safe='')}")
        t0 = time.time()
        data = http_json(url, timeout=10, verify=self.verify)
        rtt_ms = (time.time() - t0) * 1000
        tick_data = data.get("tick") or {}
        if not tick_data:
            raise RuntimeError("返回缺少 tick 字段")
        server_ms = data.get("ts")
        if server_ms:
            self._update_clock_offset(float(server_ms), t0 + rtt_ms / 1000, rtt_ms)
        price = float(tick_data.get("close") or 0)
        if price <= 0:
            raise RuntimeError("价格异常")
        self.status["rest"] = {
            "last_ok_ts": time.time(),
            "last_error": None,
            "latency_ms": round(rtt_ms, 1),
            "calls": self.status["rest"].get("calls", 0) + 1,
        }
        self._mark_ok(symbol)
        return Tick(
            symbol=symbol, price=price, local_ts=time.time(), src="rest",
            exch_ts=(server_ms / 1000) if server_ms else None,
            delivery_ms=round(rtt_ms / 2, 1),
            open24=float(tick_data.get("open") or 0) or None,
            high24=float(tick_data.get("high") or 0) or None,
            low24=float(tick_data.get("low") or 0) or None,
            turnover24=float(tick_data.get("trade_turnover") or 0) or None,
            count24=int(float(tick_data.get("count") or 0)) or None,
        )

    def _rest_loop(self) -> None:
        while not self._stop.is_set():
            for sym in self.symbols:
                if self._stop.is_set():
                    return
                try:
                    tick = self._fetch_rest(sym)
                    if tick:
                        self._emit(tick)
                except Exception as exc:
                    self.status["rest"]["last_error"] = f"{sym}: {exc}"
                    self._mark_bad(sym, str(exc))
            self._stop.wait(self.poll_interval)

    # ------------------------------------------------------------ WS
    def _ws_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            ws: WebSocketClient | None = None
            try:
                ws = WebSocketClient(self.ws_url, timeout=20, verify=self.verify)
                ws.connect()
                with self._ws_lock:
                    self._ws = ws
                for sym in list(self.symbols):
                    ws.send_text(json.dumps({"sub": f"market.{sym}.trade.detail", "id": f"t-{sym}"}))
                    ws.send_text(json.dumps({"sub": f"market.{sym}.detail", "id": f"d-{sym}"}))
                self.status["ws"].update({"connected": True, "last_error": None,
                                          "subscribed": list(self.symbols)})
                backoff = 1.0
                last_ping = time.time()
                while not self._stop.is_set():
                    msg = ws.recv_text()
                    now = time.time()
                    if now - last_ping > 5:
                        ws.send_text(json.dumps({"ping": int(now * 1000)}))
                        last_ping = now
                    if msg is None:
                        continue
                    self.status["ws"]["last_msg_ts"] = now
                    self._handle_ws(msg, now)
            except Exception as exc:
                self.status["ws"].update({"connected": False, "last_error": str(exc)[:200]})
                self.status["ws"]["reconnects"] = self.status["ws"].get("reconnects", 0) + 1
                with self._ws_lock:
                    self._ws = None
                if ws:
                    ws.close()
                self._stop.wait(min(backoff, 30))
                backoff = min(backoff * 2, 30)

    def _handle_ws(self, raw: str, now: float) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            return
        if "ping" in msg:
            return
        ch = msg.get("ch") or ""
        if not ch.startswith("market."):
            return
        parts = ch.split(".")
        if len(parts) < 3:
            return
        symbol, kind = parts[1], parts[2]
        tick_data = msg.get("tick") or {}
        offset_ms = self._clock_offset_ms or 0.0
        exch_ts = msg.get("ts")

        if kind == "trade.detail":
            rows = (tick_data.get("data") or [])
            if not rows:
                return
            last = rows[-1]
            price = float(last.get("price") or 0)
            exch_ts = last.get("ts") or exch_ts
        elif kind == "detail":
            price = float(tick_data.get("price") or 0)
        else:
            return
        if price <= 0:
            return

        delivery = None
        if exch_ts:
            delivery = round(now * 1000 - float(exch_ts) - offset_ms, 1)
        prev = self.tick(symbol)
        self._emit(Tick(
            symbol=symbol, price=price, local_ts=now, src="ws",
            exch_ts=(float(exch_ts) / 1000) if exch_ts else None,
            delivery_ms=delivery,
            open24=float(tick_data.get("open") or 0) or (prev.open24 if prev else None),
            high24=float(tick_data.get("high") or 0) or (prev.high24 if prev else None),
            low24=float(tick_data.get("low") or 0) or (prev.low24 if prev else None),
            turnover24=float(tick_data.get("trade_turnover") or 0) or (prev.turnover24 if prev else None),
            count24=int(float(tick_data.get("count") or 0)) or (prev.count24 if prev else None),
        ))

    # ------------------------------------------------------------ 历史回补
    def _bootstrap_history(self) -> None:
        minutes = int(self.cfg.get("history_bootstrap_min", 120))
        size = max(10, min(minutes, 2000))
        for sym in self.symbols:
            if self._stop.is_set():
                return
            try:
                url = (f"{self.rest_base}/linear-swap-ex/market/history/kline"
                       f"?contract_code={sym}&period=1min&size={size}")
                data = http_json(url, timeout=15, verify=self.verify)
                rows = data.get("data") or []
                with self._lock:
                    hist = self._history.setdefault(sym, deque())
                    for row in reversed(rows):          # 接口返回按时间倒序
                        hist.append((float(row["id"]), float(row["close"])))
                    existing = self._ticks.get(sym)
                    if rows and existing is None:
                        last = rows[0]
                        self._ticks[sym] = Tick(
                            symbol=sym, price=float(last["close"]), local_ts=time.time(),
                            src="kline",
                        )
            except Exception as exc:
                print(f"[feed] {sym} 历史回补失败: {exc}")


def feed_status(feed: PriceFeed) -> dict[str, Any]:
    st = dict(feed.status)
    st["ticks"] = {s: t.to_dict() for s, t in feed.all_ticks().items()}
    return st
