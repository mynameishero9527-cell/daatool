"""13.0.42：对子/连号/吉利价特征；缺K线不编近期低点。"""
from __future__ import annotations

import unittest

from datetime import timedelta

from app.database import execute, init_db, set_meta_json
from app.services import intelpick, lucky_price, strategy
from tests.test_strategy_points import CODE_OK, _purge, _seed


def _seed_lucky_low_kline(code: str, *, n=20, px=6.66) -> None:
    end = strategy._shanghai_today()
    days = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    execute("DELETE FROM daily_kline WHERE code=?", (code,))
    for i, day in enumerate(days):
        execute(
            "INSERT OR REPLACE INTO daily_kline(code,date,open,close,high,low,volume)"
            " VALUES(?,?,?,?,?,?,?)",
            (code, day.isoformat(), px, px, px + 0.08, px, 1000 + i),
        )


class LuckyPriceTests(unittest.TestCase):
    def test_classify_pairs_run_lucky(self):
        self.assertIn("对子", lucky_price.classify(6.66)["lucky_tags"])
        self.assertIn("全同号", lucky_price.classify(8.88)["lucky_tags"])
        self.assertIn("吉利", lucky_price.classify(8.88)["lucky_tags"])
        self.assertIn("对子", lucky_price.classify(12.12)["lucky_tags"])
        self.assertIn("连号", lucky_price.classify(1.23)["lucky_tags"])
        self.assertIn("连号", lucky_price.classify(12.34)["lucky_tags"])
        self.assertIn("吉利", lucky_price.classify(5.20)["lucky_tags"])
        self.assertIn("吉利", lucky_price.classify(16.88)["lucky_tags"])
        self.assertEqual(lucky_price.classify(10.25)["lucky_tags"], [])
        self.assertEqual(lucky_price.classify(None)["lucky_tags"], [])
        self.assertEqual(intelpick.get_page("up")["items"], [])

    def test_lucky_low_needs_real_low(self):
        near = lucky_price.annotate(6.66, 6.66)
        self.assertTrue(near["lucky_low"])
        self.assertIn("对子", near["lucky_tags"])
        far = lucky_price.annotate(8.00, 6.66)
        self.assertFalse(far["lucky_low"])
        no_low = lucky_price.annotate(6.66, None)
        self.assertFalse(no_low["lucky_low"])
        self.assertIn("对子", no_low["lucky_tags"])
        plain = lucky_price.annotate(10.25, 10.25)
        self.assertFalse(plain["lucky_low"])


class LuckyLowStrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        _purge(CODE_OK)
        set_meta_json(strategy.RULES_VER_KEY, strategy.RULES_VER)
        set_meta_json(strategy.META_KEY_BUY, ["BZ"])
        set_meta_json(strategy.META_KEY_HIDDEN_BUY, [])

    def tearDown(self):
        _purge(CODE_OK)
        set_meta_json(strategy.META_KEY_BUY, list(strategy.DEFAULT_BUY_IDS))
        set_meta_json(strategy.META_KEY_HIDDEN_BUY, [])

    def test_bz_takes_lucky_low_without_stabilize(self):
        _seed(
            CODE_OK, name="吉利低", price=6.66, buy_index=99.6, stabilize_score=None,
            pos60=0.40, main_net_in=2000, pct=0.8, rsi14=40,
        )
        _seed_lucky_low_kline(CODE_OK, n=20, px=6.66)
        self.assertFalse(self._sql_bz(CODE_OK))
        hits = strategy.collect_hits("buy", enabled=["BZ"], limit=80, use_week_gate=False)
        row = next((r for r in hits if r["code"] == CODE_OK), None)
        self.assertIsNotNone(row)
        self.assertTrue(row.get("lucky_low"))
        self.assertIn("BZ", row.get("plans") or [])
        self.assertEqual(intelpick.get_page("down")["items"], [])

    def test_no_kline_not_invented_into_bz(self):
        _seed(
            CODE_OK, name="无K吉利", price=6.66, buy_index=60, stabilize_score=None,
            pos60=0.40, main_net_in=2000, pct=0.8,
        )
        hits = strategy.collect_hits("buy", enabled=["BZ"], limit=80, use_week_gate=False)
        self.assertNotIn(CODE_OK, {r["code"] for r in hits})

    def test_plan_a_not_opened_by_lucky_alone(self):
        _seed(
            CODE_OK, name="只吉利", price=6.66, buy_index=50, stabilize_score=None,
            pos60=0.40, main_net_in=2000, pct=0.8,
        )
        _seed_lucky_low_kline(CODE_OK, n=20, px=6.66)
        hits = strategy.collect_hits("buy", enabled=["A"], limit=80, use_week_gate=False)
        self.assertNotIn(CODE_OK, {r["code"] for r in hits})

    def _sql_bz(self, code: str) -> bool:
        from app.database import query
        plan = strategy.BUY_PLANS["BZ"]
        rows = query(
            "SELECT s.code FROM stock_metrics m JOIN stock_snapshot s ON s.code=m.code "
            f"WHERE {strategy._where(plan.where)} AND s.code=?",
            (code,),
        )
        return bool(rows)


if __name__ == "__main__":
    unittest.main()
