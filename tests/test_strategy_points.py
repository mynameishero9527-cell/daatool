"""13.0.29：买点潜力结构 / 卖点止盈避险。不共用方案名，不回退观察池。"""
from __future__ import annotations

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


class StrategyPointsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        _purge(CODE_OK, CODE_RISK, CODE_HIGH)
        set_meta_json(strategy.META_KEY_BUY, ["BP"])
        set_meta_json(strategy.META_KEY_SELL, ["ST"])

    def tearDown(self):
        _purge(CODE_OK, CODE_RISK, CODE_HIGH)
        set_meta_json(strategy.META_KEY_BUY, ["BP"])
        set_meta_json(strategy.META_KEY_SELL, ["ST"])

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

    def test_empty_sell_has_reason(self):
        rows, src, note = strategy.collect_sell_points(8)
        if not rows:
            self.assertEqual(src, "empty")
            self.assertTrue(note)
        self.assertEqual(intelpick.get_page("up")["items"], [])


if __name__ == "__main__":
    unittest.main()
