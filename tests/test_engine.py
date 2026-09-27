"""规则引擎状态机测试（用假 feed 喂价）。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from htxmon.engine import RuleEngine  # noqa: E402
from htxmon.feed import Tick  # noqa: E402
from htxmon.models import Rule  # noqa: E402


class FakeFeed:
    def __init__(self):
        self.price = 100.0
        self.hist = {}

    def tick(self, symbol):
        return Tick(symbol=symbol, price=self.price, local_ts=0.0, src="test")

    def price_ago(self, symbol, seconds):
        return self.hist.get(seconds)


class FakeStore:
    def __init__(self, rules):
        self.rules = rules

    def all(self):
        return list(self.rules)

    def save(self):
        pass


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, title, body, payload=None):
        self.sent.append(body)
        return {"ui": "ok"}


class FakeEvents:
    def __init__(self):
        self.items = []

    def add(self, kind, message, **extra):
        self.items.append((kind, message))
        return {"kind": kind}


def make_engine(rules):
    feed = FakeFeed()
    notifier = FakeNotifier()
    events = FakeEvents()
    eng = RuleEngine(FakeStore(rules), feed, notifier, events)
    return eng, feed, notifier, events


def feed_price(eng, price, symbol="BTC-USDT"):
    eng.on_tick(Tick(symbol=symbol, price=price, local_ts=0.0, src="test"))


class TestEngine(unittest.TestCase):
    def test_cross_down_needs_arming(self):
        r = Rule(symbol="BTC-USDT", type="cross_down", level=90.0)
        eng, feed, notifier, _ = make_engine([r])
        feed_price(eng, 85.0)          # 已在下方：只建立基准，不触发
        self.assertEqual(len(notifier.sent), 0)
        feed_price(eng, 95.0)
        feed_price(eng, 89.0)          # 向下穿越 -> 触发
        self.assertEqual(len(notifier.sent), 1)
        self.assertEqual(r.status, "triggered")

    def test_cross_up(self):
        r = Rule(symbol="BTC-USDT", type="cross_up", level=110.0)
        eng, feed, notifier, _ = make_engine([r])
        feed_price(eng, 105.0)
        self.assertEqual(len(notifier.sent), 0)
        feed_price(eng, 112.0)
        self.assertEqual(len(notifier.sent), 1)

    def test_triggered_once_only(self):
        r = Rule(symbol="BTC-USDT", type="cross_up", level=110.0)
        eng, feed, notifier, _ = make_engine([r])
        feed_price(eng, 100.0)
        feed_price(eng, 115.0)
        feed_price(eng, 100.0)
        feed_price(eng, 115.0)
        self.assertEqual(len(notifier.sent), 1)

    def test_repeat_with_cooldown(self):
        r = Rule(symbol="BTC-USDT", type="cross_up", level=110.0, repeat="always", cooldown_sec=3600)
        eng, feed, notifier, _ = make_engine([r])
        feed_price(eng, 100.0)
        feed_price(eng, 115.0)
        feed_price(eng, 100.0)
        feed_price(eng, 115.0)
        self.assertEqual(len(notifier.sent), 1)   # 冷却期内不重复
        self.assertEqual(r.status, "active")

    def test_touch_crossing(self):
        r = Rule(symbol="BTC-USDT", type="touch", level=100.0)
        eng, feed, notifier, _ = make_engine([r])
        feed_price(eng, 105.0)
        feed_price(eng, 95.0)
        self.assertEqual(len(notifier.sent), 1)

    def test_near(self):
        r = Rule(symbol="BTC-USDT", type="near", level=100.0, pct=1.0)
        eng, feed, notifier, _ = make_engine([r])
        feed_price(eng, 90.0)
        self.assertEqual(len(notifier.sent), 0)
        feed_price(eng, 99.5)
        self.assertEqual(len(notifier.sent), 1)

    def test_pct_up(self):
        r = Rule(symbol="BTC-USDT", type="pct_up", pct=2.0, window_sec=300)
        eng, feed, notifier, _ = make_engine([r])
        feed.hist[300] = 100.0
        feed_price(eng, 101.0)
        self.assertEqual(len(notifier.sent), 0)
        feed_price(eng, 103.0)
        self.assertEqual(len(notifier.sent), 1)

    def test_in_range(self):
        r = Rule(symbol="BTC-USDT", type="in_range", level=100.0, level2=110.0)
        eng, feed, notifier, _ = make_engine([r])
        feed_price(eng, 120.0)
        feed_price(eng, 105.0)
        self.assertEqual(len(notifier.sent), 1)

    def test_out_range(self):
        r = Rule(symbol="BTC-USDT", type="out_range", level=100.0, level2=110.0)
        eng, feed, notifier, _ = make_engine([r])
        feed_price(eng, 105.0)
        self.assertEqual(len(notifier.sent), 0)
        feed_price(eng, 112.0)
        self.assertEqual(len(notifier.sent), 1)

    def test_disabled_rule_ignored(self):
        r = Rule(symbol="BTC-USDT", type="cross_up", level=110.0, status="disabled")
        eng, feed, notifier, _ = make_engine([r])
        feed_price(eng, 100.0)
        feed_price(eng, 115.0)
        self.assertEqual(len(notifier.sent), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
