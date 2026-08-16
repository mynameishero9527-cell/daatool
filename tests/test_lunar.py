"""13.0.22：农历对照表与已知春节/节日对齐，超出区间不猜测。"""
from __future__ import annotations

import unittest
from datetime import date

from app.services.lunar import solar_to_lunar


class LunarConvertTests(unittest.TestCase):
    def test_table_covers_1900_2100(self):
        from app.services.lunar import _LUNAR_INFO
        self.assertEqual(len(_LUNAR_INFO), 201)

    def test_known_spring_festivals(self):
        self.assertEqual(solar_to_lunar(date(2025, 1, 29))["text"], "正月初一")
        self.assertEqual(solar_to_lunar(date(2026, 2, 17))["text"], "正月初一")
        self.assertEqual(solar_to_lunar(date(2027, 2, 6))["text"], "正月初一")
        self.assertEqual(solar_to_lunar(date(2026, 2, 16))["text"], "腊月廿九")
        self.assertEqual(solar_to_lunar(date(2026, 2, 16))["year"], 2025)

    def test_known_lunar_festivals_2026(self):
        self.assertEqual(solar_to_lunar(date(2026, 3, 3))["text"], "正月十五")
        self.assertEqual(solar_to_lunar(date(2026, 6, 19))["text"], "五月初五")
        self.assertEqual(solar_to_lunar(date(2026, 8, 19))["text"], "七月初七")
        self.assertEqual(solar_to_lunar(date(2026, 9, 25))["text"], "八月十五")
        self.assertEqual(solar_to_lunar(date(2026, 10, 18))["text"], "九月初九")

    def test_anchor_and_base(self):
        self.assertEqual(solar_to_lunar(date(1900, 1, 31))["text"], "正月初一")
        self.assertEqual(solar_to_lunar(date(1949, 10, 1))["text"], "八月初十")

    def test_out_of_range_not_guessed(self):
        self.assertIsNone(solar_to_lunar(date(1899, 12, 31)))
        self.assertIsNone(solar_to_lunar(date(2101, 1, 1)))


if __name__ == "__main__":
    unittest.main()
