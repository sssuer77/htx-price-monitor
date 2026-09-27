"""规则引擎：把行情 tick 喂给规则状态机，命中即触发提醒。

状态机用「位置量」统一表达：
  pos = 1  价格在价位上方 / 区间上沿之上
  pos = 0  价格贴在价位上 / 落在区间内
  pos = -1 价格在价位下方 / 区间下沿之下
所有穿越型规则都基于 pos 的跳变判定，天然避免「创建时价格已在对面」导致的误触发。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from .feed import PriceFeed, Tick
from .models import Rule
from .notify import Notifier
from .store import EventLog, RuleStore

TOUCH_TOL_PCT = 0.05        # 触及判定的容差（%）


def _pos(price: float, rule: Rule) -> int | None:
    if rule.level is None:
        return None
    if rule.type in ("in_range", "out_range") and rule.level2 is not None:
        lo, hi = min(rule.level, rule.level2), max(rule.level, rule.level2)
        if price > hi:
            return 1
        if price < lo:
            return -1
        return 0
    if price > rule.level:
        return 1
    if price < rule.level:
        return -1
    return 0


class RuleEngine:
    def __init__(self, store: RuleStore, feed: PriceFeed, notifier: Notifier, events: EventLog):
        self.store = store
        self.feed = feed
        self.notifier = notifier
        self.events = events
        self._lock = threading.RLock()
        self.fired_total = 0

    # ------------------------------------------------------------ 主入口
    def on_tick(self, tick: Tick) -> None:
        with self._lock:
            rules = [r for r in self.store.all() if r.symbol == tick.symbol and r.status == "active"]
        changed = False
        for rule in rules:
            try:
                fired, detail = self._evaluate(rule, tick)
            except Exception as exc:
                self.events.add("error", f"规则 {rule.id} 评估异常: {exc}")
                continue
            if fired:
                self._fire(rule, tick, detail)
                changed = True
            else:
                changed = True
        if changed:
            self.store.save()

    # ------------------------------------------------------------ 判定
    def _evaluate(self, rule: Rule, tick: Tick) -> tuple[bool, dict[str, Any]]:
        st = rule.state
        price = tick.price
        detail: dict[str, Any] = {"price": price}

        # ---- 区间涨跌幅类（无位置状态） ----
        if rule.type in ("pct_up", "pct_down", "pct_abs"):
            window = rule.window_sec or 300
            ref = self.feed.price_ago(rule.symbol, window)
            if not ref:
                return False, detail
            chg = (price / ref - 1) * 100
            detail.update({"ref_price": ref, "change_pct": chg, "window_sec": window})
            if st.get("last_ref") == ref and st.get("fired_for_ref"):
                return False, detail
            hit = (
                (rule.type == "pct_up" and chg >= (rule.pct or 0))
                or (rule.type == "pct_down" and chg <= -(rule.pct or 0))
                or (rule.type == "pct_abs" and abs(chg) >= (rule.pct or 0))
            )
            if hit:
                st["last_ref"] = ref
                st["fired_for_ref"] = True
                return True, detail
            st["last_ref"] = ref
            st["fired_for_ref"] = False
            return False, detail

        # ---- 接近类 ----
        if rule.type == "near":
            if not rule.level:
                return False, detail
            dist = abs(price / rule.level - 1) * 100
            detail["distance_pct"] = dist
            was = bool(st.get("near", False))
            now_near = dist <= (rule.pct or 0.5)
            st["near"] = now_near
            return (now_near and not was), detail

        pos = _pos(price, rule)
        if pos is None:
            return False, detail
        prev = st.get("pos")
        st["pos"] = pos
        st["last_price"] = price
        if rule.level:
            detail["distance_pct"] = (price / rule.level - 1) * 100
        if prev is None:
            return False, detail          # 第一条 tick 只用于建立基准

        t = rule.type
        if t == "cross_up":
            return (prev == -1 and pos == 1), detail
        if t == "cross_down":
            return (prev == 1 and pos == -1), detail
        if t == "above":
            return (prev != 1 and pos == 1), detail
        if t == "below":
            return (prev != -1 and pos == -1), detail
        if t == "touch":
            if prev != pos:
                return True, detail
            if rule.level:
                return abs(price / rule.level - 1) * 100 <= TOUCH_TOL_PCT, detail
            return False, detail
        if t == "in_range":
            return (prev != 0 and pos == 0), detail
        if t == "out_range":
            return (pos != 0 and (prev == 0 or prev * pos == -1)), detail
        return False, detail

    # ------------------------------------------------------------ 触发
    def _fire(self, rule: Rule, tick: Tick, detail: dict[str, Any]) -> None:
        now = time.time()
        cooldown = rule.cooldown_sec or 0
        last = rule.state.get("last_fire_ts") or 0
        if rule.repeat == "always" and cooldown and now - last < cooldown:
            return

        rule.state["last_fire_ts"] = now
        rule.state["fired_count"] = rule.state.get("fired_count", 0) + 1
        if rule.repeat == "once":
            rule.status = "triggered"
        self.fired_total += 1

        title = f"⚡ {rule.symbol} 触发提醒"
        body = self._message(rule, tick, detail)
        results = self.notifier.send(title, body, {
            "rule_id": rule.id, "symbol": rule.symbol, "type": rule.type,
            "price": tick.price, "level": rule.level, "detail": detail,
        })
        self.events.add(
            "alert", body, rule_id=rule.id, symbol=rule.symbol,
            price=tick.price, rule_type=rule.type, detail=detail, channels=results,
        )

    def _message(self, rule: Rule, tick: Tick, detail: dict[str, Any]) -> str:
        def f(v: float | None) -> str:
            if v is None:
                return "-"
            if abs(v) >= 1000:
                return f"{v:,.2f}"
            return f"{v:.6f}".rstrip("0").rstrip(".")

        head = rule.describe()
        parts = [f"现价 {f(tick.price)}"]
        if rule.level:
            parts.append(f"距价位 {(tick.price / rule.level - 1) * 100:+.2f}%")
        if "change_pct" in detail:
            parts.append(f"{detail['window_sec'] // 60 or 1}分钟变动 {detail['change_pct']:+.2f}%")
        if tick.change_pct24 is not None:
            parts.append(f"24h {tick.change_pct24:+.2f}%")
        parts.append("WS" if tick.src == "ws" else tick.src.upper())
        return f"{head}｜" + "｜".join(parts)

    # ------------------------------------------------------------ 新建规则后初始化状态
    # 创建时若条件已成立，立即提醒一次（这类规则是「状态型」，用户问的是「现在是不是」）
    _STATE_TYPES = {"above", "below", "near", "in_range", "pct_up", "pct_down", "pct_abs"}

    def _condition_now(self, rule: Rule, tick: Tick, detail: dict[str, Any]) -> bool:
        """无状态地判断「此刻条件是否成立」，供新建规则时使用。"""
        price = tick.price
        if rule.type == "near":
            if not rule.level:
                return False
            dist = abs(price / rule.level - 1) * 100
            detail["distance_pct"] = dist
            detail["price"] = price
            return dist <= (rule.pct or 0.5)
        if rule.type in ("pct_up", "pct_down", "pct_abs"):
            window = rule.window_sec or 300
            ref = self.feed.price_ago(rule.symbol, window)
            if not ref:
                return False
            chg = (price / ref - 1) * 100
            detail.update({"price": price, "ref_price": ref, "change_pct": chg, "window_sec": window})
            return ((rule.type == "pct_up" and chg >= (rule.pct or 0))
                    or (rule.type == "pct_down" and chg <= -(rule.pct or 0))
                    or (rule.type == "pct_abs" and abs(chg) >= (rule.pct or 0)))
        pos = _pos(price, rule)
        if pos is None:
            return False
        detail["price"] = price
        if rule.level:
            detail["distance_pct"] = (price / rule.level - 1) * 100
        if rule.type == "above":
            return pos == 1
        if rule.type == "below":
            return pos == -1
        if rule.type == "in_range":
            return pos == 0
        return False

    def prime_rule(self, rule: Rule) -> str:
        """按当前价格初始化规则状态，返回一句人类可读的就绪说明。

        穿越型规则（cross_up / cross_down / touch / out_range）只建立基准、绝不立即触发，
        避免「刚建好就误报」；状态型规则若条件已成立，则立即提醒一次。
        """
        tick = self.feed.tick(rule.symbol)
        if not tick:
            return "等待行情…"
        pos = _pos(tick.price, rule)

        if rule.type in self._STATE_TYPES:
            detail: dict[str, Any] = {}
            if self._condition_now(rule, tick, detail):
                rule.state["pos"] = pos
                self._fire(rule, tick, detail)
                return f"创建时条件已成立（现价 {tick.price:g}），已立即提醒一次"
        if pos is not None:
            rule.state["pos"] = pos
        if rule.type in ("cross_up", "cross_down"):
            if rule.type == "cross_up" and pos == 1:
                return f"当前价 {tick.price:g} 已在目标上方，等回落后再次上穿才触发"
            if rule.type == "cross_down" and pos == -1:
                return f"当前价 {tick.price:g} 已在目标下方，等回升后再次下穿才触发"
        if rule.type == "near":
            return f"当前价 {tick.price:g} 尚不在 {rule.pct or 0.5}% 范围内，等价格靠近即触发"
        if rule.type == "in_range" and pos == 0:
            return f"当前价已在区间内，等离开后再次进入才触发"
        if rule.type == "out_range" and pos in (0,):
            return "当前价在区间内，突破上/下沿即触发"
        return "已就绪，等待触发"
