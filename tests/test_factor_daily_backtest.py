"""13.0.23：factor_daily 无未来函数；A–H 回放关闭；样本<30 无夏普。"""
from __future__ import annotations

from datetime import date, timedelta
import unittest

from app.database import execute, init_db
from app.services import backtest, factor_frame

CODE = "sz998802"


def _purge() -> None:
    try:
        execute("DELETE FROM daily_kline WHERE code=?", (CODE,))
        execute("DELETE FROM factor_daily WHERE code=?", (CODE,))
    except Exception:  # noqa: BLE001
        pass


def _bar(d: date, o, h, l, c, v=1000):
    return {"date": d.isoformat(), "open": o, "high": h, "low": l, "close": c, "volume": v}


class FactorFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        factor_frame.ensure_tables()

    def test_append_bar_no_lookahead(self):
        bars = []
        d0 = date(2026, 1, 5)
        for i in range(30):
            c = 10.0 + i * 0.1
            bars.append(_bar(d0 + timedelta(days=i), c, c, c, c))
        mid = factor_frame.append_bar(bars, 20)
        leaked = factor_frame.append_bar(bars[:21], 20)
        self.assertEqual(mid["ma5"], leaked["ma5"])
        self.assertEqual(mid["ma20"], leaked["ma20"])
        full = factor_frame.append_bar(bars, 29)
        self.assertNotEqual(full["ma5"], mid["ma5"])

    def test_insufficient_bars_null_macd(self):
        bars = [_bar(date(2026, 1, 5) + timedelta(days=i), 10, 10, 10, 10) for i in range(10)]
        f = factor_frame.append_bar(bars, 9)
        self.assertIsNone(f["macd_bar"])
        self.assertIsNone(f["ma20"])


class BacktestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        factor_frame.ensure_tables()

    def setUp(self):
        _purge()

    def tearDown(self):
        _purge()

    def test_ah_replay_closed(self):
        st = backtest.ah_replay_status()
        self.assertFalse(st["open"])
        self.assertIn("metrics_daily", st["reason"])

    def test_factor_closed_without_rows(self):
        execute("DELETE FROM factor_daily WHERE code=?", (CODE,))
        # 不依赖全库是否已有别人的因子行：直接测空表逻辑用 status.rows
        empty = backtest.run_factor_backtest("2099-01-01", "2099-01-02")
        self.assertTrue(empty["ok"])
        if not factor_frame.has_rows():
            self.assertFalse(empty["open"])

    def test_limit_up_and_ambiguous_and_no_sharpe(self):
        d0 = date(2025, 1, 2)
        bars = []
        # 先铺 30 根上涨形成均线多头，再给信号与成交
        for i in range(40):
            c = 10.0 + i * 0.05
            bars.append((CODE, (d0 + timedelta(days=i)).isoformat(), c, c, c + 0.1, c - 0.1, 1000))
        # 第 40 根后：一字涨停
        lim = d0 + timedelta(days=40)
        bars.append((CODE, lim.isoformat(), 13.2, 13.2, 13.2, 13.2, 1000))
        for row in bars:
            execute(
                "INSERT OR REPLACE INTO daily_kline(code,date,open,close,high,low,volume) VALUES(?,?,?,?,?,?,?)",
                row,
            )
        n = factor_frame.build_one(CODE)
        self.assertGreater(n, 0)
        out = backtest.run_factor_backtest("2025-01-02", "2025-03-20", fees=True, max_codes=10, codes=[CODE])
        self.assertTrue(out["open"])
        self.assertFalse(out["ah_replay"]["open"])
        self.assertLess(out["summary"]["sample"], 30)
        self.assertIsNone(out["summary"]["sharpe"])
        self.assertIn("样本 < 30", out["summary"]["sharpe_note"])

    def test_ambiguous_helper(self):
        self.assertTrue(backtest._limit_locked(10.0, 11.0, 11.0, 11.0, 11.0))
        self.assertFalse(backtest._limit_locked(10.0, 10.2, 10.5, 10.1, 10.3))
        f_buy = {"ma5": 12, "ma10": 11, "ma20": 10, "macd_bar": 0.2, "rsi14": 55, "bias20": 0}
        self.assertTrue(backtest.buy_signal(f_buy))
        self.assertTrue(backtest.sell_signal({"ma5": 9, "ma10": 10, "macd_bar": 0.1, "rsi14": 50}))


if __name__ == "__main__":
    unittest.main()
