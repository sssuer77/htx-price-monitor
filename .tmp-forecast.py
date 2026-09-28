"""牛来-USDT 真实数据体检（订单已修正：接口按时间升序返回）。"""
import time
from urllib.parse import quote
from htxmon.config import load_config
from htxmon.netutil import http_json

cfg = load_config(); BASE = cfg["rest_base"].rstrip("/"); VERIFY = cfg.get("tls_verify", True)
SYM = "牛来-USDT"; Q = quote(SYM, safe="")


def rest(path, **p):
    url = f"{BASE}{path}?contract_code={Q}" + "".join(f"&{k}={v}" for k, v in p.items())
    return http_json(url, timeout=20, verify=VERIFY)


def klines(period, size=2000):
    return rest("/linear-swap-ex/market/history/kline", period=period, size=size).get("data") or []


def ma(v, n):
    return sum(v[-n:]) / n if len(v) >= n else float("nan")


def rsi(c, n=14):
    if len(c) < n + 1:
        return float("nan")
    g = l = 0.0
    for i in range(1, n + 1):
        ch = c[i] - c[i - 1]; g += max(ch, 0); l += max(-ch, 0)
    ag, al = g / n, l / n
    for i in range(n + 1, len(c)):
        ch = c[i] - c[i - 1]
        ag = (ag * (n - 1) + max(ch, 0)) / n
        al = (al * (n - 1) + max(-ch, 0)) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def atr(rows, n=14):
    trs = []
    for i in range(1, len(rows)):
        h, l, pc = float(rows[i]["high"]), float(rows[i]["low"]), float(rows[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / n if len(trs) >= n else float("nan")


def d(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


day, h4, h1 = klines("1day"), klines("4hour"), klines("60min")
cd_ = [float(r["close"]) for r in day]
c4 = [float(r["close"]) for r in h4]
c1 = [float(r["close"]) for r in h1]
price = float((rest("/linear-swap-ex/market/detail/merged")["tick"])["close"])

print("上市首日", d(day[0]["id"]), "｜已有", len(day), "个交易日｜现价", price)
hi = max(float(r["high"]) for r in day); lo = min(float(r["low"]) for r in day)
print(f"历史区间 {lo} ~ {hi}，现价位于 {(price-lo)/(hi-lo)*100:.1f}%")
print(f"\n日线 RSI14={rsi(cd_):.1f}  ATR14={atr(day):.5f} (={atr(day)/price*100:.1f}%)  MA7={ma(cd_,7):.5f}  MA14={ma(cd_,14):.5f}")
print(f"4h   RSI14={rsi(c4):.1f}  ATR14={atr(h4):.5f} (={atr(h4)/price*100:.1f}%)  MA20={ma(c4,20):.5f} MA50={ma(c4,50):.5f} MA100={ma(c4,100):.5f}")
print(f"1h   RSI14={rsi(c1):.1f}  MA20={ma(c1,20):.5f} MA50={ma(c1,50):.5f} MA100={ma(c1,100):.5f}")

print("\n最近 8 个 4h（时间 开 高 低 收 涨跌% 成交额U）:")
for r in h4[-8:]:
    o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
    print(f"  {time.strftime('%m-%d %H:%M', time.localtime(r['id']))}  {o:<9.5f} {h:<9.5f} {l:<9.5f} {c:<9.5f} {(c-o)/o*100:+6.2f}%  {float(r['trade_turnover']):,.0f}")

print("\n最近 24h 逐小时:")
for r in h1[-24:]:
    o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
    print(f"  {time.strftime('%m-%d %H:%M', time.localtime(r['id']))}  {c:<9.5f} {(c-o)/o*100:+6.2f}%  额={float(r['trade_turnover']):>12,.0f}")

amts = [float(r["trade_turnover"]) for r in day]
print(f"\n日成交额（USDT）近 7 日: {[f'{x/1e6:.1f}M' for x in amts[-7:]]}")
print(f"4h 成交额 近 6 根: {[f'{float(r[chr(39)+chr(39)] if False else r['trade_turnover'])/1e6:.2f}M' for r in h4[-6:]]}")

# 持仓量（data 是 list）
oi = rest("/linear-swap-api/v1/swap_open_interest")
rows = oi.get("data") or []
print("\n持仓量:", [(r.get("contract_code"), f"{r.get('amount')}币", f"{float(r.get('value') or 0):,.0f}U") for r in (rows if isinstance(rows, list) else [rows])])

his = rest("/linear-swap-api/v1/swap_his_open_interest", period="4hour", size=18, amount_type="1")
hr = his.get("data") or {}
ts_ = hr.get("tick") or []
print("持仓量近 18 个 4h:", [x.get("value") for x in ts_[::3]] if ts_ else his)

fr = rest("/linear-swap-api/v1/swap_funding_rate")
print("\n资金费率原始返回:", fr)
hfr = rest("/linear-swap-api/v1/swap_his_funding_rate", page_index=1, page_size=6)
print("历史资金费率:", hfr.get("data"))
