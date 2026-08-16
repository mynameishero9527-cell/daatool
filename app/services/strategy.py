"""选股策略：最佳买点默认第一版「选股方案A」，最佳卖点看止盈/避险。

SQL 条件全部硬编码在本模块，不接受前端拼 SQL。
财报评级为独立维度，不并入购买指数。
空命中必须带回原因，不拿无流入观察池凑数。
方案 I/J 只用已缓存持股/解禁，缺则零命中。
最佳买点默认选股方案A：购买指数≥80 且主力净流入>0；
无主规则命中时回退≥65 且净流入>0（较好观察，非极佳）。
卖点仍须交叉命中至少 2 个方案，并结合近半年真实日K。
买点窗口不套用引擎周线门；未评级不伪造 A。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..database import get_meta_json, query, set_meta_json

META_KEY = "strategy_enabled"  # 兼容旧键：曾同时控制买/卖
META_KEY_BUY = "strategy_enabled_buy"
META_KEY_SELL = "strategy_enabled_sell"
META_KEY_HIDDEN_BUY = "strategy_hidden_buy"
META_KEY_HIDDEN_SELL = "strategy_hidden_sell"
RULES_VER_KEY = "strategy_rules_ver"
RULES_VER = "13.0.41"
DEFAULT_BUY_IDS = ["A"]
DEFAULT_SELL_IDS = ["ST", "SO", "SR"]
DEFAULT_IDS = list(DEFAULT_BUY_IDS)  # 兼容旧测试/调用，仅表示买点默认
MIN_PLAN_HITS = 2
HALF_CAL_DAYS = 180
HALF_MIN_BARS = 60
HALF_MAX_RANGE_BUY = 200.0
NEW_LAST_DAYS = 12
NEW_BUY_MIN_SCORE = 65.0
NEW_SELL_MIN_SCORE = 72.0
NEW_BUY_GRADES = ("A", "B")
BUY_MIN_SCORE = 60.0
BUY_MIN_ROOM = 12.0
BUY_MAX_HALF_POS = 0.68
SELL_MAX_ROOM = 8.0
SELL_TINY_ROOM = 5.0
SELL_MIN_HALF_POS = 0.80
HOT_FLOOR = -12.0
GOOD_GRADES = ("A", "B")
WEAK_GRADES = ("C", "D")
NAME_OK = "s.name NOT LIKE '%ST%' AND s.name NOT LIKE '%退%'"
PER_PLAN_CAP = 80
_TZ = ZoneInfo("Asia/Shanghai")
_EMPTY_BUY_TAIL = (
    "最佳买点按选股方案A：购买指数与主力净流入。无流入的观察池不凑数。"
    "主规则无命中时才回退到购买指数≥65且净流入>0（较好观察，非极佳）。"
)
_EMPTY_SELL_TAIL = (
    "卖点不再用低购买指数弱票凑数。缺日K会即时补真实K线，不用涨跌幅冒充。"
)

# 买点共用安全闸：排除暴跌、情绪过热追高、已在 60 日顶部
_BUY_SAFE = (
    "s.price IS NOT NULL AND s.pct > -5 "
    "AND (m.sentiment IS NULL OR m.sentiment < 85) "
    "AND (m.pos60 IS NULL OR m.pos60 <= 0.78)"
)

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
  Dark    = 暗盘力量
  Env     = 市场环境分
  BuyIndex = clamp(0.25·F_pos + 0.20·F_trend + 0.20·F_fund + 0.15·Dark + 0.10·F_sent + 0.10·Env)
  若过企稳四闸门：BuyIndex += min(6, Stabilize/20)
分档：≥80 极佳 · ≥65 较好 · ≥50 中性 · ≥35 偏差 · 否则高风险。买点策略另加结构过滤，不用分档单独凑名单。"""

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
class SidePlan:
    id: str
    name: str
    summary: str
    formula: str
    where: str
    action: str
    order: str = "m.buy_index DESC"
    extra_docs: str = ""
    default_off: bool = False
    fallback_where: str = ""


BUY_PLANS: dict[str, SidePlan] = {}
SELL_PLANS: dict[str, SidePlan] = {}
PLANS: dict[str, SidePlan] = {}


def _reg_buy(plan: SidePlan) -> SidePlan:
    BUY_PLANS[plan.id] = plan
    PLANS[plan.id] = plan
    return plan


def _reg_sell(plan: SidePlan) -> SidePlan:
    SELL_PLANS[plan.id] = plan
    PLANS[plan.id] = plan
    return plan


def catalog_map(kind: str) -> dict[str, SidePlan]:
    return SELL_PLANS if kind == "sell" else BUY_PLANS


# —— 买点默认：第一版选股方案A ——
_reg_buy(SidePlan(
    id="A",
    name="选股方案A",
    summary="第一版线上买点。购买指数高位且主力净流入；无极佳命中时回退较好观察，不拿无流入名单凑数。",
    formula=(
        "主规则：BuyIndex ≥ 80  ∧  主力净流入 main_net_in > 0\n"
        "回退：当日无主规则命中时，BuyIndex ≥ 65  ∧  main_net_in > 0（较好买点观察，非极佳）\n"
        "排除 ST / 退市。不回退无净流入的观察池。"
    ),
    where="m.buy_index >= 80 AND s.main_net_in > 0",
    fallback_where="m.buy_index >= 65 AND s.main_net_in > 0",
    action="极佳买点·可分批建仓",
    extra_docs=BUY_INDEX_FORMULA,
))

# —— 其余买点方案可并行勾选，不再作为默认 ——
_reg_buy(SidePlan(
    id="BP",
    name="潜力主升",
    summary="中位区间、持续净流入、未超买。可选买点，用来找还有空间的票，不接暴跌也不追高。",
    formula=(
        "BuyIndex ≥ 70  ∧  当日主力净流入>0  ∧  5日主力净流入≥0\n"
        "∧  0.22 ≤ pos60 ≤ 0.66  ∧  当日涨跌 > −3%\n"
        "∧  量比 0.85–2.6  ∧  情绪<82  ∧  乖离<12  ∧  RSI 在 40–68（有值才限）\n"
        "排除 ST/退市、暴跌、情绪过热、60日顶部。"
    ),
    where=(
        f"{_BUY_SAFE} AND m.buy_index >= 70 AND s.main_net_in > 0 "
        "AND COALESCE(s.main_net_in_d5, 0) >= 0 "
        "AND m.pos60 IS NOT NULL AND m.pos60 >= 0.22 AND m.pos60 <= 0.66 "
        "AND s.pct > -3 "
        "AND (s.pct_d5 IS NULL OR s.pct_d5 > -8) "
        "AND (s.volume_ratio IS NULL OR (s.volume_ratio >= 0.85 AND s.volume_ratio <= 2.6)) "
        "AND (m.sentiment IS NULL OR m.sentiment < 82) "
        "AND (m.bias20 IS NULL OR m.bias20 < 12) "
        "AND (m.rsi14 IS NULL OR (m.rsi14 >= 40 AND m.rsi14 <= 68))"
    ),
    action="中位净流入·潜力观察，可分批跟踪",
    extra_docs=BUY_INDEX_FORMULA,
))

_reg_buy(SidePlan(
    id="BT",
    name="趋势回踩",
    summary="多头趋势里缩量回踩，资金仍在。用来找主升浪中的低吸，不买均线已坏的票。",
    formula=(
        "均线多头  ∧  站上MA20  ∧  MACD柱>0  ∧  主力净流入>0\n"
        "∧  0.32 ≤ pos60 ≤ 0.68  ∧  (缩量回踩 ∨ 当日振幅温和)\n"
        "∧  BuyIndex ≥ 58  ∧  乖离<10"
    ),
    where=(
        f"{_BUY_SAFE} AND m.ma_bull = 1 AND m.above_ma20 = 1 AND m.macd_bar > 0 "
        "AND s.main_net_in > 0 AND m.buy_index >= 58 "
        "AND m.pos60 IS NOT NULL AND m.pos60 >= 0.32 AND m.pos60 <= 0.68 "
        "AND (m.pullback_shrink = 1 OR (s.pct >= -2.5 AND s.pct <= 2.5)) "
        "AND (m.bias20 IS NULL OR m.bias20 < 10)"
    ),
    action="趋势回踩·等稳可跟",
    order="m.macd_bar DESC",
))

_reg_buy(SidePlan(
    id="BZ",
    name="企稳蓄势",
    summary="四闸门已过、低位、资金连续回流，且 RSI 已离开冰点。不是超卖接飞刀。",
    formula=(
        "stabilize_score ≥ 65  ∧  当日及5日主力净流入不差\n"
        "∧  pos60 ≤ 0.42  ∧  BuyIndex ≥ 55\n"
        "∧  RSI 有值则 ≥ 32（已离开极端超卖） ∧  5日跌幅 > −10%"
    ),
    where=(
        f"{_BUY_SAFE} AND m.stabilize_score IS NOT NULL AND m.stabilize_score >= 65 "
        "AND s.main_net_in > 0 AND COALESCE(s.main_net_in_d5, 0) >= 0 "
        "AND m.pos60 IS NOT NULL AND m.pos60 <= 0.42 AND m.buy_index >= 55 "
        "AND (m.rsi14 IS NULL OR m.rsi14 >= 32) "
        "AND (s.pct_d5 IS NULL OR s.pct_d5 > -10)"
    ),
    action="低位企稳·轻仓蓄势",
    order="m.stabilize_score DESC",
    extra_docs=STABILIZE_FORMULA,
))

_reg_buy(SidePlan(
    id="BD",
    name="资金吸筹",
    summary="价跌资金进且暗盘偏强，但拒绝自由落体。用来跟踪主力吸筹，不是抄底暴跌。",
    formula=(
        "divergence=暗中吸筹  ∧  Dark≥68  ∧  主力净流入>0\n"
        "∧  pos60≤0.58  ∧  BuyIndex≥52  ∧  当日>−4%  ∧  5日>−8%"
    ),
    where=(
        f"{_BUY_SAFE} AND m.divergence = '暗中吸筹' AND m.dark_power >= 68 "
        "AND s.main_net_in > 0 AND m.buy_index >= 52 "
        "AND m.pos60 IS NOT NULL AND m.pos60 <= 0.58 "
        "AND s.pct > -4 AND (s.pct_d5 IS NULL OR s.pct_d5 > -8)"
    ),
    action="暗中吸筹·跟踪资金",
    order="m.dark_power DESC",
    extra_docs=DARK_FORMULA,
))

_reg_buy(SidePlan(
    id="I",
    name="筹码集中",
    summary="只用已缓存持股。户数环比下降且当期有机构披露，再叠加净流入与位置。缺缓存零命中。默认关闭。",
    formula=(
        "holders_qoq < 0  ∧  当期有机构披露  ∧  主力净流入>0\n"
        "∧  BuyIndex ≥ 58  ∧  pos60 ≤ 0.70  ∧  当日 > −5%\n"
        "不编造户数，不扫全市场 HTTP。"
    ),
    where=(
        f"{_BUY_SAFE} AND s.main_net_in > 0 AND m.buy_index >= 58 "
        "AND (m.pos60 IS NULL OR m.pos60 <= 0.70) AND s.pct > -5 AND EXISTS ("
        "SELECT 1 FROM holder_feature h WHERE h.code=m.code "
        "AND h.holders_qoq IS NOT NULL AND h.holders_qoq < 0 AND h.has_institution=1)"
    ),
    action="筹码趋向集中·可跟踪",
    extra_docs="缺持股缓存则该股不命中。禁止用十大股东名单猜户数。",
    default_off=True,
))

_reg_buy(SidePlan(
    id="J",
    name="解禁后回流",
    summary="解禁已过≥5个工作日、资金仍进、位置未到顶部。无解禁数据不买。默认关闭。",
    formula=(
        "最近一次解禁已过 ≥ 5 个工作日  ∧  当日及5日净流入不差\n"
        "∧  BuyIndex ≥ 65  ∧  pos60 ≤ 0.65"
    ),
    where=(
        f"{_BUY_SAFE} AND s.main_net_in > 0 AND m.buy_index >= 65 "
        "AND COALESCE(s.main_net_in_d5, 0) >= 0 "
        "AND (m.pos60 IS NULL OR m.pos60 <= 0.65) AND EXISTS ("
        "SELECT 1 FROM holder_feature h WHERE h.code=m.code "
        "AND h.last_unlock_days_ago IS NOT NULL AND h.last_unlock_days_ago >= 5)"
    ),
    action="解禁后回流·严观察",
    extra_docs="工作日口径剔除周末，无官方节假日表。无解禁列表则零命中。",
    default_off=True,
))

# —— 卖点：止盈兑现 + 风险离场，不把已经砸死的弱票当卖点 ——
_reg_sell(SidePlan(
    id="ST",
    name="高位止盈",
    summary="默认卖点之一。走到 60 日高位后再叠加超买、过热或资金转出，用来兑现利润，不是找已经跌残的票。",
    formula=(
        "pos60 ≥ 0.86  ∧  (乖离≥9 ∨ RSI≥72 ∨ 情绪≥76 ∨ 主力净流入<0)"
    ),
    where=(
        "m.pos60 IS NOT NULL AND m.pos60 >= 0.86 AND ("
        "(m.bias20 IS NOT NULL AND m.bias20 >= 9) "
        "OR (m.rsi14 IS NOT NULL AND m.rsi14 >= 72) "
        "OR (m.sentiment IS NOT NULL AND m.sentiment >= 76) "
        "OR s.main_net_in < 0)"
    ),
    action="高位止盈·建议减仓兑现",
    order="m.pos60 DESC",
))

_reg_sell(SidePlan(
    id="SR",
    name="资金出逃避险",
    summary="高位或中高位出现持续净流出/暗中派发。用来规避主力离场，不把低位阴跌弱票标成卖点。",
    formula=(
        "当日主力净流入 < −5000万  ∧  (5日净流入<0 ∨ 暗中派发)\n"
        "∧  pos60 ≥ 0.62"
    ),
    where=(
        "s.main_net_in < -5000 AND (COALESCE(s.main_net_in_d5, 0) < 0 OR m.divergence = '暗中派发') "
        "AND m.pos60 IS NOT NULL AND m.pos60 >= 0.62"
    ),
    action="资金出逃·避险减仓",
    order="s.main_net_in ASC",
))

_reg_sell(SidePlan(
    id="SO",
    name="超买回吐",
    summary="高位超买且正乖离过大，均值回归风险高，适合止盈而不是追涨。",
    formula="RSI14 > 74  ∧  bias20 > 11%  ∧  pos60 ≥ 0.72",
    where=(
        "m.rsi14 IS NOT NULL AND m.rsi14 > 74 "
        "AND m.bias20 IS NOT NULL AND m.bias20 > 11 "
        "AND m.pos60 IS NOT NULL AND m.pos60 >= 0.72"
    ),
    action="超买高乖离·止盈防回吐",
    order="m.rsi14 DESC",
))

_reg_sell(SidePlan(
    id="SD",
    name="暗中派发",
    summary="已经走出一段后出现价涨资金出。只在中高位预警，避免把底部震荡标成卖点。",
    formula="divergence=暗中派发  ∧  (Dark≤42 ∨ 主力净流入<0)  ∧  pos60≥0.50",
    where=(
        "m.divergence = '暗中派发' AND (m.dark_power <= 42 OR s.main_net_in < 0) "
        "AND m.pos60 IS NOT NULL AND m.pos60 >= 0.50"
    ),
    action="暗中派发·警惕减仓",
    order="m.dark_power ASC",
    extra_docs=DARK_FORMULA,
))

_reg_sell(SidePlan(
    id="SB",
    name="趋势破坏",
    summary="曾经有过一段走势后均线破坏、MACD 转负且近5日下跌。用来止损避险，不是罗列长期弱势股。",
    formula=(
        "均线多头破坏  ∧  MACD柱<0  ∧  近5日涨跌<−3%\n"
        "∧  pos60 ≥ 0.38  ∧  BuyIndex≤55（有值才限）"
    ),
    where=(
        "m.ma_bull = 0 AND m.macd_bar < 0 AND s.pct_d5 < -3 "
        "AND m.pos60 IS NOT NULL AND m.pos60 >= 0.38 "
        "AND (m.buy_index IS NULL OR m.buy_index <= 55)"
    ),
    action="趋势转弱·止损避险",
    order="s.pct_d5 ASC",
))

_reg_sell(SidePlan(
    id="I",
    name="户数扩散",
    summary="只用已缓存持股。户数环比明显上升且当期有机构披露。缺缓存零命中。默认关闭。",
    formula="holders_qoq ≥ 5  ∧  当期有机构披露。不编造机构占比下降。",
    where=(
        "EXISTS (SELECT 1 FROM holder_feature h WHERE h.code=m.code "
        "AND h.holders_qoq IS NOT NULL AND h.holders_qoq >= 5 AND h.has_institution=1)"
    ),
    action="户数明显扩散·回避",
    extra_docs="缺持股缓存则该股不命中。禁止用十大股东名单猜户数。",
    default_off=True,
))

_reg_sell(SidePlan(
    id="J",
    name="解禁避让",
    summary="未来10个工作日大解禁且位置偏高。无解禁数据不卖。默认关闭。",
    formula="未来10个工作日解禁占流通≥3%  ∧  pos60≥0.6",
    where=(
        "m.pos60 >= 0.6 AND EXISTS ("
        "SELECT 1 FROM holder_feature h WHERE h.code=m.code "
        "AND h.unlock_days_to IS NOT NULL AND h.unlock_days_to BETWEEN 0 AND 10 "
        "AND h.unlock_float_ratio IS NOT NULL AND h.unlock_float_ratio >= 3)"
    ),
    action="临近大解禁·避让",
    extra_docs="工作日口径剔除周末，无官方节假日表。无解禁列表则零命中。",
    default_off=True,
))

BUY_ORDER = ["A", "BP", "BT", "BZ", "BD", "I", "J"]
SELL_ORDER = ["ST", "SR", "SO", "SD", "SB", "I", "J"]
PLAN_ORDER = list(dict.fromkeys(BUY_ORDER + SELL_ORDER))

# 买点 A 就是第一版选股方案A，不再映射到潜力主升。其余旧字母仍映射到后加方案。
_BUY_LEGACY = {
    "B": "BZ", "C": "BZ", "D": "BD", "E": "BT", "F": "BP", "G": "BT", "H": "BZ",
}
_SELL_LEGACY = {
    "A": "ST", "B": "ST", "C": "SO", "D": "SD", "E": "SB", "F": "ST", "G": "SO", "H": "SO",
}


def _defaults(kind: str) -> list[str]:
    return list(DEFAULT_SELL_IDS if kind == "sell" else DEFAULT_BUY_IDS)


def _hidden_key(kind: str) -> str:
    return META_KEY_HIDDEN_SELL if kind == "sell" else META_KEY_HIDDEN_BUY


def _enabled_key(kind: str) -> str:
    return META_KEY_SELL if kind == "sell" else META_KEY_BUY


def _norm_kind(kind: str) -> str:
    return "sell" if str(kind or "").strip().lower() == "sell" else "buy"


def _norm_pid(raw) -> str:
    return str(raw or "").strip().upper()


def get_hidden(kind: str = "buy") -> list[str]:
    """用户删除的方案（软删除）。代码里的 SQL 方案还在，恢复默认会加回。"""
    kind = _norm_kind(kind)
    catalog = catalog_map(kind)
    raw = get_meta_json(_hidden_key(kind), [])
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for item in raw:
        pid = _norm_pid(item)
        if pid in catalog and pid not in out:
            out.append(pid)
    return out


def set_hidden(kind: str, ids) -> list[str]:
    kind = _norm_kind(kind)
    catalog = catalog_map(kind)
    out = []
    for item in ids or []:
        pid = _norm_pid(item)
        if pid in catalog and pid not in out:
            out.append(pid)
    set_meta_json(_hidden_key(kind), out)
    return out


def visible_ids(kind: str = "buy") -> list[str]:
    kind = _norm_kind(kind)
    hidden = set(get_hidden(kind))
    order = SELL_ORDER if kind == "sell" else BUY_ORDER
    return [pid for pid in order if pid not in hidden]


def _visible_defaults(kind: str, hidden: set[str] | None = None) -> list[str]:
    kind = _norm_kind(kind)
    hidden = set(hidden if hidden is not None else get_hidden(kind))
    vis = [pid for pid in _defaults(kind) if pid not in hidden]
    if vis:
        return vis
    remain = [pid for pid in (SELL_ORDER if kind == "sell" else BUY_ORDER) if pid not in hidden]
    return remain[:1]


def _normalize_ids(ids, kind: str = "buy") -> list[str]:
    """只保留未删除的合法方案。勾空时回落到仍可见的默认，不把已删方案加回来。"""
    kind = _norm_kind(kind)
    catalog = catalog_map(kind)
    legacy = _SELL_LEGACY if kind == "sell" else _BUY_LEGACY
    hidden = set(get_hidden(kind))
    if not isinstance(ids, (list, tuple)):
        return _visible_defaults(kind, hidden)
    out = []
    for raw in ids:
        pid = legacy.get(_norm_pid(raw), _norm_pid(raw))
        if pid in catalog and pid not in hidden and pid not in out:
            out.append(pid)
    return out or _visible_defaults(kind, hidden)


def _raw_id_list(raw) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        return None
    return [str(x or "").strip().upper() for x in raw]


def ensure_multi_plan_defaults() -> None:
    """一次性把旧版单方案默认升级为可交叉命中的多方案。用户自选组合不覆盖。"""
    ver = get_meta_json(RULES_VER_KEY, None)
    if ver == RULES_VER:
        return
    buy_raw = get_meta_json(META_KEY_BUY, None)
    if buy_raw is None:
        buy_raw = get_meta_json(META_KEY, None)
    buy_list = _raw_id_list(buy_raw)
    old_buy = {
        None,
        frozenset(),
        frozenset({"A"}),
        frozenset({"BP"}),
        frozenset({"BP", "BT", "BZ"}),
    }
    if buy_list is None or frozenset(buy_list) in old_buy:
        set_meta_json(META_KEY_BUY, list(DEFAULT_BUY_IDS))
    sell_raw = get_meta_json(META_KEY_SELL, None)
    if sell_raw is None:
        sell_raw = get_meta_json(META_KEY, None)
    sell_list = _raw_id_list(sell_raw)
    if sell_list in (None, ["A"], ["ST"]):
        set_meta_json(META_KEY_SELL, list(DEFAULT_SELL_IDS))
    set_meta_json(RULES_VER_KEY, RULES_VER)


def get_enabled(kind: str = "buy") -> list[str]:
    ensure_multi_plan_defaults()
    key = META_KEY_BUY if kind == "buy" else META_KEY_SELL
    raw = get_meta_json(key, None)
    if raw is None:
        raw = get_meta_json(META_KEY, None)
    if raw is None:
        return _defaults(kind)
    return _normalize_ids(raw, kind)


def set_enabled(ids=None, *, buy_ids=None, sell_ids=None) -> dict:
    """买点方案与卖点方案分开保存。旧参数 ids 会同时写入两侧（兼容，并按侧映射）。"""
    if buy_ids is None and sell_ids is None and ids is not None:
        buy_ids = sell_ids = ids
    if buy_ids is not None:
        set_meta_json(META_KEY_BUY, _normalize_ids(buy_ids, "buy"))
    if sell_ids is not None:
        set_meta_json(META_KEY_SELL, _normalize_ids(sell_ids, "sell"))
    return {"buy": get_enabled("buy"), "sell": get_enabled("sell")}


def delete_plan(kind: str, pid: str) -> dict:
    """软删除一侧方案：移出列表和启用集。至少保留一个，避免买/卖点窗口无策略。"""
    kind = _norm_kind(kind)
    pid = _norm_pid(pid)
    catalog = catalog_map(kind)
    if pid not in catalog:
        return {"ok": False, "error": "方案不存在或已失效", "kind": kind, "id": pid}
    hidden = get_hidden(kind)
    visible = [p for p in (SELL_ORDER if kind == "sell" else BUY_ORDER) if p not in set(hidden)]
    if pid not in visible:
        return {
            "ok": True, "already": True, "removed": pid, "kind": kind,
            "hidden": hidden, "enabled": get_enabled(kind),
        }
    if len(visible) <= 1:
        return {
            "ok": False, "error": "至少保留一个方案，不能全部删除",
            "kind": kind, "id": pid,
        }
    hidden = hidden + [pid]
    set_hidden(kind, hidden)
    enabled = [p for p in get_enabled(kind) if p != pid]
    if not enabled:
        enabled = _visible_defaults(kind, set(hidden))
    set_meta_json(_enabled_key(kind), enabled)
    return {
        "ok": True, "removed": pid, "kind": kind,
        "hidden": get_hidden(kind), "enabled": get_enabled(kind),
    }


def reset_side(kind: str) -> dict:
    """恢复一侧默认方案，并把已删除的方案加回列表。"""
    kind = _norm_kind(kind)
    set_hidden(kind, [])
    set_meta_json(_enabled_key(kind), list(_defaults(kind)))
    return {
        "ok": True, "kind": kind,
        "enabled": get_enabled(kind),
        "hidden": get_hidden(kind),
    }


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
        pos = r.get("pos60")
        pos = float(pos) if pos is not None else 0.0
        if kind == "buy":
            return (-n, -bi, pos)
        return (-n, -pos, bi)
    items.sort(key=key)
    return items


def plan_caption(pid: str, kind: str = "buy") -> str:
    p = catalog_map(kind).get(pid)
    if kind != "sell" and pid == "A":
        return (p.name if p else "选股方案A")
    prefix = "买点方案" if kind == "buy" else "卖点方案"
    name = p.name if p else pid
    return f"{prefix}{pid}·{name}"


def stamp_plans(row: dict, plans: list[str] | None, kind: str) -> dict:
    """给命中行打上该侧方案出处。买点只标买点方案，卖点只标卖点方案。"""
    catalog = catalog_map(kind)
    ids = [p for p in (plans or []) if p in catalog]
    row["side"] = "sell" if kind == "sell" else "buy"
    row["plans"] = ids
    row["plan_id"] = ",".join(ids)
    row["plan_labels"] = [plan_caption(p, kind) for p in ids]
    row["plan_names"] = "、".join(row["plan_labels"])
    actions = [catalog[p].action for p in ids]
    row["hit_action"] = "；".join(actions) if actions else ""
    if ids:
        row["picked_by"] = row["plan_names"]
        row["picked_text"] = f"由{'、'.join(row['plan_labels'])}选出"
    else:
        row["picked_by"] = ""
        row["picked_text"] = "未命中策略（不展示）"
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


def _week_gate_on() -> bool:
    try:
        from . import engine as engine_svc
        return bool(engine_svc.get_config().get("week_gate", True))
    except Exception:  # noqa: BLE001
        return True


def collect_hits(
    kind: str,
    enabled: list[str] | None = None,
    limit: int = PER_PLAN_CAP,
    min_hits: int = 1,
    use_week_gate: bool | None = None,
) -> list[dict]:
    """启用方案并集。买点只跑买点规则，卖点只跑卖点规则，互不混用。

    min_hits 默认 1，供引擎/方案 I/J 单测与策略选股使用。
    最佳卖点窗口要求 ≥2；最佳买点取并集后再走质量门禁。
    """
    if kind not in ("buy", "sell"):
        raise ValueError("kind must be buy or sell")
    catalog = catalog_map(kind)
    enabled = enabled if enabled is not None else get_enabled(kind)
    enabled = [p for p in enabled if p in catalog]
    need = max(1, int(min_hits or 1))
    if any(p in enabled for p in ("I", "J")):
        from . import holder_feature
        holder_feature.refresh_all_features()
    bucket: dict[str, dict] = {}
    for pid in enabled:
        plan = catalog.get(pid)
        if not plan:
            continue
        for row in _fetch(plan.where, plan.order):
            _merge(bucket, row, pid)
    items = _annotate(_sort_hits(list(bucket.values()), kind), kind)
    items = [r for r in items if len(r.get("plans") or []) >= need]
    if kind == "buy" and use_week_gate is not False:
        from . import week_gate
        on = _week_gate_on() if use_week_gate is None else bool(use_week_gate)
        items = week_gate.apply_buy_gate(items, enabled=on)
    return _fair_take(items, enabled, limit)


def _shanghai_today():
    return datetime.now(_TZ).date()


def half_year_stats(codes: list[str]) -> dict[str, dict]:
    """近半年真实日K波动。只用 daily_kline，不用涨跌幅编造。"""
    codes = [c for c in codes if c]
    if not codes:
        return {}
    start = (_shanghai_today() - timedelta(days=HALF_CAL_DAYS)).isoformat()
    out: dict[str, dict] = {}
    chunk = 400
    for i in range(0, len(codes), chunk):
        part = codes[i:i + chunk]
        marks = ",".join("?" * len(part))
        rows = query(
            f"SELECT code, date, open, high, low, close FROM daily_kline "
            f"WHERE code IN ({marks}) AND date >= ? ORDER BY code, date",
            tuple(part) + (start,),
        )
        buckets: dict[str, list] = {}
        for row in rows:
            buckets.setdefault(row["code"], []).append(row)
        for code, bars in buckets.items():
            highs: list[float] = []
            lows: list[float] = []
            closes: list[float] = []
            for b in bars:
                close = b.get("close")
                if close is None:
                    continue
                try:
                    close = float(close)
                except (TypeError, ValueError):
                    continue
                high = b.get("high")
                low = b.get("low")
                try:
                    hi = float(high) if high is not None else close
                    lo = float(low) if low is not None else close
                except (TypeError, ValueError):
                    hi = lo = close
                if hi < lo:
                    hi, lo = lo, hi
                highs.append(hi)
                lows.append(lo)
                closes.append(close)
            if not closes:
                continue
            half_high = max(highs)
            half_low = min(lows)
            first = closes[0]
            last = closes[-1]
            last_date = None
            for b in reversed(bars):
                if b.get("close") is None:
                    continue
                last_date = str(b.get("date") or "")[:10]
                break
            span = half_high - half_low
            half_ret = ((last / first) - 1.0) * 100.0 if first else None
            half_range = (span / half_low * 100.0) if half_low else None
            out[code] = {
                "half_bars": len(closes),
                "half_high": half_high,
                "half_low": half_low,
                "half_first": first,
                "half_last": last,
                "half_last_date": last_date,
                "half_ret": half_ret,
                "half_range_pct": half_range,
            }
    return out


def attach_half_year(row: dict, stats: dict | None) -> dict:
    """把半年波动贴到当前价位上。缺 K 线不编造 half_*。"""
    if not stats:
        row["half_bars"] = 0
        row["half_high"] = None
        row["half_low"] = None
        row["half_ret"] = None
        row["half_range_pct"] = None
        row["half_pos"] = None
        row["half_last_date"] = None
        row["room_to_high"] = None
        return row
    row.update(stats)
    price = row.get("price")
    try:
        price = float(price) if price is not None else float(stats["half_last"])
    except (TypeError, ValueError, KeyError):
        price = stats.get("half_last")
    hi = stats.get("half_high")
    lo = stats.get("half_low")
    span = None
    if hi is not None and lo is not None:
        span = hi - lo
    if price is None or span is None or span <= 0:
        row["half_pos"] = None
    else:
        row["half_pos"] = (price - lo) / span
    hi = stats.get("half_high") if stats else None
    if price and hi is not None and price > 0:
        row["room_to_high"] = (float(hi) - float(price)) / float(price) * 100.0
    else:
        row["room_to_high"] = None
    return row


def half_year_pass(row: dict, kind: str) -> bool:
    bars = int(row.get("half_bars") or 0)
    if bars < HALF_MIN_BARS:
        return False
    rng = row.get("half_range_pct")
    pos = row.get("half_pos")
    ret = row.get("half_ret")
    room = row.get("room_to_high")
    if rng is None or pos is None or ret is None:
        return False
    if kind == "sell":
        if pos < SELL_MIN_HALF_POS or rng < 10:
            return False
        if room is None or room > SELL_MAX_ROOM:
            return False
        return ret >= 0 or pos >= 0.88
    if rng < 12 or rng > HALF_MAX_RANGE_BUY:
        return False
    if pos < 0.18 or pos > BUY_MAX_HALF_POS:
        return False
    if room is None or room < BUY_MIN_ROOM:
        return False
    return ret > -40


def _codes_needing_backfill(codes: list[str]) -> list[str]:
    stats = half_year_stats(codes)
    return [c for c in codes if int((stats.get(c) or {}).get("half_bars") or 0) < HALF_MIN_BARS]


def _attach_score_finance(items: list[dict]) -> None:
    from . import finance as finance_svc
    from . import rating as rating_svc
    from . import sector as sector_svc
    finance_svc.attach_grades(items)
    heat_map = {}
    try:
        heat_map = sector_svc.industry_heat_map()
    except Exception:  # noqa: BLE001
        heat_map = {}
    for r in items:
        score, advice = rating_svc.quick_score(r)
        r["score"] = score
        r["score_advice"] = advice
        r["finance_grade"] = (r.get("finance_grade") or "").strip()
        industry = (r.get("industry") or "").strip()
        r["sector_hot"] = heat_map.get(industry)
        r["op_advice"] = point_advice(r.get("side") or "", advice, r.get("room_to_high"))


def point_advice(kind: str, score_advice: str, room_to_high=None) -> str:
    """买/卖点操作建议与名单方向对齐，避免买点亮减持、卖点亮增持。"""
    if kind == "sell":
        return "减持"
    if score_advice == "减持":
        return "减持"
    if score_advice == "增持":
        return "增持"
    return "保持不变"


def quality_pass(row: dict, kind: str) -> bool:
    """板块热度、综合评分、财报、上涨空间。财报不并入评分，未评级不伪造 A。"""
    score = row.get("score")
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None
    grade = (row.get("finance_grade") or "").strip()
    advice = row.get("score_advice") or row.get("op_advice") or ""
    room = row.get("room_to_high")
    heat = row.get("sector_hot")
    if kind == "sell":
        if room is None or room > SELL_MAX_ROOM:
            return False
        if advice == "增持" and room > SELL_TINY_ROOM:
            return False
        # 板块仍热且离半年高点还有空间：先不标卖点，避免和热度/评分打架
        if heat is not None and heat >= 5 and room > SELL_TINY_ROOM:
            return False
        return True
    if score is None or score < BUY_MIN_SCORE:
        return False
    if advice == "减持":
        return False
    if heat is not None and heat < HOT_FLOOR:
        return False
    if grade in WEAK_GRADES:
        return False
    if room is None:
        return False
    if grade in GOOD_GRADES:
        return room >= BUY_MIN_ROOM
    # 未评级：不伪造 A，按同样的评分/空间门槛，不另造等级
    return room >= BUY_MIN_ROOM


def is_new_listing(row: dict) -> bool:
    """短历史且最近一根日K仍新鲜，才当新股。拉取失败的空K线不当新股。"""
    bars = int(row.get("half_bars") or 0)
    if bars <= 0 or bars >= HALF_MIN_BARS:
        return False
    last = str(row.get("half_last_date") or "")[:10]
    if len(last) < 10:
        return False
    try:
        from datetime import date
        d = date.fromisoformat(last)
    except ValueError:
        return False
    return (_shanghai_today() - d).days <= NEW_LAST_DAYS


def new_stock_pass(row: dict, kind: str) -> bool:
    """新股门：综合评分 + 财报评级。评级不并入评分，未评级不伪造 A。"""
    score = row.get("score")
    grade = (row.get("finance_grade") or "").strip()
    if score is None:
        return False
    try:
        score = float(score)
    except (TypeError, ValueError):
        return False
    if kind == "sell":
        if grade in WEAK_GRADES and score >= 50:
            return True
        return score >= NEW_SELL_MIN_SCORE
    if (row.get("score_advice") or "") == "减持":
        return False
    return score >= NEW_BUY_MIN_SCORE and grade in NEW_BUY_GRADES


def apply_recommend_gate(
    items: list[dict],
    kind: str,
    backfill: bool = True,
    min_hits: int = MIN_PLAN_HITS,
) -> list[dict]:
    """老股：命中 min_hits 且过半年波动+质量门。新股：综合评分+财报评级。"""
    codes = [r.get("code") for r in items if r.get("code")]
    backfilled: set[str] = set()
    if backfill:
        need = _codes_needing_backfill(codes)
        if need:
            from . import kline as kline_svc
            res = kline_svc.backfill_daily_real(need, count=180, limit=min(16, len(need)))
            backfilled = set(res.get("ok") or [])
    stats = half_year_stats(codes)
    _attach_score_finance(items)
    need_hits = max(1, int(min_hits or 1))
    kept: list[dict] = []
    for r in items:
        attach_half_year(r, stats.get(r.get("code") or ""))
        r["kline_backfilled"] = r.get("code") in backfilled
        hits = len(r.get("plans") or [])
        r["side"] = "sell" if kind == "sell" else "buy"
        r["op_advice"] = point_advice(kind, r.get("score_advice") or "", r.get("room_to_high"))
        ok = False
        if half_year_pass(r, kind) and hits >= need_hits and quality_pass(r, kind):
            r["point_gate"] = "half_year"
            ok = True
        elif is_new_listing(r) and new_stock_pass(r, kind) and hits >= 1 and quality_pass(r, kind):
            r["point_gate"] = "new_stock"
            ok = True
        else:
            r["point_gate"] = ""
        if ok:
            kept.append(r)
    return kept


def apply_half_year_gate(items: list[dict], kind: str, backfill: bool = False) -> list[dict]:
    return apply_recommend_gate(items, kind, backfill=backfill, min_hits=1)


def _need_multi_reason(enabled: list[str], kind: str) -> str:
    names = "、".join(plan_caption(i, kind) for i in enabled) or "未启用"
    side = "买点" if kind == "buy" else "卖点"
    tail = _EMPTY_BUY_TAIL if kind == "buy" else _EMPTY_SELL_TAIL
    return (
        f"当前未启用任何{side}方案（{names}）。"
        f"可并行勾选多个方案；买点默认选股方案A，卖点老股须交叉命中至少 {MIN_PLAN_HITS} 个。"
        f"{tail}"
    )


def _empty_half_reason(enabled: list[str], kind: str, had_hits: bool) -> str:
    names = "、".join(plan_caption(i, kind) for i in enabled)
    side = "买点" if kind == "buy" else "卖点"
    tail = _EMPTY_BUY_TAIL if kind == "buy" else _EMPTY_SELL_TAIL
    if had_hits:
        return (
            f"当前启用{side}方案（{names}）已并行扫描到命中，"
            "但未同时通过半年波动、上涨空间、综合评分、财报或板块热度门禁。"
            f"{tail}已尝试补真实日K；补不到的不编造。"
        )
    extra = (
        "请确认已同步行情并重建指标，或确认选股方案A的购买指数与净流入条件。"
        if kind == "buy"
        else "请确认已同步行情并重建指标。"
    )
    return (
        f"当前启用{side}方案（{names}）并行扫描暂无命中。"
        f"{tail}{extra}"
    )


def _rank_kept(items: list[dict], kind: str) -> list[dict]:
    """过门禁后再按空间/评分/热度排，买点优先还有空间，卖点优先贴近半年高。"""
    def key(r: dict):
        room = r.get("room_to_high")
        score = r.get("score")
        heat = r.get("sector_hot")
        pos = r.get("half_pos")
        try:
            room_v = float(room) if room is not None else (-1.0 if kind == "buy" else 99.0)
        except (TypeError, ValueError):
            room_v = -1.0 if kind == "buy" else 99.0
        try:
            score_v = float(score) if score is not None else 0.0
        except (TypeError, ValueError):
            score_v = 0.0
        try:
            heat_v = float(heat) if heat is not None else 0.0
        except (TypeError, ValueError):
            heat_v = 0.0
        try:
            pos_v = float(pos) if pos is not None else 0.0
        except (TypeError, ValueError):
            pos_v = 0.0
        boost = 1 if (r.get("score_advice") or "") == "增持" else 0
        grade = (r.get("finance_grade") or "").strip()
        grade_v = 2 if grade == "A" else 1 if grade == "B" else 0
        n = len(r.get("plans") or [])
        if kind == "sell":
            return (-n, room_v, -pos_v, -score_v)
        return (-n, -boost, -room_v, -score_v, -heat_v, -grade_v)

    return sorted(items, key=key)


def _attach_buy_display(items: list[dict], backfill: bool = True) -> list[dict]:
    """给买点名单贴评分/财报/半年K，只作展示，不再当第一版方案A的过滤门。"""
    codes = [r.get("code") for r in items if r.get("code")]
    backfilled: set[str] = set()
    if backfill:
        need = _codes_needing_backfill(codes)
        if need:
            from . import kline as kline_svc
            res = kline_svc.backfill_daily_real(need, count=180, limit=min(16, len(need)))
            backfilled = set(res.get("ok") or [])
    stats = half_year_stats(codes)
    _attach_score_finance(items)
    for r in items:
        attach_half_year(r, stats.get(r.get("code") or ""))
        r["kline_backfilled"] = r.get("code") in backfilled
        r["side"] = "buy"
        r["op_advice"] = point_advice("buy", r.get("score_advice") or "", r.get("room_to_high"))
        r["point_gate"] = "plan_a" if "A" in (r.get("plans") or []) else "hit"
    return items


def collect_buy_points(limit: int = 12, backfill: bool = True) -> tuple[list[dict], str, str]:
    """实时买点窗：默认选股方案A（购买指数≥80且净流入>0）。不套质量门，不凑观察池。"""
    limit = max(3, min(int(limit or 12), 40))
    enabled = get_enabled("buy")
    if not enabled:
        return [], "empty", _need_multi_reason(enabled, "buy")
    raw = collect_hits("buy", enabled, limit=PER_PLAN_CAP, min_hits=1, use_week_gate=False)
    source, note = "hit", _hit_note(enabled, "buy")
    if not raw and "A" in enabled:
        plan_a = BUY_PLANS.get("A")
        if plan_a and plan_a.fallback_where:
            bucket: dict[str, dict] = {}
            for row in _fetch(plan_a.fallback_where, plan_a.order):
                _merge(bucket, row, "A")
            raw = _annotate(_sort_hits(list(bucket.values()), "buy"), "buy")
            if raw:
                source = "relaxed_65"
                note = (
                    "选股方案A主规则（购买指数≥80且主力净流入>0）暂无命中，"
                    "已按第一版回退：购买指数≥65且主力净流入>0（较好买点观察，非极佳）。"
                    "不回退无流入观察池。不构成投资建议"
                )
    if not raw:
        return [], "empty", _empty_half_reason(enabled, "buy", had_hits=False)
    kept = _attach_buy_display(raw, backfill=backfill)
    return _fair_take(_sort_hits(kept, "buy"), enabled, limit), source, note


def collect_sell_points(limit: int = 12, backfill: bool = True) -> tuple[list[dict], str, str]:
    limit = max(3, min(int(limit or 12), 40))
    enabled = get_enabled("sell")
    if not enabled:
        return [], "empty", _need_multi_reason(enabled, "sell")
    need = MIN_PLAN_HITS if len(enabled) >= MIN_PLAN_HITS else 1
    raw = collect_hits("sell", enabled, limit=PER_PLAN_CAP, min_hits=1)
    kept = apply_recommend_gate(raw, "sell", backfill=backfill, min_hits=need)
    if kept:
        return _fair_take(_rank_kept(kept, "sell"), enabled, limit), "hit", _hit_note(enabled, "sell")
    return [], "empty", _empty_half_reason(enabled, "sell", had_hits=bool(raw))


def _hit_note(enabled: list[str], kind: str) -> str:
    labels = "、".join(plan_caption(i, kind) for i in enabled)
    if kind == "buy":
        gate = (
            "最佳买点默认选股方案A：购买指数≥80且主力净流入>0；"
            "无主规则命中时回退≥65且净流入>0（较好观察，非极佳）。"
            "买点窗口不套用引擎周线门，也不再用半年空间/评分门把方案A名单滤空。"
            "未评级不伪造 A。不回退无流入观察池。"
        )
        extra = "可并行加开其他买点方案，取并集。"
        head = f"并行方案 {labels}。" if len(enabled) > 1 else f"当前执行{labels}。"
        return f"{head}{gate}{extra}不构成投资建议"
    gate = (
        f"多方案并行扫描。老股须交叉命中至少 {MIN_PLAN_HITS} 个方案，并结合近半年日K、"
        "上涨空间、综合评分、财报评级与板块热度；不对增持/仍有较大空间。"
        "日K不足会即时补真实K线；未评级不伪造 A。"
    )
    extra = "卖点只做高位止盈或中高位避险，不把已经跌残的弱票标成卖点。"
    if len(enabled) > 1:
        return f"并行方案 {labels} 交叉命中。{gate}{extra}不构成投资建议"
    return f"当前执行{labels}。{gate}{extra}不构成投资建议"


def plan_counts() -> dict[str, dict[str, int]]:
    from . import holder_feature
    holder_feature.ensure_tables()
    out: dict[str, dict[str, int]] = {}
    for pid, plan in BUY_PLANS.items():
        out.setdefault(pid, {"buy": 0, "sell": 0})
        out[pid]["buy"] = _count(plan.where)
    for pid, plan in SELL_PLANS.items():
        out.setdefault(pid, {"buy": 0, "sell": 0})
        out[pid]["sell"] = _count(plan.where)
    return out


def executing_text(kind: str = "buy", enabled: list[str] | None = None) -> dict:
    kind = "sell" if kind == "sell" else "buy"
    catalog = catalog_map(kind)
    enabled = enabled if enabled is not None else get_enabled(kind)
    prefix = "买点" if kind == "buy" else "卖点"
    names = [plan_caption(pid, kind) for pid in enabled]
    title = (prefix + " " + "、".join(names) + " 并行") if len(enabled) > 1 else names[0]
    blocks = [
        f"当前执行的{prefix}策略：{title}",
        f"{prefix}与另一侧完全分开勾选、分开扫描，方案名称也不共用。",
        (
            "买点默认选股方案A（购买指数≥80且主力净流入>0），可并行加开其他买点方案。"
            if kind == "buy"
            else f"多方案可并行勾选。老股须交叉命中至少 {MIN_PLAN_HITS} 个方案，并结合近半年真实日K。"
        ),
        (
            "无极佳命中时回退购买指数≥65且净流入>0（较好观察，非极佳）。不回退无流入观察池。"
            if kind == "buy"
            else "再叠加板块热度、综合评分、财报评级、距半年高点上涨空间；卖点与减持/兑现一致。"
        ),
        "买点窗口不套用引擎周线门。未评级不伪造 A。财报评级不并入购买指数或综合评分。",
        "空名单不编造个股，不用涨跌幅冒充日K。",
        "",
    ]
    for pid in enabled:
        p = catalog.get(pid)
        if not p:
            continue
        blocks.append(f"—— {plan_caption(pid, kind)} ——")
        blocks.append(p.summary)
        blocks.append(("买点公式：" if kind == "buy" else "卖点公式：") + p.formula)
        if p.extra_docs:
            blocks.append(p.extra_docs)
        blocks.append("")
    return {
        "ids": enabled,
        "kind": kind,
        "title": title,
        "detail": "\n".join(blocks).strip(),
        "parallel": len(enabled) > 1,
    }


def catalog(kind: str = "buy") -> list[dict]:
    kind = "sell" if kind == "sell" else "buy"
    counts = plan_counts()
    enabled = set(get_enabled(kind))
    defaults = set(_defaults(kind))
    hidden = set(get_hidden(kind))
    order = [pid for pid in (SELL_ORDER if kind == "sell" else BUY_ORDER) if pid not in hidden]
    catalog = catalog_map(kind)
    can_delete = len(order) > 1
    rows = []
    for pid in order:
        p = catalog[pid]
        c = counts.get(pid) or {"buy": 0, "sell": 0}
        rows.append({
            "id": p.id,
            "kind": kind,
            "name": p.name,
            "summary": p.summary,
            "formula": p.formula,
            "buy_formula": BUY_PLANS[pid].formula if pid in BUY_PLANS else "",
            "sell_formula": SELL_PLANS[pid].formula if pid in SELL_PLANS else "",
            "extra_docs": p.extra_docs,
            "action": p.action,
            "enabled": p.id in enabled,
            "is_default": p.id in defaults,
            "default_off": p.default_off,
            "count": c["buy"] if kind == "buy" else c["sell"],
            "buy_count": c.get("buy", 0),
            "sell_count": c.get("sell", 0),
            "caption": plan_caption(pid, kind),
            "hidden": False,
            "can_delete": can_delete,
        })
    return rows


def get_config() -> dict:
    buy_ids = get_enabled("buy")
    sell_ids = get_enabled("sell")
    buy_exe = executing_text("buy", buy_ids)
    sell_exe = executing_text("sell", sell_ids)
    return {
        "buy_enabled": buy_ids,
        "sell_enabled": sell_ids,
        "enabled": buy_ids,
        "parallel": len(buy_ids) > 1 or len(sell_ids) > 1,
        "buy_plans": catalog("buy"),
        "sell_plans": catalog("sell"),
        "plans": catalog("buy"),
        "buy_hidden": get_hidden("buy"),
        "sell_hidden": get_hidden("sell"),
        "executing": {
            "buy": buy_exe,
            "sell": sell_exe,
            "title": f"{buy_exe['title']} ｜ {sell_exe['title']}",
            "detail": "【最佳买点】\n" + buy_exe["detail"] + "\n\n【最佳卖点】\n" + sell_exe["detail"],
        },
        "note": (
            "买点策略与卖点策略分开勾选、分开扫描，名称也不共用。"
            "买点默认「选股方案A」：购买指数≥80且主力净流入>0；"
            "无命中时回退≥65且净流入>0（较好观察，非极佳）。"
            "卖点默认「高位止盈 + 超买回吐 + 资金出逃避险」，老股须交叉命中至少 "
            f"{MIN_PLAN_HITS} 个方案。"
            "空列表不拿无流入观察池凑数。"
            "方案支持删除（至少留一个）；恢复默认会把已删方案加回。"
        ),
    }
