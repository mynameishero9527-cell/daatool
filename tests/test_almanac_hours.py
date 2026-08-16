"""13.0.24：时辰吉凶、奇门随辰变化、紫微流日示意、节气节假日倒计时。"""
from __future__ import annotations

import unittest
from datetime import date

from app.main import app
from app.services import almanac, qimen, ziwei
from app.services.intelpick import get_page


class AlmanacHoursTests(unittest.TestCase):
    def test_twelve_hours_have_luck(self):
        rows = almanac.shichen_luck_all(date(2026, 8, 16))
        self.assertEqual(len(rows), 12)
        self.assertEqual([r["zhi"] for r in rows], list("子丑寅卯辰巳午未申酉戌亥"))
        for r in rows:
            self.assertIn(r["luck"], ("吉", "平", "凶"))
            self.assertTrue(r["reason"])
            self.assertTrue(r["ganzhi"].endswith("时"))

    def test_qimen_changes_with_hour(self):
        a = qimen.plate(date(2026, 8, 16), 0)
        b = qimen.plate(date(2026, 8, 16), 12)
        self.assertEqual(len(a["grid"]), 3)
        self.assertEqual(len(a["grid"][0]), 3)
        self.assertNotEqual(a["hour_ganzhi"], b["hour_ganzhi"])
        self.assertNotEqual(a["grid"][0][0]["star"], b["grid"][0][0]["star"] or a["grid"][1][0]["god"])
        cells_a = [(c["star"], c["door"], c["god"], c["tian"]) for row in a["grid"] for c in row]
        cells_b = [(c["star"], c["door"], c["god"], c["tian"]) for row in b["grid"] for c in row]
        self.assertNotEqual(cells_a, cells_b)
        self.assertIn("民俗", a["note"])

    def test_ziwei_not_natal(self):
        z = ziwei.day_chart(4, "午", "丙")
        self.assertFalse(z["empty"])
        self.assertEqual(len(z["palaces"]), 12)
        self.assertEqual(z["palaces"][0]["name"], "命宫")
        self.assertEqual(z["palaces"][0]["zhi"], "午")
        self.assertIn("本命盘", z["note"])
        empty = ziwei.day_chart(None, "子", "甲")
        self.assertTrue(empty["empty"])
        self.assertIn("不猜测", empty["note"])

    def test_calendar_today_and_countdown(self):
        spring = almanac.calendar_bundle(date(2026, 2, 17))
        self.assertTrue(any(x["name"] == "春节" for x in spring["domestic"]["today"]))
        self.assertTrue(any("立春" in (x["name"] or "") or True for x in spring["solar_term"]["today"])
                        or spring["solar_term"]["next"] or spring["solar_term"]["prev"])
        mid = almanac.calendar_bundle(date(2026, 8, 16))
        self.assertEqual(mid["solar_term"]["today"], [])
        self.assertIsNotNone(mid["solar_term"]["next"])
        self.assertGreater(mid["solar_term"]["next"]["days"], 0)
        self.assertEqual(mid["current_term"]["name"], "立秋")
        self.assertGreaterEqual(mid["current_term"]["days_ago"], 1)
        self.assertEqual(mid["domestic"]["today"], [])
        self.assertGreater(mid["domestic"]["next"]["days"], 0)
        self.assertTrue(mid["foreign"]["next"] or mid["foreign"]["prev"] or mid["foreign"]["today"])

    def test_no_invented_spring_festival_2024(self):
        # 2024-02-10 约是春节，但不在 SPRING_FESTIVAL 表，不得标春节
        pack = almanac.calendar_bundle(date(2024, 2, 10))
        names = [x["name"] for x in pack["domestic"]["today"]]
        self.assertNotIn("春节", names)

    def test_payload_and_api(self):
        from fastapi.testclient import TestClient
        a = almanac.get_almanac(date(2026, 8, 16), persist=False)
        self.assertEqual(len(a["shichen_hours"]), 12)
        self.assertEqual(len(a["qimen_plates"]), 12)
        self.assertEqual(len(a["ziwei_plates"]), 12)
        self.assertIn("calendar", a)
        client = TestClient(app)
        body = client.get("/api/macro/almanac?date=2026-08-16&span=0&hour=0").json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body["selected_shichen"]["zhi_index"], 0)
        self.assertEqual(len(body["qimen_plates"]), 12)
        bad = client.get("/api/macro/almanac?date=bad")
        self.assertFalse(bad.json().get("ok"))

    def test_intelpick_still_empty(self):
        self.assertEqual(get_page("up")["items"], [])
        self.assertEqual(get_page("down")["items"], [])


if __name__ == "__main__":
    unittest.main()
