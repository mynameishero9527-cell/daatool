"""大周期阶段研判（FR3-05）：基于上证指数日K + 市场宽度 + 量能，判定六阶段。"""
from ..cache import cached
from ..database import query
from . import kline as kline_svc

STAGES = {
    "bull_run": ("牛市主升", "趋势强劲，顺势而为", "level-5"),
    "shock_up": ("震荡上行", "结构性行情，精选个股", "level-4"),
    "high_shock": ("高位震荡", "高位分歧，注意兑现", "level-3"),
    "adjust_down": ("调整下行", "防御为主，控制仓位", "level-1"),
    "bear_bottom": ("熊市寻底", "底部未明，耐心等待", "level-0"),
    "base_build": ("底部构筑", "黎明前夜，分批布局", "level-2"),
}


def _vol_desc(ratio: float | None) -> str:
    if ratio is None:
        return "量能未知"
    if ratio >= 1.5:
        return "显著放量"
    if ratio >= 1.1:
        return "温和放量"
    if ratio >= 0.85:
        return "量能平稳"
    if ratio >= 0.6:
        return "缩量"
    return "地量"


def get_cycle() -> dict:
    def loader():
        data = kline_svc.get_kline("sh000001", "day", 320)
        closes = [k[1] for k in data["kline"]]  # 蜡烛序 [open,close,high,low]，close 为第2列
        vols = data["volumes"]
        if len(closes) < 130:
            return {"stage": "未知", "desc": "指数K线数据不足", "signals": [], "vol_desc": "未知"}
        price = closes[-1]
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60
        ma120 = sum(closes[-120:]) / 120
        ma20_prev = sum(closes[-25:-5]) / 20
        win60 = closes[-60:]
        pos60 = (price - min(win60)) / (max(win60) - min(win60)) if max(win60) > min(win60) else 0.5
        win250 = closes[-250:] if len(closes) >= 250 else closes
        pos_year = (price - min(win250)) / (max(win250) - min(win250)) if max(win250) > min(win250) else 0.5
        vol5 = sum(vols[-5:]) / 5
        vol20 = sum(vols[-20:]) / 20
        vol_ratio = vol5 / vol20 if vol20 else None
        conv10 = (max(closes[-10:]) - min(closes[-10:])) / price if price else 1

        rows = query(
            "SELECT SUM(CASE WHEN pct>0 THEN 1 ELSE 0 END) AS up, COUNT(*) AS n, "
            "SUM(CASE WHEN pct<=-9.8 THEN 1 ELSE 0 END) AS ld, "
            "SUM(CASE WHEN pct>=9.8 THEN 1 ELSE 0 END) AS lu "
            "FROM stock_snapshot WHERE pct IS NOT NULL")
        breadth = (rows[0]["up"] or 0) / rows[0]["n"] * 100 if rows and rows[0]["n"] else 50

        # ---- 恐慌指数与攻守姿态（FR6-01） ----
        rets = [(closes[i] / closes[i - 1] - 1) for i in range(-19, 0)]
        vol20 = (sum(r * r for r in rets) / len(rets)) ** 0.5 * (250 ** 0.5) * 100  # 年化波动%
        panic = 0.0
        panic += min(35.0, max(0.0, (vol20 - 12) / 28 * 35))          # 波动率 12%~40% 映射
        panic += (100 - breadth) / 100 * 25                            # 下跌家数占比
        lu, ld = (rows[0]["lu"] or 0), (rows[0]["ld"] or 0)
        if lu + ld > 0:
            panic += ld / (lu + ld) * 20                               # 涨跌停失衡
        drop3 = (closes[-1] / closes[-4] - 1) * 100 if len(closes) >= 4 else 0
        if drop3 < -2 and (vol_ratio or 1) < 0.9:
            panic += 20                                                # 缩量急跌
        elif drop3 < -1:
            panic += 10
        panic = round(min(100.0, panic), 1)
        if panic >= 60:
            stance, stance_css = "防守", "level-0"
            stance_desc = "恐慌偏高：控制仓位，回避高位股，以防守反击为主"
        elif panic >= 40:
            stance, stance_css = "均衡", "level-2"
            stance_desc = "多空拉锯：结构性参与，严格止损"
        else:
            stance, stance_css = "进攻", "level-4"
            stance_desc = "情绪平稳：可顺势积极做多强势主线"

        # 阶段判定（自上而下匹配）
        if price > ma20 > ma60 > ma120 and pos60 > 0.7 and breadth > 55:
            key = "bull_run"
        elif pos60 > 0.75 and abs(ma20 / ma20_prev - 1) < 0.005 and (vol_ratio or 1) < 0.9:
            key = "high_shock"
        elif price > ma60 and ma20 > ma20_prev and breadth >= 45:
            key = "shock_up"
        elif price < ma120 and pos60 < 0.3 and (vol_ratio or 1) < 0.8:
            key = "bear_bottom"
        elif pos60 < 0.35 and conv10 < 0.04:
            key = "base_build"
        elif price < ma20 < ma60 or breadth < 45:
            key = "adjust_down"
        else:
            key = "shock_up"

        name, desc, css = STAGES[key]
        signals = [
            {"name": "均线结构", "ok": price > ma20 > ma60,
             "text": f"指数 {price:.0f}，MA20 {ma20:.0f} / MA60 {ma60:.0f} / MA120 {ma120:.0f}"},
            {"name": "60日位置", "ok": pos60 > 0.5, "text": f"位于 60 日区间 {pos60 * 100:.0f}% 分位"},
            {"name": "年内位置", "ok": pos_year > 0.5, "text": f"位于一年区间 {pos_year * 100:.0f}% 分位"},
            {"name": "市场宽度", "ok": breadth > 50, "text": f"上涨家数占比 {breadth:.0f}%"},
            {"name": "量能趋势", "ok": (vol_ratio or 1) >= 1,
             "text": f"5日均量/20日均量 = {vol_ratio:.2f}" if vol_ratio else "量能数据不足"},
        ]
        return {
            "stage": name, "stage_desc": desc, "stage_css": css,
            "index_price": round(price, 2), "pos60": round(pos60, 2),
            "breadth": round(breadth, 1),
            "vol_desc": _vol_desc(vol_ratio), "vol_ratio": round(vol_ratio, 2) if vol_ratio else None,
            "panic_index": panic, "volatility20": round(vol20, 1),
            "stance": stance, "stance_css": stance_css, "stance_desc": stance_desc,
            "signals": signals,
        }
    return cached("market:cycle", 300, loader)


def get_stance() -> dict:
    """轻量获取攻守姿态（供个股描述接入）。"""
    try:
        c = get_cycle()
        return {"stance": c.get("stance", "均衡"), "panic": c.get("panic_index"),
                "desc": c.get("stance_desc", "")}
    except Exception:  # noqa: BLE001
        return {"stance": "均衡", "panic": None, "desc": ""}
