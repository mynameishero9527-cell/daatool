"""13.0.33：易经卜卦按卦象五行匹配热门板块+财报+综合评分。不编造个股，不写入智能选股名单。"""
from __future__ import annotations

import random
import unittest
from datetime import date

from app.database import execute, init_db
from app.main import app
from app.services import almanac, almanac_pick, intelpick, liuren, yijing
from app.services.almanac import day_ganzhi


class AlmanacYijingTests(unittest.TestCase):
    CODE_DIV = "sz012345"
    CODE_OK = "sz012346"
    CODE_DEAD = "sz012347"
    CODE_LOW = "sz012348"
    CODE_NONE = "sz012349"

    @classmethod
    def setUpClass(cls):
        init_db()

    def tearDown(self):
        for code in (self.CODE_DIV, self.CODE_OK, self.CODE_DEAD, self.CODE_LOW, self.CODE_NONE):
            execute("DELETE FROM stock_metrics WHERE code=?", (code,))
            execute("DELETE FROM stock_snapshot WHERE code=?", (code,))
            execute("DELETE FROM stock_list WHERE code=?", (code,))
            execute("DELETE FROM stock_finance_grade WHERE code=?", (code,))

    def _put_stock(self, code, name, industry, *, score_high=True, dead=False):
        execute(
            "INSERT OR REPLACE INTO stock_list(code,name,market,board,updated_at,industry) "
            "VALUES(?,?,?,?,?,?)",
            (code, name, "SZ", "主板", "2026-08-16", industry),
        )
        if dead:
            return
        pct, d5, buy, main = (3.0, 6.0, 72.0, 8000.0) if score_high else (-8.0, -12.0, 20.0, -3000.0)
        execute(
            "INSERT OR REPLACE INTO stock_snapshot("
            "code,name,price,pct,turnover_rate,volume_ratio,pe_ttm,pb,float_mv,total_mv,"
            "main_net_in,main_in,main_out,main_net_in_d5,pct_d5,pct_d10,pct_d20,pct_d60,"
            "amount,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, name, 10.0, pct, 2.0, 1.2, 12.0, 1.5, 80.0, 100.0,
             main, 9000.0, 1000.0, main, d5, d5, 8.0 if score_high else -20.0,
             10.0 if score_high else -30.0, 20000.0, "2026-08-16"),
        )
        execute(
            "INSERT OR REPLACE INTO stock_metrics("
            "code,buy_index,dark_power,stabilize_score,sentiment,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (code, buy, 60.0, 70.0 if score_high else 20.0, 55.0, "2026-08-16"),
        )

    def _put_grade(self, code, grade="A"):
        execute(
            "INSERT OR REPLACE INTO stock_finance_grade(code,grade,score,summary,updated_at)"
            " VALUES(?,?,?,?,?)",
            (code, grade, 6 if grade == "A" else 3, "测试评级", "2026-08-16"),
        )

    def _yaos_div(self):
        return [
            yijing.yao_from_coins([2, 2, 2]),  # 6 老阴 digit 0
            yijing.yao_from_coins([3, 2, 2]),  # 7 digit 1
            yijing.yao_from_coins([2, 3, 2]),  # 7 digit 2
            yijing.yao_from_coins([3, 3, 2]),  # 8 digit 3
            yijing.yao_from_coins([2, 2, 3]),  # 7 digit 4
            yijing.yao_from_coins([3, 2, 3]),  # 8 digit 5
        ]

    def test_anchor_jiazi_untouched(self):
        self.assertEqual(day_ganzhi(date(1949, 10, 1)), "甲子")

    def test_yijing_day_course_uses_lunar_or_solar(self):
        y = yijing.for_datetime(date(2026, 8, 16), 12)
        self.assertTrue(y["ok"])
        self.assertTrue(y["ben"]["name"])
        self.assertEqual(len(y["bagua"]), 8)
        self.assertIn("民俗", y["note"])
        noon = yijing.for_datetime(date(2026, 8, 16), 0)
        self.assertNotEqual(y["lower"], noon["lower"] or y["dong_yao"] != noon["dong_yao"] or True)
        # 子时与午时下卦或动爻应能变化（梅花加时）
        self.assertTrue(y["lower"] != noon["lower"] or y["dong_yao"] != noon["dong_yao"])

    def test_liuren_has_sike_and_yuejiang(self):
        rec = liuren.for_datetime(date(2026, 8, 16), 12)
        self.assertEqual(len(rec["sike"]), 4)
        self.assertEqual(len(rec["shenjiang"]), 12)
        self.assertTrue(rec["yuejiang"]["zhi"])
        self.assertIn("民俗", rec["note"])
        names = {g["god"] for g in rec["shenjiang"]}
        self.assertIn("贵人", names)
        self.assertNotIn("编造吉凶", rec["note"])

    def test_cast_six_and_match_only_local(self):
        self._put_stock(self.CODE_DIV, "测试卜卦", "银行", dead=True)
        yaos = self._yaos_div()
        out = yijing.result_from_yaos(yaos, date(2026, 8, 16), 12, backfill=False)
        self.assertEqual(len(out["yaos"]), 6)
        self.assertTrue(out["gua"]["ok"])
        self.assertIn("012345", out["bit_digits"] and "".join(str(x) for x in out["bit_digits"]))
        digits = {s["code"] for s in yijing.match_stock_codes(
            ["".join(str(x) for x in out["bit_digits"])])}
        self.assertIn(self.CODE_DIV, digits)
        self.assertGreaterEqual(out.get("digit_matched_count") or 0, 1)
        self.assertNotIn(self.CODE_DIV, {s["code"] for s in out["stocks"]})
        self.assertNotIn("sz999999", {s["code"] for s in out["stocks"]})
        self.assertFalse(any(s.get("name") == "编造" for s in out["stocks"]))
        fake = yijing.match_stock_codes(["999991", "888888"])
        self.assertEqual(fake, [])

    def test_divine_uses_gua_wx_hot_finance_score(self):
        from unittest.mock import patch
        # 本卦上下巽坎 → 木、水。银行=金，医药=木，房地产=土
        self._put_stock(self.CODE_OK, "木热高分", "医药生物", score_high=True)
        execute("UPDATE stock_metrics SET buy_index=99 WHERE code=?", (self.CODE_OK,))
        self._put_grade(self.CODE_OK, "A")
        self._put_stock(self.CODE_DEAD, "土热高分", "房地产", score_high=True)
        self._put_grade(self.CODE_DEAD, "A")
        self._put_stock(self.CODE_LOW, "木热低分", "医药生物", score_high=False)
        self._put_grade(self.CODE_LOW, "A")
        self._put_stock(self.CODE_NONE, "木热无评级", "医药生物", score_high=True)
        yaos = self._yaos_div()
        gua = yijing.hexagram_from_yaos(yaos)
        self.assertTrue({"木", "水"} <= set(yijing.gua_elements(gua)))
        with patch.object(yijing, "industry_heat_map", return_value={
            "医药生物": 18.0, "房地产": 22.0, "交通运输": 3.0, "银行": 16.0,
        }):
            out = yijing.result_from_yaos(yaos, date(2026, 8, 16), 12, backfill=False)
        codes = {s["code"] for s in out.get("stocks") or []}
        self.assertIn(self.CODE_OK, codes)
        self.assertNotIn(self.CODE_DEAD, codes)
        self.assertNotIn(self.CODE_LOW, codes)
        self.assertNotIn(self.CODE_NONE, codes)
        hit = next(s for s in out["stocks"] if s["code"] == self.CODE_OK)
        self.assertEqual(hit.get("finance_grade"), "A")
        self.assertGreaterEqual(hit.get("score") or 0, yijing.MIN_SCORE)
        self.assertTrue(set(hit.get("wuxing") or []) & {"木", "水"})
        self.assertNotEqual(hit.get("finance_grade"), "")
        self.assertIn("医药生物", out.get("hot_industries") or [])
        self.assertEqual(intelpick.get_page("up")["items"], [])

    def test_divination_invalid_date(self):
        bad = yijing.divination("不是日期", 12)
        self.assertFalse(bad.get("ok"))
        self.assertEqual(bad.get("stocks"), [])
        rng = random.Random(1)
        ok = yijing.divination("2026-08-16", 12, rng, backfill=False)
        self.assertTrue(ok.get("ok"))
        self.assertEqual(len(ok["yaos"]), 6)

    def test_qimen_predict_filters_and_cap(self):
        # 2026-08-16 秋季：金旺水相，土死。银行=金，房地产=土
        self._put_stock(self.CODE_OK, "测试旺相高分", "银行", score_high=True)
        self._put_stock(self.CODE_DEAD, "测试死气高分", "房地产", score_high=True)
        self._put_stock(self.CODE_LOW, "测试旺相低分", "银行", score_high=False)
        bad = almanac_pick.predict("bad-date", 12)
        self.assertFalse(bad.get("ok"))
        self.assertEqual(bad.get("stocks"), [])
        out = almanac_pick.predict("2026-08-16", 12)
        self.assertTrue(out.get("ok"))
        self.assertLessEqual(out.get("count") or 0, 50)
        codes = {s["code"] for s in out.get("stocks") or []}
        self.assertIn(self.CODE_OK, codes)
        self.assertNotIn(self.CODE_DEAD, codes)
        self.assertNotIn(self.CODE_LOW, codes)
        for s in out.get("stocks") or []:
            self.assertTrue(set(s.get("wuxing") or []) & {"金", "水"})
            self.assertGreaterEqual(s.get("score") or 0, 55)
            self.assertNotEqual(s.get("advice"), "减持")
        self.assertEqual(intelpick.get_page("up")["items"], [])

    def test_payload_and_api(self):
        from fastapi.testclient import TestClient
        a = almanac.get_almanac(date(2026, 8, 16), persist=False, hour=12)
        self.assertIn("yijing", a)
        self.assertEqual(len(a["yijing_plates"]), 12)
        self.assertEqual(len(a["liuren_plates"]), 12)
        self.assertEqual(a["yijing"]["zhi_index"], 6)
        client = TestClient(app)
        body = client.get("/api/macro/almanac?date=2026-08-16&span=0&hour=0").json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body["yijing"]["zhi_index"], 0)
        self.assertEqual(len(body["liuren"]["sike"]), 4)
        bad = client.post("/api/macro/almanac/divination", json={"date": "xxxx"})
        self.assertFalse(bad.json().get("ok"))
        pred = client.post("/api/macro/almanac/qimen-predict", json={"date": "2026-08-16", "hour": 12})
        self.assertTrue(pred.json().get("ok"))
        self.assertLessEqual(len(pred.json().get("stocks") or []), 50)
        self.assertEqual(intelpick.get_page("down")["items"], [])


if __name__ == "__main__":
    unittest.main()
