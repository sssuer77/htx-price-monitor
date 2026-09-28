"""命令行入口：python -m htxmon"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser
from urllib.parse import quote

from . import __version__, netutil
from .config import ROOT, ensure_example, load_config
from .engine import RuleEngine
from .feed import PriceFeed
from .notify import Notifier
from .parser import parse
from .server import App, build_server
from .store import EventLog, RuleStore
from .wsclient import WebSocketClient


def cmd_parse(text: str) -> int:
    res = parse(text)
    print(json.dumps({
        "input": text,
        "rules": [{"describe": r.describe(), **r.to_dict()} for r in res.rules],
        "warnings": res.warnings,
    }, ensure_ascii=False, indent=2))
    return 0 if res.ok else 1


def cmd_check(cfg: dict) -> int:
    ok = True
    cert = netutil.cert_info()
    print(f"[证书] 根证书 {cert['ca_count']} 个，校验={'开启' if cert['verifying'] else '关闭'} "
          f"来源={cert.get('bundle') or '系统默认'}")
    if cert["ca_count"] == 0:
        ok = False
        print("  ! 未找到任何根证书，HTTPS 将无法验证（可在 config.json 设 tls_verify=false 临时绕过）")

    sym = (cfg.get("symbols") or ["BTC-USDT"])[0]
    try:
        t0 = time.time()
        d = netutil.http_json(
            f"{cfg['rest_base'].rstrip('/')}/linear-swap-ex/market/detail/merged"
            f"?contract_code={quote(sym, safe='')}",
            timeout=10, verify=cfg.get("tls_verify", True))
        ms = (time.time() - t0) * 1000
        print(f"[REST] {sym} 最新价 {d['tick']['close']}，往返 {ms:.0f}ms")
    except Exception as exc:
        ok = False
        print(f"[REST] 失败: {exc}")

    if cfg.get("enable_ws", True):
        ws = WebSocketClient(cfg["ws_url"], timeout=8, verify=cfg.get("tls_verify", True))
        try:
            t0 = time.time()
            ws.connect()
            ws.send_text(json.dumps({"sub": f"market.{sym}.trade.detail", "id": "check"}))
            got = None
            deadline = time.time() + 10
            while time.time() < deadline:
                msg = ws.recv_text()
                if msg and "trade.detail" in msg:
                    got = msg
                    break
            if got:
                exch = json.loads(got).get("tick", {}).get("data", [{}])[-1].get("ts")
                lat = (time.time() - t0) * 1000
                print(f"[WS]   {cfg['ws_url']} 订阅成功，{lat:.0f}ms 内收到首条推送")
            else:
                ok = False
                print("[WS]   连接成功但 10 秒内没收到推送（可能被防火墙/代理拦截）")
        except Exception as exc:
            ok = False
            print(f"[WS]   失败: {exc}（将自动以 REST 轮询兜底，不影响监控）")
        finally:
            ws.close()
    print("\n诊断结果:", "全部通过 ✅" if ok else "存在告警 ⚠（见上方说明）")
    return 0 if ok else 1


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = load_config()
    ensure_example()
    if args.port:
        cfg["port"] = args.port
    if args.host:
        cfg["host"] = args.host
    if args.no_ws:
        cfg["enable_ws"] = False

    stop_evt = threading.Event()
    app = App(cfg)
    app.start()

    ball = None
    if cfg.get("ball", {}).get("enabled", True) and not args.no_ball:
        from .ball import FloatingBall

        ball = FloatingBall(app, on_quit=stop_evt.set)
        app.ball = ball
        app.notifier.ball = ball
        ball.start()

    httpd = build_server(app, cfg["host"], int(cfg["port"]))
    url = f"http://{cfg['host']}:{cfg['port']}/"
    print("=" * 62)
    print(f"  HTX 合约价格监控 v{__version__}")
    print(f"  控制台: {url}")
    print("  提示: 首次运行建议先执行  python -m htxmon --check  做连通性自检")
    print(f"  监控合约: {', '.join(cfg.get('symbols', []))}")
    print(f"  已有规则: {len(app.store.all())} 条")
    print(f"  悬浮球: {'已开启（可拖动，右键有菜单）' if ball else '已关闭'}")
    print("  停止: Ctrl+C")
    print("=" * 62)
    if cfg.get("open_browser", True) and not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    httpd_thread = threading.Thread(target=httpd.serve_forever,
                                    kwargs={"poll_interval": 0.3},
                                    name="http", daemon=True)
    httpd_thread.start()
    try:
        stop_evt.wait()
    except KeyboardInterrupt:
        print("\n正在退出…")
    finally:
        app.stop()
        if ball:
            ball.stop()
        httpd.shutdown()
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="htxmon", description="HTX 合约自然语言价格监控")
    ap.add_argument("--version", action="version", version=f"htxmon {__version__}")
    ap.add_argument("--parse", metavar="TEXT", help="只解析一句话并打印规则，不启动服务")
    ap.add_argument("--check", action="store_true", help="网络/证书/WS 连通性自检")
    ap.add_argument("--host", help="监听地址，默认取 config.json")
    ap.add_argument("--port", type=int, help="监听端口，默认取 config.json")
    ap.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    ap.add_argument("--no-ws", action="store_true", help="强制关闭 WebSocket，仅用 REST 轮询")
    ap.add_argument("--no-ball", action="store_true", help="不显示桌面悬浮球")
    args = ap.parse_args(argv)

    cfg = load_config()
    if args.parse:
        return cmd_parse(args.parse)
    if args.check:
        return cmd_check(cfg)
    return cmd_serve(args)


if __name__ == "__main__":
    sys.exit(main())
