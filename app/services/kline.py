"""K线服务：多周期获取 + 本地数据库持久化 + 技术指标计算。"""
from datetime import datetime, timedelta

from ..cache import cache, cached
from ..config import TTL_FUND_KLINE, TTL_KLINE_INTRADAY
from ..database import executemany, query
from ..datasources import eastmoney, offline, sina, tencent
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


_FUND_FIELDS = ("main_net_yi", "super_net_yi", "large_net_yi", "small_net_yi")


def _fund_day_key(row: dict) -> str:
    day = str((row or {}).get("date") or "")[:10]
    return day if len(day) >= 10 and day[4:5] == "-" else ""


def _rows_by_date(rows: list[dict] | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows or []:
        day = _fund_day_key(r)
        if day:
            out[day] = r
    return out


def _combine_fund_sources(em: list[dict], sina_rows: list[dict], local: list[dict]) -> list[dict]:
    """按日合并。东财优先（盘中最新），其次新浪历史，再补本地已落库；缺字段才用次源，不用涨跌幅。"""
    em_d, sina_d, loc_d = _rows_by_date(em), _rows_by_date(sina_rows), _rows_by_date(local)
    days = sorted(set(em_d) | set(sina_d) | set(loc_d))
    out = []
    for day in days:
        row = {"date": day}
        for k in _FUND_FIELDS:
            row[k] = None
            for src in (em_d.get(day), sina_d.get(day), loc_d.get(day)):
                if src is not None and src.get(k) is not None:
                    row[k] = src[k]
                    break
        out.append(row)
    return out


def _persist_fund_daily(code: str, rows: list[dict]) -> None:
    daily = []
    for r in rows or []:
        day = _fund_day_key(r)
        if not day:
            continue
        daily.append((code, day, r.get("main_net_yi"), r.get("super_net_yi"),
                      r.get("large_net_yi"), r.get("small_net_yi")))
    if not daily:
        return
    existing = _rows_by_date(_load_fund_daily(code, 800))
    merged = []
    for code_, day, main, super_, large, small in daily:
        prev = existing.get(day) or {}
        merged.append((
            code_, day,
            main if main is not None else prev.get("main_net_yi"),
            super_ if super_ is not None else prev.get("super_net_yi"),
            large if large is not None else prev.get("large_net_yi"),
            small if small is not None else prev.get("small_net_yi"),
        ))
    executemany(
        "INSERT OR REPLACE INTO stock_fund_daily(code,trade_date,main_net_yi,super_net_yi,large_net_yi,small_net_yi) "
        "VALUES(?,?,?,?,?,?)",
        merged,
    )


def _load_fund_daily(code: str, limit: int = 500) -> list[dict]:
    rows = query(
        "SELECT trade_date, main_net_yi, super_net_yi, large_net_yi, small_net_yi FROM stock_fund_daily "
        "WHERE code=? ORDER BY trade_date DESC LIMIT ?",
        (code, limit),
    )
    return [{
        "date": r["trade_date"],
        "main_net_yi": r["main_net_yi"],
        "super_net_yi": r["super_net_yi"],
        "large_net_yi": r["large_net_yi"],
        "small_net_yi": r.get("small_net_yi"),
    } for r in reversed(rows)]


def invalidate_fund_cache(code: str) -> None:
    c = (code or "").strip().lower()
    cache.delete(f"funddaily:{c}")
    cache.delete_prefix(f"fundk:{c}:")


def _fetch_remote_daily(code: str, lookback: int) -> tuple[list[dict], list[dict], list[str]]:
    em_rows: list[dict] = []
    sina_rows: list[dict] = []
    names: list[str] = []
    try:
        em_rows = eastmoney.fetch_stock_fflow(code, "day", lookback) or []
        if em_rows:
            names.append("东方财富")
    except Exception:  # noqa: BLE001
        em_rows = []
    try:
        sina_rows = sina.fetch_stock_moneyflow_history(code, lookback) or []
        if sina_rows:
            names.append("新浪财经")
    except Exception:  # noqa: BLE001
        sina_rows = []
    return em_rows, sina_rows, names


def sync_fund_history(code: str, lookback: int = 240) -> dict:
    """把个股主力资金日K拉全并写入本地。指数不编造。"""
    from . import market as market_svc
    code = market_svc.normalize_code(code) or (code or "").strip().lower()
    lookback = max(20, min(int(lookback or 240), 500))
    empty = {
        "ok": False, "code": code, "stored_days": 0, "bars": 0,
        "source": "", "first": "", "last": "", "error": "",
    }
    if _is_index(code):
        empty["error"] = "指数没有个股主力资金流向，不编造。"
        return empty
    if not code:
        empty["error"] = "股票代码无效"
        return empty
    invalidate_fund_cache(code)
    local = _load_fund_daily(code, 800)
    em_rows, sina_rows, names = _fetch_remote_daily(code, lookback)
    incoming = _combine_fund_sources(em_rows, sina_rows, [])
    if incoming:
        _persist_fund_daily(code, incoming)
    combined = _combine_fund_sources(em_rows, sina_rows, local)
    if not combined:
        snap = _snapshot_flow_bar(code)
        if snap:
            _persist_fund_daily(code, snap)
            combined = snap
            names = names or ["当日快照"]
    source = "+".join(names) if names else ("本地数据库" if local else "")
    if names and local:
        source = "+".join(names) + "+本地"
    return {
        "ok": bool(combined),
        "code": code,
        "stored_days": len(_load_fund_daily(code, 800)),
        "bars": len(combined),
        "source": source,
        "first": combined[0]["date"] if combined else "",
        "last": combined[-1]["date"] if combined else "",
        "error": "" if combined else "暂无主力资金流向。未用涨跌幅代替资金。",
        "note": "柱为该周期主力净流入，线为累计。单位亿元。未用涨跌幅代替资金。",
    }


def sync_watchlist_fund(lookback: int = 40, limit: int = 30) -> dict:
    """盘中增量 / 盘后回补：同步自选股主力资金日K到本地。"""
    from . import market as market_svc
    codes = []
    for w in market_svc.get_watchlist():
        c = (w.get("code") or "").strip().lower()
        if c and not _is_index(c) and c not in codes:
            codes.append(c)
        if len(codes) >= max(1, min(int(limit or 30), 50)):
            break
    synced, failed = 0, 0
    for c in codes:
        try:
            r = sync_fund_history(c, lookback)
            if r.get("ok"):
                synced += 1
            else:
                failed += 1
        except Exception:  # noqa: BLE001
            failed += 1
    return {"ok": True, "codes": len(codes), "synced": synced, "failed": failed, "lookback": lookback}


def _snapshot_flow_bar(code: str) -> list[dict]:
    rows = query("SELECT main_net_in, updated_at FROM stock_snapshot WHERE code=?", (code,))
    if not rows or rows[0]["main_net_in"] is None:
        return []
    yi = round((rows[0]["main_net_in"] or 0) / 10000.0, 4)
    day = str(rows[0]["updated_at"] or "")[:10]
    if len(day) < 10:
        day = datetime.now().strftime("%Y-%m-%d")
    return [{"date": day, "main_net_yi": yi, "super_net_yi": None, "large_net_yi": None, "small_net_yi": None}]


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
            "small_net_yi": 0.0, "_sc": 0, "_lc": 0, "_sm": 0,
        })
        b["main_net_yi"] = round((b["main_net_yi"] or 0) + float(r.get("main_net_yi") or 0), 4)
        if r.get("super_net_yi") is not None:
            b["super_net_yi"] = round((b["super_net_yi"] or 0) + float(r["super_net_yi"]), 4)
            b["_sc"] += 1
        if r.get("large_net_yi") is not None:
            b["large_net_yi"] = round((b["large_net_yi"] or 0) + float(r["large_net_yi"]), 4)
            b["_lc"] += 1
        if r.get("small_net_yi") is not None:
            b["small_net_yi"] = round((b["small_net_yi"] or 0) + float(r["small_net_yi"]), 4)
            b["_sm"] += 1
    out = []
    for key in sorted(buckets):
        b = buckets[key]
        if not b["_sc"]:
            b["super_net_yi"] = None
        if not b["_lc"]:
            b["large_net_yi"] = None
        if not b["_sm"]:
            b["small_net_yi"] = None
        b.pop("_sc", None)
        b.pop("_lc", None)
        b.pop("_sm", None)
        out.append(b)
    return out


def get_fund_kline(code: str, period: str = "day") -> dict:
    """个股主力资金图。缺数不编造、不用涨跌幅冒充资金。"""
    from . import market as market_svc
    code = market_svc.normalize_code(code) or (code or "").strip().lower()
    period = period if period in ("minute", "5day", "day", "week", "month") else "day"
    empty = {
        "code": code, "period": period, "dates": [], "main_net_yi": [],
        "cumulative_yi": [], "super_net_yi": [], "large_net_yi": [], "small_net_yi": [],
        "unit": "亿", "source": "", "offline": True, "cumulative_intraday": False,
        "empty_reason": "", "bars": 0, "stored_days": 0,
        "note": "柱为该周期主力净流入，线为累计。单位亿元。未用涨跌幅代替资金。",
    }
    if _is_index(code):
        empty["empty_reason"] = "指数没有个股主力资金流向，不编造。"
        return empty

    if period == "minute":
        def minute_loader():
            rows: list[dict] = []
            try:
                rows = eastmoney.fetch_stock_fflow(code, "minute")
            except Exception:  # noqa: BLE001
                rows = []
            return {"rows": rows or [], "source": "东方财富" if rows else "", "offline": not rows}

        data = cached(f"fundk:{code}:minute", TTL_FUND_KLINE, minute_loader)
        rows = data["rows"] or []
        if not rows:
            empty["empty_reason"] = "暂无主力资金流向。未用涨跌幅代替资金。"
            return empty
        nets = [float(r.get("main_net_yi") or 0) for r in rows]
        bars, cum, cum_flag = _interval_if_cumulative(nets)
        note = empty["note"]
        if cum_flag:
            note = "分时柱为较上一分钟增量，线为开盘累计主力净流入。单位亿元。"
        return {
            "code": code, "period": period,
            "dates": [r["date"] for r in rows],
            "main_net_yi": [round(x, 4) for x in bars],
            "cumulative_yi": cum,
            "super_net_yi": [r.get("super_net_yi") for r in rows],
            "large_net_yi": [r.get("large_net_yi") for r in rows],
            "small_net_yi": [r.get("small_net_yi") for r in rows],
            "unit": "亿", "source": data["source"], "offline": data["offline"],
            "cumulative_intraday": cum_flag, "empty_reason": "", "note": note,
            "bars": len(rows), "stored_days": 0,
        }

    def daily_loader():
        local = _load_fund_daily(code, 800)
        lookback = 240 if len(local) < 60 else 40
        em_rows, sina_rows, names = _fetch_remote_daily(code, lookback)
        incoming = _combine_fund_sources(em_rows, sina_rows, [])
        if incoming:
            _persist_fund_daily(code, incoming)
        rows = _combine_fund_sources(em_rows, sina_rows, local)
        source, offline = "+".join(names), not names
        if names and local:
            source = "+".join(names) + "+本地"
        if not rows:
            rows = _snapshot_flow_bar(code)
            if rows:
                source, offline = "当日快照", True
        stored = query("SELECT COUNT(*) AS n FROM stock_fund_daily WHERE code=?", (code,))
        return {
            "rows": rows, "source": source or ("本地数据库" if local else ""),
            "offline": offline, "stored_days": stored[0]["n"] if stored else len(rows),
        }

    data = cached(f"funddaily:{code}", TTL_FUND_KLINE, daily_loader)
    rows = list(data["rows"] or [])
    if period in ("week", "month") and rows:
        dates = [str(r.get("date") or "") for r in rows]
        daily_like = all(len(d) >= 10 and d[4:5] == "-" for d in dates if d)
        if daily_like:
            rows = _aggregate_flow(rows, period)
    if period == "5day" and len(rows) > 5:
        rows = rows[-5:]
    if not rows:
        empty["empty_reason"] = "暂无主力资金流向。未用涨跌幅代替资金。"
        empty["stored_days"] = data.get("stored_days") or 0
        return empty
    nets = [float(r.get("main_net_yi") or 0) for r in rows]
    bars, cum, cum_flag = nets, _running_sum(nets), False
    note = empty["note"]
    if len(rows) < 4:
        note = "历史资金K仍较少，已按多源写入本地待累积。未用涨跌幅补全。"
    return {
        "code": code, "period": period,
        "dates": [r["date"] for r in rows],
        "main_net_yi": [round(x, 4) for x in bars],
        "cumulative_yi": cum,
        "super_net_yi": [r.get("super_net_yi") for r in rows],
        "large_net_yi": [r.get("large_net_yi") for r in rows],
        "small_net_yi": [r.get("small_net_yi") for r in rows],
        "unit": "亿", "source": data["source"], "offline": data["offline"],
        "cumulative_intraday": cum_flag, "empty_reason": "", "note": note,
        "bars": len(rows), "stored_days": data.get("stored_days") or len(rows),
    }
