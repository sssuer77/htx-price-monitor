"""监控合约增删的测试（不联网）。"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from htxmon.feed import PriceFeed, Tick  # noqa: E402
from htxmon.server import _normalize_symbol  # noqa: E402


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

    def test_chinese_rejected(self):
        # 回归：Python 的 str.isalnum() 对汉字也返回 True，曾导致「牛来-USDT」
        # 被当成合法合约写进配置，然后 REST 请求抛 ascii 编码错误、
        # 事件里记着「已加入监控」但面板永远不显示行情。
        for bad in ("牛来-USDT", "牛来", "比特币", "BTC-牛来", "狗狗币"):
            self.assertIsNone(_normalize_symbol(bad), f"中文必须被拒绝: {bad!r}")

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


if __name__ == "__main__":
    unittest.main()

