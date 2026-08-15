"""行情服务：实时报价（多源故障切换）、大盘看板、自选股。"""
from datetime import datetime

from ..cache import cache, cached
from ..config import DEFAULT_WATCHLIST, TTL_INDEX, TTL_REALTIME
from ..database import execute, executemany, query
from ..datasources import offline, sina, tencent
from ..datasources.base import with_failover

CN_INDICES = [
    ("sh000001", "上证指数"), ("sz399001", "深证成指"),
    ("sz399006", "创业板指"), ("sh000688", "科创50"),
    ("sh000300", "沪深300"), ("sh000905", "中证500"),
    ("sh000852", "中证1000"), ("bj899050", "北证50"),
]


def normalize_code(raw: str) -> str | None:
    """把用户输入统一为带前缀代码：300432 → sz300432。"""
    s = raw.strip().lower()
    if not s:
        return None
    if s.startswith(("sh", "sz", "bj")) and len(s) == 8:
        return s
    if s.isdigit() and len(s) == 6:
        if s.startswith(("60", "68", "5")):
            return f"sh{s}"
        if s.startswith(("00", "30", "1", "2")):
            return f"sz{s}"
        if s.startswith(("8", "4", "92")):
            return f"bj{s}"
        if s.startswith("000"):
            return f"sh{s}"
    return None


def get_quotes(codes: list[str]) -> dict[str, dict]:
    """批量实时行情：腾讯 → 新浪 → 离线兜底。"""
    if not codes:
        return {}
    key = "quotes:" + ",".join(sorted(codes))

    def loader():
        return with_failover([
            ("腾讯财经", lambda: tencent.fetch_quotes(codes)),
            ("新浪财经", lambda: sina.fetch_quotes(codes)),
            ("离线兜底", lambda: offline.fetch_quotes(codes)),
        ], context="realtime quotes")

    return cached(key, TTL_REALTIME, loader) or {}


def get_indices_overview() -> list[dict]:
    codes = [c for c, _ in CN_INDICES]
    quotes = cached("cn_indices", TTL_INDEX, lambda: get_quotes(codes)) or {}
    out = []
    for code, name in CN_INDICES:
        q = quotes.get(code)
        if q:
            q = dict(q)
            q["name"] = name
            out.append(q)
    return out


def market_stats() -> dict:
    """涨跌家数等统计，来自本地快照表。"""
    rows = query(
        """SELECT
             SUM(CASE WHEN pct > 0 THEN 1 ELSE 0 END) AS up,
             SUM(CASE WHEN pct < 0 THEN 1 ELSE 0 END) AS down,
             SUM(CASE WHEN pct = 0 THEN 1 ELSE 0 END) AS flat,
             SUM(CASE WHEN pct >= 9.8 THEN 1 ELSE 0 END) AS limit_up,
             SUM(CASE WHEN pct <= -9.8 THEN 1 ELSE 0 END) AS limit_down,
             SUM(amount) AS amount, MAX(updated_at) AS updated_at
           FROM stock_snapshot WHERE pct IS NOT NULL"""
    )
    r = rows[0] if rows else {}
    return {
        "up": r.get("up") or 0, "down": r.get("down") or 0, "flat": r.get("flat") or 0,
        "limit_up": r.get("limit_up") or 0, "limit_down": r.get("limit_down") or 0,
        "amount_yi": round((r.get("amount") or 0) / 10000, 1),  # 万元 → 亿元
        "updated_at": r.get("updated_at"),
    }


def top_movers(limit: int = 5) -> dict:
    gainers = query(
        "SELECT code,name,price,pct,main_net_in FROM stock_snapshot "
        "WHERE pct IS NOT NULL ORDER BY pct DESC LIMIT ?", (limit,))
    losers = query(
        "SELECT code,name,price,pct,main_net_in FROM stock_snapshot "
        "WHERE pct IS NOT NULL ORDER BY pct ASC LIMIT ?", (limit,))
    return {"gainers": gainers, "losers": losers}


# ---------------- 自选股 ----------------

def ensure_default_watchlist() -> None:
    if query("SELECT COUNT(*) AS n FROM watchlist")[0]["n"] == 0:
        now = datetime.now().isoformat(timespec="seconds")
        for code, name in DEFAULT_WATCHLIST:
            execute("INSERT OR IGNORE INTO watchlist(code,name,pinned,created_at) VALUES(?,?,0,?)",
                    (code, name, now))


def get_watchlist() -> list[dict]:
    items = query(
        "SELECT w.code, w.name, w.pinned, l.industry "
        "FROM watchlist w LEFT JOIN stock_list l ON l.code = w.code "
        "ORDER BY w.pinned DESC, w.created_at")
    quotes = get_quotes([i["code"] for i in items])
    codes = [i["code"] for i in items]
    snaps = {}
    if codes:
        ph = ",".join("?" * len(codes))
        for r in query(
            f"SELECT code, main_in, main_out, main_net_in, amount, volume_ratio, turnover_rate "
            f"FROM stock_snapshot WHERE code IN ({ph})",
            tuple(codes),
        ):
            snaps[r["code"]] = r
    out = []
    for item in items:
        q = quotes.get(item["code"], {})
        snap = snaps.get(item["code"], {})
        row = {**item, **{k: q.get(k) for k in (
            "price", "pct", "change", "volume", "amount", "volume_ratio",
            "turnover_rate", "amplitude", "time", "source")}}
        row["main_in"] = snap.get("main_in")
        row["main_out"] = snap.get("main_out")
        row["main_net_in"] = snap.get("main_net_in")
        if row.get("amount") is None:
            row["amount"] = snap.get("amount")
        if row.get("volume_ratio") is None:
            row["volume_ratio"] = snap.get("volume_ratio")
        if row.get("turnover_rate") is None:
            row["turnover_rate"] = snap.get("turnover_rate")
        out.append(row)
    from . import finance as finance_svc
    from . import metrics as metrics_svc
    from . import wuxing
    wuxing.tags_for_list(out)
    finance_svc.attach_grades(out)
    metrics_svc.attach_flow_list(out)
    return out


def add_watch(code: str) -> dict:
    norm = normalize_code(code)
    if not norm:
        return {"ok": False, "error": f"无效代码: {code}"}
    name = ""
    quotes = get_quotes([norm])
    q = quotes.get(norm) or {}
    if q.get("name"):
        name = q["name"]
    if not name:
        rows = query("SELECT name FROM stock_list WHERE code=?", (norm,))
        name = (rows[0]["name"] if rows else "") or ""
    if not name:
        return {"ok": False, "error": f"本地没有 {norm}，请先全量同步或检查代码"}
    execute("INSERT OR IGNORE INTO watchlist(code,name,pinned,created_at) VALUES(?,?,0,?)",
            (norm, name, datetime.now().isoformat(timespec="seconds")))
    cache.delete("watchlist")
    return {"ok": True, "code": norm, "name": name}


def remove_watch(code: str) -> dict:
    norm = normalize_code(code) or (code or "").strip().lower()
    if not norm:
        return {"ok": False, "error": "无效代码"}
    execute("DELETE FROM watchlist WHERE code=?", (norm,))
    cache.delete("watchlist")
    return {"ok": True, "code": norm}


def toggle_pin(code: str) -> dict:
    execute("UPDATE watchlist SET pinned = 1 - pinned WHERE code=?", (code,))
    return {"ok": True}


def record_market_volume(asof: str = "") -> dict:
    """落库大A每日量能：成交额优先中证全指；成交量来自指数日K。不编造历史成交额。"""
    from . import kline as kline_svc
    from ..datasources import eastmoney

    day = (asof or "").strip()
    if len(day) < 10:
        rows = query("SELECT MAX(updated_at) AS t FROM stock_snapshot")
        raw = ((rows[0]["t"] if rows else "") or "").strip()
        day = raw[:10] if len(raw) >= 10 else datetime.now().strftime("%Y-%m-%d")

    amounts = {}
    try:
        amounts = eastmoney.fetch_market_amounts() or {}
    except Exception:  # noqa: BLE001
        amounts = {}
    if not amounts.get("amount_yi"):
        quotes = get_quotes(["sh000001", "sz399001", "bj899050", "sh000985"])

        def yi(code: str) -> float | None:
            wan = (quotes.get(code) or {}).get("amount")
            return round(wan / 10000.0, 1) if wan else None

        sh, sz, bj, csi = yi("sh000001"), yi("sz399001"), yi("bj899050"), yi("sh000985")
        amounts = {
            "amount_yi": csi if csi else round((sh or 0) + (sz or 0) + (bj or 0), 1),
            "sh_amount_yi": sh, "sz_amount_yi": sz, "bj_amount_yi": bj,
            "csi_amount_yi": csi, "source": "腾讯财经",
        }

    k_csi = kline_svc.get_kline("sh000985", "day", 80)
    k_sh = kline_svc.get_kline("sh000001", "day", 80)
    sh_vol = {d: v for d, v in zip(k_sh.get("dates") or [], k_sh.get("volumes") or [])}
    dates = k_csi.get("dates") or k_sh.get("dates") or []
    vols = k_csi.get("volumes") or k_sh.get("volumes") or []
    closes = [row[1] for row in (k_csi.get("kline") or k_sh.get("kline") or [])]
    payload = []
    for i, d in enumerate(dates):
        close = closes[i] if i < len(closes) else None
        prev = closes[i - 1] if i > 0 and i - 1 < len(closes) else None
        pct = round((close / prev - 1) * 100, 2) if close and prev else None
        vol = vols[i] if i < len(vols) else None
        is_today = d == day
        payload.append((
            d,
            amounts.get("amount_yi") if is_today else None,
            amounts.get("sh_amount_yi") if is_today else None,
            amounts.get("sz_amount_yi") if is_today else None,
            amounts.get("bj_amount_yi") if is_today else None,
            vol, sh_vol.get(d), close, pct,
            amounts.get("source") if is_today else "kline",
        ))
    if payload:
        executemany(
            """INSERT INTO market_volume_daily(
                   trade_date, amount_yi, sh_amount_yi, sz_amount_yi, bj_amount_yi,
                   volume, sh_volume, close, pct, source)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(trade_date) DO UPDATE SET
                 amount_yi=COALESCE(excluded.amount_yi, market_volume_daily.amount_yi),
                 sh_amount_yi=COALESCE(excluded.sh_amount_yi, market_volume_daily.sh_amount_yi),
                 sz_amount_yi=COALESCE(excluded.sz_amount_yi, market_volume_daily.sz_amount_yi),
                 bj_amount_yi=COALESCE(excluded.bj_amount_yi, market_volume_daily.bj_amount_yi),
                 volume=COALESCE(excluded.volume, market_volume_daily.volume),
                 sh_volume=COALESCE(excluded.sh_volume, market_volume_daily.sh_volume),
                 close=COALESCE(excluded.close, market_volume_daily.close),
                 pct=COALESCE(excluded.pct, market_volume_daily.pct),
                 source=COALESCE(excluded.source, market_volume_daily.source)""",
            payload)
    latest = query("SELECT * FROM market_volume_daily WHERE trade_date=? ", (day,))
    return {"date": day, "days": len(payload), **(latest[0] if latest else amounts)}
