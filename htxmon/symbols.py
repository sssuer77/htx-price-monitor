"""币种识别：把「比特币 / 大饼 / btc / BTCUSDT / 牛来」统一成 HTX 永续合约代码。"""

from __future__ import annotations

import re
from collections.abc import Iterable

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
    # HTX 真的把汉字当合约代码（链上迷因币），这些不是笔误
    "牛来": "牛来", "哈基米": "哈基米", "币安人生": "币安人生", "龙虾": "龙虾",
}

# 别名按长度倒序匹配，避免 "eth" 命中 "ethereum" 的前缀这类问题
_SORTED_ALIASES = sorted(ALIASES.keys(), key=len, reverse=True)

# 显式合约写法：BTC-USDT / btcusdt / 1000PEPE-USDT
_CONTRACT_ASCII_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z0-9]{2,15})\s*[-/_]?\s*(?:usdt|usd)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# 中文合约代码：牛来-USDT / 哈基米usdt（HTX 真有这种代码）
_CONTRACT_CJK_RE = re.compile(
    r"([\u4e00-\u9fff][\u4e00-\u9fff0-9]{0,14})\s*[-/_]?\s*(?:usdt|usd)(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def to_contract(base: str) -> str:
    """基础币种 -> HTX 合约代码。ASCII 统一大写，汉字原样保留。"""
    base = base.strip()
    return f"{base.upper() if base.isascii() else base}-USDT"


def _boundary_ok(haystack: str, idx: int, token: str) -> bool:
    """ASCII 片段要求词边界（别让 eth 命中 ethereum），中文片段不需要。"""
    if not token.isascii():
        return True
    before = haystack[idx - 1] if idx > 0 else ""
    after = haystack[idx + len(token)] if idx + len(token) < len(haystack) else ""
    return not ((before.isascii() and before.isalnum())
                or (after.isascii() and after.isalnum()))


def _known_candidates(known: Iterable[str] | None) -> list[tuple[str, str]]:
    """把监控列表拆成 (合约代码, 可匹配片段)，长片段优先匹配。

    例如 1000PEPE-USDT 既可以用全称，也可以只写 1000PEPE。
    """
    out: list[tuple[str, str]] = []
    for code in known or ():
        text = str(code or "").strip()
        if not text:
            continue
        out.append((text, text))
        base = text.rpartition("-")[0]
        if base and base != text:
            out.append((text, base))
    out.sort(key=lambda pair: len(pair[1]), reverse=True)
    return out


def detect_symbol(text: str,
                  known: Iterable[str] | None = None) -> tuple[str | None, str | None]:
    """从文本中识别币种。

    返回 (合约代码, 命中的原文片段)；无法识别时返回 (None, None)。
    优先级：监控列表里的合约 > 显式合约写法（BTC-USDT / 牛来-USDT）> 中英文别名。
    known 传当前监控列表，这样别名表里没有的中文合约（如「牛来」）也能直接用中文建规则。
    """
    if not text:
        return None, None

    lowered = text.lower()

    for code, token in _known_candidates(known):
        idx = lowered.find(token.lower())
        while idx != -1:
            if _boundary_ok(lowered, idx, token):
                return code, text[idx:idx + len(token)]
            idx = lowered.find(token.lower(), idx + 1)

    m = _CONTRACT_ASCII_RE.search(text) or _CONTRACT_CJK_RE.search(text)
    if m:
        base = m.group(1)
        if not base.isascii():          # 比特币USDT 这类写法回退到别名表
            base = ALIASES.get(base, base)
        return to_contract(base), m.group(0)

    for alias in _SORTED_ALIASES:
        idx = lowered.find(alias)
        while idx != -1:
            if _boundary_ok(lowered, idx, alias):
                return to_contract(ALIASES[alias]), text[idx:idx + len(alias)]
            idx = lowered.find(alias, idx + 1)
    return None, None


def strip_symbol(text: str, matched: str | None) -> str:
    """把已识别的币种片段从原句中移除，便于后续解析数字与方向词。"""
    if not matched:
        return text
    return text.replace(matched, " ", 1)
