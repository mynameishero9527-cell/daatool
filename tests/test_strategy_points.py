"""13.0.31：买/卖点须命中≥2个方案，并结合近半年真实日K。不回退观察池。"""
from __future__ import annotations

from datetime import timedelta
import unittest

from unittest.mock import patch

from app.database import execute, init_db, query, set_meta_json
from app.services import intelpick, strategy


CODE_OK = "sz998811"
CODE_RISK = "sz998812"
CODE_HIGH = "sz998813"


def _purge(*codes: str) -> None:
    for code in codes:
        for sql in (
            "DELETE FROM stock_snapshot WHERE code=?",
            "DELETE FROM stock_metrics WHERE code=?",
            "DELETE FROM stock_list WHERE code=?",
            "DELETE FROM daily_kline WHERE code=?",
        ):
            execute(sql, (code,))


def _seed(code: str, *, name="测", **kw) -> None:
    execute(
        "INSERT OR REPLACE INTO stock_list(code,name,market,board,updated_at,industry)"
        " VALUES(?,?,?,?,?,?)",
        (code, name, "SZ", "主板", "2026-08-16", "电子"),
    )
    snap = dict(
        price=10.0, pct=1.2, turnover_rate=2.0, volume_ratio=1.3,
        pe_ttm=12.0, pb=1.5, float_mv=80.0, total_mv=100.0,
        main_net_in=3000.0, main_in=4000.0, main_out=1000.0,
        main_net_in_d5=2000.0, pct_d5=3.0, pct_d10=4.0, pct_d20=6.0, pct_d60=8.0,
        amount=20000.0,
    )
    met = dict(
        ma5=10, ma10=9.8, ma20=9.5, ma60=9.0, rsi14=55, macd_bar=0.2,
        macd_gold=1, ma_bull=1, above_ma20=1, break20_high=0, pullback_shrink=1,
        pos60=0.48, drawdown60=12, bias20=2.0, stab_g1=1, stab_g2=1, stab_g3=1,
        stab_g4=1, stabilize_score=70, buy_index=76, sentiment=58, dark_power=62,
        divergence="无",
    )
    for k, v in kw.items():
        if k in snap:
            snap[k] = v
        else:
            met[k] = v
    execute(
        "INSERT OR REPLACE INTO stock_snapshot("
        "code,name,price,pct,turnover_rate,volume_ratio,pe_ttm,pb,float_mv,total_mv,"
        "main_net_in,main_in,main_out,main_net_in_d5,pct_d5,pct_d10,pct_d20,pct_d60,amount,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (code, name, snap["price"], snap["pct"], snap["turnover_rate"], snap["volume_ratio"],
         snap["pe_ttm"], snap["pb"], snap["float_mv"], snap["total_mv"],
         snap["main_net_in"], snap["main_in"], snap["main_out"], snap["main_net_in_d5"],
         snap["pct_d5"], snap["pct_d10"], snap["pct_d20"], snap["pct_d60"], snap["amount"],
         "2026-08-16"),
    )
    execute(
        "INSERT OR REPLACE INTO stock_metrics("
        "code,ma5,ma10,ma20,ma60,rsi14,macd_bar,macd_gold,ma_bull,above_ma20,"
        "break20_high,pullback_shrink,pos60,drawdown60,bias20,"
        "stab_g1,stab_g2,stab_g3,stab_g4,stabilize_score,buy_index,sentiment,"
        "dark_power,divergence,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (code, met["ma5"], met["ma10"], met["ma20"], met["ma60"], met["rsi14"], met["macd_bar"],
         met["macd_gold"], met["ma_bull"], met["above_ma20"], met["break20_high"],
         met["pullback_shrink"], met["pos60"], met["drawdown60"], met["bias20"],
         met["stab_g1"], met["stab_g2"], met["stab_g3"], met["stab_g4"],
         met["stabilize_score"], met["buy_index"], met["sentiment"], met["dark_power"],
         met["divergence"], "2026-08-16"),
    )


def _seed_half_kline(code: str, *, n=80, first=8.5, last=10.0, low=8.0, high=12.0) -> None:
    """写入真实 OHLC 日K，不用涨跌幅编造。"""
    end = strategy._shanghai_today()
    days = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    execute("DELETE FROM daily_kline WHERE code=?", (code,))
    for i, day in enumerate(days):
        t = i / max(1, n - 1)
        close = first + (last - first) * t
        hi = close + 0.25
        lo = close - 0.25
        if i == 0:
            lo = low
        if i == n - 1:
            hi = max(high, close)
        if hi < lo:
            hi, lo = lo, hi
        execute(
            "INSERT OR REPLACE INTO daily_kline(code,date,open,close,high,low,volume)"
            " VALUES(?,?,?,?,?,?,?)",
            (code, day.isoformat(), close, close, hi, lo, 1000 + i),
        )


class StrategyPointsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        _purge(CODE_OK, CODE_RISK, CODE_HIGH)
        set_meta_json(strategy.RULES_VER_KEY, strategy.RULES_VER)
        set_meta_json(strategy.META_KEY_BUY, ["BP"])
        set_meta_json(strategy.META_KEY_SELL, ["ST"])

    def tearDown(self):
        _purge(CODE_OK, CODE_RISK, CODE_HIGH)
        set_meta_json(strategy.RULES_VER_KEY, strategy.RULES_VER)
        set_meta_json(strategy.META_KEY_BUY, list(strategy.DEFAULT_BUY_IDS))
        set_meta_json(strategy.META_KEY_SELL, list(strategy.DEFAULT_SELL_IDS))

    def test_buy_and_sell_names_differ(self):
        buy_names = {p.name for p in strategy.BUY_PLANS.values() if p.id not in ("I", "J")}
        sell_names = {p.name for p in strategy.SELL_PLANS.values() if p.id not in ("I", "J")}
        self.assertTrue(buy_names)
        self.assertTrue(sell_names)
        self.assertFalse(buy_names & sell_names)
        self.assertIn("潜力主升", buy_names)
        self.assertIn("高位止盈", sell_names)

    def test_legacy_a_maps_separately(self):
        self.assertEqual(strategy._normalize_ids(["A"], "buy"), ["BP"])
        self.assertEqual(strategy._normalize_ids(["A"], "sell"), ["ST"])
        self.assertEqual(strategy._normalize_ids(["E", "D"], "buy"), ["BT", "BD"])
        self.assertEqual(strategy._normalize_ids(["E", "D"], "sell"), ["SB", "SD"])

    def test_buy_keeps_potential_drops_crash(self):
        _seed(CODE_OK, name="潜力股")
        _seed(CODE_RISK, name="暴跌股", pct=-7.5, pct_d5=-15, buy_index=88, pos60=0.20, rsi14=22)
        self.assertTrue(self._matches("buy", "BP", CODE_OK))
        self.assertFalse(self._matches("buy", "BP", CODE_RISK))
        hits = strategy.collect_hits("buy", enabled=["BP"], limit=80)
        self.assertNotIn(CODE_RISK, {r["code"] for r in hits})

    def _matches(self, kind: str, pid: str, code: str) -> bool:
        plan = strategy.catalog_map(kind)[pid]
        rows = query(
            "SELECT s.code FROM stock_metrics m JOIN stock_snapshot s ON s.code=m.code "
            f"WHERE {strategy._where(plan.where)} AND s.code=?",
            (code,),
        )
        return bool(rows)

    def test_no_watch_pool_fallback(self):
        _seed(CODE_RISK, name="仅指数高", buy_index=99, main_net_in=-100, pos60=0.95, rsi14=80, bias20=16)
        self.assertFalse(self._matches("buy", "BP", CODE_RISK))
        rows, src, note = strategy.collect_buy_points(8)
        self.assertNotIn(src, ("top_buy_index", "relaxed_65"))
        self.assertNotIn(CODE_RISK, {r["code"] for r in rows})
        with patch.object(strategy, "collect_hits", return_value=[]):
            empty, src2, note2 = strategy.collect_buy_points(8)
        self.assertEqual(empty, [])
        self.assertEqual(src2, "empty")
        self.assertIn("观察池", note2)

    def test_sell_take_profit_not_dead_weak(self):
        _seed(CODE_HIGH, name="高位股", pos60=0.92, rsi14=76, bias20=12, sentiment=80, main_net_in=-200)
        _seed(CODE_RISK, name="残弱股", pos60=0.12, buy_index=18, main_net_in=-9000, rsi14=28, pct_d5=-12)
        self.assertTrue(self._matches("sell", "ST", CODE_HIGH))
        self.assertFalse(self._matches("sell", "ST", CODE_RISK))
        hits = strategy.collect_hits("sell", enabled=["ST"], limit=80)
        self.assertNotIn(CODE_RISK, {r["code"] for r in hits})

    def test_decorate_exposes_finance_and_wuxing(self):
        from app.services import alerts
        _seed(CODE_OK, name="潜力股")
        rows = [{
            "code": CODE_OK, "name": "潜力股", "industry": "电子",
            "buy_index": 76, "sentiment": 58, "main_net_in": 3000,
            "volume_ratio": 1.3, "price": 10, "pct": 1.2,
        }]
        alerts._decorate_buy_rows(rows, "buy")
        self.assertIn("finance_grade", rows[0])
        self.assertNotEqual(rows[0].get("finance_grade"), "A")
        self.assertIsInstance(rows[0].get("wuxing"), list)
        self.assertTrue(rows[0]["wuxing"])
        summary = rows[0].get("advice_summary") or ""
        self.assertIn("财报评级", summary)
        self.assertIn("五行", summary)

    def test_empty_sell_has_reason(self):
        rows, src, note = strategy.collect_sell_points(8)
        if not rows:
            self.assertEqual(src, "empty")
            self.assertTrue(note)
        self.assertEqual(intelpick.get_page("up")["items"], [])

    def test_one_plan_enabled_not_recommended(self):
        _seed(CODE_OK, name="潜力股")
        _seed_half_kline(CODE_OK)
        rows, src, note = strategy.collect_buy_points(8)
        self.assertEqual(rows, [])
        self.assertEqual(src, "empty")
        self.assertIn("至少 2 个方案", note)
        self.assertIn("观察池", note)

    def test_single_plan_hit_not_recommended(self):
        set_meta_json(strategy.META_KEY_BUY, ["BP", "BT", "BZ"])
        _seed(CODE_OK, name="仅主升", ma_bull=0, pullback_shrink=0, pct=3.0, pos60=0.50)
        _seed_half_kline(CODE_OK)
        self.assertTrue(self._matches("buy", "BP", CODE_OK))
        self.assertFalse(self._matches("buy", "BT", CODE_OK))
        self.assertFalse(self._matches("buy", "BZ", CODE_OK))
        rows, src, note = strategy.collect_buy_points(8)
        self.assertNotIn(CODE_OK, {r["code"] for r in rows})
        if not rows:
            self.assertEqual(src, "empty")
            self.assertIn("至少 2 个方案", note)

    def test_multi_hit_with_half_kline_recommended(self):
        set_meta_json(strategy.META_KEY_BUY, ["BP", "BT", "BZ"])
        _seed(CODE_OK, name="潜力股", buy_index=99.5, macd_bar=9.5)
        _seed_half_kline(CODE_OK)
        self.assertTrue(self._matches("buy", "BP", CODE_OK))
        self.assertTrue(self._matches("buy", "BT", CODE_OK))
        rows, src, note = strategy.collect_buy_points(40)
        codes = {r["code"] for r in rows}
        self.assertIn(CODE_OK, codes)
        self.assertEqual(src, "hit")
        hit = next(r for r in rows if r["code"] == CODE_OK)
        self.assertGreaterEqual(len(hit.get("plans") or []), 2)
        self.assertGreaterEqual(hit.get("half_bars") or 0, 60)
        self.assertIsNotNone(hit.get("half_pos"))
        self.assertIsNotNone(hit.get("half_range_pct"))
        self.assertIn("近半年", note)

    def test_missing_kline_dropped(self):
        set_meta_json(strategy.META_KEY_BUY, ["BP", "BT", "BZ"])
        _seed(CODE_OK, name="潜力股")
        self.assertTrue(self._matches("buy", "BP", CODE_OK))
        self.assertTrue(self._matches("buy", "BT", CODE_OK))
        rows, src, note = strategy.collect_buy_points(8)
        self.assertNotIn(CODE_OK, {r["code"] for r in rows})
        if not rows:
            self.assertEqual(src, "empty")
            self.assertIn("日K", note)

    def test_no_invent_kline_from_pct(self):
        set_meta_json(strategy.META_KEY_BUY, ["BP", "BT", "BZ"])
        _seed(CODE_OK, name="潜力股", pct_d20=18, pct_d60=30)
        rows, src, _note = strategy.collect_buy_points(8)
        self.assertNotIn(CODE_OK, {r["code"] for r in rows})
        stats = strategy.half_year_stats([CODE_OK])
        self.assertNotIn(CODE_OK, stats)

    def test_sell_multi_hit_with_half_kline(self):
        set_meta_json(strategy.META_KEY_SELL, ["ST", "SO", "SR"])
        _seed(CODE_HIGH, name="高位股", pos60=0.99, rsi14=92, bias20=18, sentiment=88, main_net_in=-200)
        _seed_half_kline(CODE_HIGH, first=7.2, last=10.0, low=7.0, high=10.3)
        self.assertTrue(self._matches("sell", "ST", CODE_HIGH))
        self.assertTrue(self._matches("sell", "SO", CODE_HIGH))
        raw = {
            "code": CODE_HIGH, "name": "高位股", "price": 10.0, "pct": 1.2,
            "buy_index": 40, "pos60": 0.99, "rsi14": 92, "bias20": 18,
            "plans": ["ST", "SO"],
        }
        strategy.stamp_plans(raw, ["ST", "SO"], "sell")
        with patch.object(strategy, "collect_hits", return_value=[raw]):
            rows, src, _note = strategy.collect_sell_points(8)
        self.assertEqual(src, "hit")
        self.assertIn(CODE_HIGH, {r["code"] for r in rows})
        hit = next(r for r in rows if r["code"] == CODE_HIGH)
        self.assertGreaterEqual(len(hit.get("plans") or []), 2)
        self.assertGreaterEqual(hit.get("half_pos") or 0, 0.68)
        self.assertTrue(strategy.half_year_pass(hit, "sell"))

    def test_upgrade_old_single_default(self):
        set_meta_json(strategy.META_KEY_BUY, ["BP"])
        set_meta_json(strategy.META_KEY_SELL, ["ST"])
        set_meta_json(strategy.RULES_VER_KEY, "13.0.30")
        strategy.ensure_multi_plan_defaults()
        self.assertEqual(strategy.get_enabled("buy"), ["BP", "BT", "BZ"])
        self.assertEqual(strategy.get_enabled("sell"), ["ST", "SO", "SR"])

    def test_upgrade_keeps_custom(self):
        set_meta_json(strategy.META_KEY_BUY, ["BP", "BD"])
        set_meta_json(strategy.META_KEY_SELL, ["ST", "SB"])
        set_meta_json(strategy.RULES_VER_KEY, "13.0.30")
        strategy.ensure_multi_plan_defaults()
        self.assertEqual(strategy.get_enabled("buy"), ["BP", "BD"])
        self.assertEqual(strategy.get_enabled("sell"), ["ST", "SB"])

    def test_collect_hits_min_hits_keeps_single_for_engine(self):
        _seed(CODE_OK, name="潜力股")
        one = strategy.collect_hits("buy", enabled=["BP"], limit=80, min_hits=1)
        two = strategy.collect_hits("buy", enabled=["BP"], limit=80, min_hits=2)
        self.assertNotIn(CODE_OK, {r["code"] for r in two})
        if any(r["code"] == CODE_OK for r in one):
            self.assertEqual(len(next(r for r in one if r["code"] == CODE_OK)["plans"]), 1)


if __name__ == "__main__":
    unittest.main()
