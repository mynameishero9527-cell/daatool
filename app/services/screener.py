"""个股筛选器（FR2-01）：多条件组合筛选，基于本地快照+指标表，全部 SQL 本地执行。"""
import json
from datetime import datetime

from ..database import execute, query
from . import metrics as metrics_svc

# 档位定义：前端平铺按钮直接引用这些 key
BUCKETS = {
    "mv": {  # 流通市值（亿）
        "lt50": "s.float_mv < 50", "50to100": "s.float_mv BETWEEN 50 AND 100",
        "100to500": "s.float_mv BETWEEN 100 AND 500", "500to1000": "s.float_mv BETWEEN 500 AND 1000",
        "gt1000": "s.float_mv > 1000",
    },
    "pct_today": {
        "limit_up": "s.pct >= 9.8", "gt5": "s.pct > 5", "0to5": "s.pct BETWEEN 0 AND 5",
        "neg5to0": "s.pct BETWEEN -5 AND 0", "ltneg5": "s.pct < -5", "limit_down": "s.pct <= -9.8",
    },
    "pct_d5": {"gt10": "s.pct_d5 > 10", "0to10": "s.pct_d5 BETWEEN 0 AND 10",
               "neg10to0": "s.pct_d5 BETWEEN -10 AND 0", "ltneg10": "s.pct_d5 < -10"},
    "pct_d20": {"gt20": "s.pct_d20 > 20", "0to20": "s.pct_d20 BETWEEN 0 AND 20",
                "neg20to0": "s.pct_d20 BETWEEN -20 AND 0", "ltneg20": "s.pct_d20 < -20"},
    "turnover": {
        "lt1": "s.turnover_rate < 1", "1to3": "s.turnover_rate BETWEEN 1 AND 3",
        "3to7": "s.turnover_rate BETWEEN 3 AND 7", "7to15": "s.turnover_rate BETWEEN 7 AND 15",
        "gt15": "s.turnover_rate > 15",
    },
    "volume_ratio": {
        "ground": "s.volume_ratio < 0.5", "shrink": "s.volume_ratio BETWEEN 0.5 AND 0.8",
        "normal": "s.volume_ratio BETWEEN 0.8 AND 1.5",
        "mild": "s.volume_ratio BETWEEN 1.5 AND 2.5", "surge": "s.volume_ratio >= 2.5",
    },
    "main_flow": {
        "gt1yi": "s.main_net_in > 10000", "gt5kw": "s.main_net_in > 5000",
        "inflow": "s.main_net_in > 0", "outflow": "s.main_net_in < 0",
        "big_out": "s.main_net_in < -5000",
    },
    "main_flow_d5": {"in5": "s.main_net_in_d5 > 0", "out5": "s.main_net_in_d5 < 0"},
    "pe": {"lt15": "s.pe_ttm > 0 AND s.pe_ttm < 15", "15to30": "s.pe_ttm BETWEEN 15 AND 30",
           "30to60": "s.pe_ttm BETWEEN 30 AND 60", "gt60": "s.pe_ttm > 60",
           "loss": "(s.pe_ttm <= 0 OR s.pe_ttm IS NULL)"},
    "pb": {"lt1": "s.pb > 0 AND s.pb < 1", "1to3": "s.pb BETWEEN 1 AND 3",
           "3to8": "s.pb BETWEEN 3 AND 8", "gt8": "s.pb > 8"},
    "tech": {  # 技术形态（依赖指标表）
        "ma_bull": "m.ma_bull = 1", "above_ma20": "m.above_ma20 = 1",
        "break20_high": "m.break20_high = 1", "pullback_shrink": "m.pullback_shrink = 1",
        "macd_gold": "m.macd_gold = 1", "rsi_oversold": "m.rsi14 < 30",
        "stabilized": "m.stabilize_score IS NOT NULL",
        "dark_buy": "m.divergence = '暗中吸筹'",
    },
}

BUCKET_LABELS = {
    "mv": {"lt50": "<50亿", "50to100": "50-100亿", "100to500": "100-500亿",
           "500to1000": "500-1000亿", "gt1000": ">1000亿"},
    "pct_today": {"limit_up": "涨停", "gt5": ">5%", "0to5": "0~5%", "neg5to0": "-5~0%",
                  "ltneg5": "<-5%", "limit_down": "跌停"},
    "pct_d5": {"gt10": ">10%", "0to10": "0~10%", "neg10to0": "-10~0%", "ltneg10": "<-10%"},
    "pct_d20": {"gt20": ">20%", "0to20": "0~20%", "neg20to0": "-20~0%", "ltneg20": "<-20%"},
    "turnover": {"lt1": "<1%", "1to3": "1-3%", "3to7": "3-7%", "7to15": "7-15%", "gt15": ">15%"},
    "volume_ratio": {"ground": "地量", "shrink": "缩量", "normal": "平稳", "mild": "温和放量", "surge": "显著放量"},
    "main_flow": {"gt1yi": "净流入>1亿", "gt5kw": "净流入>5千万", "inflow": "净流入",
                  "outflow": "净流出", "big_out": "大幅流出"},
    "main_flow_d5": {"in5": "5日持续流入", "out5": "5日持续流出"},
    "pe": {"lt15": "<15", "15to30": "15-30", "30to60": "30-60", "gt60": ">60", "loss": "亏损"},
    "pb": {"lt1": "破净(<1)", "1to3": "1-3", "3to8": "3-8", "gt8": ">8"},
    "tech": {"ma_bull": "均线多头", "above_ma20": "站上20日线", "break20_high": "突破20日新高",
             "pullback_shrink": "缩量回调", "macd_gold": "MACD金叉", "rsi_oversold": "RSI超卖",
             "stabilized": "底部企稳", "dark_buy": "暗中吸筹"},
}

BOARD_MAP = {"主板": "主板", "创业板": "创业板", "科创板": "科创板", "北交所": "北交所"}

PRESETS = [
    {"name": "低估蓝筹", "conditions": {"pe": ["lt15"], "pb": ["lt1", "1to3"], "mv": ["500to1000", "gt1000"],
                                        "exclude_st": True}},
    {"name": "主力吸筹", "conditions": {"tech": ["dark_buy"], "main_flow_d5": ["in5"], "exclude_st": True}},
    {"name": "强势突破", "conditions": {"tech": ["break20_high", "ma_bull"],
                                        "volume_ratio": ["mild", "surge"], "exclude_st": True}},
    {"name": "超跌企稳", "conditions": {"tech": ["stabilized"], "exclude_st": True}},
]


def run(conditions: dict, limit: int = 100) -> dict:
    """执行筛选。conditions 结构：
    {boards:[], industries:[], mv:[], price_min, price_max, exclude_st, exclude_new,
     pct_today:[], pct_d5:[], pct_d20:[], turnover:[], volume_ratio:[],
     main_flow:[], main_flow_d5:[], pe:[], pb:[], tech:[], order_by}
    """
    where, params = ["s.price IS NOT NULL"], []

    if conditions.get("exclude_st", True):
        where.append("s.name NOT LIKE '%ST%' AND s.name NOT LIKE '%退%'")
    boards = conditions.get("boards") or []
    if boards:
        where.append(f"l.board IN ({','.join('?' * len(boards))})")
        params.extend(boards)
    industries = conditions.get("industries") or []
    if industries:
        where.append(f"l.industry IN ({','.join('?' * len(industries))})")
        params.extend(industries)
    if conditions.get("price_min") not in (None, ""):
        where.append("s.price >= ?")
        params.append(float(conditions["price_min"]))
    if conditions.get("price_max") not in (None, ""):
        where.append("s.price <= ?")
        params.append(float(conditions["price_max"]))

    for group, defs in BUCKETS.items():
        keys = conditions.get(group) or []
        clauses = [defs[k] for k in keys if k in defs]
        if clauses:
            where.append("(" + " OR ".join(clauses) + ")")

    order = {
        "buy_index": "m.buy_index DESC", "score": "m.buy_index DESC",
        "pct": "s.pct DESC", "main_flow": "s.main_net_in DESC",
        "dark": "m.dark_power DESC", "stabilize": "m.stabilize_score DESC",
    }.get(conditions.get("order_by", "buy_index"), "m.buy_index DESC")

    sql = f"""
        SELECT s.code, s.name, l.board, l.industry, s.price, s.pct, s.pct_d5, s.pct_d20,
               s.turnover_rate, s.volume_ratio, s.pe_ttm, s.pb, s.float_mv, s.main_net_in,
               m.buy_index, m.sentiment, m.dark_power, m.stabilize_score, m.divergence, m.rsi14
        FROM stock_snapshot s
        JOIN stock_list l ON l.code = s.code
        LEFT JOIN stock_metrics m ON m.code = s.code
        WHERE {' AND '.join(where)}
        ORDER BY {order} NULLS LAST
        LIMIT ?"""
    rows = query(sql, (*params, limit))
    for r in rows:
        r["buy_level"] = metrics_svc.buy_index_level(r["buy_index"])[0] if r["buy_index"] is not None else None
        r["sent_level"] = metrics_svc.sentiment_level(r["sentiment"])[0] if r["sentiment"] is not None else None
    from . import wuxing
    wuxing.tags_for_list(rows)
    return {"total": len(rows), "items": rows}


def industries_list() -> list[str]:
    rows = query("SELECT DISTINCT industry FROM stock_list WHERE industry != '' ORDER BY industry")
    return [r["industry"] for r in rows]


def meta() -> dict:
    return {
        "buckets": BUCKET_LABELS,
        "boards": list(BOARD_MAP),
        "industries": industries_list(),
        "presets": PRESETS,
    }


# ---------------- 语义解析（FR8-05 / FR8-06-2） ----------------

_SEMANTIC_RULES: list[tuple[tuple, str, list[str]]] = [
    (("低估值", "便宜", "低市盈"), "pe", ["lt15"]),
    (("破净",), "pb", ["lt1"]),
    (("放量", "量能放大"), "volume_ratio", ["mild", "surge"]),
    (("缩量",), "volume_ratio", ["ground", "shrink"]),
    (("涨停",), "pct_today", ["limit_up"]),
    (("跌停",), "pct_today", ["limit_down"]),
    (("大涨", "强势"), "pct_today", ["gt5"]),
    (("超跌",), "pct_d20", ["ltneg20"]),
    (("企稳", "止跌"), "tech", ["stabilized"]),
    (("主力流入", "资金流入", "主力买入"), "main_flow", ["inflow"]),
    (("主力流出", "资金流出"), "main_flow", ["outflow"]),
    (("吸筹", "暗中吸筹"), "tech", ["dark_buy"]),
    (("小市值", "小盘"), "mv", ["lt50", "50to100"]),
    (("大市值", "大盘股", "权重"), "mv", ["500to1000", "gt1000"]),
    (("中盘",), "mv", ["100to500"]),
    (("高换手", "活跃"), "turnover", ["7to15", "gt15"]),
    (("金叉",), "tech", ["macd_gold"]),
    (("新高", "突破"), "tech", ["break20_high"]),
    (("多头", "均线多头"), "tech", ["ma_bull"]),
    (("超卖",), "tech", ["rsi_oversold"]),
    (("回调",), "tech", ["pullback_shrink"]),
    (("持续流入",), "main_flow_d5", ["in5"]),
]

_SEMANTIC_LABELS = {
    "pe": "PE", "pb": "PB", "volume_ratio": "量能", "pct_today": "今日涨跌",
    "pct_d20": "20日涨跌", "tech": "形态", "main_flow": "主力资金",
    "main_flow_d5": "5日资金", "mv": "市值", "turnover": "换手",
}


def parse_semantic(text: str) -> dict:
    """自然语言 → 筛选条件 JSON + 解析说明。"""
    text = text.strip()
    conditions: dict = {"exclude_st": True}
    explains: list[str] = []

    for board in BOARD_MAP:
        if board in text:
            conditions.setdefault("boards", []).append(board)
            explains.append(f"板块={board}")
    for ind in industries_list():
        if ind and ind in text:
            conditions.setdefault("industries", []).append(ind)
            explains.append(f"行业={ind}")
    # 概念题材（按活跃概念名匹配）
    concepts = [r["concept"] for r in query(
        "SELECT concept FROM concept_board ORDER BY turnover DESC LIMIT 150")]
    hit_concepts = [c for c in concepts if len(c) >= 2 and c in text]
    if hit_concepts:
        conditions["concepts"] = hit_concepts[:3]
        explains.append("题材=" + "、".join(hit_concepts[:3]))

    for keywords, group, buckets in _SEMANTIC_RULES:
        if any(k in text for k in keywords):
            existing = conditions.setdefault(group, [])
            for b in buckets:
                if b not in existing:
                    existing.append(b)
            labels = BUCKET_LABELS.get(group, {})
            explains.append(f"{_SEMANTIC_LABELS.get(group, group)}={'/'.join(labels.get(b, b) for b in buckets)}")

    return {"conditions": conditions, "explains": explains,
            "summary": (" 且 ".join(explains) if explains else "未解析出规则，请换个描述（如：低估值放量的银行股）")}


def run_semantic(text: str, limit: int = 50) -> dict:
    parsed = parse_semantic(text)
    cond = dict(parsed["conditions"])
    concepts = cond.pop("concepts", [])
    result = run(cond, limit=100)
    items = result["items"]
    if concepts:
        codes = {r["code"] for r in query(
            f"SELECT DISTINCT code FROM concept_map WHERE concept IN ({','.join('?' * len(concepts))})",
            tuple(concepts))}
        items = [i for i in items if i["code"] in codes]
    return {**parsed, "total": len(items[:limit]), "items": items[:limit]}


# ---------------- 方案保存 ----------------

def save_plan(name: str, conditions: dict) -> dict:
    execute("INSERT INTO screener_plan(name,conditions,created_at) VALUES(?,?,?)",
            (name, json.dumps(conditions, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")))
    return {"ok": True}


def list_plans() -> list[dict]:
    rows = query("SELECT plan_id, name, conditions, created_at FROM screener_plan ORDER BY plan_id DESC")
    for r in rows:
        r["conditions"] = json.loads(r["conditions"])
    return rows


def delete_plan(plan_id: int) -> dict:
    execute("DELETE FROM screener_plan WHERE plan_id=?", (plan_id,))
    return {"ok": True}
