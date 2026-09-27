"""自然语言解析器测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from htxmon.parser import parse  # noqa: E402


class TestParser(unittest.TestCase):
    def one(self, text):
        res = parse(text)
        self.assertTrue(res.ok, f"未解析出规则: {text} -> {res.warnings}")
        self.assertEqual(len(res.rules), 1, f"期望 1 条规则: {text} -> {[r.describe() for r in res.rules]}")
        return res.rules[0]

    def test_cross_down(self):
        r = self.one("BTC 跌破 83000 提醒我")
        self.assertEqual((r.symbol, r.type, r.level), ("BTC-USDT", "cross_down", 83000.0))

    def test_cross_up(self):
        r = self.one("比特币涨到 86000 叫我")
        self.assertEqual((r.type, r.level), ("cross_up", 86000.0))

    def test_above_state(self):
        r = self.one("ETH 高于 2800 提醒我")
        self.assertEqual((r.type, r.level), ("above", 2800.0))

    def test_pct_with_window(self):
        r = self.one("ETH 5分钟涨2% 提醒")
        self.assertEqual((r.type, r.pct, r.window_sec), ("pct_up", 2.0, 300))

    def test_pct_default_window(self):
        r = self.one("ETH 每次跌1.5% 提醒我")
        self.assertEqual((r.type, r.window_sec, r.repeat), ("pct_down", 300, "always"))

    def test_range(self):
        r = self.one("sol 在 120 到 122.5 之间提醒我")
        self.assertEqual((r.type, r.level, r.level2), ("in_range", 120.0, 122.5))

    def test_out_range(self):
        r = self.one("BTC 突破 83000-85000 区间")
        self.assertEqual((r.type, r.level, r.level2), ("out_range", 83000.0, 85000.0))

    def test_out_range_down(self):
        r = self.one("BTC 跌出 84000-86000 提醒")
        self.assertEqual((r.type, r.level, r.level2), ("out_range", 84000.0, 86000.0))

    def test_out_range_detach(self):
        r = self.one("ETH 脱离 2600~2700 区间提醒我")
        self.assertEqual((r.type, r.level, r.level2), ("out_range", 2600.0, 2700.0))

    def test_in_range_back(self):
        r = self.one("BTC 回到 84000-86000 提醒我")
        self.assertEqual((r.type, r.level, r.level2), ("in_range", 84000.0, 86000.0))

    def test_near(self):
        r = self.one("XMR 接近 560 提醒")
        self.assertEqual((r.type, r.level, r.pct), ("near", 560.0, 0.5))

    def test_near_with_pct(self):
        r = self.one("BTC 距离 90000 还有 1% 时提醒")
        self.assertEqual((r.type, r.pct), ("near", 1.0))

    def test_touch_default(self):
        r = self.one("BTC 88000")
        self.assertEqual((r.type, r.level), ("touch", 88000.0))

    def test_symbol_variants(self):
        for text, sym in (("BTCUSDT 上穿 85000", "BTC-USDT"),
                          ("btc-usdt 跌破 80000", "BTC-USDT"),
                          ("狗狗币 跌到 0.05", "DOGE-USDT")):
            self.assertEqual(self.one(text).symbol, sym)

    def test_wan_unit(self):
        self.assertEqual(self.one("BTC 8.45万跌破提醒").level, 84500.0)

    def test_thousand_separator(self):
        self.assertEqual(self.one("BTC 跌破 83,000 提醒我").level, 83000.0)

    def test_multi_clause_inherits_symbol(self):
        res = parse("BTC 突破 87000 或 跌破 83000 通知我")
        self.assertEqual(len(res.rules), 2)
        self.assertEqual({r.symbol for r in res.rules}, {"BTC-USDT"})
        self.assertEqual({r.type for r in res.rules}, {"cross_up", "cross_down"})

    def test_comma_separated_clauses(self):
        res = parse("BTC 跌破83000，ETH 涨到2800")
        self.assertEqual(len(res.rules), 2)
        self.assertEqual({r.symbol for r in res.rules}, {"BTC-USDT", "ETH-USDT"})

    def test_unparsable(self):
        res = parse("今天天气不错")
        self.assertFalse(res.ok)
        self.assertTrue(res.warnings)

    def test_empty(self):
        self.assertFalse(parse("").ok)

    def test_default_symbol(self):
        res = parse("跌破 80000", default_symbol="SOL-USDT")
        self.assertTrue(res.ok)
        self.assertEqual(res.rules[0].symbol, "SOL-USDT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
