"""自然语言 -> 监控规则。

设计原则：
1. 纯本地正则解析，零依赖、零延迟、不联网，覆盖口语化中文的常见说法。
2. 解析不确定时给出 warnings，绝不瞎猜关键价位。
3. 复杂语句可交给 llm.py（可选）兜底，但本地解析永远是第一顺位。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from .models import Rule
from .symbols import detect_symbol, strip_symbol

# ---------------------------------------------------------------- 关键词表

# 顺序即优先级，长词在前，避免「跌破」被「跌」抢先匹配
DIRECTION_WORDS: list[tuple[tuple[str, ...], str]] = [
    (("向下跌破", "跌破", "跌穿", "下破", "失守", "下穿", "向下突破",
      "break below", "breaks below", "cross down", "crosses down", "breakdown"), "cross_down"),
    (("向上突破", "突破", "涨破", "上破", "升破", "上穿", "站上", "站稳",
      "break above", "breaks above", "break out", "breakout", "cross up", "crosses up"), "cross_up"),
    (("跌到", "降到", "回落到", "下跌到", "fall to", "falls to", "drop to", "drops to"), "cross_down"),
    (("涨到", "升到", "反弹到", "上涨到", "rise to", "rises to", "rally to"), "cross_up"),
    (("高于", "大于", "超过", "在.*之上", "above", "over", "greater than"), "above"),
    (("低于", "小于", "少于", "在.*之下", "below", "under", "less than"), "below"),
    (("触及", "碰到", "到达", "抵达", "touch", "touches", "reach", "reaches", "hit", "hits"), "touch"),
]

NEAR_WORDS = ("靠近", "接近", "逼近", "距离", "距", "near", "approaching")
PCT_UP_WORDS = ("涨幅", "涨", "拉升", "上涨", "up ")
PCT_DOWN_WORDS = ("跌幅", "跌", "下跌", "砸", "down ")
PCT_ABS_WORDS = ("振幅", "波动", "涨跌", "震荡")

REPEAT_WORDS = ("每次", "每次都", "重复", "一直提醒", "持续提醒", "every time", "repeat")

FILLER_WORDS = (
    "提醒我", "提醒一下", "提醒下", "提醒", "通知我", "通知", "告诉我", "叫我", "喊我",
    "报警", "预警", "提示我", "提示", "帮我盯着", "帮我盯", "盯着", "监控一下", "监控",
    "关注一下", "关注", "watch", "alert me", "alert", "notify me", "notify", "remind me", "remind",
)

WINDOW_UNITS = {
    "秒": 1, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "分钟": 60, "分": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "m": 60,
    "小时": 3600, "時": 3600, "时": 3600, "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600,
    "天": 86400, "日": 86400, "d": 86400, "day": 86400, "days": 86400,
}

DEFAULT_PCT_WINDOW = 300     # 「涨2%」未写周期时的默认窗口
DEFAULT_NEAR_PCT = 0.5       # 「接近 83000」未写距离时的默认百分比

# ---------------------------------------------------------------- 预处理

_FW_MAP = str.maketrans({
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4", "５": "5",
    "６": "6", "７": "7", "８": "8", "９": "9", "％": "%", "．": ".", "～": "~",
    "－": "-", "—": "-", "－": "-",
})


def _normalize(text: str) -> str:
    t = text.translate(_FW_MAP)
    t = t.replace("　", " ")
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _split_clauses(text: str) -> list[str]:
    """按连接词/标点拆分多条件；分号、顿号必定拆，逗号仅在非数字间拆。"""
    parts = re.split(r"或者|或|、|；|;|另外|同时|并且|以及", text)
    out: list[str] = []
    for p in parts:
        out.extend(re.split(r"[，]", p))
    refined: list[str] = []
    for p in out:
        refined.extend(re.split(r"(?<!\d),(?!\d)", p))
    return [c.strip() for c in refined if c.strip()]


def _clean_numbers(text: str) -> str:
    """去掉千分位逗号：83,000 -> 83000。"""
    return re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)


# ---------------------------------------------------------------- 数值解析

_NUM = r"\d+(?:\.\d+)?"


def _parse_amount(num: str, unit: str | None) -> float:
    v = float(num)
    if not unit:
        return v
    u = unit.lower()
    if u in ("万", "w"):
        return v * 10_000
    if u in ("千", "k"):
        return v * 1_000
    if u in ("亿",):
        return v * 100_000_000
    return v


@dataclass
class _Slots:
    """从句子里抽出来的要素。"""

    window_sec: int | None = None
    pct: float | None = None
    range_low: float | None = None
    range_high: float | None = None
    level: float | None = None
    pct_dir: str | None = None      # up / down / abs
    near: bool = False
    direction: str | None = None
    range_kw: bool = False
    leftover: str = ""
    notes: list[str] = field(default_factory=list)


def _extract(text: str) -> _Slots:
    s = _Slots()
    work = _clean_numbers(_normalize(text))

    # 1) 时间窗口：X分钟 / X小时 / Xm / Xh
    def _win_repl(m: re.Match) -> str:
        unit = (m.group(2) or "").lower()
        if unit in WINDOW_UNITS:
            s.window_sec = int(float(m.group(1)) * WINDOW_UNITS[unit])
            return " "
        return m.group(0)

    win_pat = (r"(\d+(?:\.\d+)?)\s*"
               r"(分钟|小时|時|时|分|秒|天|日|seconds?|minutes?|hours?|secs?|mins?|hrs?|days?|ms|[smhd])")
    work = re.sub(win_pat, _win_repl, work, flags=re.IGNORECASE)

    # 2) 百分比
    pct_pat = r"(\d+(?:\.\d+)?)\s*(?:%|个点|个百分点|percent)"
    m = re.search(pct_pat, work)
    if m:
        s.pct = float(m.group(1))
        work = work[:m.start()] + " " + work[m.end():]
    else:
        m2 = re.search(r"百分之\s*(\d+(?:\.\d+)?)", work)
        if m2:
            s.pct = float(m2.group(1))
            work = work[:m2.start()] + " " + work[m2.end():]

    # 3) 区间：A到B / A-B / A~B / A至B
    rng_pat = rf"({_NUM})\s*(?:万|w|k|千)?\s*(?:到|至|~|-|—|–|and)\s*({_NUM})\s*(万|w|k|千)?"
    m = re.search(rng_pat, work)
    if m:
        lo_u = re.search(r"(万|w|k|千)", m.group(0)[:m.start(2) - m.start(0)])
        hi_u = m.group(3)
        s.range_low = _parse_amount(m.group(1), lo_u.group(1) if lo_u else None)
        s.range_high = _parse_amount(m.group(2), hi_u)
        if s.range_low > s.range_high:
            s.range_low, s.range_high = s.range_high, s.range_low
        work = work[:m.start()] + " " + work[m.end():]
        s.range_kw = True

    # 4) 方向词
    low = work.lower()
    for words, rtype in DIRECTION_WORDS:
        for w in words:
            if re.search(w, low):
                s.direction = rtype
                work = re.sub(w, " ", work, flags=re.IGNORECASE)
                low = work.lower()
                break
        if s.direction:
            break

    # 5) 接近 / 突破区间 类关键词
    if any(re.search(w, low) for w in NEAR_WORDS):
        s.near = True
        for w in NEAR_WORDS:
            work = re.sub(w, " ", work, flags=re.IGNORECASE)
    if re.search(r"区间|之间|范围内|between", low):
        s.range_kw = True
        work = re.sub(r"区间|之间|范围内|between", " ", work, flags=re.IGNORECASE)
    if s.range_low is not None:
        # 区间语境：「跌出 / 涨出 / 脱离」= 离开区间；「回到 / 回落进」= 重新进入
        if re.search(r"突破|跌出|涨出|升出|冲出|脱离|离开|失守", low):
            s.direction = "out_range"
        elif re.search(r"进入|落入|回到|重回|回踩到|inside", low):
            s.direction = "in_range"
    elif re.search(r"进入|落入|回到区间", low):
        s.direction = "in_range"

    # 6) 百分比方向
    if s.pct is not None:
        if any(w in low for w in PCT_ABS_WORDS):
            s.pct_dir = "abs"
        elif any(w in low for w in PCT_DOWN_WORDS):
            s.pct_dir = "down"
        elif any(w in low for w in PCT_UP_WORDS):
            s.pct_dir = "up"
        else:
            s.pct_dir = "abs"
        for w in PCT_ABS_WORDS + PCT_DOWN_WORDS + PCT_UP_WORDS:
            work = work.replace(w, " ")

    # 7) 剩下的数字当价位
    m = re.search(rf"({_NUM})\s*(万|w|k|千|亿)?", work)
    if m:
        s.level = _parse_amount(m.group(1), m.group(2))
        work = work[:m.start()] + " " + work[m.end():]
    s.leftover = re.sub(r"\s+", " ", work).strip(" .,:：,。!！?？~-")
    return s


# ---------------------------------------------------------------- 组装规则


def _build_rule(sym: str, raw: str, s: _Slots, warnings: list[str]) -> Rule | None:
    common = dict(symbol=sym, raw=raw)

    if s.pct is not None and s.level is None and not s.range_kw:
        win = s.window_sec or DEFAULT_PCT_WINDOW
        if s.window_sec is None:
            warnings.append(f"「{raw}」未指定时间周期，已按 {win // 60} 分钟处理")
        rtype = {"up": "pct_up", "down": "pct_down", "abs": "pct_abs"}[s.pct_dir or "abs"]
        return Rule(type=rtype, pct=s.pct, window_sec=win, **common)

    if s.range_low is not None and s.range_high is not None:
        if s.direction in ("cross_up", "above"):
            rtype = "out_range"
        elif s.direction in ("cross_down", "below"):
            rtype = "out_range"
        elif s.direction in ("in_range", "out_range"):
            rtype = s.direction
        else:
            rtype = "in_range"
        return Rule(type=rtype, level=s.range_low, level2=s.range_high, **common)

    if s.level is None:
        warnings.append(f"没看懂这句（缺少价位）：{raw}")
        return None

    if s.near:
        pct = s.pct if s.pct is not None else DEFAULT_NEAR_PCT
        if s.pct is None:
            warnings.append(f"「{raw}」未指定接近距离，已按 {DEFAULT_NEAR_PCT}% 处理")
        return Rule(type="near", level=s.level, pct=pct, **common)

    if s.pct is not None:
        win = s.window_sec or DEFAULT_PCT_WINDOW
        rtype = {"up": "pct_up", "down": "pct_down", "abs": "pct_abs"}[s.pct_dir or "abs"]
        return Rule(type=rtype, level=s.level, pct=s.pct, window_sec=win, **common)

    rtype = s.direction or "touch"
    if rtype in ("in_range", "out_range"):
        rtype = "touch"
    return Rule(type=rtype, level=s.level, **common)


@dataclass
class ParseResult:
    rules: list[Rule] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.rules)


def parse(text: str, default_symbol: str | None = None,
          known_symbols: Iterable[str] | None = None) -> ParseResult:
    """把一句（或几句）自然语言解析成监控规则。

    known_symbols 传当前监控列表，这样别名表里没有的中文合约（如「牛来-USDT」）
    也能直接用中文名建立规则。
    """
    res = ParseResult()
    if not text or not text.strip():
        res.warnings.append("内容为空")
        return res

    clauses = _split_clauses(_normalize(text))
    if not clauses:
        clauses = [text]

    last_symbol: str | None = default_symbol
    for clause in clauses:
        sym, matched = detect_symbol(clause, known_symbols)
        body = strip_symbol(clause, matched)
        if sym is None:
            if last_symbol:
                sym = last_symbol
                res.warnings.append(f"「{clause}」未写币种，沿用 {last_symbol}")
            else:
                res.warnings.append(f"没识别出币种：{clause}")
                continue
        last_symbol = sym

        s = _extract(body)
        # 冷却 / 重复
        repeat = "always" if any(w in clause for w in REPEAT_WORDS) else "once"
        rule = _build_rule(sym, clause, s, res.warnings)
        if rule is None:
            continue
        rule.repeat = repeat
        if repeat == "always" and rule.cooldown_sec <= 0:
            rule.cooldown_sec = 300
        # 清理原话里残留的连接词与语气词
        note = clause
        for w in FILLER_WORDS:
            note = note.replace(w, "")
        rule.note = re.sub(r"\s+", " ", note).strip(" ，,。.！!?？") or clause
        res.rules.append(rule)

    if not res.rules and not res.warnings:
        res.warnings.append("没有解析出任何监控条件")
    return res
