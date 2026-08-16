"""日 K 回放网关。A–H 缺 metrics_daily 保持关闭；有 factor_daily 才开放技术因子回放。"""
from __future__ import annotations

from . import factor_frame
from ..database import query

AH_REPLAY_REASON = (
    "方案 A–H 历史重放需要 metrics_daily 按日截面。"
    "当前 stock_metrics 仅为最新一天，若用它回放会把今天的指标当成昨天的条件，属于未来函数。"
    "入口保持关闭。"
)

FACTOR_RULE = (
    "技术因子回放（不是方案 A–H / I/J）：\n"
    "买：ma5>ma10>ma20 ∧ macd_bar>0 ∧ 45≤rsi14≤70 ∧ bias20>-2\n"
    "卖：ma5<ma10 ∨ macd_bar<0 ∨ rsi14>80\n"
    "撮合：信号日下一交易日开盘；无开盘用下一根收盘并标 fill=close。\n"
    "T+1：买入当日不能卖出。止盈 +12% / 止损 −8%，用当日高低价近似；"
    "同日高低都触及记 invalid_ambiguous，不选边。\n"
    "一字涨停（开=高=低=收且相对昨收≥9.5%）不成交。\n"
    "费用默认双边万三 + 印花税（卖出 0.1%），可关。\n"
    "样本 < 30 不展示年化/夏普。高低价触及为近似，不是 tick 成交。"
)

STOP_PCT = 0.08
TAKE_PCT = 0.12
MAX_HOLD = 20
COMMISSION = 0.0003
STAMP = 0.001


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def buy_signal(f: dict) -> bool:
    ma5, ma10, ma20 = _f(f.get("ma5")), _f(f.get("ma10")), _f(f.get("ma20"))
    macd, rsi, bias = _f(f.get("macd_bar")), _f(f.get("rsi14")), _f(f.get("bias20"))
    if None in (ma5, ma10, ma20, macd, rsi, bias):
        return False
    return ma5 > ma10 > ma20 and macd > 0 and 45 <= rsi <= 70 and bias > -2


def sell_signal(f: dict) -> bool:
    ma5, ma10 = _f(f.get("ma5")), _f(f.get("ma10"))
    macd, rsi = _f(f.get("macd_bar")), _f(f.get("rsi14"))
    if ma5 is not None and ma10 is not None and ma5 < ma10:
        return True
    if macd is not None and macd < 0:
        return True
    if rsi is not None and rsi > 80:
        return True
    return False


def _limit_locked(prev_close, o, h, l, c) -> bool:
    vals = (_f(prev_close), _f(o), _f(h), _f(l), _f(c))
    if any(v is None for v in vals):
        return False
    pc, o_, h_, l_, c_ = vals
    if pc <= 0:
        return False
    return o_ == h_ == l_ == c_ and o_ >= pc * 1.095


def _net_ret(entry: float, exit_px: float, fees: bool) -> float:
    raw = (exit_px - entry) / entry
    if not fees:
        return raw
    buy_cost = entry * COMMISSION
    sell_cost = exit_px * (COMMISSION + STAMP)
    return (exit_px - entry - buy_cost - sell_cost) / entry


def _fwd(bars: list[dict], i: int, n: int, entry: float) -> float | None:
    j = i + n
    if j >= len(bars) or entry <= 0:
        return None
    close = _f(bars[j].get("close"))
    if close is None:
        return None
    return round((close - entry) / entry * 100.0, 2)


def ah_replay_status() -> dict:
    return {"open": False, "reason": AH_REPLAY_REASON}


def factor_status() -> dict:
    has = factor_frame.has_rows()
    return {
        "open": has,
        "rows": factor_frame.row_count(),
        "reason": "" if has else "缺少按日因子表 factor_daily，回测入口关闭，避免用最新截面做假回测。",
        "rule": FACTOR_RULE,
        "ah_replay": ah_replay_status(),
    }


def run_factor_backtest(
    start: str = "",
    end: str = "",
    fees: bool = True,
    max_codes: int = 80,
    codes: list[str] | None = None,
) -> dict:
    st = factor_status()
    if not st["open"]:
        return {
            "ok": True, "open": False, "reason": st["reason"],
            "trades": [], "sample": 0, "disclaimer": "量化参考，不构成投资建议",
        }
    where = ["1=1"]
    params: list = []
    if start:
        where.append("date>=?")
        params.append(start[:10])
    if end:
        where.append("date<=?")
        params.append(end[:10])
    if codes:
        picked = [c for c in codes if c]
    else:
        picked = [
            r["code"] for r in query(
                f"SELECT DISTINCT code FROM factor_daily WHERE {' AND '.join(where)} ORDER BY code LIMIT ?",
                (*params, max(5, min(int(max_codes or 80), 200))),
            )
        ]
    codes = picked
    trades = []
    for code in codes:
        bars = query(
            "SELECT date,open,high,low,close FROM daily_kline WHERE code=? ORDER BY date",
            (code,),
        )
        facts = {
            r["date"]: r for r in query(
                "SELECT * FROM factor_daily WHERE code=? ORDER BY date", (code,)
            )
        }
        if len(bars) < 30:
            continue
        pos = None
        for i, bar in enumerate(bars):
            d = bar.get("date") or ""
            if start and d < start[:10]:
                continue
            if end and d > end[:10]:
                break
            f = facts.get(d)
            if pos is None:
                if not f or not buy_signal(f):
                    continue
                if i + 1 >= len(bars):
                    break
                nxt = bars[i + 1]
                if _limit_locked(bar.get("close"), nxt.get("open"), nxt.get("high"), nxt.get("low"), nxt.get("close")):
                    trades.append({
                        "code": code, "signal_date": d, "status": "invalid",
                        "note": "一字涨停无法成交，不成交", "fill": None,
                    })
                    continue
                fill_px = _f(nxt.get("open"))
                fill = "open"
                if fill_px is None:
                    fill_px = _f(nxt.get("close"))
                    fill = "close"
                if fill_px is None or fill_px <= 0:
                    continue
                pos = {
                    "signal_date": d,
                    "entry_date": nxt["date"],
                    "entry_i": i + 1,
                    "entry": fill_px,
                    "fill": fill,
                    "stop": round(fill_px * (1 - STOP_PCT), 4),
                    "take": round(fill_px * (1 + TAKE_PCT), 4),
                }
                continue
            # T+1：买入当日不卖
            if d <= pos["entry_date"]:
                continue
            hi, lo, close = _f(bar.get("high")), _f(bar.get("low")), _f(bar.get("close"))
            hit_stop = hi is not None and lo is not None and lo <= pos["stop"]
            hit_take = hi is not None and lo is not None and hi >= pos["take"]
            held = i - pos["entry_i"]
            status = note = None
            exit_px = None
            if hit_stop and hit_take:
                status, exit_px, note = "invalid_ambiguous", close, "当日高低价同时触及止盈与止损，不选边"
            elif hit_take:
                status, exit_px, note = "hit_take", pos["take"], "高低价触及止盈（近似）"
            elif hit_stop:
                status, exit_px, note = "hit_stop", pos["stop"], "高低价触及止损（近似）"
            elif f and sell_signal(f):
                if i + 1 < len(bars):
                    nxt = bars[i + 1]
                    exit_px = _f(nxt.get("open")) or _f(nxt.get("close"))
                    status = "signal_exit"
                    note = "卖出信号，下一根开盘（无开盘则收盘）"
                    d = nxt.get("date") or d
                else:
                    status, exit_px, note = "expired", close, "区间结束，按收盘"
            elif held >= MAX_HOLD:
                status, exit_px, note = "expired", close, "超过有效期，按收盘结束"
            if not status:
                continue
            ret = None if exit_px is None or pos["entry"] <= 0 else round(
                _net_ret(pos["entry"], float(exit_px), fees) * 100.0, 2)
            ei = pos["entry_i"]
            trades.append({
                "code": code,
                "signal_date": pos["signal_date"],
                "entry_date": pos["entry_date"],
                "exit_date": d,
                "entry_px": pos["entry"],
                "exit_px": exit_px,
                "fill": pos["fill"],
                "status": status,
                "ret_pct": ret,
                "ret_1": _fwd(bars, ei, 1, pos["entry"]),
                "ret_5": _fwd(bars, ei, 5, pos["entry"]),
                "ret_20": _fwd(bars, ei, 20, pos["entry"]),
                "note": note,
            })
            pos = None
        if pos:
            trades.append({
                "code": code,
                "signal_date": pos["signal_date"],
                "entry_date": pos["entry_date"],
                "exit_date": None,
                "entry_px": pos["entry"],
                "exit_px": None,
                "fill": pos["fill"],
                "status": "open",
                "ret_pct": None,
                "ret_1": _fwd(bars, pos["entry_i"], 1, pos["entry"]),
                "ret_5": _fwd(bars, pos["entry_i"], 5, pos["entry"]),
                "ret_20": _fwd(bars, pos["entry_i"], 20, pos["entry"]),
                "note": "区间内未平仓",
            })

    closed = [t for t in trades if t.get("status") not in ("open", "invalid", "invalid_ambiguous") and t.get("ret_pct") is not None]
    sample = len(closed)
    avg = lambda key: (
        round(sum(t[key] for t in closed if t.get(key) is not None) / max(1, sum(1 for t in closed if t.get(key) is not None)), 2)
        if any(t.get(key) is not None for t in closed) else None
    )
    summary = {
        "sample": sample,
        "trades": len(trades),
        "closed": sample,
        "avg_ret_pct": round(sum(t["ret_pct"] for t in closed) / sample, 2) if sample else None,
        "avg_ret_1": avg("ret_1"),
        "avg_ret_5": avg("ret_5"),
        "avg_ret_20": avg("ret_20"),
        "sharpe": None,
        "ann_ret": None,
        "sharpe_note": "样本 < 30，不展示年化/夏普" if sample < 30 else "本回放未计算组合日收益序列，仍不展示年化/夏普",
    }
    return {
        "ok": True,
        "open": True,
        "kind": "factor",
        "rule": FACTOR_RULE,
        "ah_replay": ah_replay_status(),
        "fees": bool(fees),
        "fill_note": "默认下一根开盘；无开盘用收盘并标 fill=close。高低价触及为止盈止损近似。",
        "interval": {"start": start or "", "end": end or ""},
        "codes": len(codes),
        "summary": summary,
        "trades": trades[:200],
        "disclaimer": "研究回放，不是实盘资金曲线。量化参考，不构成投资建议",
    }
