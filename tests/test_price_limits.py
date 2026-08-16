"""13.0.14：涨跌停价按昨收×板块幅度，不编造。"""
from __future__ import annotations

import unittest

from app.services.market import attach_price_limits, limit_pct, price_limits


class PriceLimitsTests(unittest.TestCase):
    def test_main_board_10pct(self):
        self.assertEqual(limit_pct("sz000001", "平安银行", "主板"), 0.10)
        d = price_limits("sz000001", 10.00, "平安银行", "主板")
        self.assertEqual(d["up_limit"], 11.00)
        self.assertEqual(d["down_limit"], 9.00)
        self.assertEqual(d["limit_pct"], 0.10)

    def test_chinext_and_star_20pct(self):
        self.assertEqual(limit_pct("sz300432", "某创业", "创业板"), 0.20)
        self.assertEqual(limit_pct("sz301001", "某创业", "创业板"), 0.20)
        self.assertEqual(limit_pct("sh688001", "某科创", "科创板"), 0.20)
        d = price_limits("sh688001", 20.00, "某科创", "科创板")
        self.assertEqual(d["up_limit"], 24.00)
        self.assertEqual(d["down_limit"], 16.00)

    def test_star_not_treated_as_bj(self):
        self.assertEqual(limit_pct("sh688001", "某科创", ""), 0.20)

    def test_bj_30pct(self):
        self.assertEqual(limit_pct("bj430047", "某北交", "北交所"), 0.30)
        d = price_limits("bj430047", 10.00, "某北交", "北交所")
        self.assertEqual(d["up_limit"], 13.00)
        self.assertEqual(d["down_limit"], 7.00)

    def test_main_st_5pct(self):
        self.assertEqual(limit_pct("sh600000", "*ST某", "主板"), 0.05)
        d = price_limits("sh600000", 4.00, "*ST某", "主板")
        self.assertEqual(d["up_limit"], 4.20)
        self.assertEqual(d["down_limit"], 3.80)

    def test_chinext_st_stays_20pct(self):
        self.assertEqual(limit_pct("sz300001", "ST某", "创业板"), 0.20)

    def test_null_prev_close_no_invent(self):
        d = price_limits("sz000001", None, "平安银行", "主板")
        self.assertIsNone(d["up_limit"])
        self.assertIsNone(d["down_limit"])
        self.assertEqual(d["limit_pct"], 0.10)

    def test_attach_infers_prev_from_pct(self):
        q = attach_price_limits({"price": 11.0, "pct": 10.0}, "sz000001", "平安银行", "主板")
        self.assertEqual(q["up_limit"], 11.00)
        self.assertEqual(q["down_limit"], 9.00)

    def test_attach_does_not_mutate_input(self):
        src = {"price": 10.0, "prev_close": 10.0, "pct": 0}
        out = attach_price_limits(src, "sz000001", "平安银行", "主板")
        self.assertNotIn("up_limit", src)
        self.assertEqual(out["up_limit"], 11.00)


if __name__ == "__main__":
    unittest.main()
