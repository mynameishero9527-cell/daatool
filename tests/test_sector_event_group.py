"""13.0.10：板块事件按日/周/月合并到对应时间桶，并汇总板块。"""
from __future__ import annotations

import unittest

from app.services import macro


class SectorEventGroupTests(unittest.TestCase):
    def test_day_merges_same_calendar_day_even_with_time(self):
        items = [
            {"date": "2026-08-16 01:00:00", "title": "发射窗口", "sectors": "军工"},
            {"date": "2026-08-16", "title": "航展预热", "sectors": "军工、航空"},
            {"date": "2026-08-17", "title": "另一天", "sectors": "新能源"},
        ]
        g = macro.group_sector_events(items, "day")
        self.assertEqual(len(g), 2)
        first = next(x for x in g if x["key"] == "d:2026-08-16")
        self.assertEqual(first["count"], 2)
        self.assertEqual(first["sectors"], ["军工", "航空"])
        self.assertIn("周", first["label"])

    def test_week_merges_mon_to_sun(self):
        items = [
            {"date": "2026-08-10", "title": "周一", "sectors": "军工、航空"},
            {"date": "2026-08-12 18:00:00", "title": "周三", "sectors": "航空、无人机"},
            {"date": "2026-08-16", "title": "周日", "sectors": "军工"},
            {"date": "2026-08-17", "title": "下周一", "sectors": "新能源"},
        ]
        g = macro.group_sector_events(items, "week")
        self.assertEqual(len(g), 2)
        this_week = next(x for x in g if x["key"] == "w:2026-08-10")
        next_week = next(x for x in g if x["key"] == "w:2026-08-17")
        self.assertEqual(this_week["count"], 3)
        self.assertEqual(this_week["sectors"], ["军工", "航空", "无人机"])
        self.assertIn("当周", this_week["label"])
        self.assertEqual(next_week["count"], 1)
        self.assertEqual(next_week["sectors"], ["新能源"])

    def test_month_merges_and_skips_empty_sector_aliases(self):
        items = [
            {"date": "2026-08-01", "title": "A", "sectors": "芯片、政策"},
            {"date": "2026-08-20", "title": "B", "affected_sectors": ["半导体", "未映射影响板块"]},
            {"date": "2026-09-02", "title": "C", "sectors": "消费"},
        ]
        g = macro.group_sector_events(items, "month")
        self.assertEqual([x["key"] for x in g], ["m:2026-09", "m:2026-08"])
        aug = next(x for x in g if x["key"] == "m:2026-08")
        self.assertEqual(aug["count"], 2)
        self.assertEqual(aug["label"], "2026年8月")
        self.assertEqual(aug["sectors"], ["芯片", "半导体"])
        self.assertNotIn("政策", aug["sectors"])

    def test_blank_dates_bucket_last(self):
        items = [
            {"date": "", "title": "无日期", "sectors": "金融"},
            {"date": "2026-08-15", "title": "有日期", "sectors": "消费"},
        ]
        g = macro.group_sector_events(items, "week")
        self.assertEqual(g[-1]["key"], "none")
        self.assertEqual(g[-1]["count"], 1)

    def test_invalid_dim_falls_back_to_day(self):
        items = [{"date": "2026-08-15", "title": "A", "sectors": "消费"}]
        g = macro.group_sector_events(items, "year")
        self.assertEqual(g[0]["dim"], "day")
        self.assertEqual(g[0]["key"], "d:2026-08-15")


if __name__ == "__main__":
    unittest.main()
