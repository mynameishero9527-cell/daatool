"""评分与评级服务：综合评分、量能描述、操作提示、机构评级汇总。

评分权重（产品文档 §5.1）：技术面 40% + 资金面 30% + 基本面 20% + 消息面 10%。
机构评级为规则化模拟数据（基于个股真实指标推导），界面标注「模拟」。
"""
import hashlib
from datetime import date, timedelta

from ..database import query
from . import macro


def _clamp(x: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, x))


def volume_desc(volume_ratio: float | None) -> str:
    if volume_ratio is None:
        return "量能未知"
    if volume_ratio >= 2.5:
        return "显著放量"
    if volume_ratio >= 1.5:
        return "温和放量"
    if volume_ratio >= 0.8:
        return "量能平稳"
    if volume_ratio >= 0.5:
        return "缩量"
    return "地量"


def grade_of(score: float) -> dict:
    table = [
        (85, "强烈看多", "量能充沛", "level-5"),
        (70, "看多", "量能良好", "level-4"),
        (55, "中性偏多", "量能一般", "level-3"),
        (45, "中性", "量能平淡", "level-2"),
        (30, "看空", "量能萎缩", "level-1"),
        (0, "强烈看空", "严重缩量/风险", "level-0"),
    ]
    for threshold, name, vol, css in table:
        if score >= threshold:
            return {"name": name, "vol_hint": vol, "css": css}
    return {"name": "中性", "vol_hint": "量能平淡", "css": "level-2"}


def _news_score(sectors_hit: list[dict]) -> tuple[float, str]:
    """消息面：命中高影响事件加减分。"""
    if not sectors_hit:
        return 50.0, "近期无高影响事件"
    score, reasons = 50.0, []
    for ev in sectors_hit[:3]:
        delta = ev["impact_level"] * 4
        if ev["impact_direction"] == "利好":
            score += delta
            reasons.append(f"利好[{ev['impact_desc']}]")
        elif ev["impact_direction"] == "利空":
            score -= delta
            reasons.append(f"利空[{ev['impact_desc']}]")
    return _clamp(score), "、".join(reasons) or "事件方向中性"


def score_stock(code: str) -> dict:
    """基于本地快照 + 宏观事件计算综合评分与操作提示。"""
    rows = query("SELECT * FROM stock_snapshot WHERE code=?", (code,))
    if not rows:
        return {
            "code": code, "score": None,
            "message": "本地暂无该股快照数据，请先在设置页执行全量同步",
        }
    s = rows[0]

    # 技术面（40%）：多周期动量
    tech = 50.0
    for pct, weight in ((s["pct"], 2.0), (s["pct_d5"], 1.5), (s["pct_d20"], 1.0), (s["pct_d60"], 0.5)):
        if pct is not None:
            tech += pct * weight
    tech = _clamp(tech)

    # 资金面（30%）：主力净流入相对流通市值 + 量比
    fund = 50.0
    if s["main_net_in"] is not None and s["float_mv"]:
        ratio = s["main_net_in"] / (s["float_mv"] * 10000) * 100  # 万元 / 亿元*1e4 → %
        fund += _clamp(ratio * 400, -30, 30)
    if s["volume_ratio"] is not None:
        fund += _clamp((s["volume_ratio"] - 1) * 10, -15, 15)
    fund = _clamp(fund)

    # 基本面（20%）：估值合理性
    fundamental = 50.0
    if s["pe_ttm"] is not None:
        if 0 < s["pe_ttm"] <= 20:
            fundamental += 20
        elif 20 < s["pe_ttm"] <= 40:
            fundamental += 10
        elif s["pe_ttm"] > 80 or s["pe_ttm"] <= 0:
            fundamental -= 20
    if s["pb"] is not None and 0 < s["pb"] <= 2:
        fundamental += 10
    fundamental = _clamp(fundamental)

    # 消息面（10%）：所属板块命中的高影响事件
    events = macro.get_major_events(30)
    news, news_reason = _news_score(events[:2])

    score = round(tech * 0.4 + fund * 0.3 + fundamental * 0.2 + news * 0.1, 1)
    grade = grade_of(score)
    vol_desc = volume_desc(s["volume_ratio"])

    # 操作提示（产品文档 §5.2）
    main_in = s["main_net_in"] or 0
    bad_news = any(e["impact_direction"] == "利空" and e["impact_level"] >= 4 for e in events[:5])
    if score >= 70 and main_in > 0 and not bad_news:
        advice, advice_css = "增持", "advice-buy"
        reason = f"综合评分 {score}，主力净流入 {main_in / 10000:.2f} 亿，{vol_desc}"
    elif score <= 44 or (main_in < -8000 and bad_news):
        advice, advice_css = "减持", "advice-sell"
        reason = f"综合评分 {score}，" + ("主力净流出明显，" if main_in < 0 else "") + f"{vol_desc}"
    else:
        advice, advice_css = "保持不变", "advice-hold"
        reason = f"综合评分 {score}，多空信号均衡，{vol_desc}"

    # 攻守语境（FR6-01-4）
    from . import cycle as cycle_svc
    stance_info = cycle_svc.get_stance()
    stance = stance_info["stance"]
    if stance == "防守":
        reason += f"；当前市场处于防守姿态（恐慌指数 {stance_info['panic']}），建议降低仓位预期"
    elif stance == "进攻":
        reason += f"；当前市场处于进攻姿态（恐慌指数 {stance_info['panic']}），可顺势积极操作"
    else:
        reason += "；市场攻守均衡，结构性参与为主"

    return {
        "code": code, "name": s["name"], "score": score,
        "grade": grade["name"], "grade_css": grade["css"],
        "volume_desc": vol_desc,
        "stance": stance, "stance_desc": stance_info["desc"],
        "advice": advice, "advice_css": advice_css, "advice_reason": reason,
        "components": {
            "技术面": round(tech, 1), "资金面": round(fund, 1),
            "基本面": round(fundamental, 1), "消息面": round(news, 1),
        },
        "news_reason": news_reason,
        "snapshot": {
            "price": s["price"], "pct": s["pct"], "pe_ttm": s["pe_ttm"], "pb": s["pb"],
            "turnover_rate": s["turnover_rate"], "volume_ratio": s["volume_ratio"],
            "float_mv": s["float_mv"], "total_mv": s["total_mv"],
            "main_net_in": s["main_net_in"], "main_net_in_d5": s["main_net_in_d5"],
            "pct_d5": s["pct_d5"], "pct_d20": s["pct_d20"], "pct_d60": s["pct_d60"],
        },
        "broker_ratings": broker_ratings(code, score),
    }


def quick_score(r: dict) -> tuple[float, str]:
    """轻量评分（榜单/持仓列表通用）：技术55% + 资金45%。"""
    tech = 50.0
    for pct_v, w in ((r.get("pct"), 2.0), (r.get("pct_d5"), 1.5),
                     (r.get("pct_d20"), 1.0), (r.get("pct_d60"), 0.5)):
        if pct_v is not None:
            tech += pct_v * w
    tech = _clamp(tech)
    fund = 50.0
    if r.get("main_net_in") is not None and r.get("float_mv"):
        fund += _clamp(r["main_net_in"] / (r["float_mv"] * 10000) * 100 * 400, -30, 30)
    if r.get("volume_ratio") is not None:
        fund += _clamp((r["volume_ratio"] - 1) * 10, -15, 15)
    fund = _clamp(fund)
    score = round(tech * 0.55 + fund * 0.45, 1)
    main_in = r.get("main_net_in") or 0
    advice = "增持" if score >= 70 and main_in > 0 else "减持" if score <= 44 else "保持不变"
    return score, advice


_BROKERS = [
    ("中金公司", "国内券商"), ("中信证券", "国内券商"), ("华泰证券", "国内券商"),
    ("国泰君安", "国内券商"), ("招商证券", "国内券商"),
    ("高盛", "外资投行"), ("摩根士丹利", "外资投行"), ("瑞银", "外资投行"),
    ("野村证券", "外资投行"), ("易方达基金", "公募基金"),
]
_RATING_NAMES = ["买入", "增持", "中性", "减持", "卖出"]


def broker_ratings(code: str, score: float) -> dict:
    """模拟机构评级：以综合评分为中枢、代码哈希扰动，输出评级分布与目标价。"""
    rows = query("SELECT price FROM stock_snapshot WHERE code=?", (code,))
    price = rows[0]["price"] if rows and rows[0]["price"] else 10.0
    items, dist = [], {r: 0 for r in _RATING_NAMES}
    for i, (broker, btype) in enumerate(_BROKERS):
        h = int(hashlib.md5(f"{code}{broker}".encode()).hexdigest()[:6], 16) / 0xFFFFFF
        adj = score + (h - 0.5) * 30
        idx = 0 if adj >= 75 else 1 if adj >= 60 else 2 if adj >= 42 else 3 if adj >= 30 else 4
        rating = _RATING_NAMES[idx]
        dist[rating] += 1
        target = round(price * (1 + (adj - 50) / 100 * 0.6 + (h - 0.5) * 0.08), 2)
        items.append({
            "broker": broker, "type": btype, "rating": rating, "target_price": target,
            "date": (date.today() - timedelta(days=int(h * 85))).isoformat(),
        })
    targets = [it["target_price"] for it in items]
    return {
        "simulated": True,
        "items": sorted(items, key=lambda x: x["date"], reverse=True),
        "distribution": dist,
        "consensus_target": round(sum(targets) / len(targets), 2),
    }
