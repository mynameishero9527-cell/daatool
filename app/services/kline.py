"""K线服务：多周期获取 + 本地数据库持久化 + 技术指标计算。"""
from ..cache import cached
from ..config import TTL_KLINE_INTRADAY
from ..database import executemany, query
from ..datasources import offline, tencent
from ..datasources.base import with_failover

PERIODS = {"day": "日K", "week": "周K", "month": "月K"}


def _persist_day(code: str, rows: list[list]) -> None:
    executemany(
        "INSERT OR REPLACE INTO daily_kline(code,date,open,close,high,low,volume) VALUES(?,?,?,?,?,?,?)",
        [(code, r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows],
    )


def _load_day_from_db(code: str, limit: int = 320) -> list[list]:
    rows = query(
        "SELECT date,open,close,high,low,volume FROM daily_kline WHERE code=? ORDER BY date DESC LIMIT ?",
        (code, limit),
    )
    return [[r["date"], r["open"], r["close"], r["high"], r["low"], r["volume"]] for r in reversed(rows)]


def get_kline(code: str, period: str = "day", count: int = 320) -> dict:
    """K线：优先网络（腾讯），日K落库；网络失败回退本地库，再退离线生成。"""
    period = period if period in PERIODS else "day"

    def loader():
        try:
            rows = with_failover([
                ("腾讯财经", lambda: tencent.fetch_kline(code, period, count)),
            ], context=f"kline {code}")
            if rows and period == "day":
                _persist_day(code, rows)
            if rows:
                return {"rows": rows, "source": "腾讯财经", "offline": False}
        except Exception:  # noqa: BLE001
            pass
        if period == "day":
            db_rows = _load_day_from_db(code, count)
            if db_rows:
                return {"rows": db_rows, "source": "本地数据库", "offline": True}
        return {"rows": offline.fetch_kline(code, period, count), "source": "离线兜底", "offline": True}

    data = cached(f"kline:{code}:{period}:{count}", TTL_KLINE_INTRADAY, loader)
    rows = data["rows"]
    return {
        "code": code, "period": period,
        "dates": [r[0] for r in rows],
        "kline": [[r[1], r[2], r[3], r[4]] for r in rows],   # open close high low（ECharts 蜡烛序）
        "volumes": [r[5] for r in rows],
        "ma": {n: _ma([r[2] for r in rows], n) for n in (5, 10, 20, 60)},
        "macd": _macd([r[2] for r in rows]),
        "source": data["source"], "offline": data["offline"],
    }


def get_minute(code: str) -> dict:
    def loader():
        try:
            return {**tencent.fetch_minute(code), "offline": False}
        except Exception:  # noqa: BLE001
            rows = offline.fetch_kline(code, "day", 48)
            return {
                "date": "offline", "prev_close": rows[0][2],
                "points": [[f"{9 + i // 60:02d}{i % 60:02d}", r[2], r[5]] for i, r in enumerate(rows)],
                "offline": True,
            }
    return cached(f"minute:{code}", 60, loader)


def _ma(closes: list[float], n: int) -> list:
    out = []
    for i in range(len(closes)):
        if i + 1 < n:
            out.append(None)
        else:
            out.append(round(sum(closes[i + 1 - n:i + 1]) / n, 3))
    return out


def _ema(values: list[float], n: int) -> list[float]:
    out, k = [], 2 / (n + 1)
    prev = values[0] if values else 0
    for v in values:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def _macd(closes: list[float]) -> dict:
    if not closes:
        return {"dif": [], "dea": [], "bar": []}
    ema12, ema26 = _ema(closes, 12), _ema(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = _ema(dif, 9)
    bar = [round((a - b) * 2, 4) for a, b in zip(dif, dea)]
    return {"dif": [round(x, 4) for x in dif], "dea": [round(x, 4) for x in dea], "bar": bar}
