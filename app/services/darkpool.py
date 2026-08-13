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


def get_pull_smash(code: str) -> dict:
    """盘口拉砸分析（FR6-03）：主力拉升/砸盘力度 + 地天/天地板识别。"""
    from . import kline as kline_svc
    from . import market as market_svc

    try:
        minute = kline_svc.get_minute(code)
        points = minute.get("points") or []
        prev_close = minute.get("prev_close")
    except Exception:  # noqa: BLE001
        points, prev_close = [], None
    quote = (market_svc.get_quotes([code]) or {}).get(code) or {}
    prev_close = prev_close or quote.get("prev_close")
    if len(points) < 30 or not prev_close:
        return {"available": False, "desc": "分时数据不足，暂无法分析盘口拉砸"}

    prices = [p[1] for p in points]
    window = 30
    max_rise, rise_at, max_drop, drop_at = 0.0, "", 0.0, ""
    for i in range(window, len(prices)):
        base = prices[i - window]
        if not base:
            continue
        chg = (prices[i] / base - 1) * 100
        if chg > max_rise:
            max_rise, rise_at = chg, points[i][0]
        if chg < max_drop:
            max_drop, drop_at = chg, points[i][0]

    pull_score = round(min(100.0, max_rise / 5 * 100), 0)
    smash_score = round(min(100.0, -max_drop / 5 * 100), 0)

    # 涨跌停价（按板块幅度）
    limit = 0.20 if code[2:].startswith(("30", "68")) else 0.30 if code.startswith("bj") else 0.10
    up_limit = round(prev_close * (1 + limit), 2)
    down_limit = round(prev_close * (1 - limit), 2)
    high = quote.get("high") or max(prices)
    low = quote.get("low") or min(prices)
    price = quote.get("price") or prices[-1]

    touched_up = high >= up_limit * 0.998
    touched_down = low <= down_limit * 1.002
    pattern, pattern_score = "无地天/天地形态", None
    if touched_down and touched_up:
        pattern, pattern_score = ("地天板（跌停翻涨停）" if price >= prev_close else "巨幅震荡（双向触板）"), 100
    elif touched_up and price <= prev_close * (1 - limit * 0.8):
        pattern, pattern_score = "天地板（涨停砸跌停）", 100
    elif touched_up and price < high * 0.95:
        pattern, pattern_score = "炸板回落", 70
    elif touched_down and price > low * 1.05:
        pattern, pattern_score = "跌停撬板", 70

    fmt_t = lambda t: f"{t[:2]}:{t[2:]}" if len(t) == 4 else t
    desc = (f"主力拉升力度 {pull_score:.0f}"
            + (f"（{fmt_t(rise_at)} 快速拉升 +{max_rise:.1f}%）" if max_rise > 0.5 else "")
            + f"，砸盘力度 {smash_score:.0f}"
            + (f"（{fmt_t(drop_at)} 快速下砸 {max_drop:.1f}%）" if max_drop < -0.5 else "")
            + f"；{pattern}")
    return {
        "available": True,
        "pull_score": pull_score, "pull_at": fmt_t(rise_at), "pull_pct": round(max_rise, 2),
        "smash_score": smash_score, "smash_at": fmt_t(drop_at), "smash_pct": round(max_drop, 2),
        "pattern": pattern, "pattern_score": pattern_score,
        "up_limit": up_limit, "down_limit": down_limit,
        "desc": desc,
    }


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
