"""按日因子框：第 t 日只用 bars[0..t]，禁止未来函数。"""
from __future__ import annotations

import logging

from ..database import execute, executemany, query

log = logging.getLogger("factor_frame")

FACTOR_COLS = ("ma5", "ma10", "ma20", "macd_bar", "rsi14", "bias20", "pos60", "vol_ratio")


def ensure_tables() -> None:
    execute(
        "CREATE TABLE IF NOT EXISTS factor_daily ("
        "code TEXT NOT NULL, date TEXT NOT NULL,"
        "ma5 REAL, ma10 REAL, ma20 REAL, macd_bar REAL, rsi14 REAL,"
        "bias20 REAL, pos60 REAL, vol_ratio REAL,"
        "PRIMARY KEY (code, date))"
    )
    execute("CREATE INDEX IF NOT EXISTS idx_factor_daily_date ON factor_daily(date)")


def _mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return sum(xs) / len(xs)


def _ema(values: list[float], span: int) -> float | None:
    if not values:
        return None
    k = 2.0 / (span + 1.0)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1.0 - k)
    return e


def _rsi14(closes: list[float]) -> float | None:
    if len(closes) < 15:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = _mean(gains[-14:]) or 0.0
    al = _mean(losses[-14:]) or 0.0
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def _macd_bar(closes: list[float]) -> float | None:
    if len(closes) < 26:
        return None
    dif_series = []
    for i in range(len(closes)):
        prefix = closes[: i + 1]
        e12 = _ema(prefix, 12)
        e26 = _ema(prefix, 26)
        if e12 is None or e26 is None:
            continue
        dif_series.append(e12 - e26)
    if len(dif_series) < 9:
        return None
    dea = _ema(dif_series, 9)
    if dea is None:
        return None
    return 2.0 * (dif_series[-1] - dea)


def append_bar(bars: list[dict], t: int) -> dict:
    """bars 按日期升序。只使用 bars[0..t]。"""
    prefix = bars[: t + 1]
    if not prefix:
        return {k: None for k in FACTOR_COLS} | {"date": None}
    closes: list[float] = []
    volumes: list[float] = []
    for b in prefix:
        try:
            closes.append(float(b["close"]))
        except (TypeError, ValueError, KeyError):
            continue
        try:
            volumes.append(float(b.get("volume") or 0))
        except (TypeError, ValueError):
            volumes.append(0.0)
    n = len(closes)
    c = closes[-1] if closes else None
    ma5 = _mean(closes[-5:]) if n >= 5 else None
    ma10 = _mean(closes[-10:]) if n >= 10 else None
    ma20 = _mean(closes[-20:]) if n >= 20 else None
    macd_bar = _macd_bar(closes)
    rsi14 = _rsi14(closes)
    bias20 = ((c / ma20) - 1.0) * 100.0 if c is not None and ma20 else None
    pos60 = None
    if n >= 60 and c is not None:
        lo, hi = min(closes[-60:]), max(closes[-60:])
        pos60 = (c - lo) / (hi - lo) if hi > lo else 0.5
    vol_ma = _mean(volumes[-20:]) if n >= 20 else None
    vol_ratio = None
    if vol_ma and vol_ma > 0 and volumes:
        vol_ratio = volumes[-1] / vol_ma
    last = prefix[-1]
    return {
        "date": last.get("date"),
        "ma5": None if ma5 is None else round(ma5, 4),
        "ma10": None if ma10 is None else round(ma10, 4),
        "ma20": None if ma20 is None else round(ma20, 4),
        "macd_bar": None if macd_bar is None else round(macd_bar, 6),
        "rsi14": None if rsi14 is None else round(rsi14, 4),
        "bias20": None if bias20 is None else round(bias20, 4),
        "pos60": None if pos60 is None else round(pos60, 4),
        "vol_ratio": None if vol_ratio is None else round(vol_ratio, 4),
    }


def build_one(code: str) -> int:
    ensure_tables()
    bars = query(
        "SELECT date,open,high,low,close,volume FROM daily_kline "
        "WHERE code=? AND close IS NOT NULL ORDER BY date",
        (code,),
    )
    if not bars:
        return 0
    rows = []
    for t in range(len(bars)):
        f = append_bar(bars, t)
        if not f.get("date"):
            continue
        rows.append((
            code, f["date"], f["ma5"], f["ma10"], f["ma20"], f["macd_bar"],
            f["rsi14"], f["bias20"], f["pos60"], f["vol_ratio"],
        ))
    if rows:
        execute("DELETE FROM factor_daily WHERE code=?", (code,))
        executemany(
            "INSERT OR REPLACE INTO factor_daily("
            "code,date,ma5,ma10,ma20,macd_bar,rsi14,bias20,pos60,vol_ratio)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


def build_all(max_codes: int | None = None) -> dict:
    ensure_tables()
    codes = [r["code"] for r in query("SELECT DISTINCT code FROM daily_kline ORDER BY code")]
    if max_codes:
        codes = codes[: max(1, int(max_codes))]
    total = 0
    for code in codes:
        try:
            total += build_one(code)
        except Exception as exc:  # noqa: BLE001
            log.warning("因子生成失败 %s: %s", code, exc)
    return {"ok": True, "codes": len(codes), "rows": total}


def has_rows() -> bool:
    ensure_tables()
    try:
        return int((query("SELECT COUNT(*) AS n FROM factor_daily")[0] or {}).get("n") or 0) > 0
    except Exception:  # noqa: BLE001
        return False


def row_count() -> int:
    ensure_tables()
    try:
        return int((query("SELECT COUNT(*) AS n FROM factor_daily")[0] or {}).get("n") or 0)
    except Exception:  # noqa: BLE001
        return 0
