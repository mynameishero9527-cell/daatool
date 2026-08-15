"""明日看涨概率（FR4-02-2）：市场宽度 + 指数动量 + 量能 + 周期阶段的启发式估计。

仅为量化参考概率，非预测承诺，界面须附免责声明。
"""
from ..cache import cached
from ..database import query
from . import cycle as cycle_svc
from . import kline as kline_svc


def get_forecast() -> dict:
    def loader():
        factors = []
        prob = 50.0

        rows = query("SELECT SUM(CASE WHEN pct>0 THEN 1 ELSE 0 END) AS up, COUNT(*) AS n, "
                     "SUM(CASE WHEN pct>=9.8 THEN 1 ELSE 0 END) AS lu "
                     "FROM stock_snapshot WHERE pct IS NOT NULL")
        r = rows[0] if rows else {}
        if r.get("n"):
            breadth = (r["up"] or 0) / r["n"] * 100
            delta = (breadth - 50) * 0.35
            prob += delta
            factors.append({"name": "市场宽度", "value": f"上涨占比 {breadth:.0f}%", "impact": round(delta, 1)})
            if (r.get("lu") or 0) >= 60:
                prob += 3
                factors.append({"name": "涨停效应", "value": f"涨停 {r['lu']} 家，赚钱效应偏强", "impact": 3.0})

        try:
            data = kline_svc.get_kline("sh000001", "day", 30)
            closes = [k[1] for k in data["kline"]]
            vols = data["volumes"]
            if len(closes) >= 6:
                mom5 = (closes[-1] / closes[-6] - 1) * 100
                delta = max(-8.0, min(8.0, mom5 * 1.5))
                prob += delta
                factors.append({"name": "指数动量", "value": f"上证 5 日 {mom5:+.2f}%", "impact": round(delta, 1)})
            if len(vols) >= 20:
                vr = (sum(vols[-5:]) / 5) / (sum(vols[-20:]) / 20)
                delta = 3 if vr >= 1.1 else -3 if vr <= 0.75 else 0
                prob += delta
                factors.append({"name": "量能趋势", "value": f"5日/20日均量比 {vr:.2f}", "impact": float(delta)})
        except Exception:  # noqa: BLE001
            pass

        try:
            c = cycle_svc.get_cycle()
            stage_adj = {"牛市主升": 6, "震荡上行": 3, "底部构筑": 2, "高位震荡": -2,
                         "调整下行": -5, "熊市寻底": -4}.get(c.get("stage"), 0)
            prob += stage_adj
            factors.append({"name": "周期阶段", "value": c.get("stage", "未知"), "impact": float(stage_adj)})
            panic = c.get("panic_index")
            if panic is not None:
                padj = -4.0 if panic >= 60 else 2.0 if panic < 35 else 0.0
                if padj:
                    prob += padj
                    factors.append({"name": "恐慌指数", "value": f"{panic}", "impact": padj})
        except Exception:  # noqa: BLE001
            pass

        flow = query("SELECT SUM(main_net_in) AS f, "
                     "SUM(CASE WHEN pct<=-9.8 THEN 1 ELSE 0 END) AS ld "
                     "FROM stock_snapshot WHERE pct IS NOT NULL")
        if flow:
            fsum = flow[0]["f"]
            if fsum is not None:
                fadj = 2.5 if fsum > 0 else -2.5
                prob += fadj
                factors.append({"name": "主力资金",
                                "value": f"全市场合计净{'流入' if fsum > 0 else '流出'} {abs(fsum) / 10000:.0f} 亿",
                                "impact": fadj})
            ld = flow[0]["ld"] or 0
            if ld >= 40:
                prob -= 3
                factors.append({"name": "跌停压力", "value": f"跌停 {ld} 家", "impact": -3.0})

        prob = round(max(20.0, min(80.0, prob)), 1)
        view = ("偏多" if prob >= 58 else "偏空" if prob <= 42 else "震荡")
        return {
            "prob_up": prob, "view": view,
            "desc": f"综合宽度/动量/量能/周期/恐慌/资金，明日大盘看涨概率约 {prob}%（{view}）",
            "factors": factors,
            "disclaimer": "概率为启发式量化估计，仅供参考，不构成投资建议",
        }
    return cached("market:forecast", 300, loader)
