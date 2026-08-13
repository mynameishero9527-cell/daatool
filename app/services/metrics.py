"""2.0 指标引擎：全市场K线同步、行业映射、企稳四闸门、购买指数、情绪温度、暗盘力量。

全部指标基于本地数据批量计算（盘后任务/手动触发），结果写入 stock_metrics 表。
"""
import logging
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta

from ..database import executemany, execute, get_meta, query, set_meta, set_meta_json, get_meta_json
from ..datasources import tencent
from ..cache import cache

log = logging.getLogger("metrics")

_lock = threading.Lock()
STATE = {"running": False, "stage": "未开始", "progress": 0, "total": 0}

KLINE_DAYS = 120
SYNC_WORKERS = 10


# ---------------- 全市场K线同步（OPT-01） ----------------

def _sync_one_kline(code: str) -> int:
    rows = tencent.fetch_kline(code, "day", KLINE_DAYS)
    if rows:
        executemany(
            "INSERT OR REPLACE INTO daily_kline(code,date,open,close,high,low,volume) VALUES(?,?,?,?,?,?,?)",
            [(code, r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows],
        )
    return len(rows)


def sync_all_klines() -> None:
    codes = [r["code"] for r in query("SELECT code FROM stock_list")]
    STATE.update(stage="K线同步", progress=0, total=len(codes))
    done = 0
    with ThreadPoolExecutor(max_workers=SYNC_WORKERS) as pool:
        futures = {pool.submit(_sync_one_kline, c): c for c in codes}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception:  # noqa: BLE001 - 单股失败不阻断整体
                pass
            done += 1
            if done % 200 == 0:
                STATE.update(progress=done)
    STATE.update(progress=done)
    set_meta("kline_last_sync", datetime.now().isoformat(timespec="seconds"))


# ---------------- 行业映射（OPT-08） ----------------

def sync_industries() -> None:
    STATE.update(stage="行业映射")
    boards = tencent.fetch_industries()
    set_meta_json("industry_boards", boards)
    pairs = []
    for b in boards:
        offset = 0
        while True:
            try:
                codes = tencent.fetch_board_stocks(b["board_code"], offset=offset)
            except Exception:  # noqa: BLE001
                break
            pairs.extend((b["name"], c) for c in codes if c)
            if len(codes) < 200:
                break
            offset += 200
            time.sleep(0.1)
    if pairs:
        executemany("UPDATE stock_list SET industry=? WHERE code=?", pairs)
    set_meta("industry_last_sync", datetime.now().isoformat(timespec="seconds"))


def get_industry_boards() -> list[dict]:
    return get_meta_json("industry_boards", [])


# ---------------- 技术指标工具 ----------------

def _ma(vals: list[float], n: int) -> float | None:
    return round(sum(vals[-n:]) / n, 3) if len(vals) >= n else None


def _rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if gains + losses == 0:
        return 50.0
    return round(gains / (gains + losses) * 100, 1)


def _macd(closes: list[float]) -> tuple[list[float], list[float]]:
    def ema(vals, n):
        k, out, prev = 2 / (n + 1), [], vals[0]
        for v in vals:
            prev = v * k + prev * (1 - k)
            out.append(prev)
        return out
    if not closes:
        return [], []
    e12, e26 = ema(closes, 12), ema(closes, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, 9)
    return dif, dea


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


# ---------------- 单股指标计算 ----------------

def _compute_one(code: str, snap: dict, kl: list[dict], market_env: float) -> tuple | None:
    closes = [r["close"] for r in kl]
    vols = [r["volume"] or 0 for r in kl]
    opens = [r["open"] for r in kl]
    if len(closes) < 30 or not snap:
        return None
    price = snap.get("price") or closes[-1]

    ma5, ma10, ma20, ma60 = _ma(closes, 5), _ma(closes, 10), _ma(closes, 20), _ma(closes, 60)
    rsi14 = _rsi(closes)
    dif, dea = _macd(closes)
    macd_bar = round((dif[-1] - dea[-1]) * 2, 4)
    macd_gold = int(any(dif[-i] > dea[-i] and dif[-i - 1] <= dea[-i - 1] for i in range(1, min(4, len(dif)))))
    ma_bull = int(bool(ma5 and ma10 and ma20 and ma5 > ma10 > ma20))
    above_ma20 = int(bool(ma20 and price > ma20))
    win60 = closes[-60:]
    high60, low60 = max(win60), min(win60)
    pos60 = round((price - low60) / (high60 - low60), 3) if high60 > low60 else 0.5
    drawdown60 = round((high60 - price) / high60 * 100, 1) if high60 else 0.0
    bias20 = round((price / ma20 - 1) * 100, 2) if ma20 else 0.0
    break20_high = int(price >= max(closes[-20:]))
    vol5 = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0
    vol20 = sum(vols[-20:]) / 20 if len(vols) >= 20 else 0
    vol60 = sum(vols[-60:]) / 60 if len(vols) >= 60 else 0
    pct3 = (closes[-1] / closes[-4] - 1) * 100 if len(closes) >= 4 else 0
    pullback_shrink = int(-5 < pct3 < 0 and vol20 and vol5 < vol20 * 0.8)

    # ---- 企稳四闸门（FR2-02） ----
    g1 = int(drawdown60 >= 25 and pos60 <= 0.30)
    recent10 = closes[-10:]
    conv = statistics.pstdev(recent10) / statistics.mean(recent10) if statistics.mean(recent10) else 1
    no_new_low = min(closes[-5:]) > low60 or win60.index(low60) < len(win60) - 5
    ma5_prev = _ma(closes[:-3], 5)
    ma5_slope = (ma5 / ma5_prev - 1) if (ma5 and ma5_prev) else 0
    g2 = int(conv < 0.03 and no_new_low and ma5_slope >= -0.001)
    mild_vol_up = False
    for i in range(-5, 0):
        if len(closes) + i - 1 < 0:
            continue
        day_pct = (closes[i] / closes[i - 1] - 1) * 100
        if vol5 and vols[i] >= vol5 * 1.2 and closes[i] > opens[i] and 1 <= day_pct <= 6:
            mild_vol_up = True
    g3 = int(bool(vol60 and vol5 < vol60 * 0.75 and mild_vol_up))
    main_net = snap.get("main_net_in") or 0
    main_net_d5 = snap.get("main_net_in_d5") or 0
    float_mv = snap.get("float_mv") or 0
    g4 = int(main_net_d5 > 0 or (float_mv and main_net > float_mv * 10000 * 0.003))
    stabilize_score = None
    if g1 and g2 and g3 and g4:
        s_depth = _clamp((drawdown60 - 25) * 2 + 50)
        s_conv = _clamp((0.03 - conv) / 0.03 * 100)
        s_vol = _clamp((0.75 - vol5 / vol60) / 0.75 * 60 + 40) if vol60 else 50
        s_fund = _clamp(main_net_d5 / (float_mv * 10000 + 1) * 100 * 800 + 50)
        stabilize_score = round(s_depth * 0.25 + s_conv * 0.25 + s_vol * 0.25 + s_fund * 0.25, 1)

    # ---- 暗盘力量（FR2-06，两档口径批量版） ----
    pct_today = snap.get("pct") or 0
    dark = 50.0
    if float_mv:
        dark += _clamp(main_net / (float_mv * 10000) * 100 * 500, -25, 25)
        dark += _clamp(main_net_d5 / (float_mv * 10000) * 100 * 200, -15, 15)
    divergence = "无"
    if main_net > 0 and pct_today <= 0:
        divergence = "暗中吸筹"
        dark += 10
    elif main_net < 0 and pct_today > 1:
        divergence = "暗中派发"
        dark -= 10
    dark = round(_clamp(dark), 1)

    # ---- 情绪温度（FR2-04 批量版） ----
    vr = snap.get("volume_ratio")
    turnover = snap.get("turnover_rate") or 0
    heat = 50.0
    if vol60 and vols:
        heat += _clamp((vols[-1] / vol60 - 1) * 30, -25, 25)
    if vr:
        heat += _clamp((vr - 1) * 8, -10, 15)
    behavior = _clamp(50 + pct_today * 3 + (snap.get("pct_d5") or 0) * 1.5)
    diverg_sig = 60 if (main_net > 0) == (pct_today > 0) else 40
    heat += _clamp(min(turnover, 15) - 3, -5, 10)
    sentiment = round(_clamp(heat * 0.4 + behavior * 0.35 + diverg_sig * 0.25), 1)

    # ---- 购买指数（FR2-03） ----
    f_pos = _clamp((1 - pos60) * 100 - max(bias20 - 8, 0) * 3)
    f_trend = _clamp(50 + ma_bull * 15 + above_ma20 * 10 + (10 if macd_bar > 0 else -10)
                     + _clamp((snap.get("pct_d20") or 0), -20, 20))
    f_fund = 50.0
    if float_mv:
        f_fund += _clamp(main_net / (float_mv * 10000) * 100 * 400, -30, 30)
    if vr:
        f_fund += _clamp((vr - 1) * 10, -15, 15)
    f_fund = _clamp(f_fund)
    f_sent = _clamp(100 - abs(sentiment - 60) * 1.8)  # 过热/过冷都扣分，60 附近最佳
    if sentiment < 15:
        f_sent += 15  # 恐慌冰点反向加分
    buy_index = round(_clamp(
        f_pos * 0.25 + f_trend * 0.20 + f_fund * 0.20 + dark * 0.15
        + f_sent * 0.10 + market_env * 0.10), 1)

    now = datetime.now().isoformat(timespec="seconds")
    return (code, ma5, ma10, ma20, ma60, rsi14, macd_bar, macd_gold, ma_bull, above_ma20,
            break20_high, pullback_shrink, pos60, drawdown60, bias20,
            g1, g2, g3, g4, stabilize_score, buy_index, sentiment, dark, divergence, now)


def _market_env() -> float:
    """大盘环境因子 0-100：涨跌家数比 + 指数20日趋势。"""
    rows = query("SELECT SUM(CASE WHEN pct>0 THEN 1 ELSE 0 END) AS up, "
                 "SUM(CASE WHEN pct<0 THEN 1 ELSE 0 END) AS down FROM stock_snapshot")
    up, down = (rows[0]["up"] or 0), (rows[0]["down"] or 0)
    env = 50.0
    if up + down:
        env += (up / (up + down) - 0.5) * 60
    idx = query("SELECT close FROM daily_kline WHERE code='sh000001' ORDER BY date DESC LIMIT 20")
    if len(idx) >= 20:
        closes = [r["close"] for r in reversed(idx)]
        if closes[-1] > sum(closes) / len(closes):
            env += 10
        else:
            env -= 10
    return _clamp(env)


def compute_all_metrics() -> int:
    """全市场指标重算（依赖 K 线已入库）。"""
    STATE.update(stage="指标计算")
    snaps = {r["code"]: r for r in query("SELECT * FROM stock_snapshot")}
    env = _market_env()
    kl_rows = query("SELECT code,date,open,close,volume FROM daily_kline ORDER BY code,date")
    by_code: dict[str, list[dict]] = {}
    for r in kl_rows:
        by_code.setdefault(r["code"], []).append(r)
    results = []
    for code, kl in by_code.items():
        snap = snaps.get(code)
        if not snap:
            continue
        try:
            row = _compute_one(code, snap, kl, env)
            if row:
                results.append(row)
        except Exception:  # noqa: BLE001
            continue
    if results:
        executemany(
            "INSERT OR REPLACE INTO stock_metrics(code,ma5,ma10,ma20,ma60,rsi14,macd_bar,macd_gold,"
            "ma_bull,above_ma20,break20_high,pullback_shrink,pos60,drawdown60,bias20,"
            "stab_g1,stab_g2,stab_g3,stab_g4,stabilize_score,buy_index,sentiment,dark_power,divergence,updated_at)"
            " VALUES(" + ",".join("?" * 25) + ")",
            results,
        )
    set_meta("metrics_last_compute", datetime.now().isoformat(timespec="seconds"))
    _record_stabilize()
    _archive_market_sentiment()
    for key in list(cache._store):  # noqa: SLF001
        if key.startswith(("recommend:", "screener:")):
            cache.delete(key)
    return len(results)


# ---------------- 企稳入选记录与胜率回看 ----------------

def _record_stabilize() -> None:
    today = date.today().isoformat()
    rows = query(
        "SELECT m.code, m.stabilize_score, s.price FROM stock_metrics m "
        "JOIN stock_snapshot s ON s.code=m.code WHERE m.stabilize_score IS NOT NULL")
    if rows:
        executemany(
            "INSERT OR REPLACE INTO stabilize_record(code,select_date,score,price) VALUES(?,?,?,?)",
            [(r["code"], today, r["stabilize_score"], r["price"]) for r in rows])
    # 回填历史入选的 5/10/20 日后涨幅
    pending = query("SELECT code,select_date,price FROM stabilize_record WHERE pct_after_20d IS NULL")
    updates = []
    for p in pending:
        kl = query("SELECT date,close FROM daily_kline WHERE code=? AND date>=? ORDER BY date LIMIT 21",
                   (p["code"], p["select_date"]))
        if not kl or not p["price"]:
            continue
        base = p["price"]
        def pct_at(n):
            return round((kl[n]["close"] / base - 1) * 100, 2) if len(kl) > n else None
        updates.append((pct_at(5), pct_at(10), pct_at(20), p["code"], p["select_date"]))
    if updates:
        executemany(
            "UPDATE stabilize_record SET pct_after_5d=?, pct_after_10d=?, pct_after_20d=? "
            "WHERE code=? AND select_date=?", updates)


def stabilize_stats() -> dict:
    rows = query(
        """SELECT COUNT(*) AS n,
             AVG(pct_after_5d) AS avg5, AVG(pct_after_10d) AS avg10, AVG(pct_after_20d) AS avg20,
             AVG(CASE WHEN pct_after_5d > 0 THEN 100.0 ELSE 0 END) AS win5,
             AVG(CASE WHEN pct_after_10d > 0 THEN 100.0 ELSE 0 END) AS win10,
             AVG(CASE WHEN pct_after_20d > 0 THEN 100.0 ELSE 0 END) AS win20
           FROM stabilize_record WHERE pct_after_5d IS NOT NULL""")
    r = rows[0] if rows else {}
    return {k: (round(v, 1) if v is not None else None) for k, v in r.items()}


# ---------------- 市场情绪归档 ----------------

def _archive_market_sentiment() -> None:
    temp = market_sentiment()["temp"]
    if temp is not None:
        execute("INSERT OR REPLACE INTO sentiment_history(date,market_temp) VALUES(?,?)",
                (date.today().isoformat(), temp))


def market_sentiment() -> dict:
    rows = query("SELECT AVG(sentiment) AS t FROM stock_metrics WHERE sentiment IS NOT NULL")
    temp = rows[0]["t"] if rows and rows[0]["t"] is not None else None
    stats = query(
        "SELECT SUM(CASE WHEN pct>=9.8 THEN 1 ELSE 0 END) AS lu, "
        "SUM(CASE WHEN pct>0 THEN 1 ELSE 0 END) AS up, COUNT(*) AS n "
        "FROM stock_snapshot WHERE pct IS NOT NULL")
    s = stats[0] if stats else {}
    if temp is None and s.get("n"):
        temp = _clamp(50 + ((s["up"] or 0) / s["n"] - 0.5) * 80)
    if temp is None:
        return {"temp": None, "level": "未知", "desc": "指标尚未计算", "history": []}
    temp = round(temp, 1)
    level, desc = sentiment_level(temp)
    history = query("SELECT date, market_temp FROM sentiment_history ORDER BY date DESC LIMIT 30")
    return {"temp": temp, "level": level, "desc": desc,
            "limit_up": s.get("lu") or 0,
            "history": [dict(h) for h in reversed(history)]}


def sentiment_level(temp: float) -> tuple[str, str]:
    table = [
        (85, "亢奋", "情绪亢奋，交投火爆，注意过热风险"),
        (70, "乐观", "人气回升，做多情绪积极"),
        (55, "偏暖", "情绪偏暖，资金温和参与"),
        (45, "平静", "交投平淡，观望情绪浓"),
        (30, "谨慎", "情绪转冷，参与意愿下降"),
        (15, "悲观", "杀跌情绪蔓延，人气低迷"),
        (0, "恐慌", "恐慌宣泄，或接近情绪冰点"),
    ]
    for th, lv, ds in table:
        if temp >= th:
            return lv, ds
    return "平静", ""


def buy_index_level(v: float) -> tuple[str, str]:
    table = [
        (80, "极佳买点", "可分批建仓"),
        (65, "较好买点", "轻仓试探"),
        (50, "中性", "观望等待"),
        (35, "偏差买点", "不宜追入"),
        (0, "高风险买点", "回避"),
    ]
    for th, lv, act in table:
        if v >= th:
            return lv, act
    return "中性", "观望等待"


def dark_level(v: float) -> tuple[str, str]:
    table = [
        (75, "暗盘强买", "隐性资金持续吸筹"),
        (60, "暗盘偏买", "大资金温和流入"),
        (40, "均衡", "买卖力量相当"),
        (25, "暗盘偏卖", "大资金悄然减仓"),
        (0, "暗盘强卖", "隐性派发明显，警惕"),
    ]
    for th, lv, ds in table:
        if v >= th:
            return lv, ds
    return "均衡", ""


# ---------------- 全流程入口 ----------------

def rebuild_all(include_kline: bool = True) -> dict:
    """K线同步 → 行业映射 → 指标重算。后台线程执行。"""
    if not _lock.acquire(blocking=False):
        return state()
    try:
        STATE.update(running=True)
        if include_kline:
            sync_all_klines()
            try:
                sync_industries()
            except Exception as exc:  # noqa: BLE001
                log.warning("行业映射失败: %s", exc)
        n = compute_all_metrics()
        STATE.update(running=False, stage=f"完成，共 {n} 只")
        return state()
    except Exception as exc:  # noqa: BLE001
        STATE.update(running=False, stage=f"失败: {exc}")
        return state()
    finally:
        _lock.release()


def state() -> dict:
    return {
        **STATE,
        "kline_last_sync": get_meta("kline_last_sync", "从未"),
        "metrics_last_compute": get_meta("metrics_last_compute", "从未"),
        "industry_last_sync": get_meta("industry_last_sync", "从未"),
        "metrics_count": query("SELECT COUNT(*) AS n FROM stock_metrics")[0]["n"],
        "kline_codes": query("SELECT COUNT(DISTINCT code) AS n FROM daily_kline")[0]["n"],
    }
