"""官方政策信息：近半年归档，分时间 / 国内国外 / 国家 / 文件类型。

不编造文件：只落库公开资讯源中能识别为官方政策/大会会议的条目。
增量每 4 小时；每日回补更早页面，直到覆盖近 183 天或源站翻页耗尽。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..database import execute, executemany, get_meta, get_meta_json, query, set_meta, set_meta_json
from ..datasources import eastmoney, sina
from . import macro

log = logging.getLogger("policy_archive")
_TZ = ZoneInfo("Asia/Shanghai")

KEEP_DAYS = 183
INCREMENTAL_PAGES = 8
BACKFILL_PAGES = 36
SINA_ROLL_PAGES = 5

# 东方财富专栏：财经 / 要闻 / 国际（覆盖国内与国外维度）
_EM_COLUMNS = (
    (345, "东方财富·财经"),
    (350, "东方财富·要闻"),
    (351, "东方财富·国际"),
)

DOC_TYPES = ("政策文件", "大会会议", "通知意见", "监管动态")

_MEETING = (
    "两会", "政府工作报告", "中央经济工作会议", "政治局会议", "中央财经委员会",
    "人大常委会", "政协会议", "党代会", "全会", "峰会", "G20", "G7",
    "FOMC", "议息会议", "国会", "议会", "听证会", "部长级会议", "闭幕", "开幕",
)
_DOC_FILE = (
    "印发", "颁布", "施行", "规划纲要", "白皮书", "条例", "办法", "法案",
    "实施方案", "行动计划", "指导目录", "修订草案", "法律",
)
_NOTICE = ("征求意见", "指导意见", "通知", "公告", "意见稿", "公开征求")
_REG = (
    "证监会", "央行", "人民银行", "银保监", "金融监管", "国资委", "发改委",
    "财政部", "工信部", "商务部", "住建部", "监管新规", "窗口指导",
)
_ACTORS = (
    "国务院", "中共中央", "中央政治局", "全国人大", "全国政协", "国家发改委",
    "发改委", "财政部", "工信部", "央行", "人民银行", "证监会", "国资委",
    "商务部", "住建部", "农业农村部", "科技部", "国家能源局", "外汇局",
    "白宫", "美联储", "欧央行", "欧洲央行", "英国央行", "日本央行", "韩国央行",
    "欧洲议会", "欧盟委员会", "财政部部长", "央行行长",
)
_POLICY_HINT = (
    "政策", "监管", "关税", "降准", "降息", "加息", "补贴", "限购", "专项债",
    "国债", "MLF", "LPR", "再贷款", "财政政策", "货币政策", "产业政策",
)

# 先匹配更具体的国家/地区
_COUNTRY = (
    ("美国", ("美国", "美联储", "白宫", "特朗普", "美股", "非农", "FOMC", "国会山")),
    ("欧盟", ("欧盟", "欧央行", "欧洲央行", "欧元区", "布鲁塞尔")),
    ("德国", ("德国", "柏林")),
    ("法国", ("法国", "巴黎")),
    ("英国", ("英国", "英央行", "伦敦")),
    ("日本", ("日本", "日央行", "东京")),
    ("韩国", ("韩国", "韩央行", "首尔")),
    ("俄罗斯", ("俄罗斯", "莫斯科")),
    ("印度", ("印度", "新德里")),
    ("中东", ("伊朗", "沙特", "以色列", "霍尔木兹", "中东", "阿联酋")),
    ("中国", ("中国", "国内", "国务院", "央行", "证监会", "发改委", "财政部",
              "两会", "政治局", "A股", "人民币", "工信部", "国资委",
              "中央经济工作会议", "中央财经", "中共中央", "全国人大", "全国政协",
              "人民银行", "政府工作报告")),
)


def _now() -> datetime:
    return datetime.now(_TZ).replace(tzinfo=None)


def _cutoff() -> str:
    return (_now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d 00:00:00")


def classify_official(title: str, summary: str = "") -> dict | None:
    """识别是否官方政策/大会会议；非官方返回 None（不编条目）。"""
    text = f"{title or ''} {summary or ''}"
    if len(text.strip()) < 6:
        return None
    meeting = any(w in text for w in _MEETING)
    actor = any(w in text for w in _ACTORS)
    hint = any(w in text for w in _POLICY_HINT) or any(w in text for w in _DOC_FILE) or any(w in text for w in _NOTICE)
    if not (meeting or (actor and hint)):
        return None
    if any(w in text for w in _MEETING):
        doc_type = "大会会议"
    elif any(w in text for w in _NOTICE):
        doc_type = "通知意见"
    elif any(w in text for w in _DOC_FILE):
        doc_type = "政策文件"
    elif any(w in text for w in _REG):
        doc_type = "监管动态"
    else:
        doc_type = "政策文件"
    country = "全球"
    for name, kws in _COUNTRY:
        if any(k in text for k in kws):
            country = name
            break
    if country == "全球" and actor and any(w in text for w in ("国务院", "发改委", "证监会", "央行", "两会", "政治局")):
        country = "中国"
    scope = "国内" if country == "中国" else "国外"
    return {"doc_type": doc_type, "country": country, "scope": scope}


def _fmt_ctime(ctime) -> str:
    try:
        ts = int(str(ctime).strip())
        if ts > 10_000_000_000:
            ts //= 1000
        return datetime.fromtimestamp(ts, _TZ).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _row_from_item(prefix: str, item: dict, source: str) -> tuple | None:
    title = (item.get("title") or item.get("text") or "").strip()
    summary = (item.get("summary") or item.get("text") or title).strip()
    if not title:
        return None
    cls = classify_official(title, summary)
    if not cls:
        return None
    event_time = (item.get("time") or "").strip() or _fmt_ctime(item.get("ctime"))
    if event_time and event_time < _cutoff():
        return None
    impact = macro.assess_impact(f"{title} {summary}")
    pid = f"{prefix}_{item.get('id') or title[:24]}"
    now = _now().isoformat(timespec="seconds")
    raw = {
        "url": item.get("url") or "",
        "media": item.get("media") or "",
        "source": source,
    }
    return (
        pid, title[:200], summary[:800], event_time, cls["scope"], cls["country"],
        cls["doc_type"], source, item.get("url") or "",
        json.dumps(impact.get("affected_sectors") or [], ensure_ascii=False),
        impact.get("impact_level") or 1, impact.get("impact_direction") or "",
        json.dumps(raw, ensure_ascii=False), now,
    )


def _save(rows: list[tuple]) -> int:
    if not rows:
        return 0
    executemany(
        """INSERT OR REPLACE INTO official_policy(
             policy_id,title,summary,event_time,scope,country,doc_type,source,url,
             affected_sectors,impact_level,impact_direction,raw_json,fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows)
    return len(rows)


def _prune() -> None:
    execute("DELETE FROM official_policy WHERE event_time IS NOT NULL AND event_time < ?",
            (_cutoff(),))


def _ingest_intel() -> int:
    """把已缓存的政策快讯并入归档（同源去重靠 policy_id）。"""
    cutoff = _cutoff()
    try:
        items = query(
            "SELECT item_id,title,summary,event_time,source FROM intel_cache "
            "WHERE kind='policy' AND (event_time IS NULL OR event_time>=?) LIMIT 800",
            (cutoff,))
    except Exception:  # noqa: BLE001
        items = []
    rows = []
    for it in items:
        rec = _row_from_item(
            "intel",
            {"id": it["item_id"], "title": it["title"], "summary": it["summary"],
             "time": it["event_time"], "url": ""},
            it.get("source") or "本地情报缓存",
        )
        if rec:
            rows.append(rec)
    return _save(rows)


def sync_official_policy(mode: str = "incremental") -> dict:
    """mode=incremental 拉最近若干页；backfill 从上次页码继续往历史翻。"""
    errors: list[str] = []
    fetched = 0
    saved = 0
    oldest = ""
    pages_done: dict[str, int] = {}
    state = get_meta_json("official_policy_backfill", {}) or {}
    incremental = mode != "backfill"

    for col, source in _EM_COLUMNS:
        key = str(col)
        start = 1 if incremental else max(1, int(state.get(key) or 1))
        n_pages = INCREMENTAL_PAGES if incremental else BACKFILL_PAGES
        last_page = start - 1
        hit_old = False
        for i in range(n_pages):
            page = start + i
            try:
                items = eastmoney.fetch_column_news(col, page=page, size=50)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{source} p{page}: {exc}")
                break
            if not items:
                hit_old = True
                break
            fetched += len(items)
            batch = []
            for it in items:
                rec = _row_from_item(f"em{col}", it, source)
                if rec:
                    batch.append(rec)
                    if rec[3] and (not oldest or rec[3] < oldest):
                        oldest = rec[3]
                t = (it.get("time") or "")
                if t and t < _cutoff():
                    hit_old = True
            saved += _save(batch)
            last_page = page
            if hit_old:
                break
            time.sleep(0.04)
        pages_done[key] = last_page
        if incremental:
            # 回补游标不回退
            state[key] = max(int(state.get(key) or 1), last_page + 1 if last_page else 1)
        else:
            state[key] = last_page + 1
            if hit_old:
                state[f"{key}_done"] = True

    if incremental:
        for page in range(1, SINA_ROLL_PAGES + 1):
            try:
                items = sina.fetch_roll_news(lid=2516, page=page, num=50)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"新浪滚动 p{page}: {exc}")
                break
            fetched += len(items)
            batch = []
            for it in items:
                rec = _row_from_item("sina", {**it, "time": _fmt_ctime(it.get("ctime"))}, "新浪财经滚动")
                if rec:
                    batch.append(rec)
            saved += _save(batch)

    intel_n = _ingest_intel()
    _prune()
    now = _now().isoformat(timespec="seconds")
    set_meta("official_policy_last_sync", now)
    set_meta("official_policy_last_mode", mode)
    if not incremental:
        cols_done = all(state.get(f"{c}_done") for c, _ in _EM_COLUMNS)
        state["done"] = bool(cols_done)
    set_meta_json("official_policy_backfill", state)
    stats = archive_stats()
    return {
        "ok": True, "mode": mode, "fetched": fetched, "saved": saved,
        "from_intel": intel_n, "oldest": oldest, "pages": pages_done,
        "errors": errors[:8], "stats": stats,
        "note": "仅收录可识别的官方政策/大会会议，未命中规则的资讯不入库。",
    }


def archive_stats() -> dict:
    cutoff = _cutoff()
    try:
        total = query("SELECT COUNT(*) AS n FROM official_policy WHERE event_time>=? OR event_time IS NULL",
                      (cutoff,))[0]["n"]
        by_scope = {r["scope"]: r["n"] for r in query(
            "SELECT scope, COUNT(*) AS n FROM official_policy "
            "WHERE event_time>=? OR event_time IS NULL GROUP BY scope", (cutoff,))}
        by_type = {r["doc_type"]: r["n"] for r in query(
            "SELECT doc_type, COUNT(*) AS n FROM official_policy "
            "WHERE event_time>=? OR event_time IS NULL GROUP BY doc_type", (cutoff,))}
        countries = [r["country"] for r in query(
            "SELECT country, COUNT(*) AS n FROM official_policy "
            "WHERE (event_time>=? OR event_time IS NULL) AND country!='' "
            "GROUP BY country ORDER BY n DESC", (cutoff,))]
        oldest = query("SELECT MIN(event_time) AS t FROM official_policy")[0]["t"]
        newest = query("SELECT MAX(event_time) AS t FROM official_policy")[0]["t"]
    except Exception:  # noqa: BLE001
        return {"total": 0, "by_scope": {}, "by_type": {}, "countries": [],
                "keep_days": KEEP_DAYS, "last_sync": "从未"}
    return {
        "total": total, "by_scope": by_scope, "by_type": by_type,
        "countries": countries, "oldest": oldest, "newest": newest,
        "keep_days": KEEP_DAYS,
        "last_sync": get_meta("official_policy_last_sync", "从未"),
        "last_mode": get_meta("official_policy_last_mode", ""),
        "update": "增量每4小时；每日凌晨回补历史页，目标覆盖近半年；热词每小时重算。",
    }


def list_official_policy(scope: str = "", country: str = "", doc_type: str = "",
                         days: int = 180, limit: int = 80) -> dict:
    limit = max(10, min(int(limit or 80), 200))
    days = max(1, min(int(days or KEEP_DAYS), KEEP_DAYS))
    cutoff = (_now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
    where = ["(event_time>=? OR event_time IS NULL)"]
    params: list = [cutoff]
    if scope in ("国内", "国外"):
        where.append("scope=?")
        params.append(scope)
    if country:
        where.append("country=?")
        params.append(country)
    if doc_type in DOC_TYPES:
        where.append("doc_type=?")
        params.append(doc_type)
    rows = query(
        f"SELECT * FROM official_policy WHERE {' AND '.join(where)} "
        "ORDER BY event_time DESC LIMIT ?", (*params, limit))
    items = []
    for r in rows:
        try:
            sectors = json.loads(r.get("affected_sectors") or "[]")
        except Exception:  # noqa: BLE001
            sectors = []
        items.append({
            "id": r["policy_id"], "title": r["title"], "summary": r["summary"],
            "time": r["event_time"], "date": (r["event_time"] or "")[:10],
            "scope": r["scope"], "country": r["country"], "doc_type": r["doc_type"],
            "source": r["source"], "url": r["url"],
            "affected_sectors": sectors, "impact_level": r["impact_level"] or 1,
            "impact_direction": r["impact_direction"] or "",
            "cached": True,
        })
    empty_reason = ""
    if not items:
        empty_reason = (
            f"近{days}天本地尚无符合筛选的官方政策。"
            "不会编造文件；请等待定时同步或在设置页点击「缓存宏观情报」。"
        )
    return {
        "items": items, "count": len(items), "stats": archive_stats(),
        "filters": {"scope": scope, "country": country, "doc_type": doc_type, "days": days},
        "empty_reason": empty_reason,
    }
