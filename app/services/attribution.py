"""个股解释性分析（FR7-01）：今日涨跌归因、板块重大事件、利空风险指数。"""
from ..database import query
from . import macro
from . import rating as rating_svc


def _industry_of(code: str) -> str:
    rows = query("SELECT industry FROM stock_list WHERE code=?", (code,))
    return rows[0]["industry"] if rows else ""


def _stock_news(name: str, industry: str, limit: int = 120) -> tuple[list, list]:
    """命中该股/该行业的近端新闻：(个股直达, 行业相关)。"""
    news = macro.get_news(limit)
    direct = [n for n in news if name and name in n["text"]]
    sector_hits = [n for n in news
                   if n.get("affected_sectors") and any(
                       s in industry or industry in s for s in n["affected_sectors"] if industry)]
    return direct[:5], sector_hits[:5]


def get_attribution(code: str) -> dict:
    snap_rows = query("SELECT * FROM stock_snapshot WHERE code=?", (code,))
    if not snap_rows:
        return {"reasons": [], "sector_events": [], "risk_index": None,
                "risk_warning": "", "message": "暂无快照数据"}
    s = snap_rows[0]
    m_rows = query("SELECT * FROM stock_metrics WHERE code=?", (code,))
    m = m_rows[0] if m_rows else {}
    industry = _industry_of(code)
    name = s["name"]
    pct = s["pct"] or 0
    up = pct >= 0

    # ---- 今日涨跌归因（FR7-01-2） ----
    reasons = []
    ind_pct = None
    if industry:
        rows = query("SELECT ROUND(AVG(s2.pct),2) AS p FROM stock_snapshot s2 "
                     "JOIN stock_list l2 ON l2.code=s2.code WHERE l2.industry=?", (industry,))
        ind_pct = rows[0]["p"] if rows else None
    if ind_pct is not None:
        same_dir = (ind_pct >= 0) == up
        reasons.append({
            "type": "板块联动",
            "text": f"所属「{industry}」板块今日平均{'涨' if ind_pct >= 0 else '跌'} {abs(ind_pct)}%，"
                    + ("个股与板块同向，存在板块联动效应" if same_dir else "个股走势独立于板块，属个股行情"),
        })
    main_in = s["main_net_in"] or 0
    if abs(main_in) > 1000:
        reasons.append({
            "type": "资金驱动",
            "text": f"主力资金净{'流入' if main_in > 0 else '流出'} {abs(main_in) / 10000:.2f} 亿"
                    + ("，大资金推动上行" if main_in > 0 and up else
                       "，抛压主导下行" if main_in < 0 and not up else
                       "，与股价方向背离（暗盘特征，关注反转）"),
        })
    vr = s["volume_ratio"]
    if vr is not None:
        reasons.append({
            "type": "量能变化",
            "text": f"{rating_svc.volume_desc(vr)}（量比 {vr}），"
                    + ("放量配合，动能充足" if vr >= 1.5 and up else
                       "放量下跌，出逃迹象" if vr >= 1.5 and not up else
                       "缩量态势，参与度有限" if vr < 0.8 else "量能中性"),
        })
    direct_news, sector_news = _stock_news(name, industry)
    for n in direct_news[:2]:
        reasons.append({
            "type": "消息面",
            "text": f"公司相关消息（{n['impact_direction']}·{n['impact_desc']}）：{n['text'][:60]}…",
        })
    if not direct_news and sector_news:
        n = sector_news[0]
        reasons.append({
            "type": "消息面",
            "text": f"板块消息（{n['impact_direction']}）：{n['text'][:60]}…",
        })
    if not reasons:
        reasons.append({"type": "常规波动", "text": "未发现明显驱动因素，属常规市场波动"})

    # ---- 板块重大事件（FR7-01-3） ----
    sector_events = [{
        "text": n["text"][:90], "direction": n["impact_direction"],
        "level": n["impact_level"], "desc": n["impact_desc"], "time": n.get("time", ""),
    } for n in sector_news if n["impact_level"] >= 3][:4]

    # ---- 利空风险指数（FR7-01-4） ----
    risk = 0.0
    factors = []
    bad_direct = [n for n in direct_news if n["impact_direction"] == "利空"]
    bad_sector = [n for n in sector_news if n["impact_direction"] == "利空" and n["impact_level"] >= 4]
    news_risk = min(35.0, len(bad_direct) * 20 + len(bad_sector) * 10)
    if news_risk:
        factors.append(f"命中利空消息 {len(bad_direct) + len(bad_sector)} 条")
    risk += news_risk
    main_d5 = s["main_net_in_d5"] or 0
    float_mv = s["float_mv"] or 0
    if float_mv and main_d5 < 0:
        outflow_ratio = -main_d5 / (float_mv * 10000)
        flow_risk = min(25.0, outflow_ratio * 100 * 800)
        if flow_risk > 8:
            factors.append(f"5日主力持续净流出 {abs(main_d5) / 10000:.1f} 亿")
        risk += flow_risk
    pos60 = m.get("pos60")
    if pos60 is not None and pos60 > 0.8 and (vr or 0) >= 1.5 and pct <= 0:
        risk += 20
        factors.append("高位放量滞涨，警惕出货")
    pe = s["pe_ttm"]
    if pe is not None and (pe > 100 or pe <= 0):
        risk += 20
        factors.append(f"估值极端（PE {pe if pe > 0 else '亏损'}）")
    risk = round(min(100.0, risk), 0)
    if risk >= 60:
        warning = "⚠️ 当前/未来存在重大利空暴雷潜在风险，建议严格控制仓位"
    elif risk >= 40:
        warning = "存在一定利空风险因素，保持警惕"
    else:
        warning = "暂未发现显著利空风险信号"

    return {
        "reasons": reasons,
        "sector_events": sector_events,
        "industry": industry, "industry_pct": ind_pct,
        "risk_index": risk, "risk_factors": factors, "risk_warning": warning,
    }
