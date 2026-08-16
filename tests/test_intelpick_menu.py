"""13.0.6 / 13.0.43：智能选股菜单骨架；主页与预测推荐分 tab；不编造个股涨跌名单。"""
from __future__ import annotations

import unittest
from pathlib import Path

from app.services import intelpick

ROOT = Path(__file__).resolve().parents[1]


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

    def test_home_tab_and_forecast_pane_split(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-page="home">主页</button>', html)
        self.assertIn('data-page="forecast">预测推荐</button>', html)
        self.assertIn('id="ipHomePane"', html)
        self.assertIn('id="ipForecastPane"', html)
        home = html[html.index('id="ipHomePane"'):html.index('id="ipForecastPane"')]
        forecast = html[html.index('id="ipForecastPane"'):]
        self.assertIn("上涨预测", home)
        self.assertIn("下跌预测", home)
        self.assertIn("ipAlmanac", home)
        self.assertNotIn("ipForecastTable", home)
        self.assertIn("ipForecastTable", forecast)
        self.assertNotIn('data-side="forecast"', html)
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('let intelpickPage = "home"', js)
        self.assertIn('intelpickPage === "forecast"', js)
        self.assertNotIn('intelpickSub === "forecast"', js)


if __name__ == "__main__":
    unittest.main()
