"""币种识别：把「比特币 / 大饼 / btc / BTCUSDT」统一成 HTX 永续合约代码 BTC-USDT。"""

from __future__ import annotations

import re

# 中文名 / 英文别名 -> 合约基础币种
ALIASES: dict[str, str] = {
    # 主流
    "btc": "BTC", "比特币": "BTC", "大饼": "BTC", "比特": "BTC", "bitcoin": "BTC", "xbt": "BTC",
    "eth": "ETH", "以太坊": "ETH", "以太": "ETH", "二饼": "ETH", "ethereum": "ETH",
    "sol": "SOL", "索拉纳": "SOL", "solana": "SOL",
    "xrp": "XRP", "瑞波": "XRP", "瑞波币": "XRP", "ripple": "XRP",
    "bnb": "BNB", "币安币": "BNB", "binance": "BNB",
    "doge": "DOGE", "狗狗币": "DOGE", "狗币": "DOGE", "dogecoin": "DOGE",
    "ada": "ADA", "艾达": "ADA", "艾达币": "ADA", "cardano": "ADA",
    "trx": "TRX", "波场": "TRX", "tron": "TRX",
    "ltc": "LTC", "莱特币": "LTC", "litecoin": "LTC",
    "bch": "BCH", "比特现金": "BCH", "比特耶稣": "BCH",
    "link": "LINK", "链环": "LINK", "chainlink": "LINK",
    "dot": "DOT", "波卡": "DOT", "polkadot": "DOT",
    "avax": "AVAX", "雪崩": "AVAX",
    "atom": "ATOM", "阿童木": "ATOM", "cosmos": "ATOM",
    "uni": "UNI", "uniswap": "UNI",
    "aave": "AAVE",
    "etc": "ETC", "以太经典": "ETC",
    "fil": "FIL", "文件币": "FIL",
    "near": "NEAR",
    "sui": "SUI",
    "apt": "APT", "aptos": "APT",
    "arb": "ARB", "arbitrum": "ARB",
    "op": "OP", "optimism": "OP",
    "ton": "TON",
    "icp": "ICP",
    "xlm": "XLM", "恒星币": "XLM",
    "hbar": "HBAR",
    "algo": "ALGO",
    "vet": "VET", "唯链": "VET",
    "xmr": "XMR", "门罗": "XMR", "门罗币": "XMR", "monero": "XMR",
    "zec": "ZEC", "大零币": "ZEC", "zcash": "ZEC",
    "dash": "DASH", "达世": "DASH", "达世币": "DASH",
    "trump": "TRUMP",
    "pepe": "PEPE",
    "shib": "SHIB", "屎币": "SHIB",
    "wif": "WIF",
    "ena": "ENA",
    "ondo": "ONDO",
    "tao": "TAO",
    "jup": "JUP",
    "pyth": "PYTH",
    "sei": "SEI",
    "tia": "TIA",
    "inj": "INJ",
    "gala": "GALA",
    "crv": "CRV",
    "cfx": "CFX", "Conflux": "CFX",
    "filcoin": "FIL",
    "ldo": "LDO",
    "wld": "WLD", "世界币": "WLD",
    "hype": "HYPE",
    "sats": "SATS",
    "ordi": "ORDI",
    "gmx": "GMX",
    "dydx": "DYDX",
    "strk": "STRK",
    "zk": "ZK",
    "blur": "BLUR",
    "pengu": "PENGU",
    "virtual": "VIRTUAL",
    "kas": "KAS", "kaspa": "KAS",
    "xdc": "XDC",
    "xvg": "XVG",
    "rlc": "RLC",
    "zen": "ZEN",
    "epic": "EPIC",
}

# 别名按长度倒序匹配，避免 "eth" 命中 "ethereum" 的前缀这类问题
_SORTED_ALIASES = sorted(ALIASES.keys(), key=len, reverse=True)

_CONTRACT_RE = re.compile(
    r"\b([A-Za-z0-9]{2,12})\s*[-/_]?\s*(usdt|usd)\b",
    re.IGNORECASE,
)


def to_contract(base: str) -> str:
    """基础币种 -> HTX 合约代码。"""
    return f"{base.upper()}-USDT"


def detect_symbol(text: str) -> tuple[str | None, str | None]:
    """从文本中识别币种。

    返回 (合约代码, 命中的原文片段)；无法识别时返回 (None, None)。
    优先识别显式合约写法（BTC-USDT / btcusdt），其次识别中英文别名。
    """
    if not text:
        return None, None

    m = _CONTRACT_RE.search(text)
    if m:
        base = m.group(1).upper()
        quoted = base.lower() in ALIASES or base in ALIASES.values()
        if quoted or len(base) >= 2:
            return to_contract(base), m.group(0)

    lowered = text.lower()
    for alias in _SORTED_ALIASES:
        idx = lowered.find(alias)
        while idx != -1:
            before = lowered[idx - 1] if idx > 0 else ""
            after = lowered[idx + len(alias)] if idx + len(alias) < len(lowered) else ""
            # 英文别名要求词边界，中文别名不需要
            if alias.isascii():
                boundary_ok = not (before.isalnum() or after.isalnum())
            else:
                boundary_ok = True
            if boundary_ok:
                return to_contract(ALIASES[alias]), text[idx:idx + len(alias)]
            idx = lowered.find(alias, idx + 1)
    return None, None


def strip_symbol(text: str, matched: str | None) -> str:
    """把已识别的币种片段从原句中移除，便于后续解析数字与方向词。"""
    if not matched:
        return text
    return text.replace(matched, " ", 1)
