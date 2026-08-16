"""智能选股（股价未来方向分析）：菜单骨架。

大菜单下「主页」放黄历与上涨/下跌方向；上涨/下跌两个子栏不编造个股涨跌名单。
「预测推荐」是独立 tab 的数据层，另表回显易经卜卦 / 奇门遁甲已筛出的本地个股，
不写入本模块 items，也不混进主页。
大盘方向复用已有启发式明日概率。
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
