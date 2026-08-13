"""暗盘买卖指标服务（FR2-06）。

数据口径说明：分单四档/大宗交易/龙虎榜接口在当前网络不可达，
按需求文档降级策略采用「主动买卖盘（实时）+ 主力/散户两档结构（批量）」合成，界面标注口径。
"""
from ..cache import cached
from ..database import query
from ..datasources import tencent
from . import metrics as metrics_svc


def get_orderflow(code: str) -> dict | None:
    """内外盘 + 五档委比（盘中 10s 缓存）。"""
    def loader():
        try:
            return tencent.fetch_orderbook(code)
        except Exception:  # noqa: BLE001
            return None
    return cached(f"orderflow:{code}", 10, loader)


def get_dark_power(code: str) -> dict:
    """暗盘力量综合：批量两档口径(65%) + 实时主动买卖盘(35%)。"""
    rows = query("SELECT dark_power, divergence FROM stock_metrics WHERE code=?", (code,))
    base = rows[0]["dark_power"] if rows and rows[0]["dark_power"] is not None else None
    divergence = rows[0]["divergence"] if rows else "无"

    flow = get_orderflow(code)
    flow_score = None
    if flow and flow.get("outer_ratio") is not None:
        flow_score = max(0.0, min(100.0, flow["outer_ratio"] * 100))
        if flow.get("order_ratio") is not None:
            flow_score = max(0.0, min(100.0, flow_score * 0.7 + (50 + flow["order_ratio"] / 2) * 0.3))

    if base is not None and flow_score is not None:
        power = round(base * 0.65 + flow_score * 0.35, 1)
        scope = "两档资金+实时买卖盘口径"
    elif base is not None:
        power = base
        scope = "两档资金口径（盘口暂不可用）"
    elif flow_score is not None:
        power = round(flow_score, 1)
        scope = "仅实时买卖盘口径（指标待盘后计算）"
    else:
        return {"code": code, "power": None, "level": "未知", "desc": "数据不足，请先执行指标重算",
                "scope": "无数据", "divergence": divergence, "orderflow": flow}

    level, desc = metrics_svc.dark_level(power)
    return {
        "code": code, "power": power, "level": level, "desc": desc,
        "scope": scope, "divergence": divergence,
        "orderflow": flow,
        "note": "分单四档/大宗交易/龙虎榜数据源暂不可达，已按降级口径计算",
    }
