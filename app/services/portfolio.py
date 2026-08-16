"""研究组合约束：等权后再按单只/单行业上限切分，剩余不重分配到已达上限行业。"""
from __future__ import annotations

from collections import defaultdict

from . import holder_feature, strategy as strategy_svc

STOCK_CAP = 0.10
INDUSTRY_CAP = 0.30
TOTAL_CAP = 1.0


def constrain(
    items: list[dict],
    *,
    stock_cap: float = STOCK_CAP,
    industry_cap: float = INDUSTRY_CAP,
    total_cap: float = TOTAL_CAP,
    drop_sell: bool = True,
    drop_unlock: bool = False,
) -> tuple[list[dict], dict]:
    rows = [dict(r) for r in items]
    notes: list[str] = []
    if drop_sell:
        sell_codes = {r.get("code") for r in strategy_svc.collect_hits("sell", limit=80) if r.get("code")}
        before = len(rows)
        rows = [r for r in rows if r.get("code") not in sell_codes]
        dropped = before - len(rows)
        if dropped:
            notes.append(f"剔除卖点池 {dropped} 只")
    if drop_unlock:
        unlock_codes = holder_feature.near_unlock_codes()
        if unlock_codes is None:
            notes.append("无解禁特征，大解禁剔除跳过")
        else:
            before = len(rows)
            rows = [r for r in rows if r.get("code") not in unlock_codes]
            dropped = before - len(rows)
            if dropped:
                notes.append(f"剔除未来10日大解禁 {dropped} 只")
    n = len(rows)
    if n == 0:
        return [], {
            "ok": True, "count": 0, "total": 0.0, "leftover": 0.0,
            "stock_cap": stock_cap, "industry_cap": industry_cap, "notes": notes,
        }
    equal = total_cap / n
    weights: dict[str, float] = {}
    leftover = 0.0
    for r in rows:
        code = r.get("code") or ""
        w = min(equal, stock_cap)
        leftover += equal - w
        weights[code] = w
        r["industry_unconstrained"] = not bool((r.get("industry") or "").strip())

    by_ind: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        ind = (r.get("industry") or "").strip()
        if ind:
            by_ind[ind].append(r)
    for _ind, members in by_ind.items():
        s = sum(weights[m["code"]] for m in members)
        if s > industry_cap + 1e-12:
            scale = industry_cap / s
            for m in members:
                old = weights[m["code"]]
                new = old * scale
                leftover += old - new
                weights[m["code"]] = new

    total = 0.0
    for r in rows:
        w = weights.get(r.get("code") or "") or 0.0
        r["suggest_weight"] = round(w, 6)
        r["suggest_weight_pct"] = round(w * 100.0, 2)
        total += w
    notes.append(
        f"等权后单只≤{stock_cap:.0%}、单行业≤{industry_cap:.0%}；"
        f"未分配 {leftover * 100:.2f}% 不重分配到已达上限行业"
    )
    return rows, {
        "ok": True,
        "count": n,
        "total": round(total, 6),
        "leftover": round(leftover, 6),
        "stock_cap": stock_cap,
        "industry_cap": industry_cap,
        "notes": notes,
    }
