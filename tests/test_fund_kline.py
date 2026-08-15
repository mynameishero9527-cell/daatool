"""13.0.13：个股主力资金K，不用涨跌幅冒充资金。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.cache import cache
from app.database import init_db
from app.datasources import eastmoney
from app.services import kline


class StockFundKlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        cache.clear()

    def test_em_secid_stocks_not_index(self):
        self.assertEqual(eastmoney.em_stock_secid("sh600519"), "1.600519")
        self.assertEqual(eastmoney.em_stock_secid("sz000001"), "0.000001")
        self.assertEqual(eastmoney.em_stock_secid("sz300432"), "0.300432")
        self.assertIsNone(eastmoney.em_stock_secid("sh000001"))
        self.assertIsNone(eastmoney.em_stock_secid("sz399001"))
        self.assertIsNone(eastmoney.em_stock_secid("bj899050"))

    def test_parse_day_and_minute(self):
        day = eastmoney._parse_stock_fflow_klines([
            "2026-08-14,217130144.0,-248228.0,-216881904.0,-100010992.0,317141136.0,"
            "5.40,-0.01,-5.39,-2.49,7.88,1341.99,-0.98,0.00,0.00",
        ])
        self.assertEqual(len(day), 1)
        self.assertEqual(day[0]["date"], "2026-08-14")
        self.assertAlmostEqual(day[0]["main_net_yi"], 2.1713, places=3)
        self.assertAlmostEqual(day[0]["super_net_yi"], 3.1714, places=3)
        minute = eastmoney._parse_stock_fflow_klines([
            "2026-08-14 09:31,21668494.0,0.0,-21533166.0,-12322659.0,33991153.0",
            "2026-08-14 09:32,22000000.0,0.0,-21000000.0,-12000000.0,34000000.0",
        ], minute=True)
        self.assertEqual([r["date"] for r in minute], ["09:31", "09:32"])

    def test_index_empty_reason(self):
        d = kline.get_fund_kline("sh000001", "day")
        self.assertEqual(d["dates"], [])
        self.assertIn("指数", d["empty_reason"])
        self.assertFalse(d["main_net_yi"])

    def test_minute_cumulative_to_interval(self):
        bars, cum, flag = kline._interval_if_cumulative([0.2, 0.5, 1.2, 2.17])
        self.assertTrue(flag)
        self.assertAlmostEqual(bars[0], 0.2)
        self.assertAlmostEqual(bars[-1], round(2.17 - 1.2, 4))
        self.assertEqual(cum[-1], 2.17)
        bars2, _, flag2 = kline._interval_if_cumulative([1.2, -0.4, 0.8, -0.2])
        self.assertFalse(flag2)
        self.assertEqual(bars2[0], 1.2)

    def test_does_not_use_pct_as_flow(self):
        rows = [{"date": "2026-08-01", "main_net_yi": 1.2},
                {"date": "2026-08-08", "main_net_yi": -0.4}]
        with patch("app.services.kline.eastmoney.fetch_stock_fflow", return_value=rows), \
             patch("app.services.kline._persist_fund_daily"):
            d = kline.get_fund_kline("sz300432", "day")
        self.assertEqual(d["main_net_yi"], [1.2, -0.4])
        self.assertEqual(d["cumulative_yi"][-1], 0.8)
        self.assertEqual(d["empty_reason"], "")
        self.assertNotIn("pct", d)

    def test_week_aggregates_daily_rows(self):
        rows = [
            {"date": "2026-08-10", "main_net_yi": 1.0, "super_net_yi": 0.2, "large_net_yi": 0.1},
            {"date": "2026-08-12", "main_net_yi": 0.5, "super_net_yi": 0.1, "large_net_yi": 0.2},
            {"date": "2026-08-17", "main_net_yi": -0.2, "super_net_yi": None, "large_net_yi": None},
        ]
        with patch("app.services.kline.eastmoney.fetch_stock_fflow", return_value=rows), \
             patch("app.services.kline._persist_fund_daily"):
            d = kline.get_fund_kline("sz300432", "week")
        self.assertEqual(len(d["dates"]), 2)
        self.assertAlmostEqual(d["main_net_yi"][0], 1.5)


if __name__ == "__main__":
    unittest.main()
