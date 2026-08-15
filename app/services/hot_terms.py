"""近两周热度词汇：热度值 / 上升 / 下降，词→热门板块→个股 TOP20。

热词来自本地已缓存快讯、官方政策标题，对照行业/概念/政策词典计数，
与前一个 14 天窗口对比得到升/降。不随机编造热度。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..database import execute, executemany, get_meta, query, set_meta
from . import macro
from . import sector as sector_svc

_TZ = ZoneInfo("Asia/Shanghai")
WINDOW_DAYS = 14
TOP_N = 48
MIN_COUNT = 2

_EXTRA_TERMS = (
    "降准", "降息", "加息", "LPR", "MLF", "专项债", "国债", "再贷款",
    "关税", "制裁", "补贴", "国产替代", "专精特新", "以旧换新",
    "人工智能", "算力", "大模型", "芯片", "半导体", "光刻", "先进封装",
    "新能源", "光伏", "储能", "锂电", "固态电池", "氢能", "核电",
    "机器人", "低空经济", "商业航天", "卫星互联网",
    "创新药", "集采", "医疗器械", "中药",
    "房地产", "化债", "城中村", "保障房",
    "券商", "银行", "保险", "白酒", "消费",
    "军工", "国防", "有色", "稀土", "黄金", "原油", "煤炭",
    "两会", "政治局", "中央经济工作会议", "证监会", "央行",
    "数据中心", "液冷", "CPO", "光模块", "华为链",
)


def _now() -> datetime:
    return datetime.now(_TZ).replace(tzinfo=None)


def _lexicon() -> list[str]:
    terms: set[str] = set(_EXTRA_TERMS)
    for name, kws in macro._SECTOR_KEYWORDS.items():  # noqa: SLF001
        terms.add(name)
        terms.update(kws)
    try:
        for r in query("SELECT DISTINCT industry FROM stock_list WHERE industry != ''"):
            n = (r.get("industry") or "").strip()
            if 2 <= len(n) <= 12:
                terms.add(n)
        for r in query("SELECT concept FROM concept_board"):
            n = (r.get("concept") or "").strip()
            if 2 <= len(n) <= 12:
                terms.add(n)
    except Exception:  # noqa: BLE001
        pass
    # 过短单字噪声剔除；长词优先匹配时仍独立计数
    return [t for t in terms if len(t) >= 2]


def _load_docs(start: str, end: str) -> list[dict]:
    docs = []
    try:
        rows = query(
            "SELECT title, summary, event_time, affected_sectors, impact_level FROM intel_cache "
            "WHERE event_time>=? AND event_time<? ORDER BY event_time DESC LIMIT 2500",
            (start, end))
        docs.extend(rows)
    except Exception:  # noqa: BLE001
        pass
    try:
        rows = query(
            "SELECT title, summary, event_time, affected_sectors, impact_level FROM official_policy "
            "WHERE event_time>=? AND event_time<? ORDER BY event_time DESC LIMIT 1500",
            (start, end))
        docs.extend(rows)
    except Exception:  # noqa: BLE001
        pass
    return docs


def _count_terms(docs: list[dict], terms: list[str]) -> tuple[dict[str, dict], dict[str, int]]:
    """term -> {count, impact_sum, samples[], sectors set}"""
    stats: dict[str, dict] = {}
    for doc in docs:
        text = f"{doc.get('title') or ''} {doc.get('summary') or ''}"
        if not text.strip():
            continue
        try:
            secs = json.loads(doc.get("affected_sectors") or "[]")
        except Exception:  # noqa: BLE001
            secs = []
        impact = int(doc.get("impact_level") or 1)
        title = (doc.get("title") or "")[:80]
        hit = [t for t in terms if t in text]
        if not hit:
            continue
        for t in hit:
            rec = stats.setdefault(t, {"count": 0, "impact": 0, "samples": [], "sectors": set()})
            rec["count"] += 1
            rec["impact"] += impact
            if title and title not in rec["samples"] and len(rec["samples"]) < 4:
                rec["samples"].append(title)
            for s in secs:
                if s:
                    rec["sectors"].add(s)
            mapped = [k for k, kws in macro._SECTOR_KEYWORDS.items()  # noqa: SLF001
                      if t == k or t in kws]
            rec["sectors"].update(mapped)
    counts = {k: v["count"] for k, v in stats.items()}
    return stats, counts


def _is_board(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    if n in macro._SECTOR_KEYWORDS:  # noqa: SLF001
        return True
    try:
        if query("SELECT 1 FROM stock_list WHERE industry=? LIMIT 1", (n,)):
            return True
        if query("SELECT 1 FROM concept_map WHERE concept=? LIMIT 1", (n,)):
            return True
        if query("SELECT 1 FROM concept_board WHERE concept=? LIMIT 1", (n,)):
            return True
    except Exception:  # noqa: BLE001
        return n in macro._SECTOR_KEYWORDS  # noqa: SLF001
    return False


def _related_sectors(term: str, tagged: set[str]) -> list[str]:
    names = set(tagged)
    if _is_board(term):
        names.add(term)
    try:
        for r in query(
                "SELECT DISTINCT industry AS n FROM stock_list WHERE industry LIKE ? LIMIT 12",
                (f"%{term}%",)):
            if r.get("n"):
                names.add(r["n"])
        for r in query(
                "SELECT DISTINCT concept AS n FROM concept_map WHERE concept LIKE ? LIMIT 12",
                (f"%{term}%",)):
            if r.get("n"):
                names.add(r["n"])
        for r in query(
                "SELECT DISTINCT concept AS n FROM concept_board WHERE concept LIKE ? LIMIT 12",
                (f"%{term}%",)):
            if r.get("n"):
                names.add(r["n"])
    except Exception:  # noqa: BLE001
        pass
    out = []
    seen = set()
    for n in names:
        n = (n or "").strip()
        if not n or n in seen or n in ("政策", "未映射影响板块") or not _is_board(n):
            continue
        seen.add(n)
        out.append(n)
    return out[:12]


def rebuild_hot_terms() -> dict:
    terms = _lexicon()
    today = _now()
    now_start = (today - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d 00:00:00")
    now_end = today.strftime("%Y-%m-%d %H:%M:%S")
    prev_start = (today - timedelta(days=WINDOW_DAYS * 2)).strftime("%Y-%m-%d 00:00:00")
    docs_now = _load_docs(now_start, now_end)
    docs_prev = _load_docs(prev_start, now_start)
    st_now, c_now = _count_terms(docs_now, terms)
    _, c_prev = _count_terms(docs_prev, terms)
    window_end = today.strftime("%Y-%m-%d")
    rows = []
    for term, rec in st_now.items():
        cnt = rec["count"]
        if cnt < MIN_COUNT:
            continue
        prev = c_prev.get(term, 0)
        heat = round(cnt * 10 + rec["impact"] * 2, 1)
        heat_prev = round(prev * 10, 1)
        rise = round(max(0.0, heat - heat_prev), 1)
        fall = round(max(0.0, heat_prev - heat), 1)
        sectors = _related_sectors(term, rec["sectors"])
        rows.append((
            term, window_end, heat, heat_prev, rise, fall, cnt, prev,
            json.dumps(sectors, ensure_ascii=False),
            json.dumps(rec["samples"], ensure_ascii=False),
            today.isoformat(timespec="seconds"),
        ))
    rows.sort(key=lambda r: -r[2])
    rows = rows[:TOP_N]
    execute("DELETE FROM hot_term WHERE window_end=?", (window_end,))
    if rows:
        executemany(
            """INSERT OR REPLACE INTO hot_term(
                 term,window_end,heat,heat_prev,rise,fall,count_now,count_prev,
                 sectors,sample_titles,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""", rows)
    set_meta("hot_terms_last_sync", today.isoformat(timespec="seconds"))
    return {
        "ok": True, "count": len(rows),
        "docs_now": len(docs_now), "docs_prev": len(docs_prev),
        "window_days": WINDOW_DAYS, "window_end": window_end,
        "last_sync": get_meta("hot_terms_last_sync", "从未"),
        "note": "热词由近两周本地情报统计，对比前两周得到升/降；情报不足时列表为空。",
    }


def list_hot_terms(kind: str = "") -> dict:
    today = _now().strftime("%Y-%m-%d")
    rows = query(
        "SELECT * FROM hot_term WHERE window_end=? ORDER BY heat DESC LIMIT ?",
        (today, TOP_N))
    if not rows:
        # 取最近一次快照
        latest = query("SELECT MAX(window_end) AS d FROM hot_term")
        day = (latest[0]["d"] if latest else None) or today
        rows = query(
            "SELECT * FROM hot_term WHERE window_end=? ORDER BY heat DESC LIMIT ?",
            (day, TOP_N))
    items = []
    for r in rows:
        try:
            sectors = json.loads(r.get("sectors") or "[]")
        except Exception:  # noqa: BLE001
            sectors = []
        try:
            samples = json.loads(r.get("sample_titles") or "[]")
        except Exception:  # noqa: BLE001
            samples = []
        trend = "上升" if (r["rise"] or 0) > (r["fall"] or 0) and (r["rise"] or 0) > 0 else (
            "下降" if (r["fall"] or 0) > 0 else "平稳")
        if kind == "rise" and trend != "上升":
            continue
        if kind == "fall" and trend != "下降":
            continue
        items.append({
            "term": r["term"], "heat": r["heat"], "heat_prev": r["heat_prev"],
            "rise": r["rise"], "fall": r["fall"],
            "count_now": r["count_now"], "count_prev": r["count_prev"],
            "trend": trend, "sectors": sectors, "samples": samples,
            "window_end": r["window_end"],
        })
    empty_reason = ""
    if not items:
        empty_reason = (
            "近两周本地情报不足以统计热词。请先在设置页缓存宏观情报，"
            "或等待每小时热词任务；不会随机生成热度。"
        )
    return {
        "items": items, "count": len(items),
        "window_days": WINDOW_DAYS,
        "last_sync": get_meta("hot_terms_last_sync", "从未"),
        "update": "每小时根据近14天本地快讯/官方政策重算，对比前14天。",
        "empty_reason": empty_reason,
    }


def _sector_heat_map() -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        rows = query(
            """SELECT l.industry AS name, ROUND(AVG(s.pct),2) AS pct,
                      ROUND(AVG(s.pct_d5),2) AS d5,
                      ROUND(SUM(s.main_net_in)/10000,1) AS net_in_yi,
                      COUNT(*) AS n
               FROM stock_snapshot s JOIN stock_list l ON l.code=s.code
               WHERE l.industry != '' AND s.pct IS NOT NULL
               GROUP BY l.industry""")
        for r in rows:
            name = r.get("name")
            if not name:
                continue
            hot = round((r["pct"] or 0) * 3 + (r["d5"] or 0) * 1.5
                        + min(max((r["net_in_yi"] or 0), -20), 20), 1)
            out[name] = {**r, "hot_score": hot}
    except Exception:  # noqa: BLE001
        pass
    if not out:
        try:
            flow = sector_svc.get_flow("industry") or {}
            for it in (flow.get("items") or [])[:80]:
                name = it.get("name")
                if name:
                    out[name] = {
                        "name": name, "hot_score": it.get("pct"),
                        "pct": it.get("pct"), "net_in_yi": it.get("net_in_yi"),
                        "n": it.get("stocks"),
                    }
        except Exception:  # noqa: BLE001
            pass
    return out


def hot_term_sectors(term: str) -> dict:
    term = (term or "").strip()
    if not term:
        return {"term": "", "sectors": [], "empty_reason": "未指定热词"}
    today = _now().strftime("%Y-%m-%d")
    rows = query("SELECT * FROM hot_term WHERE term=? AND window_end=? LIMIT 1", (term, today))
    if not rows:
        rows = query("SELECT * FROM hot_term WHERE term=? ORDER BY window_end DESC LIMIT 1", (term,))
    tagged: set[str] = set()
    samples = []
    if rows:
        try:
            tagged = set(json.loads(rows[0].get("sectors") or "[]"))
        except Exception:  # noqa: BLE001
            tagged = set()
        try:
            samples = json.loads(rows[0].get("sample_titles") or "[]")
        except Exception:  # noqa: BLE001
            samples = []
    names = _related_sectors(term, tagged)
    heat_map = _sector_heat_map()
    sectors = []
    for name in names:
        info = heat_map.get(name) or {"name": name, "hot_score": None, "pct": None}
        info = dict(info)
        info["name"] = name
        sectors.append(info)
    sectors.sort(key=lambda x: -(x.get("hot_score") or x.get("pct") or 0))
    empty_reason = "" if sectors else f"「{term}」暂未映射到可交易板块（本地行业/概念无匹配）。"
    return {
        "term": term, "sectors": sectors[:20], "samples": samples,
        "empty_reason": empty_reason,
        "note": "点击板块查看相关个股 TOP20（按主力净流入，非编造名单）。",
    }


def hot_sector_stocks(sector: str, limit: int = 20) -> dict:
    sector = (sector or "").strip()
    limit = max(20, min(int(limit or 20), 50))
    if not sector:
        return {"sector": "", "stocks": [], "empty_reason": "未指定板块"}
    stocks = macro._stocks_for_sectors([sector], limit=limit)  # noqa: SLF001
    empty_reason = "" if stocks else f"板块「{sector}」在本地快照中没有匹配个股。"
    return {
        "sector": sector, "stocks": stocks[:limit], "count": len(stocks),
        "empty_reason": empty_reason,
        "disclaimer": "个股来自本地行业/概念映射，按主力净流入排序，仅供参考。",
    }
