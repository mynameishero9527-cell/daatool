"""K线服务：多周期获取 + 本地数据库持久化 + 技术指标计算。"""
from datetime import datetime, timedelta

from ..cache import cached
from ..config import TTL_KLINE_INTRADAY
from ..database import executemany, query
from ..datasources import eastmoney, offline, tencent
from ..datasources.base import with_failover

PERIODS = {"day": "日K", "week": "周K", "month": "月K", "5day": "5日"}
_INDEX_PREFIX = ("sh000", "sz399", "bj899", "sh880")


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
            if period == "5day":
                rows = tencent.fetch_m5_kline(code, 240)
            else:
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
    kline = []
    for r in rows:
        o, c, h, l = r[1], r[2], r[3], r[4]
        lo, hi = (min(h, l), max(h, l)) if None not in (h, l) else (l, h)
        kline.append([o, c, lo, hi])  # ECharts 蜡烛：open, close, low, high
    return {
        "code": code, "period": period,
        "dates": [r[0] for r in rows],
        "kline": kline,
        "volumes": [r[5] for r in rows],
        "ma": {n: _ma([r[2] for r in rows], n) for n in (5, 10, 20, 60)},
        "macd": _macd([r[2] for r in rows]),
        "source": data["source"], "offline": data["offline"],
        "bars": len(rows),
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


def _is_index(code: str) -> bool:
    s = (code or "").strip().lower()
    return s.startswith(_INDEX_PREFIX)


def _running_sum(values: list[float]) -> list[float]:
    total, out = 0.0, []
    for v in values:
        total += float(v or 0)
        out.append(round(total, 4))
    return out


def _interval_if_cumulative(values: list[float]) -> tuple[list[float], list[float], bool]:
    """分时资金接口多为开盘累计。识别后柱用增量、线用累计。"""
    nums = [float(v or 0) for v in values]
    if len(nums) < 4:
        return nums, _running_sum(nums), False
    diffs = [nums[0]] + [round(nums[i] - nums[i - 1], 4) for i in range(1, len(nums))]
    last_pos = nums[-1] >= 0
    same = sum(1 for v in nums if (v >= 0) == last_pos)
    increasing = sum(
        1 for i in range(1, len(nums)) if abs(nums[i]) + 1e-9 >= abs(nums[i - 1]) * 0.92
    )
    mixed = sum(1 for v in nums if (v >= 0) != (nums[0] >= 0))
    cumulative = (
        same / len(nums) >= 0.85
        and increasing / (len(nums) - 1) >= 0.7
        and mixed <= len(nums) * 0.2
    )
    if cumulative:
        return diffs, [round(x, 4) for x in nums], True
    return nums, _running_sum(nums), False


def _persist_fund_daily(code: str, rows: list[dict]) -> None:
    daily = [r for r in rows if r.get("date") and len(str(r["date"])) >= 10 and "-" in str(r["date"])]
    if not daily:
        return
    executemany(
        "INSERT OR REPLACE INTO stock_fund_daily(code,trade_date,main_net_yi,super_net_yi,large_net_yi) "
        "VALUES(?,?,?,?,?)",
        [(code, r["date"][:10], r.get("main_net_yi"), r.get("super_net_yi"), r.get("large_net_yi"))
         for r in daily],
    )


def _load_fund_daily(code: str, limit: int = 240) -> list[dict]:
    rows = query(
        "SELECT trade_date, main_net_yi, super_net_yi, large_net_yi FROM stock_fund_daily "
        "WHERE code=? ORDER BY trade_date DESC LIMIT ?",
        (code, limit),
    )
    return [{
        "date": r["trade_date"],
        "main_net_yi": r["main_net_yi"] if r["main_net_yi"] is not None else 0.0,
        "super_net_yi": r["super_net_yi"],
        "large_net_yi": r["large_net_yi"],
    } for r in reversed(rows)]


def _snapshot_flow_bar(code: str) -> list[dict]:
    rows = query("SELECT main_net_in, updated_at FROM stock_snapshot WHERE code=?", (code,))
    if not rows or rows[0]["main_net_in"] is None:
        return []
    yi = round((rows[0]["main_net_in"] or 0) / 10000.0, 4)
    day = str(rows[0]["updated_at"] or "")[:10]
    if len(day) < 10:
        day = datetime.now().strftime("%Y-%m-%d")
    return [{"date": day, "main_net_yi": yi, "super_net_yi": None, "large_net_yi": None}]


def _week_key(day: str) -> str:
    dt = datetime.strptime(day[:10], "%Y-%m-%d")
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y-%m-%d")


def _aggregate_flow(rows: list[dict], period: str) -> list[dict]:
    if period not in ("week", "month") or not rows:
        return rows
    buckets: dict[str, dict] = {}
    for r in rows:
        day = str(r.get("date") or "")
        if len(day) < 10:
            continue
        key = _week_key(day) if period == "week" else day[:7]
        b = buckets.setdefault(key, {
            "date": key, "main_net_yi": 0.0, "super_net_yi": 0.0, "large_net_yi": 0.0,
            "_sc": 0, "_lc": 0,
        })
        b["main_net_yi"] = round((b["main_net_yi"] or 0) + float(r.get("main_net_yi") or 0), 4)
        if r.get("super_net_yi") is not None:
            b["super_net_yi"] = round((b["super_net_yi"] or 0) + float(r["super_net_yi"]), 4)
            b["_sc"] += 1
        if r.get("large_net_yi") is not None:
            b["large_net_yi"] = round((b["large_net_yi"] or 0) + float(r["large_net_yi"]), 4)
            b["_lc"] += 1
    out = []
    for key in sorted(buckets):
        b = buckets[key]
        if not b["_sc"]:
            b["super_net_yi"] = None
        if not b["_lc"]:
            b["large_net_yi"] = None
        b.pop("_sc", None)
        b.pop("_lc", None)
        out.append(b)
    return out


def get_fund_kline(code: str, period: str = "day") -> dict:
    """个股主力资金图。缺数不编造、不用涨跌幅冒充资金。"""
    from . import market as market_svc
    code = market_svc.normalize_code(code) or (code or "").strip().lower()
    period = period if period in ("minute", "5day", "day", "week", "month") else "day"
    empty = {
        "code": code, "period": period, "dates": [], "main_net_yi": [],
        "cumulative_yi": [], "super_net_yi": [], "large_net_yi": [],
        "unit": "亿", "source": "", "offline": True, "cumulative_intraday": False,
        "empty_reason": "", "note": "柱为该周期主力净流入，线为累计。单位亿元。未用涨跌幅代替资金。",
    }
    if _is_index(code):
        empty["empty_reason"] = "指数没有个股主力资金流向，不编造。"
        return empty

    def loader():
        rows: list[dict] = []
        source, offline = "东方财富", False
        try:
            rows = eastmoney.fetch_stock_fflow(code, period)
        except Exception:  # noqa: BLE001
            rows = []
        if rows and period != "minute":
            _persist_fund_daily(code, rows)
        if not rows and period != "minute":
            rows = _load_fund_daily(code, 240 if period != "5day" else 5)
            if rows:
                source, offline = "本地数据库", True
        if not rows and period in ("day", "5day", "week", "month"):
            rows = _snapshot_flow_bar(code)
            if rows:
                source, offline = "当日快照", True
        if period in ("week", "month") and rows:
            dates = [str(r.get("date") or "") for r in rows]
            daily_like = all(len(d) >= 10 and d[4:5] == "-" for d in dates if d)
            if daily_like:
                rows = _aggregate_flow(rows, period)
        if period == "5day" and len(rows) > 5:
            rows = rows[-5:]
        return {"rows": rows, "source": source, "offline": offline}

    data = cached(f"fundk:{code}:{period}", 60 if period == "minute" else TTL_KLINE_INTRADAY, loader)
    rows = data["rows"] or []
    if not rows:
        empty["empty_reason"] = "暂无主力资金流向。未用涨跌幅代替资金。"
        return empty
    nets = [float(r.get("main_net_yi") or 0) for r in rows]
    if period == "minute":
        bars, cum, cum_flag = _interval_if_cumulative(nets)
    else:
        bars, cum, cum_flag = nets, _running_sum(nets), False
    note = empty["note"]
    if period == "minute" and cum_flag:
        note = "分时柱为较上一分钟增量，线为开盘累计主力净流入。单位亿元。"
    elif len(rows) < 4 and period != "minute":
        note = "历史资金K暂仅获取到最近交易日，已落库待累积。未用涨跌幅补全。"
    return {
        "code": code, "period": period,
        "dates": [r["date"] for r in rows],
        "main_net_yi": [round(x, 4) for x in bars],
        "cumulative_yi": cum,
        "super_net_yi": [r.get("super_net_yi") for r in rows],
        "large_net_yi": [r.get("large_net_yi") for r in rows],
        "unit": "亿", "source": data["source"], "offline": data["offline"],
        "cumulative_intraday": cum_flag, "empty_reason": "", "note": note,
    }
