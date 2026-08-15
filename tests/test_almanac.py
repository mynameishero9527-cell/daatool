"""13.0.19：黄历可按日期查询；非法日期不猜测。"""
from __future__ import annotations

import unittest
from datetime import date

from app.main import app
from app.services import almanac


class AlmanacDateTests(unittest.TestCase):
    def test_resolve_empty_is_today(self):
        self.assertEqual(almanac.resolve_almanac_date(""), date.today())
        self.assertEqual(almanac.resolve_almanac_date(None), date.today())

    def test_resolve_invalid_is_none(self):
        self.assertIsNone(almanac.resolve_almanac_date("2026-13-01"))
        self.assertIsNone(almanac.resolve_almanac_date("not-a-date"))
        self.assertIsNone(almanac.resolve_almanac_date("2026/08/15"))

    def test_different_days_different_ganzhi(self):
        a = almanac.get_almanac(date(2026, 2, 17))
        b = almanac.get_almanac(date(2026, 8, 15))
        self.assertEqual(a["date"], "2026-02-17")
        self.assertEqual(b["date"], "2026-08-15")
        self.assertNotEqual(a["day_ganzhi"], b["day_ganzhi"])
        self.assertTrue(a.get("ok"))
        self.assertIn("jiugong", a)
        self.assertEqual(len(a["jiugong"]), 3)

    def test_spring_festival_year_boundary(self):
        before = almanac.get_almanac(date(2026, 2, 16))
        after = almanac.get_almanac(date(2026, 2, 17))
        self.assertNotEqual(before["year_ganzhi"], after["year_ganzhi"])

    def test_api_date_param(self):
        from fastapi.testclient import TestClient
        client = TestClient(app)
        ok = client.get("/api/macro/almanac?date=2026-02-17")
        self.assertEqual(ok.status_code, 200)
        body = ok.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("date"), "2026-02-17")
        bad = client.get("/api/macro/almanac?date=bad")
        self.assertEqual(bad.status_code, 200)
        self.assertFalse(bad.json().get("ok"))
        self.assertIn("日期", bad.json().get("error", ""))


if __name__ == "__main__":
    unittest.main()
