"""行情服务：实时报价（多源故障切换）、大盘看板、自选股。"""
from datetime import datetime

from ..cache import cache, cached
from ..config import DEFAULT_WATCHLIST, TTL_INDEX, TTL_REALTIME
from ..database import execute, query
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
    execute("DELETE FROM watchlist WHERE code=?", (code,))
    return {"ok": True}


def toggle_pin(code: str) -> dict:
    execute("UPDATE watchlist SET pinned = 1 - pinned WHERE code=?", (code,))
    return {"ok": True}
