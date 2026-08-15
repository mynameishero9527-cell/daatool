"""智能选股（股价未来方向分析）：菜单骨架。

本轮只建一级菜单与上涨/下跌两个子栏，不编造个股涨跌名单。
大盘方向复用已有启发式明日概率，个股名单待下一轮接入。
"""
from . import forecast as forecast_svc

SIDES = (
    {"id": "up", "name": "上涨预测", "hint": "量化上更可能走强的方向"},
    {"id": "down", "name": "下跌预测", "hint": "量化上更可能走弱的方向"},
)
SIDE_IDS = {s["id"] for s in SIDES}


def get_page(side: str = "up") -> dict:
    side = (side or "up").strip()
    if side not in SIDE_IDS:
        side = "up"
    market = {}
    try:
        market = forecast_svc.get_forecast() or {}
    except Exception:  # noqa: BLE001
        market = {}
    empty_reason = (
        "个股涨跌预测名单尚未接入。本页已创建「上涨预测 / 下跌预测」菜单。"
        "不会用随机名单或当日涨跌幅冒充未来预测。"
    )
    return {
        "ok": True,
        "side": side,
        "sides": list(SIDES),
        "items": [],
        "count": 0,
        "market": {
            "prob_up": market.get("prob_up"),
            "view": market.get("view") or "",
            "desc": market.get("desc") or "",
            "factors": market.get("factors") or [],
            "disclaimer": market.get("disclaimer") or "",
        },
        "note": "智能选股展示股价未来涨跌方向分析。综合打分与策略命中请用「策略选股」。",
        "empty_reason": empty_reason,
        "disclaimer": "分析仅为量化参考，不构成投资建议，不承诺涨跌。",
    }
