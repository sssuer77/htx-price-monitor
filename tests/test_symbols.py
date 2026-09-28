"""监控合约增删的测试（不联网）。"""

import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from htxmon.feed import PriceFeed, Tick  # noqa: E402
from htxmon.parser import parse  # noqa: E402
from htxmon.server import Handler, _normalize_symbol  # noqa: E402
from htxmon.symbols import detect_symbol  # noqa: E402


class TestNormalize(unittest.TestCase):
    def test_bare_code(self):
        self.assertEqual(_normalize_symbol("doge"), "DOGE-USDT")

    def test_already_full(self):
        self.assertEqual(_normalize_symbol("BTC-USDT"), "BTC-USDT")

    def test_slash_and_underscore(self):
        self.assertEqual(_normalize_symbol("btc/usdt"), "BTC-USDT")
        self.assertEqual(_normalize_symbol("eth_usdt"), "ETH-USDT")

    def test_usd_quote(self):
        self.assertEqual(_normalize_symbol("sol-usd"), "SOL-USDT")

    def test_lowercase_and_spaces(self):
        self.assertEqual(_normalize_symbol("  xrp  "), "XRP-USDT")

    def test_invalid(self):
        for bad in ("", None, "!!", "-USDT", "TOOLONGCODENAME21-USDT", "btc-busd",
                    "BTC-USDT-EXTRA", "USDT"):
            self.assertIsNone(_normalize_symbol(bad), f"应判为非法: {bad!r}")

    def test_chinese_accepted(self):
        # HTX 真的用汉字当合约代码：「牛来-USDT」「哈基米-USDT」「币安人生-USDT」
        # 都是链上迷因币的真实代码，必须放行（曾经被误判为非法而加不进去）。
        self.assertEqual(_normalize_symbol("牛来-USDT"), "牛来-USDT")
        self.assertEqual(_normalize_symbol("牛来"), "牛来-USDT")
        self.assertEqual(_normalize_symbol("  牛来  "), "牛来-USDT")
        self.assertEqual(_normalize_symbol("哈基米"), "哈基米-USDT")

    def test_symbols_with_junk_rejected(self):
        for bad in ("牛 来!", "<<BTC>>", "BTC!", "BTC-USDT-EXTRA", "btc@usdt"):
            self.assertIsNone(_normalize_symbol(bad), f"应判为非法: {bad!r}")

    def test_space_as_separator(self):
        # 「BTC USDT」不应被拼成 BTCUSDT-USDT
        self.assertEqual(_normalize_symbol("BTC USDT"), "BTC-USDT")
        self.assertEqual(_normalize_symbol("btc  -  usdt"), "BTC-USDT")


class TestFeedSymbols(unittest.TestCase):
    def setUp(self):
        self.feed = PriceFeed({"symbols": ["BTC-USDT", "ETH-USDT"], "rest_base": "https://x",
                               "ws_url": "wss://x", "tls_verify": True})

    def test_add_and_has(self):
        self.assertFalse(self.feed.has_symbol("doge-usdt"))
        self.assertTrue(self.feed.add_symbol("doge-usdt"))
        self.assertTrue(self.feed.has_symbol("DOGE-USDT"))
        self.assertIn("DOGE-USDT", self.feed.symbols)

    def test_add_twice_is_noop(self):
        self.assertTrue(self.feed.add_symbol("DOGE-USDT"))
        self.assertFalse(self.feed.add_symbol("DOGE-USDT"))
        self.assertEqual(self.feed.symbols.count("DOGE-USDT"), 1)

    def test_remove_purges_tick_and_history(self):
        self.feed.add_symbol("DOGE-USDT")
        self.feed._ticks["DOGE-USDT"] = Tick(symbol="DOGE-USDT", price=0.2,
                                             local_ts=1.0, src="rest")
        self.feed._history["DOGE-USDT"].append((1.0, 0.2))
        self.assertTrue(self.feed.remove_symbol("DOGE-USDT"))
        self.assertNotIn("DOGE-USDT", self.feed.symbols)
        self.assertIsNone(self.feed.tick("DOGE-USDT"))
        self.assertIsNone(self.feed.price_ago("DOGE-USDT", 60))

    def test_remove_missing_returns_false(self):
        self.assertFalse(self.feed.remove_symbol("NOPE-USDT"))


class TestContractInfo(unittest.TestCase):
    """fetch_contract_info 的三种结论（用假 http 客户端，不联网）。"""

    def setUp(self):
        self.feed = PriceFeed({"symbols": [], "rest_base": "https://x",
                               "ws_url": "wss://x", "tls_verify": True})

    def test_exists(self):
        payload = {"status": "ok", "data": [{"contract_code": "BTC-USDT",
                                             "contract_size": 0.001}]}
        with mock.patch("htxmon.feed.http_json", return_value=payload):
            verdict, info = self.feed.fetch_contract_info("BTC-USDT")
        self.assertEqual(verdict, "ok")
        self.assertEqual(info["contract_size"], 0.001)

    def test_not_exists(self):
        payload = {"status": "error", "err_msg": "This contract doesnt exist."}
        with mock.patch("htxmon.feed.http_json", return_value=payload):
            verdict, info = self.feed.fetch_contract_info("NOPE-USDT")
        self.assertEqual(verdict, "none")
        self.assertIn("exist", info["error"])

    def test_network_error_is_err_not_none(self):
        # 连不上时不能判成「合约不存在」，否则断网会让人加不了任何合约
        with mock.patch("htxmon.feed.http_json", side_effect=OSError("boom")):
            verdict, _ = self.feed.fetch_contract_info("BTC-USDT")
        self.assertEqual(verdict, "err")

    def test_url_is_ascii_quoted(self):
        seen = {}

        def fake(url, **kw):
            seen["url"] = url
            return {"status": "ok", "data": [{"contract_code": "X"}]}

        with mock.patch("htxmon.feed.http_json", side_effect=fake):
            self.feed.fetch_contract_info("BTC-USDT")
        self.assertIn("contract_code=BTC-USDT", seen["url"])
        self.assertTrue(seen["url"].isascii())

    def test_chinese_symbol_is_percent_encoded(self):
        # 这是「加进去了却永远没行情」的真正根因：中文合约代码必须百分号编码，
        # 否则 urllib 会抛 "'ascii' codec can't encode characters"。
        seen = {}

        def fake(url, **kw):
            seen["url"] = url
            return {"status": "ok", "data": [{"contract_code": "牛来-USDT"}]}

        with mock.patch("htxmon.feed.http_json", side_effect=fake):
            verdict, _ = self.feed.fetch_contract_info("牛来-USDT")
        self.assertEqual(verdict, "ok")
        self.assertTrue(seen["url"].isascii(), "拼出来的 URL 必须是纯 ASCII")
        self.assertIn("%E7%89%9B%E6%9D%A5-USDT", seen["url"])

    def test_kline_and_rest_urls_are_encoded(self):
        """行情和历史 K 线两条链路也必须编码（曾经漏了）。"""
        seen = []
        self.feed.symbols = ["牛来-USDT"]

        def fake(url, **kw):
            seen.append(url)
            return {"status": "ok", "data": [],
                    "tick": {"close": "0.112", "open": "0.1", "high": "0.12", "low": "0.09"}}

        with mock.patch("htxmon.feed.http_json", side_effect=fake):
            self.feed._fetch_rest("牛来-USDT")
            self.feed._bootstrap_history()
        self.assertEqual(len(seen), 2, f"应各请求一次，实际 {seen}")
        for url in seen:
            self.assertTrue(url.isascii(), f"URL 未编码: {url}")
            self.assertIn("%E7%89%9B%E6%9D%A5-USDT", url)


class TestPerSymbolErrors(unittest.TestCase):
    """面板要能显示「哪个合约拉不到行情」。"""

    def setUp(self):
        self.feed = PriceFeed({"symbols": ["BTC-USDT"], "rest_base": "https://x",
                               "ws_url": "wss://x", "tls_verify": True})

    def test_mark_bad_then_ok(self):
        self.assertEqual(self.feed.status["bad"], {})
        self.feed._mark_bad("NOPE-USDT", "not exist")
        self.assertEqual(self.feed.status["bad"]["NOPE-USDT"], "not exist")
        self.feed._mark_ok("NOPE-USDT")
        self.assertNotIn("NOPE-USDT", self.feed.status["bad"])


class TestChineseSymbolDetect(unittest.TestCase):
    """中文合约代码的识别（HTX 的 牛来-USDT / 哈基米-USDT 不是笔误）。"""

    def test_bare_chinese_name(self):
        self.assertEqual(detect_symbol("牛来 跌破 0.1")[0], "牛来-USDT")

    def test_full_chinese_code(self):
        code, matched = detect_symbol("牛来-USDT 跌破 0.1")
        self.assertEqual(code, "牛来-USDT")
        self.assertEqual(matched, "牛来-USDT")

    def test_chinese_glued_usdt(self):
        self.assertEqual(detect_symbol("哈基米usdt 涨到 0.05")[0], "哈基米-USDT")

    def test_alias_beats_literal_chinese_suffix(self):
        self.assertEqual(detect_symbol("比特币USDT 跌破 83000")[0], "BTC-USDT")

    def test_known_symbols_enable_other_chinese_names(self):
        self.assertEqual(detect_symbol("拉布布 涨到 1", ["拉布布-USDT"])[0], "拉布布-USDT")

    def test_known_symbols_longest_first(self):
        known = ["1000PEPE-USDT", "PEPE-USDT"]
        self.assertEqual(detect_symbol("1000PEPE 跌破 0.01", known)[0], "1000PEPE-USDT")
        self.assertEqual(detect_symbol("PEPE 跌破 0.01", known)[0], "PEPE-USDT")

    def test_ascii_not_regressed(self):
        self.assertEqual(detect_symbol("BTC 跌破 83000")[0], "BTC-USDT")
        self.assertEqual(detect_symbol("btcusdt 突破 1")[0], "BTC-USDT")
        self.assertEqual(detect_symbol("狗狗币 跌到 0.05")[0], "DOGE-USDT")
        self.assertEqual(detect_symbol("今天天气不错"), (None, None))

    def test_chinese_parse_end_to_end(self):
        res = parse("牛来 跌破 0.1 提醒我")
        self.assertTrue(res.ok, res.warnings)
        self.assertEqual(res.rules[0].symbol, "牛来-USDT")


class _FakeFeed:
    def __init__(self, symbols):
        self.symbols = list(symbols)

    def has_symbol(self, symbol):
        return symbol.upper().strip() in self.symbols


class _FakeApp:
    """只实现 _handle_parse 用到的那几样，不碰网络。"""

    def __init__(self, symbols):
        self.feed = _FakeFeed(symbols)
        self.cfg = {}
        self.rules = []

    def add_rule(self, rule):
        self.rules.append(rule)
        return "已就绪，等待触发"


class TestUnmonitoredSymbolWarning(unittest.TestCase):
    """规则用到没在监控列表里的合约时要明确报出来，不能默默不触发。"""

    def _parse(self, text, symbols):
        app = _FakeApp(symbols)
        out = Handler._handle_parse(types.SimpleNamespace(app=app), {"text": text})
        return app, out

    def test_warns_when_not_monitored(self):
        app, out = self._parse("牛来 跌破 0.1 提醒我", ["BTC-USDT"])
        self.assertTrue(out["ok"])
        self.assertEqual(app.rules[0].symbol, "牛来-USDT")
        self.assertTrue(any("实时行情" in w for w in out["warnings"]), out["warnings"])
        self.assertIn("实时行情", out["rules"][0]["hint"])

    def test_quiet_when_monitored(self):
        _, out = self._parse("牛来 跌破 0.1 提醒我", ["牛来-USDT"])
        self.assertEqual(out["warnings"], [])


if __name__ == "__main__":
    unittest.main()

