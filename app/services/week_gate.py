"""方案 K：周线门。用日 K 合成周收盘，缺数据则跳过不当成失败。"""
from __future__ import annotations

from datetime import date

from ..database import query


def weekly_closes(code: str) -> list[float]:
    rows = query(
        "SELECT date, close FROM daily_kline WHERE code=? AND close IS NOT NULL ORDER BY date",
        (code,),
    )
    weeks: dict[tuple[int, int], float] = {}
    for r in rows:
        try:
            d = date.fromisoformat(str(r["date"])[:10])
            weeks[d.isocalendar()[:2]] = float(r["close"])
        except (TypeError, ValueError):
            continue
    return [weeks[k] for k in sorted(weeks)]


def week_ma_state(code: str) -> dict:
    """返回 pass/skip 与均线。少于 20 根周线 → skip（保持命中）。"""
    closes = weekly_closes(code)
    if len(closes) < 20:
        return {
            "state": "skip",
            "ma5": None,
            "ma20": None,
            "weeks": len(closes),
            "reason": "周线不足 20 根，跳过门控",
        }
    ma5 = sum(closes[-5:]) / 5.0
    ma20 = sum(closes[-20:]) / 20.0
    state = "pass" if ma5 >= ma20 else "fail"
    return {"state": state, "ma5": round(ma5, 4), "ma20": round(ma20, 4), "weeks": len(closes), "reason": ""}


def apply_buy_gate(items: list[dict], enabled: bool = True) -> list[dict]:
    """买点：周线 MA5≥MA20 才保留；缺周线 skip 保留。卖点不调用本函数。"""
    if not enabled:
        for r in items:
            r["week_gate"] = "off"
        return items
    out = []
    for r in items:
        st = week_ma_state(r.get("code") or "")
        r["week_gate"] = st["state"]
        r["week_ma5"] = st["ma5"]
        r["week_ma20"] = st["ma20"]
        if st["state"] == "fail":
            continue
        out.append(r)
    return out
