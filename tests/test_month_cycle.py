"""13.0.15：12个月板块季节性强弱；散户净流入估算不用涨跌幅冒充。"""
from __future__ import annotations

import unittest

from app.database import init_db
from app.services import sector
from app.services.metrics import attach_retail_nets
from app.services.kline import _aggregate_flow
from app.datasources import eastmoney


class MonthBoardCycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_twelve_months_strong_weak(self):
        d = sector.get_month_board_cycles()
        self.assertEqual(len(d["months"]), 12)
        self.assertIn(d["current_month"], range(1, 13))
        self.assertIn("不是官方政策", d["note"])
        self.assertIn("常识", d["note"])
        known = set(sector.SEASONAL_KNOWLEDGE)
        current = None
        for i, m in enumerate(d["months"], 1):
            self.assertEqual(m["month"], i)
            self.assertTrue(m["strong"], i)
            self.assertTrue(m["weak"], i)
            for item in m["strong"] + m["weak"]:
                self.assertIn(item["name"], known)
                self.assertTrue(item["why"])
            if m["is_current"]:
                current = m
            else:
                self.assertEqual(m["live_strong"], [])
                self.assertEqual(m["live_weak"], [])
        self.assertIsNotNone(current)
        self.assertEqual(current["month"], d["current_month"])

    def test_does_not_invent_history_from_pct(self):
        d = sector.get_month_board_cycles()
        other = [m for m in d["months"] if not m["is_current"]]
        self.assertTrue(other)
        for m in other:
            self.assertFalse(m["live_strong"])
            self.assertFalse(m["live_weak"])


class RetailNetTests(unittest.TestCase):
    def test_residual_equals_negative_main(self):
        item = attach_retail_nets({
            "amount": 10000, "main_in": 3000, "main_out": 1000, "main_net_in_d5": 500,
        })
        self.assertEqual(item["retail_net_in"], -2000.0)
        self.assertEqual(item["retail_net_in_d5"], -500.0)
        self.assertIn("估算", item["retail_note"])

    def test_missing_flow_not_filled_by_pct(self):
        item = attach_retail_nets({"pct": 9.8, "pct_d5": 12.0})
        self.assertIsNone(item["retail_net_in"])
        self.assertIsNone(item["retail_net_in_d5"])


class FundSmallNetTests(unittest.TestCase):
    def test_parse_small_net_yi(self):
        day = eastmoney._parse_stock_fflow_klines([
            "2026-08-14,217130144.0,-248228.0,-216881904.0,-100010992.0,317141136.0,"
            "5.40,-0.01,-5.39,-2.49,7.88,1341.99,-0.98,0.00,0.00",
        ])
        self.assertEqual(len(day), 1)
        self.assertIsNotNone(day[0]["small_net_yi"])
        self.assertAlmostEqual(day[0]["small_net_yi"], -0.0025, places=3)

    def test_week_aggregate_null_small_when_absent(self):
        rows = [{"date": "2026-08-10", "main_net_yi": 1.0},
                {"date": "2026-08-12", "main_net_yi": 0.5}]
        out = _aggregate_flow(rows, "week")
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]["small_net_yi"])


if __name__ == "__main__":
    unittest.main()
