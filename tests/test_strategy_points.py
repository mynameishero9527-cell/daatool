"""13.0.34：买/卖点对齐增持减持，叠加板块热度、财报、上涨空间。不回退观察池。"""
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
            "DELETE FROM stock_finance_grade WHERE code=?",
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


def _grade(code: str, grade="A") -> None:
    execute(
        "INSERT OR REPLACE INTO stock_finance_grade(code,grade,score,summary,updated_at)"
        " VALUES(?,?,?,?,?)",
        (code, grade, 6 if grade == "A" else 3, "测试评级", "2026-08-16"),
    )


def _hot_boards():
    return patch("app.services.sector.industry_heat_map", return_value={"电子": 10.0})


class StrategyPointsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        _purge(CODE_OK, CODE_RISK, CODE_HIGH)
        set_meta_json(strategy.RULES_VER_KEY, strategy.RULES_VER)
        set_meta_json(strategy.META_KEY_BUY, ["BP"])
        set_meta_json(strategy.META_KEY_SELL, ["ST"])
        set_meta_json(strategy.META_KEY_HIDDEN_BUY, [])
        set_meta_json(strategy.META_KEY_HIDDEN_SELL, [])

    def tearDown(self):
        _purge(CODE_OK, CODE_RISK, CODE_HIGH)
        set_meta_json(strategy.RULES_VER_KEY, strategy.RULES_VER)
        set_meta_json(strategy.META_KEY_BUY, list(strategy.DEFAULT_BUY_IDS))
        set_meta_json(strategy.META_KEY_SELL, list(strategy.DEFAULT_SELL_IDS))
        set_meta_json(strategy.META_KEY_HIDDEN_BUY, [])
        set_meta_json(strategy.META_KEY_HIDDEN_SELL, [])

    def test_buy_and_sell_names_differ(self):
        buy_names = {p.name for p in strategy.BUY_PLANS.values() if p.id not in ("I", "J")}
        sell_names = {p.name for p in strategy.SELL_PLANS.values() if p.id not in ("I", "J")}
        self.assertTrue(buy_names)
        self.assertTrue(sell_names)
        self.assertFalse(buy_names & sell_names)
        self.assertIn("选股方案A", buy_names)
        self.assertIn("潜力主升", buy_names)
        self.assertIn("高位止盈", sell_names)

    def test_legacy_a_maps_separately(self):
        self.assertEqual(strategy._normalize_ids(["A"], "buy"), ["A"])
        self.assertEqual(strategy._normalize_ids(["A"], "sell"), ["ST"])
        self.assertEqual(strategy._normalize_ids(["E", "D"], "buy"), ["BT", "BD"])
        self.assertEqual(strategy._normalize_ids(["E", "D"], "sell"), ["SB", "SD"])

    def test_delete_plan_keeps_last_and_reset_restores(self):
        set_meta_json(strategy.META_KEY_BUY, ["A", "BP"])
        gone = strategy.delete_plan("buy", "BP")
        self.assertTrue(gone.get("ok"))
        self.assertEqual(gone.get("removed"), "BP")
        buy_ids = [p["id"] for p in strategy.catalog("buy")]
        self.assertNotIn("BP", buy_ids)
        self.assertIn("A", buy_ids)
        self.assertEqual(strategy.get_enabled("buy"), ["A"])
        for extra in ("BT", "BZ", "BD", "I", "J"):
            self.assertTrue(strategy.delete_plan("buy", extra).get("ok"))
        self.assertEqual([p["id"] for p in strategy.catalog("buy")], ["A"])
        last = strategy.delete_plan("buy", "A")
        self.assertFalse(last.get("ok"))
        self.assertIn("至少", last.get("error") or "")
        self.assertIn("A", [p["id"] for p in strategy.catalog("buy")])
        bad = strategy.delete_plan("buy", "ZZ")
        self.assertFalse(bad.get("ok"))
        strategy.set_enabled(buy_ids=[])
        self.assertEqual(strategy.get_enabled("buy"), ["A"])
        self.assertNotIn("BP", strategy.get_enabled("buy"))
        restored = strategy.reset_side("buy")
        self.assertTrue(restored.get("ok"))
        self.assertIn("BP", [p["id"] for p in strategy.catalog("buy")])
        self.assertEqual(strategy.get_enabled("buy"), ["A"])
        self.assertEqual(intelpick.get_page("up")["items"], [])

    def test_delete_sell_does_not_touch_buy(self):
        set_meta_json(strategy.META_KEY_BUY, ["A"])
        set_meta_json(strategy.META_KEY_SELL, ["ST", "SO", "SR"])
        out = strategy.delete_plan("sell", "SO")
        self.assertTrue(out.get("ok"))
        self.assertNotIn("SO", [p["id"] for p in strategy.catalog("sell")])
        self.assertIn("A", [p["id"] for p in strategy.catalog("buy")])
        self.assertEqual(strategy.get_enabled("buy"), ["A"])
        self.assertEqual(strategy.get_hidden("buy"), [])

    def test_plan_a_first_version_and_fallback(self):
        set_meta_json(strategy.META_KEY_BUY, ["A"])
        _seed(CODE_OK, name="极佳A", buy_index=99.9, main_net_in=4000)
        self.assertTrue(self._matches("buy", "A", CODE_OK))
        rows, src, note = strategy.collect_buy_points(40, backfill=False)
        self.assertEqual(src, "hit")
        self.assertIn(CODE_OK, {r["code"] for r in rows})
        self.assertEqual(rows[0].get("hit_action"), "极佳买点·可分批建仓")
        self.assertIn("选股方案A", note)
        _purge(CODE_OK)
        _seed(CODE_OK, name="较好A", buy_index=68, main_net_in=2000)
        self.assertFalse(self._matches("buy", "A", CODE_OK))
        plan_a = strategy.BUY_PLANS["A"]
        only = query(
            strategy._SELECT.format(where=strategy._where(plan_a.fallback_where) + " AND s.code=?",
                                    order=plan_a.order),
            (CODE_OK, 8),
        )
        with patch.object(strategy, "collect_hits", return_value=[]):
            with patch.object(strategy, "_fetch", return_value=only):
                rows2, src2, note2 = strategy.collect_buy_points(8, backfill=False)
        self.assertEqual(src2, "relaxed_65")
        self.assertIn(CODE_OK, {r["code"] for r in rows2})
        self.assertIn("65", note2)
        self.assertIn("非极佳", note2)
        self.assertEqual(intelpick.get_page("up")["items"], [])

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
        rows, src, note = strategy.collect_buy_points(8, backfill=False)
        self.assertNotIn(src, ("top_buy_index", "relaxed_65"))
        self.assertNotIn(CODE_RISK, {r["code"] for r in rows})
        with patch.object(strategy, "collect_hits", return_value=[]):
            empty, src2, note2 = strategy.collect_buy_points(8, backfill=False)
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
        sell_rows = [dict(rows[0])]
        alerts._decorate_buy_rows(sell_rows, "sell")
        self.assertEqual(sell_rows[0].get("op_advice"), "减持")

    def test_empty_sell_has_reason(self):
        rows, src, note = strategy.collect_sell_points(8, backfill=False)
        if not rows:
            self.assertEqual(src, "empty")
            self.assertTrue(note)
        self.assertEqual(intelpick.get_page("up")["items"], [])

    def test_one_plan_parallel_with_half_kline(self):
        _seed(CODE_OK, name="潜力股", buy_index=99.5, macd_bar=9.5)
        _seed_half_kline(CODE_OK)
        _grade(CODE_OK)
        with _hot_boards():
            rows, src, note = strategy.collect_buy_points(40, backfill=False)
        self.assertEqual(src, "hit")
        self.assertIn(CODE_OK, {r["code"] for r in rows})
        self.assertTrue(note)

    def test_single_plan_recommended_if_quality(self):
        set_meta_json(strategy.META_KEY_BUY, ["BP", "BT", "BZ"])
        _seed(CODE_OK, name="仅主升", buy_index=99.5, ma_bull=0, pullback_shrink=0, pct=3.0, pos60=0.50)
        _seed_half_kline(CODE_OK)
        _grade(CODE_OK)
        self.assertTrue(self._matches("buy", "BP", CODE_OK))
        self.assertFalse(self._matches("buy", "BT", CODE_OK))
        self.assertFalse(self._matches("buy", "BZ", CODE_OK))
        raw = {
            "code": CODE_OK, "name": "仅主升", "price": 10.0, "pct": 3.0,
            "buy_index": 99.5, "main_net_in": 3000, "float_mv": 80,
            "volume_ratio": 1.3, "dark_power": 62, "pos60": 0.50,
            "plans": ["BP"],
        }
        strategy.stamp_plans(raw, ["BP"], "buy")
        with _hot_boards(), patch.object(strategy, "collect_hits", return_value=[raw]):
            rows, src, note = strategy.collect_buy_points(8, backfill=False)
        self.assertEqual(src, "hit")
        self.assertIn(CODE_OK, {r["code"] for r in rows})
        self.assertEqual(len(rows[0].get("plans") or []), 1)
        self.assertIn("并集", note)

    def test_week_gate_does_not_empty_buy_points(self):
        _seed(CODE_OK, name="潜力股", buy_index=99.5, macd_bar=9.5)
        _seed_half_kline(CODE_OK)
        _grade(CODE_OK)
        with _hot_boards(), patch("app.services.week_gate.apply_buy_gate", side_effect=lambda items, enabled=True: []):
            rows, src, _note = strategy.collect_buy_points(40, backfill=False)
        self.assertEqual(src, "hit")
        self.assertIn(CODE_OK, {r["code"] for r in rows})

    def test_multi_hit_with_half_kline_recommended(self):
        set_meta_json(strategy.META_KEY_BUY, ["BP", "BT", "BZ"])
        _seed(CODE_OK, name="潜力股", buy_index=99.5, macd_bar=9.5)
        _seed_half_kline(CODE_OK)
        _grade(CODE_OK)
        self.assertTrue(self._matches("buy", "BP", CODE_OK))
        self.assertTrue(self._matches("buy", "BT", CODE_OK))
        with _hot_boards():
            rows, src, note = strategy.collect_buy_points(40, backfill=False)
        codes = {r["code"] for r in rows}
        self.assertIn(CODE_OK, codes)
        self.assertEqual(src, "hit")
        hit = next(r for r in rows if r["code"] == CODE_OK)
        self.assertGreaterEqual(len(hit.get("plans") or []), 2)
        self.assertGreaterEqual(hit.get("half_bars") or 0, 60)
        self.assertIsNotNone(hit.get("half_pos"))
        self.assertIsNotNone(hit.get("half_range_pct"))
        self.assertTrue(note)

    def test_plan_a_does_not_need_half_kline(self):
        set_meta_json(strategy.META_KEY_BUY, ["A"])
        _seed(CODE_OK, name="方案A股", buy_index=99.8, main_net_in=3000)
        rows, src, note = strategy.collect_buy_points(40, backfill=False)
        self.assertEqual(src, "hit")
        self.assertIn(CODE_OK, {r["code"] for r in rows})
        self.assertIn("选股方案A", note)
        self.assertEqual(int(rows[0].get("half_bars") or 0), 0)

    def test_no_invent_kline_from_pct(self):
        set_meta_json(strategy.META_KEY_BUY, ["A"])
        _seed(CODE_OK, name="方案A股", buy_index=99.7, pct_d20=18, pct_d60=30)
        rows, src, _note = strategy.collect_buy_points(40, backfill=False)
        self.assertIn(CODE_OK, {r["code"] for r in rows})
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
            rows, src, _note = strategy.collect_sell_points(8, backfill=False)
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
        self.assertEqual(strategy.get_enabled("buy"), ["A"])
        self.assertEqual(strategy.get_enabled("sell"), ["ST", "SO", "SR"])

    def test_upgrade_keeps_custom(self):
        set_meta_json(strategy.META_KEY_BUY, ["BP", "BD"])
        set_meta_json(strategy.META_KEY_SELL, ["ST", "SB"])
        set_meta_json(strategy.RULES_VER_KEY, "13.0.30")
        strategy.ensure_multi_plan_defaults()
        self.assertEqual(strategy.get_enabled("buy"), ["BP", "BD"])
        self.assertEqual(strategy.get_enabled("sell"), ["ST", "SB"])

    def test_new_stock_uses_score_and_finance_grade(self):
        set_meta_json(strategy.META_KEY_BUY, ["BP", "BT", "BZ"])
        _seed(CODE_OK, name="新股", buy_index=90, main_net_in=8000, pct=2.0, pct_d5=5, pct_d20=8, pct_d60=10)
        _seed_half_kline(CODE_OK, n=20)
        execute(
            "INSERT OR REPLACE INTO stock_finance_grade(code,grade,score,summary,updated_at)"
            " VALUES(?,?,?,?,?)",
            (CODE_OK, "A", 6, "测试评级", "2026-08-16"),
        )
        raw = {
            "code": CODE_OK, "name": "新股", "price": 10.0, "pct": 2.0,
            "pct_d5": 5, "pct_d20": 8, "pct_d60": 10,
            "buy_index": 90, "main_net_in": 8000, "float_mv": 80,
            "volume_ratio": 1.3, "dark_power": 62, "pos60": 0.48,
            "plans": ["BP"],
        }
        strategy.stamp_plans(raw, ["BP"], "buy")
        with _hot_boards(), patch.object(strategy, "collect_hits", return_value=[raw]):
            rows, src, note = strategy.collect_buy_points(8, backfill=False)
        self.assertEqual(src, "hit")
        self.assertIn(CODE_OK, {r["code"] for r in rows})
        hit = next(r for r in rows if r["code"] == CODE_OK)
        self.assertEqual(hit.get("finance_grade"), "A")
        self.assertGreaterEqual(hit.get("score") or 0, strategy.NEW_BUY_MIN_SCORE)
        self.assertNotEqual(hit.get("finance_grade"), "")

    def test_new_stock_ungraded_not_faked(self):
        set_meta_json(strategy.META_KEY_BUY, ["BP", "BT", "BZ"])
        _seed(CODE_OK, name="新股", buy_index=90, main_net_in=8000, pct=2.0, pct_d5=5)
        _seed_half_kline(CODE_OK, n=20)
        raw = {
            "code": CODE_OK, "name": "新股", "price": 10.0, "pct": 2.0,
            "pct_d5": 5, "pct_d20": 8, "pct_d60": 10,
            "buy_index": 90, "main_net_in": 8000, "float_mv": 80,
            "volume_ratio": 1.3, "dark_power": 62, "pos60": 0.48,
            "plans": ["BP"],
        }
        strategy.stamp_plans(raw, ["BP"], "buy")
        with patch.object(strategy, "collect_hits", return_value=[raw]):
            rows, src, _note = strategy.collect_buy_points(8, backfill=False)
        self.assertEqual(src, "hit")
        self.assertIn(CODE_OK, {r["code"] for r in rows})
        hit = next(r for r in rows if r["code"] == CODE_OK)
        self.assertNotEqual((hit.get("finance_grade") or ""), "A")

    def test_backfill_persists_real_kline_not_pct(self):
        set_meta_json(strategy.META_KEY_BUY, ["BP", "BT", "BZ"])
        _seed(CODE_OK, name="潜力股", buy_index=99.5, macd_bar=9.5)
        end = strategy._shanghai_today()
        fake = []
        d = end
        days = []
        while len(days) < 80:
            if d.weekday() < 5:
                days.append(d)
            d -= timedelta(days=1)
        days.reverse()
        fake = []
        for i, day in enumerate(days):
            t = i / 79
            close = 8.5 + 1.5 * t
            hi, lo = close + 0.25, close - 0.25
            if i == 0:
                lo = 8.0
            if i == 79:
                hi = 12.0
            fake.append([day.isoformat(), close, close, hi, lo, 1000])
        _grade(CODE_OK)
        with _hot_boards(), patch("app.services.kline.fetch_daily_real", return_value=fake):
            rows, src, _note = strategy.collect_buy_points(40, backfill=True)
        stored = query("SELECT COUNT(*) AS n FROM daily_kline WHERE code=?", (CODE_OK,))[0]["n"]
        self.assertGreaterEqual(stored, 60)
        self.assertIn(CODE_OK, {r["code"] for r in rows})
        self.assertEqual(src, "hit")

    def test_quality_aligns_advice_and_room(self):
        buy = {
            "score": 72, "score_advice": "增持", "finance_grade": "A",
            "room_to_high": 18, "sector_hot": 8,
        }
        self.assertTrue(strategy.quality_pass(buy, "buy"))
        buy["score_advice"] = "减持"
        self.assertFalse(strategy.quality_pass(buy, "buy"))
        buy["score_advice"] = "增持"
        buy["room_to_high"] = 4
        self.assertFalse(strategy.quality_pass(buy, "buy"))
        buy["finance_grade"] = "D"
        buy["room_to_high"] = 20
        self.assertFalse(strategy.quality_pass(buy, "buy"))
        sell = {
            "score": 80, "score_advice": "增持", "finance_grade": "A",
            "room_to_high": 18, "sector_hot": 8,
        }
        self.assertFalse(strategy.quality_pass(sell, "sell"))
        sell["room_to_high"] = 3
        self.assertTrue(strategy.quality_pass(sell, "sell"))
        sell["room_to_high"] = 7
        self.assertFalse(strategy.quality_pass(sell, "sell"))
        buy_no_room = {
            "score": 80, "score_advice": "增持", "finance_grade": "A",
            "sector_hot": 8,
        }
        self.assertFalse(strategy.quality_pass(buy_no_room, "buy"))
        ungraded = {
            "score": 62, "score_advice": "保持不变", "finance_grade": "",
            "room_to_high": 15, "sector_hot": 2,
        }
        self.assertTrue(strategy.quality_pass(ungraded, "buy"))
        ungraded["sector_hot"] = -13
        self.assertFalse(strategy.quality_pass(ungraded, "buy"))
        wide = {
            "half_bars": 80, "half_range_pct": 124, "half_pos": 0.50,
            "half_ret": 20, "room_to_high": 26,
        }
        self.assertTrue(strategy.half_year_pass(wide, "buy"))
        self.assertEqual(strategy.point_advice("sell", "增持", 3), "减持")
        self.assertEqual(strategy.point_advice("buy", "增持", 18), "增持")

    def test_plan_a_keeps_small_room_sell_drops_large_room(self):
        set_meta_json(strategy.META_KEY_BUY, ["A"])
        set_meta_json(strategy.META_KEY_SELL, ["ST", "SO", "SR"])
        _seed(CODE_OK, name="空间小", buy_index=88, main_net_in=3000)
        _seed_half_kline(CODE_OK, first=9.2, last=10.0, low=9.0, high=10.3)
        _grade(CODE_OK)
        rows, src, _note = strategy.collect_buy_points(40, backfill=False)
        self.assertEqual(src, "hit")
        self.assertIn(CODE_OK, {r["code"] for r in rows})
        raw = {
            "code": CODE_HIGH, "name": "空间大", "price": 10.0, "pct": 1.2,
            "buy_index": 40, "pos60": 0.99, "rsi14": 92, "bias20": 18,
            "plans": ["ST", "SO"],
        }
        _seed(CODE_HIGH, name="空间大", pos60=0.99, rsi14=92, bias20=18, sentiment=88, main_net_in=-200)
        _seed_half_kline(CODE_HIGH, first=7.2, last=10.0, low=7.0, high=13.0)
        strategy.stamp_plans(raw, ["ST", "SO"], "sell")
        with patch.object(strategy, "collect_hits", return_value=[raw]):
            srows, _s, _n = strategy.collect_sell_points(8, backfill=False)
        self.assertNotIn(CODE_HIGH, {r["code"] for r in srows})

    def test_collect_hits_min_hits_keeps_single_for_engine(self):
        _seed(CODE_OK, name="潜力股")
        one = strategy.collect_hits("buy", enabled=["BP"], limit=80, min_hits=1)
        two = strategy.collect_hits("buy", enabled=["BP"], limit=80, min_hits=2)
        self.assertNotIn(CODE_OK, {r["code"] for r in two})
        if any(r["code"] == CODE_OK for r in one):
            self.assertEqual(len(next(r for r in one if r["code"] == CODE_OK)["plans"]), 1)


if __name__ == "__main__":
    unittest.main()
