"""规则数据模型。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

# 支持的规则类型
RULE_TYPES = {
    "cross_up": "上穿（先在下方向，向上穿越价位）",
    "cross_down": "下穿（先在上方向，向下穿越价位）",
    "above": "站上（价格 >= 价位）",
    "below": "跌破（价格 <= 价位）",
    "touch": "触及（任意方向到达价位附近）",
    "near": "接近（距离价位在 X% 以内）",
    "pct_up": "区间涨幅（window 内涨幅 >= X%）",
    "pct_down": "区间跌幅（window 内跌幅 >= X%）",
    "pct_abs": "区间振幅（window 内涨跌绝对值 >= X%）",
    "in_range": "进入区间（价格落入 [low, high]）",
    "out_range": "突破区间（价格离开 [low, high]）",
}


def _new_id() -> str:
    return "r_" + uuid.uuid4().hex[:10]


@dataclass
class Rule:
    """一条价格监控规则。"""

    symbol: str                       # BTC-USDT
    type: str                         # 见 RULE_TYPES
    raw: str = ""                     # 用户原话
    level: float | None = None        # 主价位
    level2: float | None = None       # 区间上沿 / 备用价位
    pct: float | None = None          # 百分比
    window_sec: int | None = None     # 时间窗口（秒）
    repeat: str = "once"              # once | always
    cooldown_sec: int = 300           # always 模式下的冷却时间
    channels: list[str] = field(default_factory=list)  # 空 = 用全局默认
    note: str = ""                    # 备注
    id: str = field(default_factory=_new_id)
    created_at: float = field(default_factory=time.time)
    status: str = "active"            # active | triggered | disabled
    state: dict[str, Any] = field(default_factory=dict)

    # ---- 序列化 ----
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rule":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    # ---- 展示 ----
    @property
    def type_label(self) -> str:
        return RULE_TYPES.get(self.type, self.type)

    def describe(self) -> str:
        lv = _fmt(self.level)
        lv2 = _fmt(self.level2)
        t = self.type
        if t == "cross_up":
            body = f"{self.symbol} 上穿 {lv}"
        elif t == "above":
            body = f"{self.symbol} 高于 {lv}"
        elif t == "cross_down":
            body = f"{self.symbol} 下穿 {lv}"
        elif t == "below":
            body = f"{self.symbol} 低于 {lv}"
        elif t == "touch":
            body = f"{self.symbol} 触及 {lv}"
        elif t == "near":
            body = f"{self.symbol} 靠近 {lv}（{_fmt(self.pct)}% 内）"
        elif t == "pct_up":
            body = f"{self.symbol} {_win(self.window_sec)}内涨幅 >= {_fmt(self.pct)}%"
        elif t == "pct_down":
            body = f"{self.symbol} {_win(self.window_sec)}内跌幅 >= {_fmt(self.pct)}%"
        elif t == "pct_abs":
            body = f"{self.symbol} {_win(self.window_sec)}内振幅 >= {_fmt(self.pct)}%"
        elif t == "in_range":
            body = f"{self.symbol} 进入区间 {lv} ~ {lv2}"
        elif t == "out_range":
            body = f"{self.symbol} 突破区间 {lv} ~ {lv2}"
        else:
            body = f"{self.symbol} {t} {lv}"
        return body + ("" if self.repeat == "once" else "（可重复）")


def _fmt(v: float | None) -> str:
    if v is None:
        return "-"
    if abs(v) >= 1000:
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if abs(v) >= 1:
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return f"{v:.8f}".rstrip("0").rstrip(".")


def _win(sec: int | None) -> str:
    if not sec:
        return "短时间内"
    if sec % 3600 == 0:
        return f"{sec // 3600}小时"
    if sec % 60 == 0:
        return f"{sec // 60}分钟"
    return f"{sec}秒"
