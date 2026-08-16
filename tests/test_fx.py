"""13.0.25：各国汇率解析与落库。不插值周末，不拿在岸价冒充离岸。"""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.cache import cache
from app.database import execute, init_db, query
from app.datasources import fx as fx_src
from app.services import fx as fx_svc
from app.services import intelpick


class FxParseTests(unittest.TestCase):
    def test_sina_empty_not_invented(self):
        self.assertIsNone(fx_src.parse_sina_fx_fields("fx_susdcny", []))
        self.assertIsNone(fx_src.parse_sina_fx_fields("fx_susdcny", ["", "", ""]))
        self.assertIsNone(fx_src.parse_sina_fx_fields("fx_susdcny", ["美元人民币"]))

    def test_sina_time_first_quote(self):
        row = fx_src.parse_sina_fx_fields(
            "fx_susdcny",
            ["23:59:00", "7.1234", "7.1200", "7.1180", "7.1300", "7.1190", "美元兑人民币"],
        )
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["rate"], 7.1234)
        self.assertEqual(row["time"], "23:59:00")
        self.assertEqual(row["name"], "美元兑人民币")
        self.assertAlmostEqual(row["prev"], 7.1200)
        self.assertLess(abs(row["pct"]), 1)

    def test_em_skips_blank_rate(self):
        out = fx_src.parse_em_fx_diff([
            {"f12": "USDCNYI", "f2": "-", "f14": "美元人民币混合"},
            {"f12": "EURCNYI", "f2": 7.85, "f18": 7.80, "f3": 0.64, "f4": 0.05, "f14": "欧元人民币混合"},
        ])
        self.assertNotIn("USDCNYI", out)
        self.assertAlmostEqual(out["EURCNYI"]["rate"], 7.85)
        self.assertAlmostEqual(out["EURCNYI"]["prev"], 7.80)

    def test_em_jpy_100_scaled_to_one(self):
        raw = {"rate": 4.2358, "prev": 4.2300, "change": 0.0058, "pct": 0.14, "source": "东方财富"}
        out = fx_svc._scale_em_quote("JPYCNY", raw)
        self.assertAlmostEqual(out["rate"], 0.042358, places=6)
        self.assertAlmostEqual(out["prev"], 0.042300, places=6)
        usd = fx_svc._scale_em_quote("USDCNY", {"rate": 6.74, "prev": 6.75, "change": -0.01, "pct": -0.15})
        self.assertAlmostEqual(usd["rate"], 6.74)

    def test_frankfurter_inverts_and_skips_weekend_and_cnh(self):
        payload = {
            "base": "CNY",
            "rates": {
                "2026-08-14": {"USD": 0.14, "EUR": 0.12},
                "2026-08-15": {"USD": 0.139},
                "not-a-date": {"USD": 0.14},
            },
        }
        rows = fx_src.parse_frankfurter_cny_timeseries(payload, ["USD", "EUR", "CNH"])
        pairs = {(r["pair"], r["trade_date"]) for r in rows}
        self.assertIn(("USDCNY", "2026-08-14"), pairs)
        self.assertIn(("EURCNY", "2026-08-14"), pairs)
        self.assertIn(("USDCNY", "2026-08-15"), pairs)
        self.assertNotIn(("EURCNY", "2026-08-15"), pairs)
        self.assertFalse(any(r["pair"] == "USDCNH" for r in rows))
        usd = next(r for r in rows if r["pair"] == "USDCNY" and r["trade_date"] == "2026-08-14")
        self.assertAlmostEqual(usd["rate"], 1 / 0.14, places=6)
        self.assertEqual(usd["source"], "欧洲央行")
        self.assertEqual(fx_src.parse_frankfurter_cny_timeseries({}, ["USD"]), [])
        self.assertEqual(fx_src.parse_frankfurter_cny_timeseries({"rates": None}, ["USD"]), [])


class FxPersistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        cache.clear()
        execute("DELETE FROM fx_daily WHERE pair IN ('USDCNY','EURCNY','USDCNH','JPYCNY')")

    def test_invalid_range_not_guessed(self):
        rng, err = fx_svc.resolve_range("2026-13-40", "2026-08-16")
        self.assertIsNone(rng)
        self.assertIn("开始日期", err)
        rng, err = fx_svc.resolve_range("2026-08-16", "2026-08-01")
        self.assertIsNone(rng)
        self.assertIn("晚于", err)
        out = fx_svc.pull_range("abcd", "2026-08-16")
        self.assertFalse(out["ok"])
        self.assertEqual(out["written"], 0)

    def test_default_range_is_one_year(self):
        rng, err = fx_svc.resolve_range("", "")
        self.assertEqual(err, "")
        s, e = rng
        self.assertEqual((e - s).days, 365)
        self.assertLessEqual(e, date.today())

    def test_persist_official_no_weekend_fill(self):
        fake = [
            {"pair": "USDCNY", "trade_date": "2026-08-14", "rate": 7.15, "source": "欧洲央行"},
            {"pair": "EURCNY", "trade_date": "2026-08-14", "rate": 8.30, "source": "欧洲央行"},
        ]
        with patch.object(fx_src, "fetch_frankfurter_range", return_value=fake):
            out = fx_svc.persist_official_range(date(2026, 8, 14), date(2026, 8, 16))
        self.assertTrue(out["ok"])
        self.assertEqual(out["written"], 2)
        days = {r["trade_date"] for r in query("SELECT trade_date FROM fx_daily WHERE pair='USDCNY'")}
        self.assertEqual(days, {"2026-08-14"})
        self.assertNotIn("2026-08-15", days)
        self.assertNotIn("2026-08-16", days)

    def test_official_not_overwritten_by_live_and_cnh_not_from_cny(self):
        fx_svc._upsert_official([
            {"pair": "USDCNY", "trade_date": "2026-08-14", "rate": 7.15, "source": "欧洲央行"},
            {"pair": "USDCNH", "trade_date": "2026-08-14", "rate": 7.20, "source": "欧洲央行"},
        ])
        fx_svc._upsert_official([
            {"pair": "USDCNY", "trade_date": "2026-08-14", "rate": 9.99, "source": "新浪财经"},
        ])
        row = query("SELECT rate, source FROM fx_daily WHERE pair='USDCNY' AND trade_date='2026-08-14'")[0]
        self.assertAlmostEqual(row["rate"], 7.15)
        self.assertEqual(row["source"], "欧洲央行")
        self.assertEqual(query("SELECT * FROM fx_daily WHERE pair='USDCNH'"), [])
        n = fx_svc.persist_cnh_snapshot({})
        self.assertEqual(n, 0)
        n = fx_svc.persist_cnh_snapshot({"USDCNH": {"rate": 7.21, "source": "新浪财经"}}, date(2026, 8, 15))
        self.assertEqual(n, 1)
        cnh = query("SELECT rate, source FROM fx_daily WHERE pair='USDCNH'")[0]
        self.assertAlmostEqual(cnh["rate"], 7.21)
        self.assertEqual(cnh["source"], "新浪财经")

    def test_history_unknown_pair(self):
        d = fx_svc.get_history("FAKECNY")
        self.assertFalse(d["ok"])
        self.assertEqual(d["items"], [])

    def test_history_empty_reason(self):
        d = fx_svc.get_history("USDCNY", "2026-01-01", "2026-01-10")
        self.assertTrue(d["ok"])
        self.assertEqual(d["items"], [])
        self.assertIn("无点", d["empty_reason"])

    def test_snapshot_falls_back_to_local(self):
        fx_svc._upsert_official([
            {"pair": "USDCNY", "trade_date": "2026-08-14", "rate": 7.15, "source": "欧洲央行"},
        ])
        with patch.object(fx_svc, "_fetch_live", return_value={}):
            cache.delete("fx:live")
            snap = fx_svc.get_snapshot()
        usd = next(x for x in snap["items"] if x["pair"] == "USDCNY")
        self.assertAlmostEqual(usd["rate"], 7.15)
        self.assertTrue(snap["offline"])
        self.assertIn("不编造", snap["reason"])
        self.assertIn("live", snap["schedule"])

    def test_prune_old(self):
        old = (date.today() - timedelta(days=500)).isoformat()
        execute(
            "INSERT INTO fx_daily(pair, trade_date, rate, source) VALUES(?,?,?,?)",
            ("USDCNY", old, 6.5, "欧洲央行"),
        )
        n = fx_svc.prune_old(400)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(query("SELECT * FROM fx_daily WHERE trade_date=?", (old,)), [])

    def test_fx_session_window(self):
        tz = ZoneInfo("Asia/Shanghai")
        sat = datetime(2026, 8, 15, 12, 0, tzinfo=tz)  # Saturday
        sun_night = datetime(2026, 8, 16, 22, 30, tzinfo=tz)
        fri_late = datetime(2026, 8, 14, 22, 30, tzinfo=tz)
        wed = datetime(2026, 8, 12, 10, 0, tzinfo=tz)
        self.assertFalse(fx_svc.fx_session_open(sat))
        self.assertTrue(fx_svc.fx_session_open(sun_night))
        self.assertFalse(fx_svc.fx_session_open(fri_late))
        self.assertTrue(fx_svc.fx_session_open(wed))

    def test_intelpick_still_empty(self):
        up = intelpick.get_page("up")
        self.assertEqual(up["items"], [])


if __name__ == "__main__":
    unittest.main()
