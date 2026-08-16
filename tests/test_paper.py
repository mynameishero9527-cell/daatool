"""13.0.23：模拟账本默认关；T+1 当日不能卖。"""
from __future__ import annotations

import unittest

from app.database import execute, init_db
from app.services import engine, paper

CODE = "sz998803"


class PaperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        paper.ensure_tables()

    def setUp(self):
        execute("DELETE FROM paper_lot WHERE code=?", (CODE,))
        execute(
            "INSERT OR REPLACE INTO stock_snapshot("
            "code,name,price,pct,turnover_rate,volume_ratio,pe_ttm,pb,float_mv,total_mv,"
            "main_net_in,main_in,main_out,main_net_in_d5,pct_d5,pct_d10,pct_d20,pct_d60,amount,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (CODE, "模拟股", 12.5, 0, 1, 1, 10, 1, 100, 100,
             0, 0, 0, 0, 0, 0, 0, 0, 1, "2026-08-15"),
        )

    def tearDown(self):
        execute("DELETE FROM paper_lot WHERE code=?", (CODE,))
        engine.save_config({"paper_enabled": False})

    def test_default_off(self):
        engine.save_config({"paper_enabled": False})
        d = paper.fill(CODE, "buy", 100)
        self.assertFalse(d["ok"])
        self.assertIn("未启用", d["error"])

    def test_t1_and_sell(self):
        engine.save_config({"paper_enabled": True})
        buy = paper.fill(CODE, "buy", 100, asof="2026-08-15")
        self.assertTrue(buy["ok"], buy)
        same = paper.fill(CODE, "sell", 100, asof="2026-08-15")
        self.assertFalse(same["ok"])
        self.assertIn("T+1", same["error"])
        nxt = paper.fill(CODE, "sell", 100, asof="2026-08-16")
        self.assertTrue(nxt["ok"], nxt)
        self.assertEqual(nxt["side"], "sell")


if __name__ == "__main__":
    unittest.main()
