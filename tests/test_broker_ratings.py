"""13.0.12：机构评级多空立场与相对现价上涨空间。"""
from __future__ import annotations

import unittest

from app.database import init_db
from app.services import rating


class BrokerRatingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_stance_counts_and_upside(self):
        d = rating.broker_ratings("000001.SZ", 70.0)
        self.assertTrue(d.get("simulated"))
        items = d.get("items") or []
        self.assertEqual(len(items), 10)
        st = d.get("stance") or {}
        self.assertEqual(st.get("增持", 0) + st.get("中性", 0) + st.get("减持", 0), len(items))
        dist = d.get("distribution") or {}
        self.assertEqual(st.get("增持"), (dist.get("买入") or 0) + (dist.get("增持") or 0))
        self.assertEqual(st.get("中性"), dist.get("中性") or 0)
        self.assertEqual(st.get("减持"), (dist.get("减持") or 0) + (dist.get("卖出") or 0))
        self.assertIsNotNone(d.get("current_price"))
        self.assertIsNotNone(d.get("consensus_target"))
        self.assertIsNotNone(d.get("upside_pct"))
        price = float(d["current_price"])
        target = float(d["consensus_target"])
        self.assertAlmostEqual(d["upside_pct"], round((target - price) / price * 100, 2))
        for it in items:
            self.assertIn(it["stance"], ("增持", "中性", "减持"))
            self.assertEqual(it["stance"], rating.rating_stance(it["rating"]))
            self.assertIsNotNone(it.get("upside_pct"))

    def test_rating_stance_buckets(self):
        self.assertEqual(rating.rating_stance("买入"), "增持")
        self.assertEqual(rating.rating_stance("增持"), "增持")
        self.assertEqual(rating.rating_stance("中性"), "中性")
        self.assertEqual(rating.rating_stance("减持"), "减持")
        self.assertEqual(rating.rating_stance("卖出"), "减持")

    def test_upside_pct_none_on_bad_price(self):
        self.assertIsNone(rating.upside_pct(12, 0))
        self.assertIsNone(rating.upside_pct(12, None))
        self.assertEqual(rating.upside_pct(12, 10), 20.0)


if __name__ == "__main__":
    unittest.main()
