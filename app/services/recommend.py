"""个股推荐服务：基于本地全市场快照+指标表的多维度榜单。"""
from ..cache import cached
from ..database import query
from . import rating
from . import metrics as metrics_svc

BOARDS = {
    "main_buy": "机构买入最多",
    "retail_buy": "散户买入最多",
    "main_sell": "卖出最多",
    "strong_break": "强势突破",
    "oversold": "超跌反弹",
    "stabilize": "企稳待涨",
    "composite": "综合推荐",
    "hot_turnover": "活跃成交",
}

_BASE_FILTER = "s.price IS NOT NULL AND s.pct IS NOT NULL AND s.name NOT LIKE '%ST%' AND s.name NOT LIKE '%退%'"
_SELECT = ("SELECT s.*, m.buy_index, m.sentiment AS senti, m.dark_power, m.stabilize_score, "
           "m.stab_g1, m.stab_g2, m.stab_g3, m.stab_g4, m.divergence "
           "FROM stock_snapshot s LEFT JOIN stock_metrics m ON m.code = s.code ")


def _rows(sql: str, params: tuple = (), limit: int = 50) -> list[dict]:
    return query(sql + " LIMIT ?", (*params, limit))


def get_board(board: str, limit: int = 50, page: int = 1, page_size: int = 20,
              advice: str = "", min_score: float = 0, vol_filter: str = "",
              order_by: str = "") -> dict:
    """榜单：先取足量候选（缓存），再做二级筛选 + 排序 + 分页。"""
    if board not in BOARDS:
        board = "composite"

    def loader():
        payload = {"board": board, "title": BOARDS[board], "items": _build(board, 200)}
        if board == "stabilize":
            payload["stats"] = metrics_svc.stabilize_stats()
        return payload

    data = cached(f"recommend:{board}", 120, loader)
    items = data["items"]

    # 二级维度筛选（FR4-04-2）
    if advice in ("增持", "减持", "保持不变"):
        items = [i for i in items if i["advice"] == advice]
    if min_score:
        items = [i for i in items if (i["score"] or 0) >= min_score]
    if vol_filter == "surge":
        items = [i for i in items if (i["volume_ratio"] or 0) >= 1.5]
    elif vol_filter == "normal":
        items = [i for i in items if 0.8 <= (i["volume_ratio"] or 0) < 1.5]
    elif vol_filter == "shrink":
        items = [i for i in items if (i["volume_ratio"] or 1) < 0.8]

    # 排序优化（FR4-04-3）
    keys = {"score": lambda i: i["score"] or 0,
            "buy_index": lambda i: i["buy_index"] or 0,
            "pct": lambda i: i["pct"] or 0,
            "metric": lambda i: i["metric_value"] or 0}
    if order_by in keys:
        items = sorted(items, key=keys[order_by], reverse=True)

    # 分页（FR4-04-1）
    page_size = page_size if page_size in (20, 50, 100) else 20
    total = len(items)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, pages))
    start = (page - 1) * page_size
    return {
        "board": board, "title": BOARDS[board],
        "items": items[start:start + page_size],
        "total": total, "page": page, "pages": pages, "page_size": page_size,
        **({"stats": data.get("stats")} if data.get("stats") else {}),
    }


def _build(board: str, limit: int) -> list[dict]:
    if board == "main_buy":
        rows = _rows(
            f"{_SELECT} WHERE {_BASE_FILTER} AND s.main_net_in IS NOT NULL "
            "ORDER BY s.main_net_in DESC", limit=limit)
        metric = ("主力净流入(亿)", lambda r: _yi(r["main_net_in"]))
        reason = lambda r: f"主力净流入 {_yi(r['main_net_in'])} 亿，{rating.volume_desc(r['volume_ratio'])}"
    elif board == "retail_buy":
        # 散户净买入 ≈ 成交额中主力之外的净流入；用主力净流出且价涨作为散户接力特征
        rows = _rows(
            f"{_SELECT} WHERE {_BASE_FILTER} AND s.main_net_in IS NOT NULL "
            "AND s.pct > 0 ORDER BY s.main_net_in ASC", limit=limit)
        metric = ("散户净买入(亿,估)", lambda r: _yi(-(r["main_net_in"] or 0)))
        reason = lambda r: f"股价上涨但主力净流出 {_yi(-(r['main_net_in'] or 0))} 亿，散户承接特征"
    elif board == "main_sell":
        rows = _rows(
            f"{_SELECT} WHERE {_BASE_FILTER} AND s.main_net_in IS NOT NULL "
            "ORDER BY s.main_net_in ASC", limit=limit)
        metric = ("主力净卖出(亿)", lambda r: _yi(-(r["main_net_in"] or 0)))
        reason = lambda r: f"主力净卖出 {_yi(-(r['main_net_in'] or 0))} 亿，注意风险"
    elif board == "strong_break":
        rows = _rows(
            f"{_SELECT} WHERE {_BASE_FILTER} AND s.volume_ratio >= 1.5 "
            "AND s.pct > 2 AND s.pct_d20 IS NOT NULL ORDER BY s.pct_d20 DESC", limit=limit)
        metric = ("20日涨幅%", lambda r: r["pct_d20"])
        reason = lambda r: f"放量上攻，20日涨幅 {r['pct_d20']}%，量比 {r['volume_ratio']}"
    elif board == "oversold":
        rows = _rows(
            f"{_SELECT} WHERE {_BASE_FILTER} AND s.pct_d20 <= -15 "
            "AND s.pct > 0 AND s.main_net_in > 0 ORDER BY s.pct_d20 ASC", limit=limit)
        metric = ("20日跌幅%", lambda r: r["pct_d20"])
        reason = lambda r: f"20日超跌 {r['pct_d20']}% 后止跌，主力回流 {_yi(r['main_net_in'])} 亿"
    elif board == "stabilize":
        rows = _rows(
            f"{_SELECT} WHERE {_BASE_FILTER} AND m.stabilize_score IS NOT NULL "
            "ORDER BY m.stabilize_score DESC", limit=limit)
        metric = ("企稳强度", lambda r: r["stabilize_score"])
        reason = lambda r: "通过四道闸门：超跌·收敛·量能确认·资金回流"
    elif board == "hot_turnover":
        rows = _rows(
            f"{_SELECT} WHERE {_BASE_FILTER} AND s.amount IS NOT NULL "
            "ORDER BY s.amount DESC", limit=limit)
        metric = ("成交额(亿)", lambda r: _yi(r["amount"]))
        reason = lambda r: f"成交额 {_yi(r['amount'])} 亿，市场焦点股"
    else:  # composite
        rows = _rows(
            f"""{_SELECT} WHERE {_BASE_FILTER}
                AND s.main_net_in IS NOT NULL AND s.volume_ratio IS NOT NULL
                ORDER BY (COALESCE(s.pct_d5,0) * 1.5 + COALESCE(s.pct_d20,0) * 0.5
                          + s.main_net_in / 5000.0
                          + (s.volume_ratio - 1) * 8) DESC""", limit=limit)
        metric = ("综合动量", lambda r: round(
            (r["pct_d5"] or 0) * 1.5 + (r["pct_d20"] or 0) * 0.5
            + (r["main_net_in"] or 0) / 5000.0 + ((r["volume_ratio"] or 1) - 1) * 8, 1))
        reason = lambda r: f"动量+资金+量能综合居前，5日涨幅 {r['pct_d5']}%"

    metric_name, metric_fn = metric
    items = []
    for r in rows:
        score, advice = _quick_score(r)
        item = {
            "code": r["code"], "name": r["name"], "price": r["price"], "pct": r["pct"],
            "metric_name": metric_name, "metric_value": metric_fn(r),
            "volume_ratio": r["volume_ratio"], "turnover_rate": r["turnover_rate"],
            "score": score, "grade": rating.grade_of(score)["name"],
            "advice": advice,
            "volume_desc": rating.volume_desc(r["volume_ratio"]),
            "buy_index": r.get("buy_index"),
            "sentiment": r.get("senti"),
            "dark_power": r.get("dark_power"),
            "reason": _rich_reason(reason(r), r),
        }
        if r.get("senti") is not None:
            item["sent_level"] = metrics_svc.sentiment_level(r["senti"])[0]
        if board == "stabilize":
            item["gates"] = [bool(r.get(f"stab_g{i}")) for i in (1, 2, 3, 4)]
            item["stars"] = "★★★" if (r["stabilize_score"] or 0) >= 80 else \
                            "★★" if (r["stabilize_score"] or 0) >= 65 else "★"
        items.append(item)
    return items


def _yi(wan: float | None) -> float | None:
    return round(wan / 10000, 2) if wan is not None else None


def _rich_reason(core: str, r: dict) -> str:
    """入选理由增强（FR4-04-4）：核心理由 + 多周期表现 + 量能 + 资金 + 暗盘特征。"""
    parts = [core]
    perf = []
    if r["pct_d5"] is not None:
        perf.append(f"5日{r['pct_d5']:+.1f}%")
    if r["pct_d20"] is not None:
        perf.append(f"20日{r['pct_d20']:+.1f}%")
    if perf:
        parts.append("、".join(perf))
    if r["volume_ratio"] is not None:
        parts.append(f"{rating.volume_desc(r['volume_ratio'])}（量比{r['volume_ratio']:.2f}）")
    if r["turnover_rate"] is not None:
        parts.append(f"换手{r['turnover_rate']:.1f}%")
    main_d5 = r.get("main_net_in_d5")
    if main_d5:
        parts.append(f"5日主力净{'流入' if main_d5 > 0 else '流出'}{abs(main_d5) / 10000:.1f}亿")
    if r.get("divergence") and r["divergence"] != "无":
        parts.append(f"具{r['divergence']}特征")
    return "；".join(dict.fromkeys(parts))


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
