"""宏观情报条目的 AI 回填：利好/利空板块、解读、关键词词库。

宏观情报全部二级菜单均可右键分析（含热度词汇、股票常识、热门信息卡片）。
热词同时写入 hot_term_ai，失败回退本地规则，不假装成功、不编造板块或热度。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ..database import execute, query
from . import hot_terms

_TZ = ZoneInfo("Asia/Shanghai")

SOURCE_LABELS = {
    "news": "实时快讯",
    "policy": "政策追踪",
    "major": "影响评估",
    "outlook": "事件展望",
    "calendar": "事件日历",
    "sector_event": "板块事件",
    "official": "官方政策信息",
    "announce": "公司公告",
    "holders": "持股情况",
    "hot_term": "热度词汇",
    "knowledge": "股票常识",
}

ALLOWED_SOURCES = set(SOURCE_LABELS)
SKIP_MENUS: set[str] = set()
MODES = {"boards", "reading", "keywords"}


def _now() -> str:
    return datetime.now(_TZ).replace(tzinfo=None).isoformat(timespec="seconds")


def ensure_table() -> None:
    execute(
        "CREATE TABLE IF NOT EXISTS intel_item_ai ("
        "item_key TEXT PRIMARY KEY, source TEXT NOT NULL, source_label TEXT, ident TEXT,"
        "title TEXT, text_excerpt TEXT, attention REAL, heat REAL, event_time TEXT,"
        "payload TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    try:
        execute("CREATE INDEX IF NOT EXISTS idx_intel_item_ai_src ON intel_item_ai(source, updated_at)")
    except Exception:  # noqa: BLE001
        pass


def make_key(source: str, ident: str = "", title: str = "", when: str = "") -> str:
    source = (source or "news").strip() or "news"
    if source not in ALLOWED_SOURCES:
        source = "news"
    ident = str(ident or "").strip()
    if not ident:
        import hashlib
        ident = hashlib.md5(f"{title or ''}|{(when or '')[:16]}".encode()).hexdigest()[:16]
    return f"{source}:{ident}"[:120]


def _parse_payload(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def load(item_key: str) -> dict | None:
    ensure_table()
    key = (item_key or "").strip()
    if not key:
        return None
    try:
        rows = query("SELECT * FROM intel_item_ai WHERE item_key=?", (key,))
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    r = rows[0]
    payload = _parse_payload(r.get("payload"))
    bull = hot_terms._norm_ai_side(payload.get("bull") or [])  # noqa: SLF001
    bear = hot_terms._norm_ai_side(payload.get("bear") or [])  # noqa: SLF001
    bull, bear = exclusive_boards(bull, bear)
    keywords = _norm_keywords(payload.get("keywords") or [])
    reading = str(payload.get("reading") or "").strip()
    return {
        "item_key": r["item_key"],
        "source": r.get("source") or "",
        "source_label": r.get("source_label") or SOURCE_LABELS.get(r.get("source") or "", ""),
        "ident": r.get("ident") or "",
        "title": r.get("title") or payload.get("title") or "",
        "text_excerpt": (r.get("text_excerpt") or "")[:240],
        "attention": r.get("attention"),
        "heat": r.get("heat"),
        "event_time": r.get("event_time") or "",
        "updated_at": r.get("updated_at") or payload.get("updated_at") or "",
        "bull": bull,
        "bear": bear,
        "keywords": keywords,
        "reading": reading,
        "reason": str(payload.get("reason") or "")[:120],
        "text": payload.get("text") or "",
        "ai_source": payload.get("source") or "",
        "has_boards": bool(bull or bear),
        "has_reading": bool(reading),
        "has_keywords": bool(keywords),
        "applied": True,
    }


def _norm_keywords(items) -> list[str]:
    out, seen = [], set()
    if not isinstance(items, list):
        if isinstance(items, str) and items.strip():
            items = re.split(r"[,，、/|\s]+", items)
        else:
            return out
    for it in items:
        if isinstance(it, dict):
            name = str(it.get("name") or it.get("term") or it.get("word") or "").strip()
        else:
            name = str(it or "").strip()
        name = re.sub(r"\s+", "", name)[:16]
        if len(name) < 2 or name in seen:
            continue
        if name in ("无明显利空", "未映射影响板块", "A股", "大盘"):
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= 16:
            break
    return out


def _row_public(rec: dict) -> dict:
    bull, bear = exclusive_boards(rec.get("bull") or [], rec.get("bear") or [])
    kws = rec.get("keywords") or []
    return {
        "item_key": rec["item_key"],
        "ident": rec.get("ident") or "",
        "source": rec.get("source") or "",
        "source_label": rec.get("source_label") or "",
        "title": rec.get("title") or "",
        "attention": rec.get("attention"),
        "heat": rec.get("heat"),
        "event_time": rec.get("event_time") or "",
        "updated_at": rec.get("updated_at") or "",
        "bull": [x["name"] if isinstance(x, dict) else x for x in bull],
        "bear": [x["name"] if isinstance(x, dict) else x for x in bear],
        "bull_detail": bull,
        "bear_detail": bear,
        "keywords": kws,
        "reason": rec.get("reason") or "",
        "has_boards": bool(bull or bear),
        "has_reading": bool(rec.get("reading")),
        "has_keywords": bool(kws),
        "ai_source": rec.get("ai_source") or "",
    }


def save(meta: dict, patch: dict) -> dict:
    """合并写入：解读不冲掉已回填板块，板块不冲掉解读。"""
    ensure_table()
    source = (meta.get("source") or "news").strip()
    if source not in ALLOWED_SOURCES:
        source = "news"
    ident = str(meta.get("ident") or "").strip()
    key = meta.get("item_key") or make_key(source, ident, meta.get("title") or "", meta.get("time") or "")
    prev = load(key) or {}
    prev_payload = {}
    try:
        rows = query("SELECT payload FROM intel_item_ai WHERE item_key=?", (key,))
        if rows:
            prev_payload = _parse_payload(rows[0].get("payload"))
    except Exception:  # noqa: BLE001
        prev_payload = {}
    body = dict(prev_payload)
    for k, v in (patch or {}).items():
        if v is None:
            continue
        if k in ("bull", "bear") and not v:
            continue
        if k == "keywords" and not v:
            continue
        if k == "reading" and not str(v).strip():
            continue
        body[k] = v
    prefer = "bear" if (patch or {}).get("bear") and not (patch or {}).get("bull") else "bull"
    if body.get("bull") or body.get("bear"):
        body["bull"], body["bear"] = exclusive_boards(body.get("bull"), body.get("bear"), prefer)
    now = _now()
    body["updated_at"] = now
    title = (meta.get("title") or prev.get("title") or "")[:160]
    excerpt = (meta.get("text") or meta.get("text_excerpt") or prev.get("text_excerpt") or "")[:400]
    att = meta.get("attention")
    if att is None:
        att = prev.get("attention")
    heat = meta.get("heat")
    if heat is None:
        heat = prev.get("heat")
    event_time = meta.get("time") or meta.get("event_time") or prev.get("event_time") or ""
    execute(
        "INSERT OR REPLACE INTO intel_item_ai("
        "item_key,source,source_label,ident,title,text_excerpt,attention,heat,event_time,payload,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (key, source, SOURCE_LABELS.get(source, source), ident or prev.get("ident") or "",
         title, excerpt, att, heat, event_time,
         json.dumps(body, ensure_ascii=False), now),
    )
    return load(key) or {"item_key": key, "applied": False}


def index() -> dict:
    """列表页徽章用：全部已分析条目的精简索引。"""
    ensure_table()
    items: dict[str, dict] = {}
    try:
        rows = query("SELECT * FROM intel_item_ai ORDER BY updated_at DESC LIMIT 800")
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows:
        rec = load(r["item_key"])
        if not rec:
            continue
        items[rec["item_key"]] = _row_public(rec)
    # 热词 AI 回填也纳入索引，供热门信息页
    try:
        ht = query("SELECT term, payload, updated_at FROM hot_term_ai LIMIT 200")
    except Exception:  # noqa: BLE001
        ht = []
    for r in ht:
        term = (r.get("term") or "").strip()
        if not term:
            continue
        key = make_key("hot_term", term)
        if key in items:
            continue
        payload = _parse_payload(r.get("payload"))
        bull = hot_terms._norm_ai_side(payload.get("bull") or [])  # noqa: SLF001
        bear = hot_terms._norm_ai_side(payload.get("bear") or [])  # noqa: SLF001
        bull, bear = exclusive_boards(bull, bear)
        reading = str(payload.get("reading") or "").strip()
        kws = _norm_keywords(payload.get("keywords") or [term])
        if not bull and not bear and not reading and not kws:
            continue
        items[key] = {
            "item_key": key, "ident": term, "source": "hot_term", "source_label": "热度词汇",
            "title": term, "attention": None, "heat": None,
            "event_time": "", "updated_at": r.get("updated_at") or "",
            "bull": [x["name"] for x in bull], "bear": [x["name"] for x in bear],
            "bull_detail": bull, "bear_detail": bear,
            "keywords": kws or [term], "reason": str(payload.get("reason") or "")[:120],
            "has_boards": bool(bull or bear), "has_reading": bool(reading or payload.get("text")),
            "has_keywords": True, "ai_source": payload.get("source") or "",
        }
    return {"ok": True, "count": len(items), "items": items}


SORT_DIMS = (
    {"id": "heat", "name": "热度"},
    {"id": "attention", "name": "关注度"},
    {"id": "updated_at", "name": "更新时间"},
    {"id": "event_time", "name": "事件时间"},
)
SORT_IDS = {d["id"] for d in SORT_DIMS}


def _num_sort_val(v, descending: bool) -> float:
    try:
        if v is None or v == "":
            raise TypeError
        return float(v)
    except (TypeError, ValueError):
        return float("-inf") if descending else float("inf")


def _sort_key(card: dict, primary: str, descending: bool):
    dims = [primary] + [d["id"] for d in SORT_DIMS if d["id"] != primary]
    keys = []
    for dim in dims:
        val = card.get(dim)
        if dim in ("heat", "attention"):
            keys.append(_num_sort_val(val, descending))
        else:
            keys.append(str(val or ""))
    return tuple(keys)


def list_hot_intel(source: str = "", limit: int = 80, sort: str = "heat", order: str = "desc") -> dict:
    """热门信息：所有 AI 分析词库/板块，块状展示。默认按热度倒序，可换维度。"""
    idx = index()
    cards = list((idx.get("items") or {}).values())
    src = (source or "").strip()
    if src and src in ALLOWED_SOURCES:
        cards = [c for c in cards if c.get("source") == src]
    # 热度词汇补 heat（只用已落库热度，不编造）
    try:
        today = datetime.now(_TZ).strftime("%Y-%m-%d")
        heats = {r["term"]: r.get("heat") for r in query(
            "SELECT term, heat FROM hot_term WHERE window_end=?", (today,))}
    except Exception:  # noqa: BLE001
        heats = {}
    for c in cards:
        if c.get("source") == "hot_term" and c.get("heat") is None:
            term = (c.get("title") or "").strip()
            if term in heats:
                c["heat"] = heats[term]
        if not c.get("keywords"):
            # 用板块名当弱词库，避免空块；不是编造热词
            names = (c.get("bull") or []) + (c.get("bear") or [])
            c["keywords"] = names[:8]
            c["has_keywords"] = bool(c["keywords"])
    sort = (sort or "heat").strip()
    if sort not in SORT_IDS:
        sort = "heat"
    order = "asc" if (order or "").strip().lower() == "asc" else "desc"
    descending = order == "desc"
    cards.sort(key=lambda x: _sort_key(x, sort, descending), reverse=descending)
    limit = max(10, min(int(limit or 80), 200))
    return {
        "ok": True,
        "items": cards[:limit],
        "count": min(len(cards), limit),
        "total": len(cards),
        "sort": sort,
        "order": order,
        "sorts": list(SORT_DIMS),
        "sources": [{"id": k, "name": v} for k, v in SOURCE_LABELS.items()],
        "note": "仅展示已成功落库的 AI 分析。默认按热度倒序，缺热度/关注度的排后面。可改关注度、更新时间、事件时间。点击卡片看板块，再点板块看个股，默认 TOP20。",
        "empty_reason": "" if cards else "尚无已保存的 AI 分析词库。请在宏观情报各栏目或持股上右键分析利好/利空、解读或提取词库。",
    }


def get_detail(item_key: str) -> dict:
    rec = load(item_key)
    if rec:
        return {**rec, "ok": True, "error": "", "hint": ""}
    # 热词回退
    if (item_key or "").startswith("hot_term:"):
        term = item_key.split(":", 1)[-1]
        detail = hot_terms.hot_term_sectors(term)
        if detail.get("ai_applied"):
            reading = str(detail.get("ai_reading") or detail.get("ai_reason") or "").strip()
            bull, bear = exclusive_boards(detail.get("bull_sectors") or [], detail.get("bear_sectors") or [])
            return {
                "ok": True, "item_key": item_key, "ident": term, "source": "hot_term",
                "source_label": "热度词汇", "title": term,
                "bull": bull,
                "bear": bear,
                "keywords": detail.get("ai_keywords") or [term],
                "reading": reading,
                "reason": detail.get("impact_summary") or "",
                "text": reading or detail.get("ai_reason") or "",
                "has_boards": True, "has_reading": bool(reading),
                "applied": True, "ai_source": detail.get("ai_source") or "",
                "updated_at": detail.get("ai_updated_at") or "",
                "error": "", "hint": "",
            }
    return {
        "ok": True, "applied": False, "item_key": item_key or "",
        "error": "", "hint": "尚未保存 AI 分析。右键可分析利好/利空或解读。",
        "bull": [], "bear": [], "keywords": [], "reading": "",
    }


def exclusive_boards(bull: list, bear: list, prefer: str = "bull") -> tuple[list, list]:
    """同一板块不可同时利好和利空。名称先规范化；冲突时 prefer 侧保留。"""
    bull_n = hot_terms._norm_ai_side(bull or [])  # noqa: SLF001
    bear_n = hot_terms._norm_ai_side(bear or [])  # noqa: SLF001
    if prefer == "bear":
        names = {x["name"] for x in bear_n}
        bull_n = [x for x in bull_n if x["name"] not in names]
    else:
        names = {x["name"] for x in bull_n}
        bear_n = [x for x in bear_n if x["name"] not in names]
    return bull_n, bear_n


def _skip_board_name(name: str) -> bool:
    return name in ("", "无明显利空", "未映射影响板块", "政策", "A股", "大盘")


def _orig_sector_names(raw) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        parts = re.split(r"[,，、/|\s]+", raw)
    elif isinstance(raw, list):
        parts = raw
    else:
        return []
    out, seen = [], set()
    for p in parts:
        if isinstance(p, dict):
            name = str(p.get("name") or p.get("sector") or "").strip()
        else:
            name = str(p or "").strip()
        if _skip_board_name(name) or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def merge_orig_boards(bull: list, bear: list, orig_sectors=None, direction: str = "") -> tuple[list, list]:
    """AI 利好/利空与情报拉取板块合并：同名以 AI 为准，其余按消息方向并入。不编造板块。"""
    bull = [x for x in (bull or []) if isinstance(x, dict) and x.get("name")]
    bear = [x for x in (bear or []) if isinstance(x, dict) and x.get("name")]
    bull_names = {x["name"] for x in bull}
    bear_names = {x["name"] for x in bear}
    direction = str(direction or "").strip()
    for name in _orig_sector_names(orig_sectors):
        resolved = hot_terms._resolve_ai_name(name)  # noqa: SLF001
        if not resolved:
            resolved = name if hot_terms._is_board(name) else None  # noqa: SLF001
        if not resolved or resolved in bull_names or resolved in bear_names:
            continue
        item = {"name": resolved, "why": "情报拉取映射"}
        if direction == "利空":
            bear.append(item)
            bear_names.add(resolved)
        else:
            bull.append(item)
            bull_names.add(resolved)
    bear = [x for x in bear if x["name"] not in bull_names]
    return exclusive_boards(bull, bear)


def _safe_num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def analyze(body: dict) -> dict:
    """mode=boards|reading|keywords。未配置/解析失败不覆盖已保存字段。"""
    from . import ai as ai_svc
    body = body or {}
    mode = (body.get("mode") or "boards").strip()
    if mode not in MODES:
        mode = "boards"
    source = (body.get("source") or "news").strip()
    if source not in ALLOWED_SOURCES:
        return {"ok": False, "applied": False, "error": "不支持的情报来源"}
    ident = str(body.get("ident") or "").strip()
    title = str(body.get("title") or "").strip()[:160]
    text = str(body.get("text") or body.get("summary") or "").strip()[:1200]
    when = str(body.get("time") or body.get("event_time") or "")[:24]
    if source == "holders" and ident:
        try:
            from . import holders as holders_svc
            snap = holders_svc.get_holders(ident)
            extra = holders_svc._holder_ai_context(snap)  # noqa: SLF001
            text = (text + "\n" + extra).strip()[:1200]
            title = title or ident
        except Exception:  # noqa: BLE001
            pass
    if source == "hot_term":
        ident = ident or title
        title = title or ident
        try:
            detail = hot_terms.hot_term_sectors(ident)
            samples = detail.get("samples") or []
            summary = detail.get("impact_summary") or ""
            extra = "；".join(str(s) for s in samples[:4] if s)
            bits = [text, summary, extra]
            text = "\n".join(b for b in bits if b).strip()[:1200]
            if body.get("heat") in (None, ""):
                rows = query(
                    "SELECT heat FROM hot_term WHERE term=? ORDER BY window_end DESC LIMIT 1",
                    (ident,))
                if rows:
                    body = dict(body)
                    body["heat"] = rows[0].get("heat")
        except Exception:  # noqa: BLE001
            pass
    key = make_key(source, ident, title, when)
    meta = {
        "item_key": key, "source": source, "ident": ident, "title": title or text[:40],
        "text": text, "time": when,
        "attention": _safe_num(body.get("attention")),
        "heat": _safe_num(body.get("heat")),
    }
    prev = load(key)
    cfg = ai_svc.get_config(masked=False)
    base_url = ai_svc.normalize_api_base(cfg.get("api_base", "") or "")
    configured = bool(cfg.get("api_key") and base_url)
    vocab = "、".join(hot_terms._board_vocab()[:36])  # noqa: SLF001
    local = (
        f"【本地规则】来源：{SOURCE_LABELS.get(source, source)}。"
        f"标题：{title or '（无）'}。未改写已保存结果。"
    )
    if prev:
        local += f" 已有分析时间 {prev.get('updated_at') or ''}。"

    def _keep(error: str = "", hint: str = "", source_txt: str = "", extra_text: str = "") -> dict:
        base = prev or {
            "item_key": key, "source": source, "source_label": SOURCE_LABELS.get(source, source),
            "title": title, "bull": [], "bear": [], "keywords": [], "reading": "",
            "applied": False,
        }
        body_txt = extra_text or local
        if ai_svc.DISCLAIMER.strip() not in body_txt:
            body_txt = body_txt + ai_svc.DISCLAIMER
        return {
            **base,
            "ok": True, "applied": False, "ai": False, "kept": bool(prev),
            "configured": configured, "mode": mode,
            "source_call": source_txt or "本地规则（未配置AI大模型）",
            "text": body_txt, "error": error, "hint": hint,
        }

    if not configured:
        return _keep(
            error="未配置AI大模型",
            hint="到 AI 分析页填写地址、密钥、模型并测试连通后再右键分析。未改写已保存结果。",
            extra_text=local,
        )
    context = (
        f"来源菜单：{SOURCE_LABELS.get(source, source)}\n"
        f"标题：{title or '无'}\n时间：{when or '无'}\n"
        f"正文：{text or title or '无'}\n"
        f"可选板块名（请尽量从中选择）：{vocab}"
    )
    if mode == "boards":
        task = (
            "请判断该信息对A股哪些板块偏利好、哪些偏利空。"
            "严格只输出一行JSON："
            '{"bull":[{"name":"银行","why":"一句话"}],'
            '"bear":[{"name":"半导体","why":"一句话"}],'
            '"reason":"不超过40字总述","keywords":["词1","词2"]}。'
            "name 必须是常见行业/概念简称。不确定的板块不要写。禁止收益承诺。"
        )
    elif mode == "reading":
        task = (
            "请解读该信息的市场含义（利好/利空、可能受益或受损板块、风险提示）。"
            "严格只输出一行JSON："
            '{"reading":"200字内分点解读","keywords":["词1","词2"],'
            '"bull":[{"name":"银行","why":"一句话"}],'
            '"bear":[{"name":"半导体","why":"一句话"}],'
            '"reason":"不超过40字总述"}。'
            "不确定就写披露不足。禁止收益承诺。"
        )
    else:
        task = (
            "请从该信息提取关键信息词库（政策/产业/事件简称，2-12个）。"
            "持股类请提取筹码相关关键词。"
            "严格只输出一行JSON："
            '{"keywords":["降准","银行"],'
            '"bull":[{"name":"银行","why":"一句话"}],'
            '"bear":[{"name":"半导体","why":"一句话"}],'
            '"reason":"不超过40字总述"}。'
            "禁止编造未出现的事实。禁止收益承诺。"
        )
    try:
        raw = ai_svc._call_llm(cfg, context, task)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        err = ai_svc.format_llm_error(exc, base_url)
        return _keep(
            error=err, hint=ai_svc._hint_for_error(err),  # noqa: SLF001
            source_txt="本地规则分析（大模型未调用）",
            extra_text=local + f"\n（大模型未调用：{err}）",
        )
    parsed = None
    m = re.search(r"\{.*\}", raw or "", re.S)
    if m:
        try:
            parsed = json.loads(m.group())
        except Exception:  # noqa: BLE001
            parsed = None
    bull = hot_terms._norm_ai_side((parsed or {}).get("bull") or [])  # noqa: SLF001
    bear = hot_terms._norm_ai_side((parsed or {}).get("bear") or [])  # noqa: SLF001
    bull, bear = exclusive_boards(bull, bear)
    keywords = _norm_keywords((parsed or {}).get("keywords") or [])
    reading = str((parsed or {}).get("reading") or "").strip()
    if mode == "reading" and not reading:
        # JSON 失败时，若原文够长则当作解读保存
        plain = re.sub(r"\{.*\}", "", raw or "", flags=re.S).strip()
        if len(plain) >= 40:
            reading = plain[:800]
    reason = str((parsed or {}).get("reason") or "")[:80]
    model_src = f"AI大模型（{cfg.get('model') or '默认'}）"

    ok_boards = bool(bull or bear)
    ok_reading = bool(reading)
    ok_kw = bool(keywords)
    if mode == "boards" and not ok_boards:
        return _keep(
            error="模型返回无法对应到本地行业/概念，未覆盖已有利好/利空",
            hint="可再试一次。",
            source_txt=model_src,
            extra_text=(raw or "") + "\n\n未能解析出有效板块名。" + ai_svc.DISCLAIMER,
        )
    if mode == "reading" and not ok_reading:
        return _keep(
            error="模型返回过短或无法解析解读，未覆盖已有解读",
            hint="可再试一次。",
            source_txt=model_src,
            extra_text=(raw or "") + ai_svc.DISCLAIMER,
        )
    if mode == "keywords" and not ok_kw:
        return _keep(
            error="未能解析出关键词，未覆盖已有词库",
            hint="可再试一次。",
            source_txt=model_src,
            extra_text=(raw or "") + ai_svc.DISCLAIMER,
        )
    if ok_boards:
        bull, bear = merge_orig_boards(
            bull, bear,
            body.get("orig_sectors") or body.get("sectors"),
            body.get("direction") or "",
        )
        ok_boards = bool(bull or bear)
    patch = {"source": model_src, "text": raw, "reason": reason}
    if ok_boards:
        patch["bull"] = bull
        patch["bear"] = bear
    if ok_reading:
        patch["reading"] = reading
    if ok_kw:
        patch["keywords"] = keywords
    saved = save(meta, patch)
    if source == "holders" and ident:
        _merge_holder_keywords(ident, keywords, reading, model_src)
    if source == "hot_term" and ident:
        _merge_hot_term_overlay(ident, {
            "bull": bull if ok_boards else None,
            "bear": bear if ok_boards else None,
            "reason": reason,
            "reading": reading if ok_reading else None,
            "keywords": keywords if ok_kw else None,
            "text": raw,
            "source": model_src,
        })
    return {
        **saved,
        "ok": True, "applied": True, "ai": True, "kept": False,
        "configured": True, "mode": mode,
        "source_call": model_src,
        "text": (raw or "") + ai_svc.DISCLAIMER,
        "error": "",         "hint": "",
    }


def _merge_holder_keywords(code: str, keywords: list[str], reading: str, source: str) -> None:
    from . import holders
    prev = holders.load_holder_ai(code) or {}
    payload = {
        "text": prev.get("text") or (reading + "\n\n——\nAI生成内容仅供参考，不构成投资建议。" if reading else ""),
        "source": prev.get("source") or source,
        "asof": prev.get("asof") or "",
        "keywords": keywords or prev.get("keywords") or [],
        "model": prev.get("model") or "",
    }
    if not payload["text"] and not payload["keywords"]:
        return
    try:
        holders.save_holder_ai(code, payload)
    except Exception:  # noqa: BLE001
        pass


def _merge_hot_term_overlay(term: str, patch: dict) -> None:
    """解读/板块写入 hot_term_ai，空字段不冲掉已回填。"""
    prev = hot_terms._ai_overlay(term) or {}  # noqa: SLF001
    body = dict(prev)
    for k, v in (patch or {}).items():
        if v is None:
            continue
        if k in ("bull", "bear", "keywords") and not v:
            continue
        if k in ("reading", "reason", "text") and not str(v).strip():
            continue
        body[k] = v
    if not (body.get("bull") or body.get("bear") or body.get("reading") or body.get("keywords")):
        return
    try:
        hot_terms.save_hot_term_ai(term, body)
    except Exception:  # noqa: BLE001
        pass
