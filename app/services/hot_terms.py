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

# 热词 → (利好板块, 利空板块)。只写常见政策/产业映射，不编造个股。
_TERM_IMPACT: list[tuple[tuple[str, ...], list[str], list[str]]] = [
    (("降准", "降息", "LPR", "MLF", "再贷款", "专项债"),
     ["银行", "券商", "金融", "房地产", "地产"], []),
    (("加息",),
     ["银行"], ["半导体", "科技", "新能源", "消费"]),
    (("关税", "制裁"),
     ["军工"], ["汽车", "消费", "科技"]),
    (("补贴", "以旧换新"),
     ["汽车", "消费", "新能源"], []),
    (("集采",),
     [], ["医药"]),
    (("国产替代",),
     ["半导体", "科技", "军工"], []),
    (("人工智能", "算力", "大模型", "AI", "数据中心", "液冷", "CPO", "光模块"),
     ["科技", "半导体"], []),
    (("芯片", "半导体", "光刻", "先进封装"),
     ["半导体", "科技"], []),
    (("新能源", "光伏", "储能", "锂电", "固态电池", "氢能", "核电", "风电"),
     ["新能源", "能源"], []),
    (("原油", "石油"),
     ["能源"], ["汽车"]),
    (("黄金", "稀土", "有色"),
     ["有色"], []),
    (("房地产", "化债", "城中村", "保障房", "楼市"),
     ["地产", "房地产", "银行"], []),
    (("两会", "政治局", "中央经济工作会议"),
     ["消费", "券商", "科技", "新能源"], []),
    (("军工", "国防", "航天"),
     ["军工"], []),
    (("创新药", "医药", "医疗器械", "中药"),
     ["医药"], []),
    (("机器人", "低空经济", "商业航天"),
     ["军工", "科技"], []),
]


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
            "SELECT title, summary, event_time, affected_sectors, impact_level, impact_direction "
            "FROM intel_cache "
            "WHERE event_time>=? AND event_time<? ORDER BY event_time DESC LIMIT 2500",
            (start, end))
        docs.extend(rows)
    except Exception:  # noqa: BLE001
        pass
    try:
        rows = query(
            "SELECT title, summary, event_time, affected_sectors, impact_level, impact_direction "
            "FROM official_policy "
            "WHERE event_time>=? AND event_time<? ORDER BY event_time DESC LIMIT 1500",
            (start, end))
        docs.extend(rows)
    except Exception:  # noqa: BLE001
        pass
    return docs


def _count_terms(docs: list[dict], terms: list[str]) -> tuple[dict[str, dict], dict[str, int]]:
    """term -> {count, impact_sum, samples[], sectors set, sector_votes}"""
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
        direction = doc.get("impact_direction") or ""
        if direction not in ("利好", "利空", "中性"):
            direction = (macro.assess_impact(text).get("impact_direction") or "中性")
        title = (doc.get("title") or "")[:80]
        hit = [t for t in terms if t in text]
        if not hit:
            continue
        for t in hit:
            rec = stats.setdefault(t, {
                "count": 0, "impact": 0, "samples": [], "sectors": set(),
                "sector_votes": {},
            })
            rec["count"] += 1
            rec["impact"] += impact
            if title and title not in rec["samples"] and len(rec["samples"]) < 4:
                rec["samples"].append(title)
            tagged = set()
            for s in secs:
                if s:
                    tagged.add(s)
            mapped = [k for k, kws in macro._SECTOR_KEYWORDS.items()  # noqa: SLF001
                      if t == k or t in kws]
            tagged.update(mapped)
            rec["sectors"].update(tagged)
            votes = rec["sector_votes"]
            for s in tagged:
                bucket = votes.setdefault(s, {"利好": 0, "利空": 0, "中性": 0})
                bucket[direction if direction in bucket else "中性"] += 1
    counts = {k: v["count"] for k, v in stats.items()}
    return stats, counts


def _lexicon_impact(term: str) -> tuple[list[str], list[str]]:
    """规则词库：该热词通常利好/利空哪些板块。"""
    bull: list[str] = []
    bear: list[str] = []
    for keywords, b1, b2, _prob in macro._EVENT_IMPACT_MAP:  # noqa: SLF001
        if any(k == term or (len(k) >= 2 and k in term) for k in keywords):
            bull = [s for s in b1 if s and s != "无明显利空"]
            bear = [s for s in b2 if s and s != "无明显利空"]
            break
    for keywords, b1, b2 in _TERM_IMPACT:
        if any(k == term or (len(k) >= 2 and k in term) for k in keywords):
            for s in b1:
                if s not in bull:
                    bull.append(s)
            for s in b2:
                if s not in bear:
                    bear.append(s)
            break
    return bull, bear


def _direction_of(name: str, lexicon_bull: list[str], lexicon_bear: list[str],
                  votes: dict) -> tuple[str, str, int, int]:
    v = votes.get(name) or {}
    bull_n = int(v.get("利好") or 0)
    bear_n = int(v.get("利空") or 0)
    in_bull = name in lexicon_bull
    in_bear = name in lexicon_bear
    if in_bull and not in_bear:
        return "利好", "词库映射", bull_n, bear_n
    if in_bear and not in_bull:
        return "利空", "词库映射", bull_n, bear_n
    if bull_n > bear_n:
        return "利好", f"近两周情报利好 {bull_n} / 利空 {bear_n}", bull_n, bear_n
    if bear_n > bull_n:
        return "利空", f"近两周情报利好 {bull_n} / 利空 {bear_n}", bull_n, bear_n
    return "中性", "仅共现，方向不明", bull_n, bear_n


def _impact_summary(bull: list[str], bear: list[str]) -> str:
    btxt = "、".join(bull[:6]) if bull else "暂无明确利好板块"
    wtxt = "、".join(bear[:6]) if bear else "暂无明确利空板块"
    return f"利好 {btxt}；利空 {wtxt}"


def _parse_sector_payload(raw) -> list[dict]:
    try:
        data = json.loads(raw or "[]")
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in data or []:
        if isinstance(it, str):
            if it.strip():
                out.append({"name": it.strip(), "direction": "中性", "why": "仅共现"})
        elif isinstance(it, dict) and (it.get("name") or "").strip():
            out.append({
                "name": it["name"].strip(),
                "direction": it.get("direction") or "中性",
                "why": it.get("why") or "",
                "bull_n": it.get("bull_n") or 0,
                "bear_n": it.get("bear_n") or 0,
            })
    return out


def _is_board(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    if n in macro._SECTOR_KEYWORDS or n in getattr(macro, "_SECTOR_SPEC", {}):  # noqa: SLF001
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


def _pack_sector_impacts(term: str, tagged: set[str], votes: dict | None = None) -> list[dict]:
    lexicon_bull, lexicon_bear = _lexicon_impact(term)
    votes = votes or {}
    items = []
    seen = set()

    def add(name: str, direction: str, why: str, bull_n: int = 0, bear_n: int = 0):
        name = (name or "").strip()
        if not name or name in seen or name in ("政策", "未映射影响板块", "无明显利空"):
            return
        if not _is_board(name):
            return
        seen.add(name)
        v = votes.get(name) or {}
        items.append({
            "name": name, "direction": direction, "why": why,
            "bull_n": bull_n or int(v.get("利好") or 0),
            "bear_n": bear_n or int(v.get("利空") or 0),
        })

    for name in lexicon_bull:
        add(name, "利好", "词库映射：该热词通常利好此板块")
    for name in lexicon_bear:
        add(name, "利空", "词库映射：该热词通常利空此板块")
    extras = set(tagged or ())
    extras.update(_related_sectors(term, tagged or set()))
    has_lexicon = bool(lexicon_bull or lexicon_bear)
    for name in extras:
        if name in seen:
            continue
        direction, why, bull_n, bear_n = _direction_of(name, [], [], votes)
        if has_lexicon:
            add(name, "中性", "与热词共现，未纳入利好/利空映射", bull_n, bear_n)
        else:
            add(name, direction, why, bull_n, bear_n)
    order = {"利好": 0, "利空": 1, "中性": 2}
    items.sort(key=lambda x: (order.get(x["direction"], 9), x["name"]))
    return items[:24]


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
        packed = _pack_sector_impacts(term, rec["sectors"], rec.get("sector_votes") or {})
        rows.append((
            term, window_end, heat, heat_prev, rise, fall, cnt, prev,
            json.dumps(packed, ensure_ascii=False),
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
            samples = json.loads(r.get("sample_titles") or "[]")
        except Exception:  # noqa: BLE001
            samples = []
        trend = "上升" if (r["rise"] or 0) > (r["fall"] or 0) and (r["rise"] or 0) > 0 else (
            "下降" if (r["fall"] or 0) > 0 else "平稳")
        if kind == "rise" and trend != "上升":
            continue
        if kind == "fall" and trend != "下降":
            continue
        stored = _parse_sector_payload(r.get("sectors"))
        tagged = {it["name"] for it in stored if it.get("name")}
        votes = {
            it["name"]: {"利好": int(it.get("bull_n") or 0), "利空": int(it.get("bear_n") or 0)}
            for it in stored if it.get("name")
        }
        packed = _pack_sector_impacts(r["term"], tagged, votes)
        packed, meta = _with_ai_overlay(r["term"], packed)
        bull = [x["name"] for x in packed if x.get("direction") == "利好"]
        bear = [x["name"] for x in packed if x.get("direction") == "利空"]
        items.append({
            "term": r["term"], "heat": r["heat"], "heat_prev": r["heat_prev"],
            "rise": r["rise"], "fall": r["fall"],
            "count_now": r["count_now"], "count_prev": r["count_prev"],
            "trend": trend, "sectors": [x["name"] for x in packed],
            "sector_impacts": packed,
            "bull_sectors": bull, "bear_sectors": bear,
            "impact_summary": _impact_summary(bull, bear),
            "samples": samples,
            "window_end": r["window_end"],
            **meta,
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
        "update": "可点「手动更新全部热词」立即重算；「一键AI回填」按当前列表写入利好/利空。小时任务仍会重算热度，不冲掉已回填。",
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


def _ai_overlay(term: str) -> dict | None:
    try:
        rows = query("SELECT payload, updated_at FROM hot_term_ai WHERE term=?", (term,))
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    try:
        data = json.loads(rows[0].get("payload") or "{}")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    data["updated_at"] = rows[0].get("updated_at") or data.get("updated_at") or ""
    return data


def _board_vocab() -> list[str]:
    names = set(macro._SECTOR_KEYWORDS) | set(getattr(macro, "_SECTOR_SPEC", {}))  # noqa: SLF001
    for kw, b1, b2 in _TERM_IMPACT:
        names.update(b1)
        names.update(b2)
    return sorted(n for n in names if n and n not in ("无明显利空", "未映射影响板块"))


def _resolve_ai_name(name: str) -> str | None:
    n = (name or "").strip().replace("板块", "")
    if not n or n in ("无明显利空", "未映射影响板块", "A股", "大盘"):
        return None
    vocab = set(_board_vocab())
    aliases = {"房产": "地产", "房地产": "地产", "券商股": "金融", "银行股": "金融",
               "黄金股": "有色", "芯片": "半导体", "AI": "科技", "人工智能": "科技"}
    if n not in vocab:
        n = aliases.get(n, n)
    if n in vocab:
        return n
    if _is_board(n):
        return n
    for key in vocab:
        if n != key and (n in key or key in n):
            return key
    try:
        rows = query("SELECT DISTINCT industry AS n FROM stock_list WHERE industry=? LIMIT 1", (n,))
        if rows and rows[0].get("n"):
            return rows[0]["n"]
        rows = query("SELECT DISTINCT concept AS n FROM concept_map WHERE concept=? LIMIT 1", (n,))
        if rows and rows[0].get("n"):
            return rows[0]["n"]
        rows = query("SELECT DISTINCT concept AS n FROM concept_board WHERE concept=? LIMIT 1", (n,))
        if rows and rows[0].get("n"):
            return rows[0]["n"]
    except Exception:  # noqa: BLE001
        pass
    return None


def _norm_ai_side(items) -> list[dict]:
    out, seen = [], set()
    if not isinstance(items, list):
        return out
    for it in items:
        if isinstance(it, str):
            name, why = it, "AI回填"
        elif isinstance(it, dict):
            name, why = it.get("name") or it.get("sector") or "", it.get("why") or it.get("reason") or "AI回填"
        else:
            continue
        resolved = _resolve_ai_name(str(name))
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        out.append({"name": resolved, "why": str(why)[:80]})
    return out[:12]


def _apply_ai_overlay(term: str, packed: list[dict]) -> list[dict]:
    ov = _ai_overlay(term)
    if not ov:
        return packed
    bull = _norm_ai_side(ov.get("bull") or ov.get("bull_sectors") or [])
    bear = _norm_ai_side(ov.get("bear") or ov.get("bear_sectors") or [])
    if not bull and not bear:
        return packed
    heat = {p["name"]: p for p in packed}
    seen = set()
    out = []
    for it in bull:
        prev = heat.get(it["name"]) or {}
        out.append({**prev, "name": it["name"], "direction": "利好",
                    "why": it["why"] or "AI回填", "ai": True})
        seen.add(it["name"])
    for it in bear:
        if it["name"] in seen:
            continue
        prev = heat.get(it["name"]) or {}
        out.append({**prev, "name": it["name"], "direction": "利空",
                    "why": it["why"] or "AI回填", "ai": True})
        seen.add(it["name"])
    for p in packed:
        if p["name"] in seen:
            continue
        q = dict(p)
        q["direction"] = "中性"
        q["why"] = "词库/共现，AI未纳入利好利空"
        q["ai"] = False
        out.append(q)
        seen.add(p["name"])
    return out


def _with_ai_overlay(term: str, packed: list[dict]) -> tuple[list[dict], dict]:
    packed = _apply_ai_overlay(term, packed)
    ov = _ai_overlay(term)
    applied = bool(ov) and any(x.get("ai") for x in packed)
    return packed, {
        "ai_applied": applied,
        "ai_reason": (ov or {}).get("reason") or "",
        "ai_updated_at": (ov or {}).get("updated_at") or "",
        "ai_source": (ov or {}).get("source") or "",
    }


def save_hot_term_ai(term: str, payload: dict) -> None:
    now = _now().isoformat(timespec="seconds")
    body = dict(payload)
    body["updated_at"] = now
    execute(
        "INSERT OR REPLACE INTO hot_term_ai(term, payload, updated_at) VALUES(?,?,?)",
        (term, json.dumps(body, ensure_ascii=False), now))


def revert_hot_term_ai(term: str) -> dict:
    term = (term or "").strip()
    if not term:
        return {"ok": False, "error": "未指定热词"}
    execute("DELETE FROM hot_term_ai WHERE term=?", (term,))
    detail = hot_term_sectors(term)
    return {**detail, "ok": True, "applied": False, "reverted": True}


def analyze_hot_term_ai(term: str) -> dict:
    """右键 AI：分析利好/利空板块并回填。失败回退词库，不假装成功、不编造板块。"""
    from . import ai as ai_svc
    term = (term or "").strip()
    if not term:
        return {"ok": False, "error": "未指定热词", "applied": False}
    base = hot_term_sectors(term)
    rule_summary = base.get("impact_summary") or ""
    samples = base.get("samples") or []
    vocab = "、".join(_board_vocab()[:36])
    local_text = (
        f"【本地规则】热词「{term}」：{rule_summary}。"
        "未改写映射。可在 AI 分析页配置大模型后右键回填。"
    )
    cfg = ai_svc.get_config(masked=False)
    base_url = ai_svc.normalize_api_base(cfg.get("api_base", "") or "")
    if not (cfg.get("api_key") and base_url):
        return {
            **base,
            "ok": True, "applied": False, "ai": False,
            "source": "本地规则（未配置AI大模型）",
            "text": local_text + ai_svc.DISCLAIMER,
            "error": "", "hint": "到 AI 分析页填写地址、密钥、模型并测试连通后再右键回填。",
        }
    context = (
        f"热词：{term}\n当前词库映射：{rule_summary}\n"
        f"近两周样例：{'；'.join(samples[:4]) or '无'}\n"
        f"可选板块名（请尽量从中选择）：{vocab}"
    )
    task = (
        "请判断该热词对A股哪些板块偏利好、哪些偏利空。"
        "严格只输出一行JSON："
        '{"bull":[{"name":"银行","why":"一句话"}],'
        '"bear":[{"name":"半导体","why":"一句话"}],'
        '"reason":"不超过40字总述"}。'
        "name 必须是常见行业/概念简称。不确定的板块不要写。禁止收益承诺。"
    )
    try:
        raw = ai_svc._call_llm(cfg, context, task)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        err = ai_svc.format_llm_error(exc, base_url)
        return {
            **base,
            "ok": True, "applied": False, "ai": False,
            "source": "本地规则分析（大模型未调用）",
            "text": local_text + f"\n（大模型未调用：{err}）" + ai_svc.DISCLAIMER,
            "error": err, "hint": ai_svc._hint_for_error(err),  # noqa: SLF001
        }
    import re
    parsed = None
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            parsed = json.loads(m.group())
        except Exception:  # noqa: BLE001
            parsed = None
    bull = _norm_ai_side((parsed or {}).get("bull") or [])
    bear = _norm_ai_side((parsed or {}).get("bear") or [])
    # 同一板块不可同时利好利空：保留先出现的利好
    bull_names = {x["name"] for x in bull}
    bear = [x for x in bear if x["name"] not in bull_names]
    if not bull and not bear:
        return {
            **base,
            "ok": True, "applied": False, "ai": True,
            "source": f"AI大模型（{cfg.get('model') or '默认'}）",
            "text": (raw or "") + "\n\n未能解析出有效板块名，未覆盖词库映射。" + ai_svc.DISCLAIMER,
            "error": "模型返回无法对应到本地行业/概念，已保留词库利好/利空",
            "hint": "可再试一次，或换更明确的热词。",
        }
    reason = str((parsed or {}).get("reason") or "")[:80]
    save_hot_term_ai(term, {
        "bull": bull, "bear": bear, "reason": reason, "text": raw,
        "source": f"AI大模型（{cfg.get('model') or '默认'}）",
    })
    detail = hot_term_sectors(term)
    return {
        **detail,
        "ok": True, "applied": True, "ai": True,
        "source": f"AI大模型（{cfg.get('model') or '默认'}）",
        "text": (raw or "") + ai_svc.DISCLAIMER,
        "error": "", "hint": "",
        "reason": reason,
    }


def analyze_hot_terms_ai_batch(kind: str = "", terms: list | None = None) -> dict:
    """一键回填当前热词列表的利好/利空。未配置或单词语失败不覆盖已有结果。"""
    from . import ai as ai_svc
    listed = list_hot_terms(kind or "")
    names: list[str] = []
    if isinstance(terms, list) and terms:
        want = {str(t).strip() for t in terms if str(t).strip()}
        names = [x["term"] for x in (listed.get("items") or []) if x.get("term") in want]
        extra = [t for t in (str(x).strip() for x in terms) if t and t not in names]
        names.extend(extra)
    else:
        names = [x["term"] for x in (listed.get("items") or []) if x.get("term")]
    cfg = ai_svc.get_config(masked=False)
    base_url = ai_svc.normalize_api_base(cfg.get("api_base", "") or "")
    if not names:
        return {
            "ok": True, "configured": bool(cfg.get("api_key") and base_url),
            "applied": 0, "failed": 0, "skipped": 0, "total": 0, "items": [],
            "error": "", "hint": "当前没有可回填的热词。请先手动更新全部热词。",
        }
    if not (cfg.get("api_key") and base_url):
        return {
            "ok": True, "configured": False, "applied": 0, "failed": 0,
            "skipped": len(names), "total": len(names), "items": [],
            "error": "",
            "hint": "到 AI 分析页填写地址、密钥、模型并测试连通后再一键回填。",
            "source": "本地规则（未配置AI大模型）",
        }
    items = []
    applied = failed = 0
    for name in names:
        one = analyze_hot_term_ai(name)
        row = {
            "term": name,
            "applied": bool(one.get("applied")),
            "error": one.get("error") or "",
            "reason": one.get("reason") or one.get("impact_summary") or "",
        }
        items.append(row)
        if row["applied"]:
            applied += 1
        else:
            failed += 1
    return {
        "ok": True, "configured": True,
        "applied": applied, "failed": failed, "skipped": 0,
        "total": len(names), "items": items,
        "source": f"AI大模型（{cfg.get('model') or '默认'}）",
        "error": "", "hint": "",
        "note": "仅成功解析出板块的热词会覆盖回填；失败的保留词库，不假装成功。",
    }


def hot_term_sectors(term: str) -> dict:
    term = (term or "").strip()
    if not term:
        return {"term": "", "sectors": [], "bull_sectors": [], "bear_sectors": [],
                "empty_reason": "未指定热词"}
    today = _now().strftime("%Y-%m-%d")
    rows = query("SELECT * FROM hot_term WHERE term=? AND window_end=? LIMIT 1", (term, today))
    if not rows:
        rows = query("SELECT * FROM hot_term WHERE term=? ORDER BY window_end DESC LIMIT 1", (term,))
    samples = []
    tagged: set[str] = set()
    votes: dict = {}
    if rows:
        packed_stored = _parse_sector_payload(rows[0].get("sectors"))
        for it in packed_stored:
            tagged.add(it["name"])
            votes[it["name"]] = {
                "利好": int(it.get("bull_n") or 0),
                "利空": int(it.get("bear_n") or 0),
                "中性": 0,
            }
        try:
            samples = json.loads(rows[0].get("sample_titles") or "[]")
        except Exception:  # noqa: BLE001
            samples = []
    packed = _pack_sector_impacts(term, tagged, votes)
    packed, meta = _with_ai_overlay(term, packed)
    heat_map = _sector_heat_map()
    for it in packed:
        info = heat_map.get(it["name"]) or {}
        it["hot_score"] = info.get("hot_score")
        it["pct"] = info.get("pct")
        it["net_in_yi"] = info.get("net_in_yi")
        it["n"] = info.get("n") or info.get("stocks")
    order_hot = lambda x: -(x.get("hot_score") or x.get("pct") or 0)  # noqa: E731
    bull = sorted([x for x in packed if x.get("direction") == "利好"], key=order_hot)
    bear = sorted([x for x in packed if x.get("direction") == "利空"], key=order_hot)
    mid = sorted([x for x in packed if x.get("direction") not in ("利好", "利空")], key=order_hot)
    empty_reason = ""
    if not packed:
        empty_reason = f"「{term}」暂未映射到可交易板块（本地行业/概念无匹配）。"
    note = ("利好/利空来自 AI 回填（右键可还原词库），不是预测。点击板块查看个股 TOP20。"
            if meta.get("ai_applied")
            else "利好/利空来自规则词库与近两周快讯方向统计，不是预测。右键热词可让 AI 分析并回填。点击板块查看个股 TOP20。")
    return {
        "term": term,
        "impact_summary": _impact_summary([x["name"] for x in bull], [x["name"] for x in bear]),
        "bull_sectors": bull,
        "bear_sectors": bear,
        "neutral_sectors": mid,
        "sectors": bull + bear + mid,
        "samples": samples,
        "empty_reason": empty_reason,
        "note": note,
        "disclaimer": "板块方向为规则化预估，仅供参考，不构成投资建议",
        **meta,
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
