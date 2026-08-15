"""13.0.20：切日时干支/黄道同步更新，并写入本地 SQLite。"""
from __future__ import annotations

import json
import unittest
from datetime import date, timedelta

from app.database import execute, init_db, query
from app.main import app
from app.services import almanac


class AlmanacDateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_resolve_empty_is_today(self):
        self.assertEqual(almanac.resolve_almanac_date(""), date.today())
        self.assertEqual(almanac.resolve_almanac_date(None), date.today())

    def test_resolve_invalid_is_none(self):
        self.assertIsNone(almanac.resolve_almanac_date("2026-13-01"))
        self.assertIsNone(almanac.resolve_almanac_date("not-a-date"))
        self.assertIsNone(almanac.resolve_almanac_date("2026/08/15"))

    def test_different_days_different_ganzhi_and_huangdao(self):
        a = almanac.get_almanac(date(2026, 2, 17), persist=False)
        b = almanac.get_almanac(date(2026, 8, 15), persist=False)
        self.assertEqual(a["date"], "2026-02-17")
        self.assertEqual(b["date"], "2026-08-15")
        self.assertNotEqual(a["day_ganzhi"], b["day_ganzhi"])
        self.assertNotEqual(a["huangdao"]["shen"], b["huangdao"]["shen"])
        self.assertTrue(a.get("ok"))
        self.assertIn("jiugong", a)
        self.assertEqual(len(a["jiugong"]), 3)
        self.assertEqual(len(a["jiugong_cells"]), 3)
        self.assertEqual(len(a["jiugong_cells"][0]), 3)

    def test_adjacent_days_huangdao_and_day_pillar_change(self):
        d0 = date(2026, 8, 15)
        a = almanac.huangdao_of(d0)
        b = almanac.huangdao_of(d0 + timedelta(days=1))
        self.assertNotEqual(a["shen"], b["shen"])
        self.assertNotEqual(almanac.day_ganzhi(d0), almanac.day_ganzhi(d0 + timedelta(days=1)))

    def test_jiugong_marks_caishen_and_zhi(self):
        d = date(2026, 8, 15)
        dgz = almanac.day_ganzhi(d)
        expect_cai = almanac.CAISHEN[dgz[0]]
        expect_zhi = almanac.ZHI_DIR[dgz[1]]
        cells = [c for row in almanac.jiugong_cells(d) for c in row]
        marked_cai = [c for c in cells if "caishen" in c["mark"]]
        marked_zhi = [c for c in cells if "zhi" in c["mark"]]
        self.assertEqual(len(marked_cai), 1)
        self.assertIn(expect_cai, marked_cai[0]["label"])
        self.assertEqual(len(marked_zhi), 1)
        self.assertIn(expect_zhi, marked_zhi[0]["label"])

    def test_spring_festival_year_boundary(self):
        before = almanac.get_almanac(date(2026, 2, 16), persist=False)
        after = almanac.get_almanac(date(2026, 2, 17), persist=False)
        self.assertNotEqual(before["year_ganzhi"], after["year_ganzhi"])

    def test_persists_to_sqlite(self):
        execute("DELETE FROM almanac_day WHERE day=?", ("2026-08-15",))
        payload = almanac.get_almanac(date(2026, 8, 15), persist=True)
        self.assertTrue(payload.get("stored"))
        rows = query("SELECT day, payload FROM almanac_day WHERE day=?", ("2026-08-15",))
        self.assertEqual(len(rows), 1)
        body = json.loads(rows[0]["payload"])
        self.assertEqual(body["day_ganzhi"], payload["day_ganzhi"])
        self.assertEqual(body["huangdao"]["shen"], payload["huangdao"]["shen"])
        self.assertEqual(body["date"], "2026-08-15")

    def test_api_date_param(self):
        from fastapi.testclient import TestClient
        client = TestClient(app)
        ok = client.get("/api/macro/almanac?date=2026-02-17&span=0")
        self.assertEqual(ok.status_code, 200)
        body = ok.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("date"), "2026-02-17")
        self.assertTrue(body.get("stored"))
        self.assertIn("jiugong_cells", body)
        self.assertIn("huangdao", body)
        bad = client.get("/api/macro/almanac?date=bad")
        self.assertEqual(bad.status_code, 200)
        self.assertFalse(bad.json().get("ok"))
        self.assertIn("日期", bad.json().get("error", ""))

    def test_invalid_date_not_inserted(self):
        from fastapi.testclient import TestClient
        before = query("SELECT COUNT(*) AS n FROM almanac_day")[0]["n"]
        client = TestClient(app)
        client.get("/api/macro/almanac?date=bad")
        after = query("SELECT COUNT(*) AS n FROM almanac_day")[0]["n"]
        self.assertEqual(before, after)

    def test_api_prefetch_neighbors_and_sync(self):
        from fastapi.testclient import TestClient
        execute("DELETE FROM almanac_day WHERE day BETWEEN '2026-08-08' AND '2026-08-22'")
        client = TestClient(app)
        ok = client.get("/api/macro/almanac?date=2026-08-15&span=7")
        self.assertEqual(ok.status_code, 200)
        body = ok.json()
        self.assertEqual(body.get("stored_days"), 15)
        n = query(
            "SELECT COUNT(*) AS n FROM almanac_day WHERE day BETWEEN '2026-08-08' AND '2026-08-22'"
        )[0]["n"]
        self.assertEqual(n, 15)
        d15 = query("SELECT payload FROM almanac_day WHERE day=?", ("2026-08-15",))[0]
        d16 = query("SELECT payload FROM almanac_day WHERE day=?", ("2026-08-16",))[0]
        p15 = json.loads(d15["payload"])
        p16 = json.loads(d16["payload"])
        self.assertNotEqual(p15["day_ganzhi"], p16["day_ganzhi"])
        self.assertNotEqual(p15["huangdao"]["shen"], p16["huangdao"]["shen"])
        sync = client.post("/api/macro/almanac/sync?date=2026-08-15&span=2")
        self.assertEqual(sync.status_code, 200)
        self.assertTrue(sync.json().get("ok"))
        self.assertEqual(sync.json().get("stored_days"), 5)


if __name__ == "__main__":
    unittest.main()
