import time
from urllib.parse import quote
from htxmon.config import load_config
from htxmon.netutil import http_json
cfg = load_config(); BASE = cfg["rest_base"].rstrip("/"); V = cfg.get("tls_verify", True)
Q = quote("牛来-USDT", safe="")

def rest(path, **p):
    url = f"{BASE}{path}?contract_code={Q}" + "".join(f"&{k}={v}" for k, v in p.items())
    return http_json(url, timeout=20, verify=V)

day = rest("/linear-swap-ex/market/history/kline", period="1day", size=2000)["data"]
print("全部日线（升序）:")
print("日期        开        高        低        收      涨跌%    成交额U     张数")
for r in day:
    o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
    print(f"{time.strftime('%m-%d', time.localtime(r['id']))}  {o:<9.5f} {h:<9.5f} {l:<9.5f} {c:<9.5f} {(c-o)/o*100:+7.2f}% {float(r['trade_turnover']):>10,.0f} {float(r['vol']):>12,.0f}")

ob = rest("/linear-swap-ex/market/depth", type="step6")["tick"]
b, a = ob["bids"], ob["asks"]
mid = (float(b[0][0]) + float(a[0][0])) / 2
print(f"\n盘口 step6 共 {len(b)}/{len(a)} 档，中间价 {mid}")
print(f"买一 {b[0][0]} x {float(b[0][1])*10:.0f}币   卖一 {a[0][0]} x {float(a[0][1])*10:.0f}币   价差 {(float(a[0][0])-float(b[0][0]))/mid*100:.4f}%")
for name, side in (("买盘", b), ("卖盘", a)):
    cum = 0.0
    for lvl in side:
        cum += float(lvl[0]) * float(lvl[1]) * 10
    print(f"{name} 全档名义 {cum:,.0f} USDT（前 5 档 {sum(float(p)*float(v)*10 for p,v in side[:5]):,.0f}）")
pw = 0.05
print(f"\n滑点测算（按 ±{pw*100:.0f}% 内的挂单量）:")
for name, side, sign in (("买入(吃卖盘)", a, 1), ("卖出(吃买盘)", b, -1)):
    cum = 0.0
    for p, v in side:
        if sign * (float(p) - mid) / mid > pw:
            break
        cum += float(p) * float(v) * 10
    print(f"  {name}: {cum:,.0f} USDT")
