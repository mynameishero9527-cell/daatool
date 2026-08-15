"""公司公告板块（FR7-04）：从 7x24 快讯库识别公告类消息，支持个股过滤 + 评级联动。

口径：来源为实时快讯识别（非交易所全量公告），界面标注。
"""
from ..database import query
from . import macro, market
from . import rating as rating_svc

_ANN_KEYWORDS = [
    "业绩预告", "业绩快报", "年报", "季报", "半年报", "回购", "增持", "减持",
    "中标", "签署", "签订", "合同", "停牌", "复牌", "立案", "调查", "警示",
    "处罚", "分红", "派息", "送转", "解禁", "质押", "冻结", "重组", "并购",
    "收购", "定增", "配股", "辞职", "变更", "澄清", "诉讼", "担保", "商誉",
]


def _classify(text: str) -> str:
    for kw in _ANN_KEYWORDS:
        if kw in text:
            return kw
    return "公告"


def get_announcements(code: str = "", limit: int = 60) -> dict:
    """公告流。code 传入时按公司名过滤并附机构评级。"""
    news = macro.get_news(200)
    anns = [n for n in news if any(k in n["text"] for k in _ANN_KEYWORDS)]

    target_name, ratings = "", None
    if code:
        norm = market.normalize_code(code) or code
        rows = query("SELECT name FROM stock_list WHERE code=?", (norm,))
        if rows:
            target_name = rows[0]["name"]
            anns = [n for n in anns if target_name in n["text"] or norm[-6:] in n["text"]]
            m = query("SELECT buy_index FROM stock_metrics WHERE code=?", (norm,))
            base_score = m[0]["buy_index"] if m and m[0]["buy_index"] is not None else 50
            ratings = rating_svc.broker_ratings(norm, base_score)
        else:
            anns = []

    items = [{
        "id": n.get("id") or "",
        "text": n["text"], "time": n.get("time", ""),
        "tag": _classify(n["text"]),
        "direction": n["impact_direction"], "impact_desc": n["impact_desc"],
        "impact_level": n["impact_level"], "brief": n.get("brief", ""),
        "affected_sectors": n.get("affected_sectors", []),
        "score": n.get("score"),
    } for n in anns[:limit]]
    return {
        "items": items, "target_name": target_name, "ratings": ratings,
        "note": "公告源为 7x24 快讯识别，非交易所全量公告，仅供参考",
    }
