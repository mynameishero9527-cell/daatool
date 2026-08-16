"""13.0.37：卜卦/奇门预测个股落本地回显；不写入智能选股涨跌名单。"""
from __future__ import annotations

import unittest

from app.database import execute, init_db, query
from app.services import forecast_rec, intelpick


class ForecastRecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        execute("DELETE FROM forecast_stock")
        execute("DELETE FROM forecast_batch")

    def tearDown(self):
        execute("DELETE FROM forecast_stock")
        execute("DELETE FROM forecast_batch")

    def _payload(self, kind="yijing", stocks=None):
        stocks = stocks if stocks is not None else [{
            "code": "sz012350", "name": "测木", "industry": "医药生物",
            "wuxing": ["木"], "finance_grade": "A", "score": 80.0,
            "advice": "增持", "buy_index": 88, "price": 10.2, "pct": 1.5,
        }]
        if kind == "yijing":
            return {
                "ok": True, "date": "2026-08-16", "hour": 12,
                "gua_wuxing": ["木", "水"],
                "gua": {"ben": {"name": "风水涣"}},
                "stocks": stocks,
            }
        return {
            "ok": True, "date": "2026-08-16", "hour": 12,
            "favor": ["金", "水"], "season": "秋",
            "qimen": {"shichen": "午时"},
            "stocks": stocks,
        }

    def test_empty_or_fail_not_saved(self):
        self.assertIsNone(forecast_rec.record("yijing", {"ok": False, "stocks": []}))
        self.assertIsNone(forecast_rec.record("yijing", {"ok": True, "stocks": []}))
        self.assertIsNone(forecast_rec.record("yijing", {"ok": True, "stocks": [{"code": "", "name": "x"}]}))
        self.assertEqual(query("SELECT COUNT(*) AS n FROM forecast_batch")[0]["n"], 0)
        self.assertEqual(intelpick.get_page("up")["items"], [])
        self.assertEqual(intelpick.get_page("down")["items"], [])

    def test_record_and_echo_sort_clear(self):
        rec = forecast_rec.record("yijing", self._payload("yijing"))
        self.assertTrue(rec)
        self.assertTrue(str(rec["batch_no"]).startswith("YJ-"))
        self.assertEqual(rec["kind_label"], "易经卜卦推测")
        self.assertEqual(rec["count"], 1)
        page = forecast_rec.list_page()
        self.assertEqual(page["count"], 1)
        row = page["items"][0]
        self.assertEqual(row["code"], "sz012350")
        self.assertEqual(row["kind"], "yijing")
        self.assertEqual(row["kind_label"], "易经卜卦推测")
        self.assertEqual(row["batch_no"], rec["batch_no"])
        self.assertTrue(row["predicted_at"])
        self.assertIn("木", row["wuxing"])
        self.assertIn("木", row["batch_wuxing"])
        self.assertEqual(intelpick.get_page("up")["items"], [])

        forecast_rec.record("qimen", self._payload("qimen", [{
            "code": "sz012351", "name": "测金", "industry": "银行",
            "wuxing": ["金"], "score": 70.0, "advice": "保持不变",
        }]))
        by_score = forecast_rec.list_page(sort="score", order="desc")
        self.assertEqual([r["code"] for r in by_score["items"]], ["sz012350", "sz012351"])
        only_qm = forecast_rec.list_page(kind="qimen")
        self.assertEqual(only_qm["count"], 1)
        self.assertEqual(only_qm["items"][0]["kind_label"], "奇门遁甲预测")

        bad = forecast_rec.clear()
        self.assertFalse(bad.get("ok"))
        one = forecast_rec.clear(batch_no=rec["batch_no"])
        self.assertEqual(one["cleared"], 1)
        left = forecast_rec.list_page()
        self.assertEqual(left["count"], 1)
        allc = forecast_rec.clear(clear_all=True)
        self.assertEqual(allc["cleared"], 1)
        self.assertEqual(forecast_rec.list_page()["count"], 0)
        self.assertEqual(intelpick.get_page("down")["items"], [])

    def test_api_saves_and_does_not_fill_intelpick(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import execute as ex
        code = "sz012352"
        ex(
            "INSERT OR REPLACE INTO stock_list(code,name,market,board,updated_at,industry) "
            "VALUES(?,?,?,?,?,?)",
            (code, "接口测金", "SZ", "主板", "2026-08-16", "银行"),
        )
        ex(
            "INSERT OR REPLACE INTO stock_snapshot("
            "code,name,price,pct,turnover_rate,volume_ratio,pe_ttm,pb,float_mv,total_mv,"
            "main_net_in,main_in,main_out,main_net_in_d5,pct_d5,pct_d10,pct_d20,pct_d60,"
            "amount,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, "接口测金", 10.0, 3.0, 2.0, 1.2, 12.0, 1.5, 80.0, 100.0,
             8000.0, 9000.0, 1000.0, 8000.0, 6.0, 6.0, 8.0, 10.0, 20000.0, "2026-08-16"),
        )
        ex(
            "INSERT OR REPLACE INTO stock_metrics(code,buy_index,dark_power,stabilize_score,sentiment,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (code, 72.0, 60.0, 70.0, 55.0, "2026-08-16"),
        )
        client = TestClient(app)
        pred = client.post("/api/macro/almanac/qimen-predict", json={"date": "2026-08-16", "hour": 12})
        body = pred.json()
        self.assertTrue(body.get("ok"))
        if body.get("stocks"):
            self.assertTrue(body.get("batch_no"))
            page = client.get("/api/intelpick/forecast?kind=qimen").json()
            self.assertGreaterEqual(page.get("count") or 0, 1)
            self.assertTrue(all(r.get("kind") == "qimen" for r in page.get("items") or []))
        self.assertEqual(client.get("/api/intelpick?side=up").json().get("items"), [])
        self.assertEqual(intelpick.get_page("up")["items"], [])
        client.post("/api/intelpick/forecast/clear", json={"all": True})
        for sql in (
            "DELETE FROM stock_metrics WHERE code=?",
            "DELETE FROM stock_snapshot WHERE code=?",
            "DELETE FROM stock_list WHERE code=?",
        ):
            ex(sql, (code,))


if __name__ == "__main__":
    unittest.main()
