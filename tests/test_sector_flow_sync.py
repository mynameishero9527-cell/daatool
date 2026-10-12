"""手动拉取板块资金：东财列表失败时仍写入新浪当日与日K。"""
import unittest
from unittest.mock import patch

from app.database import execute, init_db, query
from app.services import sector

NAME = "测板块资金同步"


class SectorFlowSyncTests(unittest.TestCase):
    def setUp(self):
        init_db()
        execute("DELETE FROM sector_flow_daily WHERE name=?", (NAME,))

    def tearDown(self):
        execute("DELETE FROM sector_flow_daily WHERE name=?", (NAME,))

    def test_eastmoney_outage_keeps_sina_history(self):
        boards = [{"name": NAME, "board_code": "new_test_flow", "net_in": 88.0, "amount": 10.0}]
        hist = [
            {"trade_date": "2026-10-09", "net_in": -4.0, "amount": 2.0},
            {"trade_date": "2026-10-12", "net_in": 12.5, "amount": 3.0},
        ]

        def moneyflow(fenlei, pages=1, num=80):
            return boards if int(fenlei) == 0 else []

        with patch("app.services.sector.sina.fetch_board_moneyflow", side_effect=moneyflow), \
             patch("app.services.sector.sina.fetch_board_flow_history", return_value=hist), \
             patch("app.services.sector.eastmoney.fetch_board_list",
                   side_effect=RuntimeError("502 Bad Gateway")):
            out = sector.sync_flow_sources()

        self.assertTrue(out["ok"])
        self.assertEqual(out["remote"]["industry"], 1)
        self.assertEqual(out["sina_history"]["days"], 2)
        self.assertEqual(out["sina_history"]["rows"], 2)
        self.assertIn("502", out["em_hy"]["error"])
        self.assertIn("502", out["em_gn"]["error"])
        rows = query(
            "SELECT trade_date, net_in FROM sector_flow_daily WHERE dim='remote_hy' AND name=? "
            "ORDER BY trade_date",
            (NAME,),
        )
        self.assertEqual([r["trade_date"] for r in rows], ["2026-10-09", "2026-10-12"])
        self.assertEqual(rows[1]["net_in"], 12.5)
