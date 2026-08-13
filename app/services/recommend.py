"""个股推荐服务：基于本地全市场快照的多维度榜单（产品文档 §2.6）。"""
from ..cache import cached
from ..database import query
from . import rating

BOARDS = {
    "main_buy": "机构买入最多",
    "retail_buy": "散户买入最多",
    "main_sell": "卖出最多",
    "strong_break": "强势突破",
    "oversold": "超跌反弹",
    "composite": "综合推荐",
    "hot_turnover": "活跃成交",
}

_BASE_FILTER = "price IS NOT NULL AND pct IS NOT NULL AND name NOT LIKE '%ST%' AND name NOT LIKE '%退%'"


def _rows(sql: str, params: tuple = (), limit: int = 50) -> list[dict]:
    return query(sql + " LIMIT ?", (*params, limit))


def get_board(board: str, limit: int = 50) -> dict:
    if board not in BOARDS:
        board = "composite"

    def loader():
        return {"board": board, "title": BOARDS[board], "items": _build(board, limit)}

    return cached(f"recommend:{board}:{limit}", 120, loader)


def _build(board: str, limit: int) -> list[dict]:
    if board == "main_buy":
        rows = _rows(
            f"SELECT * FROM stock_snapshot WHERE {_BASE_FILTER} AND main_net_in IS NOT NULL "
            "ORDER BY main_net_in DESC", limit=limit)
        metric = ("主力净流入(亿)", lambda r: _yi(r["main_net_in"]))
    elif board == "retail_buy":
        # 散户净买入 ≈ 成交额中主力之外的净流入；用主力净流出且价涨作为散户接力特征
        rows = _rows(
            f"SELECT * FROM stock_snapshot WHERE {_BASE_FILTER} AND main_net_in IS NOT NULL "
            "AND pct > 0 ORDER BY main_net_in ASC", limit=limit)
        metric = ("散户净买入(亿,估)", lambda r: _yi(-(r["main_net_in"] or 0)))
    elif board == "main_sell":
        rows = _rows(
            f"SELECT * FROM stock_snapshot WHERE {_BASE_FILTER} AND main_net_in IS NOT NULL "
            "ORDER BY main_net_in ASC", limit=limit)
        metric = ("主力净卖出(亿)", lambda r: _yi(-(r["main_net_in"] or 0)))
    elif board == "strong_break":
        rows = _rows(
            f"SELECT * FROM stock_snapshot WHERE {_BASE_FILTER} AND volume_ratio >= 1.5 "
            "AND pct > 2 AND pct_d20 IS NOT NULL ORDER BY pct_d20 DESC", limit=limit)
        metric = ("20日涨幅%", lambda r: r["pct_d20"])
    elif board == "oversold":
        rows = _rows(
            f"SELECT * FROM stock_snapshot WHERE {_BASE_FILTER} AND pct_d20 <= -15 "
            "AND pct > 0 AND main_net_in > 0 ORDER BY pct_d20 ASC", limit=limit)
        metric = ("20日跌幅%", lambda r: r["pct_d20"])
    elif board == "hot_turnover":
        rows = _rows(
            f"SELECT * FROM stock_snapshot WHERE {_BASE_FILTER} AND amount IS NOT NULL "
            "ORDER BY amount DESC", limit=limit)
        metric = ("成交额(亿)", lambda r: _yi(r["amount"]))
    else:  # composite
        rows = _rows(
            f"""SELECT * FROM stock_snapshot WHERE {_BASE_FILTER}
                AND main_net_in IS NOT NULL AND volume_ratio IS NOT NULL
                ORDER BY (COALESCE(pct_d5,0) * 1.5 + COALESCE(pct_d20,0) * 0.5
                          + main_net_in / 5000.0
                          + (volume_ratio - 1) * 8) DESC""", limit=limit)
        metric = ("综合动量", lambda r: round(
            (r["pct_d5"] or 0) * 1.5 + (r["pct_d20"] or 0) * 0.5
            + (r["main_net_in"] or 0) / 5000.0 + ((r["volume_ratio"] or 1) - 1) * 8, 1))

    metric_name, metric_fn = metric
    items = []
    for r in rows:
        score, advice = _quick_score(r)
        items.append({
            "code": r["code"], "name": r["name"], "price": r["price"], "pct": r["pct"],
            "metric_name": metric_name, "metric_value": metric_fn(r),
            "volume_ratio": r["volume_ratio"], "turnover_rate": r["turnover_rate"],
            "score": score, "grade": rating.grade_of(score)["name"],
            "advice": advice,
            "volume_desc": rating.volume_desc(r["volume_ratio"]),
        })
    return items


def _yi(wan: float | None) -> float | None:
    return round(wan / 10000, 2) if wan is not None else None


def _quick_score(r: dict) -> tuple[float, str]:
    """榜单用轻量评分（不含消息面回路，避免逐股全量计算）。"""
    tech = 50.0
    for pct, w in ((r["pct"], 2.0), (r["pct_d5"], 1.5), (r["pct_d20"], 1.0), (r["pct_d60"], 0.5)):
        if pct is not None:
            tech += pct * w
    tech = max(0, min(100, tech))
    fund = 50.0
    if r["main_net_in"] is not None and r["float_mv"]:
        fund += max(-30, min(30, r["main_net_in"] / (r["float_mv"] * 10000) * 100 * 400))
    if r["volume_ratio"] is not None:
        fund += max(-15, min(15, (r["volume_ratio"] - 1) * 10))
    fund = max(0, min(100, fund))
    score = round(tech * 0.55 + fund * 0.45, 1)
    main_in = r["main_net_in"] or 0
    advice = "增持" if score >= 70 and main_in > 0 else "减持" if score <= 44 else "保持不变"
    return score, advice
