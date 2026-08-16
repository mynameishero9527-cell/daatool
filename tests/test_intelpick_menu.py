"""13.0.6：智能选股菜单骨架；不编造个股涨跌名单。"""
from __future__ import annotations

import unittest

from app.services import intelpick


class IntelpickMenuTests(unittest.TestCase):
    def test_sides_and_empty_lists(self):
        up = intelpick.get_page("up")
        down = intelpick.get_page("down")
        self.assertEqual(up["side"], "up")
        self.assertEqual(down["side"], "down")
        self.assertEqual(up["items"], [])
        self.assertEqual(down["items"], [])
        self.assertEqual(up["count"], 0)
        ids = [s["id"] for s in up["sides"]]
        self.assertEqual(ids, ["up", "down"])
        self.assertNotIn("forecast", ids)
        self.assertIn("尚未接入", up["empty_reason"])
        self.assertIn("不构成投资建议", up["disclaimer"])
        self.assertIn("策略选股", up["note"])

    def test_unknown_side_falls_back_to_up(self):
        d = intelpick.get_page("sideways")
        self.assertEqual(d["side"], "up")
        self.assertEqual(d["items"], [])


if __name__ == "__main__":
    unittest.main()
