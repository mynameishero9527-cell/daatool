"""选股策略方案：最佳买点 / 最佳卖点可多方案并行。

方案 A 即现行线上逻辑。SQL 条件全部硬编码在本模块，不接受前端拼 SQL。
财报评级为独立维度，不并入购买指数。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..database import get_meta_json, query, set_meta_json

META_KEY = "strategy_enabled"
DEFAULT_IDS = ["A"]
NAME_OK = "s.name NOT LIKE '%ST%' AND s.name NOT LIKE '%退%'"
PER_PLAN_CAP = 80

_SELECT = """SELECT s.code, s.name, s.pct, s.volume_ratio, s.price, s.main_net_in,
       s.main_net_in_d5, s.pct_d5, s.pct_d20, s.float_mv, s.turnover_rate,
       COALESCE(l.industry, '') AS industry,
       m.buy_index, m.sentiment, m.pos60, m.stabilize_score, m.dark_power,
       m.divergence, m.rsi14, m.macd_gold, m.ma_bull, m.above_ma20, m.macd_bar,
       m.bias20, m.pullback_shrink, m.drawdown60
FROM stock_metrics m
JOIN stock_snapshot s ON s.code = m.code
LEFT JOIN stock_list l ON l.code = s.code
WHERE {where}
ORDER BY {order}
LIMIT ?"""

BUY_INDEX_FORMULA = """购买指数 BuyIndex（FR2-03，0–100，财报评级不并入）：
  F_pos   = clamp((1 − pos60)×100 − max(bias20−8, 0)×3)   // RSI14<30 时再 +8
  F_trend = clamp(50 + 15·ma_bull + 10·above_ma20 + (MACD柱>0 ? 10 : −10) + clamp(pct_d20, −20, 20))
  F_fund  = clamp(50 + clamp(当日主力净流入/流通市值×400, −30, 30) + clamp((量比−1)×10, −15, 15))
  F_sent  = clamp(100 − |情绪温度−60|×1.8)               // 情绪<15 恐慌冰点再 +15
  Dark    = 暗盘力量（见方案 D）
  Env     = 市场环境分
  BuyIndex = clamp(0.25·F_pos + 0.20·F_trend + 0.20·F_fund + 0.15·Dark + 0.10·F_sent + 0.10·Env)
  若过企稳四闸门：BuyIndex += min(6, Stabilize/20)
分档：≥80 极佳买点 · ≥65 较好 · ≥50 中性 · ≥35 偏差 · 否则高风险"""

STABILIZE_FORMULA = """企稳四闸门（FR2-02，四闸全过才计 Stabilize，否则为 NULL）：
  G1 深度：drawdown60 ≥ 25% 且 pos60 ≤ 0.30
  G2 收敛：近10日波动收缩 且 未创新低 且 MA5 走平（斜率 ≥ −0.1%）
  G3 量能：5日均量 < 60日均量×0.75，且近5日出现温和放量阳线（涨幅 1%–6%）
  G4 资金：5日主力净流入 > 0，或当日净流入 > 流通市值×0.3%
  Stabilize = 0.25·(s_depth + s_conv + s_vol + s_fund)"""

DARK_FORMULA = """暗盘力量 Dark（FR2-06，0–100）：
  Dark = 50 + clamp(当日净流入/流通市值×500, −25, 25) + clamp(5日净流入/流通市值×200, −15, 15)
  价跌且主力净流入>0 → 暗中吸筹（Dark+10）
  价涨>1%且主力净流出 → 暗中派发（Dark−10）"""


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    summary: str
    buy_formula: str
    sell_formula: str
    buy_where: str
    sell_where: str
    buy_action: str
    sell_action: str
    buy_order: str = "m.buy_index DESC"
    sell_order: str = "m.buy_index ASC"
    buy_fallback_where: str | None = None
    extra_docs: str = ""


PLANS: dict[str, Plan] = {}


def _reg(plan: Plan) -> Plan:
    PLANS[plan.id] = plan
    return plan


_reg(Plan(
    id="A",
    name="购买指数极值",
    summary="现行线上方案。买点看购买指数高位且主力净流入；卖点看指数低位或资金出逃叠加情绪过热。",
    buy_formula=(
        "主规则：BuyIndex ≥ 80  ∧  主力净流入 main_net_in > 0\n"
        "回退：当日无主规则命中时，BuyIndex ≥ 65  ∧  main_net_in > 0（较好买点观察，非极佳）\n"
        "排除 ST / 退市。观察池（按 BuyIndex 从高到低）仅在方案 A 启用且所有启用方案均无命中时使用，且不得标为极佳买点。"
    ),
    sell_formula=(
        "BuyIndex ≤ 30\n"
        "  ∨  (主力净流入 < −8000 万  ∧  情绪温度 ≥ 80)"
    ),
    buy_where="m.buy_index >= 80 AND s.main_net_in > 0",
    buy_fallback_where="m.buy_index >= 65 AND s.main_net_in > 0",
    sell_where="(m.buy_index <= 30 OR (s.main_net_in < -8000 AND m.sentiment >= 80))",
    buy_action="极佳买点·可分批建仓",
    sell_action="高风险位置·建议回避",
    extra_docs=BUY_INDEX_FORMULA,
))

_reg(Plan(
    id="B",
    name="企稳四闸门",
    summary="低位缩量后资金回流的企稳买点；高位或闸门失效视为卖点。",
    buy_formula=(
        "stabilize_score IS NOT NULL  ∧  main_net_in > 0  ∧  pos60 ≤ 0.45\n"
        "含义：四闸门全过且仍处 60 日区间下半区，当日主力净流入。"
    ),
    sell_formula="pos60 ≥ 0.85  ∧  (情绪温度 ≥ 78  ∨  stabilize_score IS NULL)",
    buy_where="m.stabilize_score IS NOT NULL AND s.main_net_in > 0 AND m.pos60 <= 0.45",
    sell_where="m.pos60 >= 0.85 AND (m.sentiment >= 78 OR m.stabilize_score IS NULL)",
    buy_action="四闸门企稳·可轻仓试探",
    sell_action="高位或闸门失效·减仓回避",
    buy_order="m.stabilize_score DESC",
    extra_docs=STABILIZE_FORMULA,
))

_reg(Plan(
    id="C",
    name="超跌 RSI 反弹",
    summary="RSI 超卖、低位缩量回调且资金回流；超买叠加高乖离兑现。",
    buy_formula=(
        "RSI14 < 32  ∧  pos60 ≤ 0.35  ∧  main_net_in > 0  ∧  pullback_shrink = 1\n"
        "RSI14 = 100 × 近14日上涨幅度 / (上涨幅度+下跌幅度)（本地简易口径）\n"
        "pullback_shrink：近3日跌幅在 (−5%, 0) 且 5日均量 < 20日均量×0.8"
    ),
    sell_formula="RSI14 > 75  ∧  bias20 > 12%\nbias20 = (现价 / MA20 − 1) × 100",
    buy_where=(
        "m.rsi14 IS NOT NULL AND m.rsi14 < 32 AND m.pos60 <= 0.35 "
        "AND s.main_net_in > 0 AND m.pullback_shrink = 1"
    ),
    sell_where="m.rsi14 IS NOT NULL AND m.rsi14 > 75 AND m.bias20 > 12",
    buy_action="超卖缩量·轻仓博弈反弹",
    sell_action="超买高乖离·注意兑现",
    buy_order="m.rsi14 ASC",
    sell_order="m.rsi14 DESC",
))

_reg(Plan(
    id="D",
    name="暗中吸筹",
    summary="价量背离：下跌时主力净流入视为吸筹；上涨时净流出视为派发。",
    buy_formula="divergence = '暗中吸筹'  ∧  Dark ≥ 65  ∧  main_net_in > 0",
    sell_formula="divergence = '暗中派发'  ∧  Dark ≤ 40",
    buy_where="m.divergence = '暗中吸筹' AND m.dark_power >= 65 AND s.main_net_in > 0",
    sell_where="m.divergence = '暗中派发' AND m.dark_power <= 40",
    buy_action="暗中吸筹·可跟踪资金",
    sell_action="暗中派发·警惕减仓",
    buy_order="m.dark_power DESC",
    sell_order="m.dark_power ASC",
    extra_docs=DARK_FORMULA,
))

_reg(Plan(
    id="E",
    name="均线多头 + MACD 金叉",
    summary="趋势跟随：均线多头、站上 MA20、近3日 MACD 金叉且资金流入。",
    buy_formula=(
        "ma_bull = 1  ∧  macd_gold = 1  ∧  above_ma20 = 1  ∧  main_net_in > 0\n"
        "ma_bull：MA5 > MA10 > MA20\n"
        "macd_gold：近3日内 DIF 上穿 DEA\n"
        "MACD柱 = 2×(DIF−DEA)，DIF=EMA12−EMA26，DEA=DIF 的 9 日 EMA"
    ),
    sell_formula="ma_bull = 0  ∧  MACD柱 < 0  ∧  近5日涨跌幅 < −3%",
    buy_where="m.ma_bull = 1 AND m.macd_gold = 1 AND m.above_ma20 = 1 AND s.main_net_in > 0",
    sell_where="m.ma_bull = 0 AND m.macd_bar < 0 AND s.pct_d5 < -3",
    buy_action="趋势金叉·可顺势跟踪",
    sell_action="均线破坏·注意止盈",
    buy_order="m.macd_bar DESC",
))

_reg(Plan(
    id="F",
    name="低位放量回补 / 高位兑现",
    summary="中低位购买指数尚可且放量净流入；走到 60 日高位叠加过热或资金流出则兑现。",
    buy_formula="BuyIndex ≥ 70  ∧  pos60 ≤ 0.50  ∧  量比 ≥ 1.2  ∧  main_net_in > 0",
    sell_formula="pos60 ≥ 0.90  ∧  (情绪温度 ≥ 78  ∨  main_net_in < 0)",
    buy_where="m.buy_index >= 70 AND m.pos60 <= 0.50 AND s.volume_ratio >= 1.2 AND s.main_net_in > 0",
    sell_where="m.pos60 >= 0.90 AND (m.sentiment >= 78 OR s.main_net_in < 0)",
    buy_action="低位放量·可回补观察",
    sell_action="高位过热·建议兑现",
))

_reg(Plan(
    id="G",
    name="量能回踩均线",
    summary="站上 MA20 后缩量回踩、MACD 柱仍红且资金流入，避免追高；放量高乖离减仓。",
    buy_formula=(
        "above_ma20 = 1  ∧  pullback_shrink = 1  ∧  MACD柱 > 0\n"
        "  ∧  main_net_in > 0  ∧  0.35 ≤ pos60 ≤ 0.70"
    ),
    sell_formula="量比 ≥ 2.5  ∧  bias20 > 10%  ∧  情绪温度 ≥ 75",
    buy_where=(
        "m.above_ma20 = 1 AND m.pullback_shrink = 1 AND m.macd_bar > 0 "
        "AND s.main_net_in > 0 AND m.pos60 >= 0.35 AND m.pos60 <= 0.70"
    ),
    sell_where="s.volume_ratio >= 2.5 AND m.bias20 > 10 AND m.sentiment >= 75",
    buy_action="回踩均线·可等企稳加仓",
    sell_action="放量高乖离·减仓防回吐",
))

_reg(Plan(
    id="H",
    name="乖离率超卖",
    summary="相对 MA20 大幅负乖离且仍有资金回流；正乖离过大叠加 RSI 超买卖出。",
    buy_formula=(
        "bias20 ≤ −8%  ∧  RSI14 < 40  ∧  main_net_in > 0  ∧  drawdown60 ≥ 15%\n"
        "bias20 = (现价 / MA20 − 1) × 100"
    ),
    sell_formula="bias20 ≥ 15%  ∧  RSI14 > 70",
    buy_where=(
        "m.bias20 <= -8 AND m.rsi14 IS NOT NULL AND m.rsi14 < 40 "
        "AND s.main_net_in > 0 AND m.drawdown60 >= 15"
    ),
    sell_where="m.bias20 >= 15 AND m.rsi14 IS NOT NULL AND m.rsi14 > 70",
    buy_action="负乖离超卖·可分批试探",
    sell_action="正乖离过大·注意回归",
    buy_order="m.bias20 ASC",
    sell_order="m.bias20 DESC",
))

PLAN_ORDER = list(PLANS.keys())


def _normalize_ids(ids) -> list[str]:
    if not isinstance(ids, (list, tuple)):
        return list(DEFAULT_IDS)
    out = []
    for raw in ids:
        pid = str(raw or "").strip().upper()
        if pid in PLANS and pid not in out:
            out.append(pid)
    return out or list(DEFAULT_IDS)


def get_enabled() -> list[str]:
    raw = get_meta_json(META_KEY, None)
    if raw is None:
        return list(DEFAULT_IDS)
    return _normalize_ids(raw)


def set_enabled(ids) -> list[str]:
    enabled = _normalize_ids(ids)
    set_meta_json(META_KEY, enabled)
    return enabled


def _where(fragment: str) -> str:
    return f"{NAME_OK} AND ({fragment})"


def _fetch(where_fragment: str, order: str, limit: int = PER_PLAN_CAP) -> list[dict]:
    sql = _SELECT.format(where=_where(where_fragment), order=order)
    return query(sql, (max(1, min(int(limit or PER_PLAN_CAP), PER_PLAN_CAP)),))


def _count(where_fragment: str) -> int:
    rows = query(
        "SELECT COUNT(*) AS n FROM stock_metrics m "
        "JOIN stock_snapshot s ON s.code = m.code "
        f"WHERE {_where(where_fragment)}"
    )
    return int(rows[0]["n"] if rows else 0)


def _merge(bucket: dict[str, dict], row: dict, pid: str) -> None:
    code = row.get("code")
    if not code:
        return
    if code not in bucket:
        item = dict(row)
        item["plans"] = [pid]
        bucket[code] = item
    elif pid not in bucket[code]["plans"]:
        bucket[code]["plans"].append(pid)


def _sort_hits(items: list[dict], kind: str) -> list[dict]:
    def key(r):
        n = len(r.get("plans") or [])
        bi = r.get("buy_index")
        bi = float(bi) if bi is not None else 0.0
        return (-n, -bi if kind == "buy" else bi)
    items.sort(key=key)
    return items


def plan_caption(pid: str) -> str:
    p = PLANS.get(pid)
    return f"方案{pid}·{p.name}" if p else f"方案{pid}"


def stamp_plans(row: dict, plans: list[str] | None, kind: str) -> dict:
    """给命中行打上可读的方案出处（完整名称，不是只写 A）。"""
    ids = [p for p in (plans or []) if p in PLANS]
    row["plans"] = ids
    row["plan_id"] = ",".join(ids)
    row["plan_labels"] = [plan_caption(p) for p in ids]
    row["plan_names"] = "、".join(row["plan_labels"])
    if kind == "buy":
        actions = [PLANS[p].buy_action for p in ids]
    else:
        actions = [PLANS[p].sell_action for p in ids]
    row["hit_action"] = "；".join(actions) if actions else ""
    if ids:
        row["picked_by"] = row["plan_names"]
        row["picked_text"] = f"由{'、'.join(row['plan_labels'])}选出"
    else:
        row["picked_by"] = ""
        row["picked_text"] = "观察池（非策略命中）"
    return row


def _annotate(items: list[dict], kind: str) -> list[dict]:
    for r in items:
        stamp_plans(r, r.get("plans") or [], kind)
    return items


def _fair_take(items: list[dict], enabled: list[str], limit: int) -> list[dict]:
    """多方案并行时按方案轮询取数，避免只显示某一方案的头部标的。"""
    limit = max(1, min(int(limit or 8), 80))
    if len(items) <= limit:
        return items
    if not enabled:
        return items[:limit]
    used: set[str] = set()
    out: list[dict] = []
    queues = {pid: [r for r in items if pid in (r.get("plans") or [])] for pid in enabled}
    cursor = {pid: 0 for pid in enabled}
    while len(out) < limit:
        progressed = False
        for pid in enabled:
            q = queues.get(pid) or []
            i = cursor.get(pid, 0)
            while i < len(q):
                r = q[i]
                i += 1
                code = r.get("code")
                if not code or code in used:
                    continue
                out.append(r)
                used.add(code)
                progressed = True
                break
            cursor[pid] = i
            if len(out) >= limit:
                return out
        if not progressed:
            break
    for r in items:
        code = r.get("code")
        if not code or code in used:
            continue
        out.append(r)
        used.add(code)
        if len(out) >= limit:
            break
    return out


def collect_hits(kind: str, enabled: list[str] | None = None, limit: int = PER_PLAN_CAP) -> list[dict]:
    """启用方案并集。同一代码命中多个方案只保留一行并叠加方案出处。"""
    if kind not in ("buy", "sell"):
        raise ValueError("kind must be buy or sell")
    enabled = enabled if enabled is not None else get_enabled()
    bucket: dict[str, dict] = {}
    for pid in enabled:
        plan = PLANS.get(pid)
        if not plan:
            continue
        fragment = plan.buy_where if kind == "buy" else plan.sell_where
        order = plan.buy_order if kind == "buy" else plan.sell_order
        for row in _fetch(fragment, order):
            _merge(bucket, row, pid)
    items = _annotate(_sort_hits(list(bucket.values()), kind), kind)
    return _fair_take(items, enabled, limit)


def collect_buy_points(limit: int = 12) -> tuple[list[dict], str, str]:
    """实时买点窗：主规则并集 →（仅当全空且 A 启用）A 的 65 回退 → 观察池。"""
    limit = max(3, min(int(limit or 12), 40))
    enabled = get_enabled()
    items = collect_hits("buy", enabled, limit=limit)
    source, note = "hit", _hit_note(enabled, "buy")
    if items:
        return items, source, note

    plan_a = PLANS["A"]
    if "A" in enabled and plan_a.buy_fallback_where:
        bucket: dict[str, dict] = {}
        for row in _fetch(plan_a.buy_fallback_where, plan_a.buy_order):
            _merge(bucket, row, "A")
        items = _annotate(_sort_hits(list(bucket.values()), "buy"), "buy")[:limit]
        if items:
            note = (
                "启用方案主规则暂无命中，已按方案 A 回退：购买指数≥65 且主力净流入>0"
                "（较好买点观察池，非极佳买点）。不构成投资建议"
            )
            return items, "relaxed_65", note

    if "A" in enabled:
        rows = _fetch("m.buy_index IS NOT NULL", "m.buy_index DESC", limit)
        for r in rows:
            stamp_plans(r, [], "buy")
            r["hit_action"] = "观察池，非策略命中"
        if rows:
            note = (
                "启用方案暂无「规则命中且主力净流入」组合，已按购买指数从高到低展示观察池"
                "（不标为极佳买点，不构成买入建议）"
            )
            return rows[:limit], "top_buy_index", note

    names = "、".join(plan_caption(i) for i in enabled)
    return [], "empty", f"当前启用方案（{names}）暂无买点命中，请确认已同步行情并重建指标"


def collect_sell_points(limit: int = 12) -> tuple[list[dict], str, str]:
    limit = max(3, min(int(limit or 12), 40))
    enabled = get_enabled()
    items = collect_hits("sell", enabled, limit=limit)
    if items:
        return items, "hit", _hit_note(enabled, "sell")
    names = "、".join(plan_caption(i) for i in enabled)
    return [], "empty", f"当前启用方案（{names}）暂无卖点命中"


def _hit_note(enabled: list[str], kind: str) -> str:
    labels = "、".join(plan_caption(i) for i in enabled)
    verb = "买点" if kind == "buy" else "卖点"
    if len(enabled) > 1:
        return (
            f"并行方案 {labels} 取并集：命中任一方案即入选；同一标的会注明全部选出方案。"
            f"不构成投资建议"
        )
    return f"当前执行{labels} 的{verb}规则。每条个股会标明由该方案选出。不构成投资建议"


def plan_counts() -> dict[str, dict[str, int]]:
    out = {}
    for pid, plan in PLANS.items():
        out[pid] = {"buy": _count(plan.buy_where), "sell": _count(plan.sell_where)}
    return out


def executing_text(enabled: list[str] | None = None) -> dict:
    enabled = enabled if enabled is not None else get_enabled()
    names = [f"{pid} {PLANS[pid].name}" for pid in enabled]
    title = ("方案 " + "、".join(names) + " 并行") if len(enabled) > 1 else f"方案 {names[0]}"
    blocks = [
        f"当前执行：{title}",
        "并行规则：启用方案同时扫描，买点/卖点取并集；同一代码命中多个方案只显示一行，并注明全部选出方案。",
        "财报评级为独立维度，不并入购买指数，也不作为本菜单的买卖条件。",
        "",
    ]
    for pid in enabled:
        p = PLANS[pid]
        blocks.append(f"—— 方案 {pid} {p.name} ——")
        blocks.append(p.summary)
        blocks.append("买点：" + p.buy_formula)
        blocks.append("卖点：" + p.sell_formula)
        if p.extra_docs:
            blocks.append(p.extra_docs)
        blocks.append("")
    if "A" in enabled:
        blocks.append("方案 A 说明：主规则与历史线上一致（购买指数≥80 且主力净流入>0）；"
                      "买点窗在全市场无主规则命中时才回退到≥65 或观察池。定时提醒只推主规则命中。")
    return {
        "ids": enabled,
        "title": title,
        "detail": "\n".join(blocks).strip(),
        "parallel": len(enabled) > 1,
    }


def catalog() -> list[dict]:
    counts = plan_counts()
    enabled = set(get_enabled())
    rows = []
    for pid in PLAN_ORDER:
        p = PLANS[pid]
        c = counts.get(pid) or {"buy": 0, "sell": 0}
        rows.append({
            "id": p.id,
            "name": p.name,
            "summary": p.summary,
            "buy_formula": p.buy_formula,
            "sell_formula": p.sell_formula,
            "extra_docs": p.extra_docs,
            "buy_action": p.buy_action,
            "sell_action": p.sell_action,
            "enabled": p.id in enabled,
            "is_default": p.id == "A",
            "buy_count": c["buy"],
            "sell_count": c["sell"],
        })
    return rows


def get_config() -> dict:
    enabled = get_enabled()
    exe = executing_text(enabled)
    return {
        "enabled": enabled,
        "parallel": len(enabled) > 1,
        "plans": catalog(),
        "executing": exe,
        "note": (
            "可多选并行。买点/卖点取启用方案并集；多方案命中同一标的会叠加标签。"
            "财报评级不并入购买指数。空列表将强制回退为方案 A。"
        ),
    }
