"""公司公告板块（FR7-04）：从 7x24 快讯库识别**公司**公告类消息，支持个股过滤 + 评级联动。

口径：来源为实时快讯识别（非交易所全量公告），界面标注。
必须像公司公告（公告称 / XX公告， / 标题含中标增持等），禁止宏观、综述、暗盘传闻。
"""
from __future__ import annotations

import json
import re

from ..database import query
from . import macro, market
from . import rating as rating_svc

_ANN_KEYWORDS = [
    "业绩预告", "业绩快报", "年报", "季报", "半年报", "回购股份", "股份回购",
    "增持", "减持", "中标", "签署", "签订", "合同", "停牌", "复牌", "立案",
    "调查", "警示", "处罚", "分红", "派息", "送转", "解禁", "质押", "冻结",
    "重组", "并购", "收购", "定增", "配股", "辞职", "变更", "澄清", "诉讼",
    "担保", "商誉", "回购",
]

_MACRO_EXCLUDE = (
    "枪支回购", "枪支", "国债", "央行", "美联储", "地质调查", "枪击",
    "航空母舰", "麦加协议", "签署国", "海关总署", "印度政府", "美国ITC",
    "国际贸易委员会", "印尼总统", "澳大利亚",
)

_ROUNDUP = ("据统计", "上百家", "多家公司", "A股共有", "数据显示，截至", "密集出炉")

_HEADLINE_NO = (
    "暗盘", "产业链", "人造太阳", "ITER", "枪支回购", "纪委", "纪律审查",
    "海关总署", "美国ITC", "国际贸易委员会", "印度政府",
)

_SQL_LIKE = (
    "%公告称%", "%发布公告%", "%晚间公告%", "%盘后公告%",
    "%公告，%", "%公告：%", "%公告。%",
    "%股份回购%", "%回购股份%", "%业绩预告%", "%业绩快报%",
    "%董事会%", "%股份有限%", "%中标%", "%增持%", "%减持%",
)


def _classify(text: str) -> str:
    for kw in _ANN_KEYWORDS:
        if kw in text:
            return kw
    return "公告"


def _headline(text: str) -> str:
    t = (text or "").strip()
    m = re.search(r"【([^】]+)】", t)
    return ((m.group(1) if m else t).strip())[:90]


def _title_key(text: str) -> str:
    h = _headline(text)
    return re.sub(r"\s+", "", h)[:48]


def _is_company_announcement(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 8:
        return False
    head = _headline(t)
    if any(x in head for x in _HEADLINE_NO):
        return False
    if any(x in t for x in _MACRO_EXCLUDE) and not re.search(
        r"(股份有限|公告称，公司|上市公司股东)", t
    ):
        return False
    if any(x in t for x in _ROUNDUP) and "公告称" not in t and "发布公告" not in t:
        return False
    if re.search(r"(公告称|发布公告|晚间公告|盘后公告)", t):
        if any(g in head or g in t[:60] for g in (
            "美国ITC", "海关总署", "印度政府", "国际贸易委员会", "政府一份公告",
        )):
            return False
        if "招聘" in head:
            return False
        return True
    if re.search(r"[\u4e00-\u9fa5A-Za-z0-9*ST]{2,16}公告[，：:]", t) and not any(
        g in head for g in ("海关", "政府", "ITC", "国务院", "证监会就")
    ):
        return True
    # 中标/回购看标题；增持减持还要像股东增减持公告，避免「基金经理减持英伟达」
    if re.search(r"(中标|回购股份|股份回购)", head):
        if re.search(r"(海关|政府|中介|暗盘)", head):
            return False
        return True
    if re.search(r"(增持|减持)", head) and re.search(r"(公告|股东|持股|股份)", head):
        return True
    if re.search(r"(业绩预告|业绩快报)", t) and re.search(r"(上市公司|股份有限|公告)", t):
        if "产业链" not in head and "多家" not in head:
            return True
    return False


def _row_to_news(r: dict) -> dict:
    text = r.get("summary") or r.get("text") or ""
    impact = macro.assess_impact(text)
    return {
        "id": r.get("event_id") or r.get("id") or "",
        "text": text,
        "time": r.get("event_time") or r.get("time") or "",
        **impact,
        "source": r.get("source") or "本地事件库",
        "tags": [],
    }


def _db_announce_candidates(limit: int) -> list[dict]:
    where = " OR ".join(["summary LIKE ?"] * len(_SQL_LIKE))
    rows = query(
        f"SELECT * FROM macro_event WHERE event_id LIKE 'news_%' AND ({where}) "
        "ORDER BY event_time DESC LIMIT ?",
        (*_SQL_LIKE, max(limit * 6, 360)),
    )
    return [_row_to_news(dict(r)) for r in rows]


def _news_pool(limit: int = 400) -> list[dict]:
    """实时快讯 + 本地库里像公告的候选，避免最新一批全是宏观新闻时页面空白/错配。"""
    live: list[dict] = []
    try:
        live = macro.get_news(80) or []
    except Exception:  # noqa: BLE001
        live = []
    hist: list[dict] = []
    try:
        hist = _db_announce_candidates(limit)
    except Exception:  # noqa: BLE001
        try:
            hist = macro._news_from_db(max(limit, 200))  # noqa: SLF001
        except Exception:  # noqa: BLE001
            hist = []
    seen_id: set[str] = set()
    seen_title: set[str] = set()
    out: list[dict] = []
    for n in list(live) + list(hist):
        text = n.get("text") or ""
        tid = str(n.get("id") or "") or text[:80]
        tkey = _title_key(text)
        if tid and tid in seen_id:
            continue
        if tkey and tkey in seen_title:
            continue
        if tid:
            seen_id.add(tid)
        if tkey:
            seen_title.add(tkey)
        out.append(n)
    return out


def get_announcements(code: str = "", limit: int = 60) -> dict:
    """公告流。code 传入时按公司名过滤并附机构评级。"""
    news = _news_pool(400)
    anns = [n for n in news if _is_company_announcement(n.get("text") or "")]

    target_name, ratings = "", None
    if code:
        norm = market.normalize_code(code) or code
        rows = query("SELECT name FROM stock_list WHERE code=?", (norm,))
        if rows:
            target_name = rows[0]["name"]
            anns = [n for n in anns if target_name in (n.get("text") or "")
                    or (norm[-6:] in (n.get("text") or ""))]
            m = query("SELECT buy_index FROM stock_metrics WHERE code=?", (norm,))
            base_score = m[0]["buy_index"] if m and m[0]["buy_index"] is not None else 50
            ratings = rating_svc.broker_ratings(norm, base_score)
        else:
            anns = []

    items = []
    for n in anns[:limit]:
        secs = n.get("affected_sectors") or []
        if isinstance(secs, str):
            try:
                secs = json.loads(secs) if secs.startswith("[") else [
                    s for s in re.split(r"[,，、]", secs) if s.strip()
                ]
            except Exception:  # noqa: BLE001
                secs = []
        if not isinstance(secs, list):
            secs = []
        secs = [s if isinstance(s, str) else str(s.get("name") or s) for s in secs]
        secs = [s for s in secs if s and s not in ("未映射影响板块", "政策")]
        items.append({
            "id": n.get("id") or "",
            "text": n.get("text") or "",
            "time": n.get("time") or "",
            "tag": _classify(n.get("text") or ""),
            "direction": n.get("impact_direction") or "中性",
            "impact_desc": n.get("impact_desc") or "",
            "impact_level": n.get("impact_level") or 1,
            "brief": n.get("brief") or "",
            "affected_sectors": secs,
            "score": n.get("score"),
        })
    empty_reason = ""
    if not items:
        empty_reason = (
            "暂无匹配的公司公告。本页从本地快讯库识别「公告称 / XX公告 / 标题中标增持」等公司语境，"
            "不把宏观新闻、行业综述、暗盘传闻算作公告。"
            + (" 当前代码未在本地股票列表中。" if code and not target_name else "")
        )
    return {
        "items": items, "target_name": target_name, "ratings": ratings,
        "note": "公告源为 7x24 快讯识别（含本地库历史），非交易所全量公告；已过滤非公司语境。仅供参考",
        "empty_reason": empty_reason,
    }
