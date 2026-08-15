"""公司公告板块（FR7-04）：从 7x24 快讯库识别**公司**公告类消息，支持个股过滤 + 评级联动。

口径：来源为实时快讯识别（非交易所全量公告），界面标注。
必须像公司公告（股份/公告/董事会等），禁止把「枪支回购」「地质调查」等宏观新闻算进来。
"""
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

_COMPANY_HINTS = (
    "公告", "董事会", "监事会", "股东大会", "股份有限", "有限公司", "上市公司",
    "本公司", "控股股东", "实际控制人", "关于", "A股", "深交所", "上交所",
    "北交所", "业绩预告", "业绩快报", "回购股份", "股份回购",
)

_EXCLUDE_IF_NOT_COMPANY = (
    "枪支回购", "枪支", "国债", "央行", "美联储", "地质调查", "枪击",
    "航空母舰", "麦加协议", "签署国",
)


def _classify(text: str) -> str:
    for kw in _ANN_KEYWORDS:
        if kw in text:
            return kw
    return "公告"


def _is_company_announcement(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 8:
        return False
    if any(x in t for x in _EXCLUDE_IF_NOT_COMPANY) and not any(
        h in t for h in ("股份", "公告", "董事会", "上市公司")
    ):
        return False
    if not any(k in t for k in _ANN_KEYWORDS):
        return False
    if any(h in t for h in _COMPANY_HINTS):
        return True
    if re.search(r"(?<!\d)\d{6}(?!\d)", t) and any(
        k in t for k in ("回购", "增持", "减持", "中标", "停牌", "分红", "公告")
    ):
        return True
    return False


def get_announcements(code: str = "", limit: int = 60) -> dict:
    """公告流。code 传入时按公司名过滤并附机构评级。"""
    news = macro.get_news(200)
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
                import json
                secs = json.loads(secs) if secs.startswith("[") else [s for s in re.split(r"[,，、]", secs) if s.strip()]
            except Exception:  # noqa: BLE001
                secs = []
        if not isinstance(secs, list):
            secs = []
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
            "暂无匹配的公司公告。本页只收录带「公告/股份/董事会」等公司语境的快讯，"
            "不把宏观新闻里的「回购/调查/签署」算作公告。"
            + (" 当前代码未在本地股票列表中。" if code and not target_name else "")
        )
    return {
        "items": items, "target_name": target_name, "ratings": ratings,
        "note": "公告源为 7x24 快讯识别，非交易所全量公告；已过滤非公司语境。仅供参考",
        "empty_reason": empty_reason,
    }
