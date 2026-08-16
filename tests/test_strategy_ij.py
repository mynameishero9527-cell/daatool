"""13.0.23：方案 I/J 只用持股缓存；缺则零命中。周线门缺数据不否决。"""
from __future__ import annotations

from datetime import date, timedelta
import unittest

from app.database import execute, init_db
from app.services import holder_feature, portfolio, strategy, week_gate


CODE = "sz998801"


def _purge(code: str = CODE) -> None:
    for sql in (
        "DELETE FROM stock_snapshot WHERE code=?",
        "DELETE FROM stock_metrics WHERE code=?",
        "DELETE FROM stock_list WHERE code=?",
        "DELETE FROM holder_feature WHERE code=?",
        "DELETE FROM stock_holders WHERE code=?",
        "DELETE FROM daily_kline WHERE code=?",
    ):
        try:
            execute(sql, (code,))
        except Exception:  # noqa: BLE001
            pass


def _seed_quote(code: str, *, name="测试股", main_net_in=1000.0, buy_index=99.0, pos60=0.7) -> None:
    execute(
        "INSERT OR REPLACE INTO stock_list(code,name,market,board,updated_at,industry)"
        " VALUES(?,?,?,?,?,?)",
        (code, name, "SZ", "主板", "2026-08-15", "电子"),
    )
    execute(
        "INSERT OR REPLACE INTO stock_snapshot("
        "code,name,price,pct,turnover_rate,volume_ratio,pe_ttm,pb,float_mv,total_mv,"
        "main_net_in,main_in,main_out,main_net_in_d5,pct_d5,pct_d10,pct_d20,pct_d60,amount,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (code, name, 10.0, 1.0, 1.0, 1.0, 10.0, 1.0, 100.0, 100.0,
         main_net_in, 10.0, 5.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, "2026-08-15"),
    )
    execute(
        "INSERT OR REPLACE INTO stock_metrics("
        "code,ma5,ma10,ma20,ma60,rsi14,macd_bar,macd_gold,ma_bull,above_ma20,"
        "break20_high,pullback_shrink,pos60,drawdown60,bias20,"
        "stab_g1,stab_g2,stab_g3,stab_g4,stabilize_score,buy_index,sentiment,"
        "dark_power,divergence,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (code, 10, 10, 10, 10, 50, 0.1, 0, 0, 1, 0, 0, pos60, 10, 0,
         None, None, None, None, None, buy_index, 50, 50, "无", "2026-08-15"),
    )


class HolderFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        holder_feature.ensure_tables()

    def test_weekday_days(self):
        self.assertEqual(holder_feature.weekday_days_between(date(2026, 8, 14), date(2026, 8, 14)), 0)
        # Fri 14 → Mon 17 = 1 weekday (Mon)
        self.assertEqual(holder_feature.weekday_days_between(date(2026, 8, 14), date(2026, 8, 17)), 1)
        self.assertEqual(holder_feature.weekday_days_between(date(2026, 8, 17), date(2026, 8, 14)), -1)

    def test_no_guess_from_top10(self):
        feat = holder_feature.features_from_payload("sz000001", {
            "top10": [{"name": "某某", "ratio": 10}],
            "latest": {},
            "unlocks": [],
        }, date(2026, 8, 15))
        self.assertIsNone(feat["holders_qoq"])
        self.assertEqual(feat["has_institution"], 0)

    def test_unlock_window(self):
        feat = holder_feature.features_from_payload("sz000001", {
            "latest": {"holders_qoq": -3.2},
            "institution_ratio": 12.5,
            "unlocks": [
                {"date": "2026-08-20", "float_ratio": 4.2},
                {"date": "2026-07-01", "float_ratio": 8.0},
            ],
        }, date(2026, 8, 15))
        self.assertEqual(feat["has_institution"], 1)
        self.assertLess(feat["holders_qoq"], 0)
        self.assertGreaterEqual(feat["unlock_days_to"], 0)
        self.assertLessEqual(feat["unlock_days_to"], 10)
        self.assertGreaterEqual(feat["last_unlock_days_ago"], 5)


class StrategyIJTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        holder_feature.ensure_tables()

    def setUp(self):
        _purge()

    def tearDown(self):
        _purge()

    def test_missing_holders_zero_hits(self):
        _seed_quote(CODE)
        buys = strategy.collect_hits("buy", enabled=["I"], limit=80)
        sells = strategy.collect_hits("sell", enabled=["I"], limit=80)
        self.assertFalse(any(r.get("code") == CODE for r in buys))
        self.assertFalse(any(r.get("code") == CODE for r in sells))

    def test_i_buy_and_sell(self):
        _seed_quote(CODE, main_net_in=2000, buy_index=70)
        holder_feature.upsert_from_payload(CODE, {
            "asof": "2026-06-30",
            "latest": {"holders_qoq": -2.5},
            "institution_count": 12,
            "unlocks": [],
        }, date(2026, 8, 15))
        buys = strategy.collect_hits("buy", enabled=["I"], limit=80)
        self.assertTrue(any(r.get("code") == CODE for r in buys))
        holder_feature.upsert_from_payload(CODE, {
            "asof": "2026-06-30",
            "latest": {"holders_qoq": 6.0},
            "institution_ratio": 8.0,
            "unlocks": [],
        }, date(2026, 8, 15))
        sells = strategy.collect_hits("sell", enabled=["I"], limit=80)
        self.assertTrue(any(r.get("code") == CODE for r in sells))

    def test_j_sell_and_buy(self):
        _seed_quote(CODE, main_net_in=1500, buy_index=70, pos60=0.72)
        holder_feature.upsert_from_payload(CODE, {
            "latest": {"holders_qoq": 0.1},
            "institution_ratio": 5.0,
            "unlocks": [{"date": "2026-08-20", "float_ratio": 4.0}],
        }, date(2026, 8, 15))
        sells = strategy.collect_hits("sell", enabled=["J"], limit=80)
        self.assertTrue(any(r.get("code") == CODE for r in sells))
        holder_feature.upsert_from_payload(CODE, {
            "latest": {"holders_qoq": 0.1},
            "institution_ratio": 5.0,
            "unlocks": [{"date": "2026-08-01", "float_ratio": 4.0}],
        }, date(2026, 8, 15))
        buys = strategy.collect_hits("buy", enabled=["J"], limit=80)
        self.assertTrue(any(r.get("code") == CODE for r in buys))

    def test_default_ids_still_a(self):
        self.assertEqual(strategy.DEFAULT_IDS, ["A"])
        self.assertIn("I", strategy.PLANS)
        self.assertIn("J", strategy.PLANS)


class WeekGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        _purge()

    def tearDown(self):
        _purge()

    def test_missing_weeks_skip(self):
        st = week_gate.week_ma_state(CODE)
        self.assertEqual(st["state"], "skip")
        kept = week_gate.apply_buy_gate([{"code": CODE, "name": "x"}], enabled=True)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["week_gate"], "skip")

    def test_fail_filters_buy(self):
        d0 = date(2025, 1, 3)
        # 20 周：前 15 周收 20，后 5 周收到 10 → MA5 < MA20
        for i in range(20):
            day = d0 + timedelta(days=i * 7)
            close = 20.0 if i < 15 else 10.0
            execute(
                "INSERT OR REPLACE INTO daily_kline(code,date,open,close,high,low,volume)"
                " VALUES(?,?,?,?,?,?,?)",
                (CODE, day.isoformat(), close, close, close, close, 1000),
            )
        st = week_gate.week_ma_state(CODE)
        self.assertEqual(st["state"], "fail")
        kept = week_gate.apply_buy_gate([{"code": CODE}], enabled=True)
        self.assertEqual(kept, [])


class PortfolioTests(unittest.TestCase):
    def test_stock_and_industry_cap_no_redistribute(self):
        items = [{"code": f"c{i}", "industry": "银行"} for i in range(10)]
        out, meta = portfolio.constrain(items, drop_sell=False, drop_unlock=False)
        self.assertEqual(len(out), 10)
        self.assertLessEqual(sum(r["suggest_weight"] for r in out) + 1e-9, 0.30 + 1e-6)
        self.assertGreater(meta["leftover"], 0.6)
        no_ind = [{"code": "x1", "industry": ""}, {"code": "x2", "industry": "电子"}]
        out2, _ = portfolio.constrain(no_ind, drop_sell=False, drop_unlock=False)
        self.assertTrue(out2[0]["industry_unconstrained"])
        self.assertFalse(out2[1]["industry_unconstrained"])
        self.assertLessEqual(out2[0]["suggest_weight"], 0.10 + 1e-9)


if __name__ == "__main__":
    unittest.main()
