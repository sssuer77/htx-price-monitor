"""监控合约增删的测试（不联网）。"""

import os
import sys
import unittest

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
        for bad in ("", None, "!!", "-USDT", "TOOLONGCODENAME-USDT", "btc-busd"):
            self.assertIsNone(_normalize_symbol(bad), f"应判为非法: {bad!r}")


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


if __name__ == "__main__":
    unittest.main()

