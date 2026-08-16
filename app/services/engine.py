"""策略引擎 13.0：采集本地库 → 向量/信号包/Brief 快照 → 智能选股消费。

策略函数禁止打行情 HTTP。缺数保留 null + 原因，不编造财报 A、政策、盘口、胜率。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..database import execute, executemany, get_meta_json, query, set_meta_json
from . import engine_blueprint as bp
from . import strategy as strategy_svc

log = logging.getLogger("engine")
_TZ = ZoneInfo("Asia/Shanghai")
META_KEY = "engine_config"

FIN_MAP = {"A": 100.0, "B": 75.0, "C": 45.0, "D": 15.0}
STOP_PCT = 0.08
TAKE_PCT = 0.12
EXPIRE_N = 10


def _now() -> datetime:
    return datetime.now(_TZ).replace(tzinfo=None)


def _today() -> str:
    return _now().strftime("%Y-%m-%d")


def ensure_tables() -> None:
    execute(
        "CREATE TABLE IF NOT EXISTS engine_run ("
        "run_id TEXT PRIMARY KEY, asof TEXT NOT NULL, kind TEXT NOT NULL,"
        "started_at TEXT, finished_at TEXT, status TEXT,"
        "domains_json TEXT, brief_json TEXT, note TEXT)"
    )
    execute(
        "CREATE TABLE IF NOT EXISTS engine_stock ("
        "run_id TEXT NOT NULL, code TEXT NOT NULL, name TEXT,"
        "price REAL, pct REAL, industry TEXT,"
        "d_buy REAL, d_sent REAL, d_dark REAL, d_stab REAL, d_flow REAL,"
        "d_vol REAL, d_fin REAL, d_sector REAL, d_macro REAL, d_ext REAL, d_env REAL,"
        "fin_missing INTEGER DEFAULT 0, score REAL,"
        "hits_json TEXT, catalysts_json TEXT, boards_json TEXT, reason_bits TEXT,"
        "PRIMARY KEY (run_id, code))"
    )
    execute("CREATE INDEX IF NOT EXISTS idx_engine_stock_score ON engine_stock(run_id, score)")
    execute(
        "CREATE TABLE IF NOT EXISTS engine_sector ("
        "run_id TEXT NOT NULL, dim TEXT NOT NULL, name TEXT NOT NULL,"
        "hot_score REAL, pct REAL, net_in REAL, direction TEXT, tags TEXT,"
        "PRIMARY KEY (run_id, dim, name))"
    )
    execute(
        "CREATE TABLE IF NOT EXISTS engine_catalyst ("
        "run_id TEXT NOT NULL, cat_id TEXT NOT NULL, kind TEXT,"
        "title TEXT, direction TEXT, impact_level INTEGER,"
        "sectors_json TEXT, event_time TEXT, extra_json TEXT,"
        "PRIMARY KEY (run_id, cat_id))"
    )
    execute(
        "CREATE TABLE IF NOT EXISTS signal_task ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "asof TEXT, code TEXT, name TEXT, side TEXT, plan_id TEXT,"
        "entry_px REAL, stop_px REAL, take_px REAL, expire_n INTEGER,"
        "status TEXT, exit_date TEXT, exit_px REAL,"
        "ret_1 REAL, ret_5 REAL, ret_20 REAL, note TEXT, run_id TEXT)"
    )
    execute("CREATE INDEX IF NOT EXISTS idx_signal_task_open ON signal_task(status, asof)")


def default_config() -> dict:
    return {
        "enabled": False,
        "intraday": False,
        "llm_brief": False,
        "intraday_minutes": 10,
        "domains": {d["id"]: bool(d.get("on", True)) for d in bp.DOMAINS},
        "weights": {w["id"]: int(w["w"]) for w in bp.WEIGHTS},
        "consume": {
            "smartpick_vector": True,
            "smartpick_signals": True,
            "smartpick_catalyst": True,
        },
        "week_gate": True,
        "build_factor_daily": False,
        "paper_enabled": False,
    }


def get_config() -> dict:
    raw = get_meta_json(META_KEY, {}) or {}
    out = default_config()
    if isinstance(raw, dict):
        if "enabled" in raw:
            out["enabled"] = bool(raw["enabled"])
        if "intraday" in raw:
            out["intraday"] = bool(raw["intraday"])
        if "llm_brief" in raw:
            out["llm_brief"] = bool(raw["llm_brief"])
        if isinstance(raw.get("domains"), dict):
            out["domains"].update({k: bool(v) for k, v in raw["domains"].items() if k in out["domains"]})
        if isinstance(raw.get("weights"), dict):
            for k, v in raw["weights"].items():
                if k in out["weights"]:
                    try:
                        out["weights"][k] = max(0, min(40, int(v)))
                    except (TypeError, ValueError):
                        pass
        if isinstance(raw.get("consume"), dict):
            out["consume"].update({k: bool(v) for k, v in raw["consume"].items() if k in out["consume"]})
        if "week_gate" in raw:
            out["week_gate"] = bool(raw["week_gate"])
        if "build_factor_daily" in raw:
            out["build_factor_daily"] = bool(raw["build_factor_daily"])
        if "paper_enabled" in raw:
            out["paper_enabled"] = bool(raw["paper_enabled"])
    return out


def save_config(payload: dict) -> dict:
    cur = get_config()
    body = payload or {}
    if "enabled" in body:
        cur["enabled"] = bool(body["enabled"])
    if "intraday" in body:
        cur["intraday"] = bool(body["intraday"])
    if "llm_brief" in body:
        cur["llm_brief"] = bool(body["llm_brief"])
    if isinstance(body.get("domains"), dict):
        for k, v in body["domains"].items():
            if k in cur["domains"]:
                cur["domains"][k] = bool(v)
    if isinstance(body.get("weights"), dict):
        for k, v in body["weights"].items():
            if k in cur["weights"]:
                try:
                    cur["weights"][k] = max(0, min(40, int(v)))
                except (TypeError, ValueError):
                    pass
    if isinstance(body.get("consume"), dict):
        for k, v in body["consume"].items():
            if k in cur["consume"]:
                cur["consume"][k] = bool(v)
    if "week_gate" in body:
        cur["week_gate"] = bool(body["week_gate"])
    if "build_factor_daily" in body:
        cur["build_factor_daily"] = bool(body["build_factor_daily"])
    if "paper_enabled" in body:
        cur["paper_enabled"] = bool(body["paper_enabled"])
    set_meta_json(META_KEY, cur)
    return {"ok": True, **cur}


def _count(sql: str, params: tuple = ()) -> int:
    try:
        return int((query(sql, params)[0] or {}).get("n") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _asof_of(sql: str, params: tuple = ()) -> str:
    try:
        rows = query(sql, params)
        v = (rows[0] or {}).get("t") if rows else None
        return (v or "")[:19]
    except Exception:  # noqa: BLE001
        return ""


def collect_domains(cfg: dict) -> dict:
    """只读本地库，记录新鲜度。不打 HTTP。"""
    on = cfg.get("domains") or {}
    specs = {
        "market": ("SELECT COUNT(*) AS n FROM stock_snapshot WHERE pct IS NOT NULL",
                   "SELECT MAX(updated_at) AS t FROM stock_snapshot"),
        "quote": ("SELECT COUNT(*) AS n FROM stock_snapshot",
                  "SELECT MAX(updated_at) AS t FROM stock_snapshot"),
        "flow": ("SELECT COUNT(*) AS n FROM stock_snapshot WHERE main_net_in IS NOT NULL",
                 "SELECT MAX(updated_at) AS t FROM stock_snapshot"),
        "metrics": ("SELECT COUNT(*) AS n FROM stock_metrics",
                    "SELECT MAX(updated_at) AS t FROM stock_metrics"),
        "finance": ("SELECT COUNT(*) AS n FROM stock_finance_grade",
                    "SELECT MAX(updated_at) AS t FROM stock_finance_grade"),
        "industry": ("SELECT COUNT(*) AS n FROM sector_flow_daily WHERE dim='industry'",
                     "SELECT MAX(trade_date) AS t FROM sector_flow_daily WHERE dim='industry'"),
        "concept": ("SELECT COUNT(*) AS n FROM concept_map", ""),
        "news": ("SELECT COUNT(*) AS n FROM intel_cache",
                 "SELECT MAX(event_time) AS t FROM intel_cache"),
        "policy": ("SELECT COUNT(*) AS n FROM official_policy",
                   "SELECT MAX(event_time) AS t FROM official_policy"),
        "hot_terms": ("SELECT COUNT(*) AS n FROM hot_term",
                      "SELECT MAX(updated_at) AS t FROM hot_term"),
        "calendar": ("SELECT COUNT(*) AS n FROM intel_cache WHERE kind='calendar'",
                     "SELECT MAX(event_time) AS t FROM intel_cache WHERE kind='calendar'"),
        "announce": ("SELECT COUNT(*) AS n FROM intel_cache", ""),
        "commodity": ("SELECT 1 AS n", ""),
        "global": ("SELECT 1 AS n", ""),
        "strategy_hit": ("SELECT COUNT(*) AS n FROM stock_metrics", ""),
        "holders": ("SELECT COUNT(*) AS n FROM stock_holders",
                    "SELECT MAX(fetched_at) AS t FROM stock_holders"),
    }
    out = {}
    for did, (cnt_sql, asof_sql) in specs.items():
        enabled = bool(on.get(did, True))
        if not enabled:
            out[did] = {"available": False, "rows": 0, "asof": "", "error": "域已关闭"}
            continue
        rows = _count(cnt_sql) if cnt_sql else 0
        asof = _asof_of(asof_sql) if asof_sql else ""
        err = "" if rows else "无数据"
        out[did] = {"available": bool(rows), "rows": rows, "asof": asof, "error": err}
    return out


def _clamp(v, lo=0.0, hi=100.0):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, x))


def market_regime() -> dict:
    stats = query(
        """SELECT SUM(CASE WHEN pct>0 THEN 1 ELSE 0 END) AS up,
                  SUM(CASE WHEN pct<0 THEN 1 ELSE 0 END) AS down,
                  COUNT(*) AS n, SUM(amount) AS amount
           FROM stock_snapshot WHERE pct IS NOT NULL"""
    )
    st = stats[0] if stats else {}
    n = int(st.get("n") or 0)
    up = int(st.get("up") or 0)
    down = int(st.get("down") or 0)
    if n < 50:
        return {"label": "未知", "d_env": None, "up_ratio": None, "temp": None,
                "volume": "未知", "sentence": "指数或涨跌家数不足，体制记为未知。",
                "missing_reason": "快照家数不足"}
    up_ratio = up / n
    idx = query("SELECT pct FROM stock_snapshot WHERE code IN ('sh000001','sz399001') AND pct IS NOT NULL")
    idx_pct = None
    if idx:
        vals = [r["pct"] for r in idx if r.get("pct") is not None]
        idx_pct = sum(vals) / len(vals) if vals else None
    sent = query("SELECT AVG(sentiment) AS t FROM stock_metrics WHERE sentiment IS NOT NULL")
    temp = sent[0]["t"] if sent and sent[0].get("t") is not None else None
    label = "震荡"
    if idx_pct is not None and up_ratio >= 0.62 and idx_pct > 0.8:
        label = "偏多"
    elif idx_pct is not None and (1 - up_ratio) >= 0.62 and idx_pct < -0.8:
        label = "偏空"
    base = {"偏多": 70.0, "震荡": 50.0, "偏空": 30.0}[label]
    if temp is not None:
        base = _clamp(base + max(-10, min(10, (float(temp) - 50) * 0.25)))
    vol = "平"
    amount = float(st.get("amount") or 0)
    sentence = (
        f"{label}：上涨{up} / 下跌{down}，上涨家数比 {up_ratio:.0%}"
        + (f"，主要指数约 {idx_pct:+.2f}%" if idx_pct is not None else "")
        + (f"，情绪均温 {temp:.0f}" if temp is not None else "")
        + f"。两市成交约 {amount/10000:.0f} 亿。"
    )
    return {
        "label": label, "d_env": base, "up_ratio": round(up_ratio, 3),
        "temp": round(float(temp), 1) if temp is not None else None,
        "volume": vol, "sentence": sentence, "idx_pct": idx_pct,
        "up": up, "down": down, "n": n,
    }


def sector_rotation() -> list[dict]:
    from . import sector as sector_svc
    items = []
    try:
        flow = sector_svc.get_flow("industry") or {}
        for it in (flow.get("items") or [])[:80]:
            name = it.get("name") or ""
            if not name:
                continue
            net = it.get("net_in_yi")
            items.append({
                "dim": "industry", "name": name,
                "hot_score": it.get("hot_score") or it.get("pct"),
                "pct": it.get("pct"), "net_in": net,
                "direction": "in" if (net or 0) > 0 else "out",
                "tags": [],
            })
    except Exception as exc:  # noqa: BLE001
        log.warning("板块轮动读取失败: %s", exc)
    return items


def macro_catalysts(days: int = 14) -> list[dict]:
    cutoff = (_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    cats: list[dict] = []
    try:
        rows = query(
            "SELECT policy_id,title,scope,country,doc_type,event_time,affected_sectors,"
            "impact_level,impact_direction FROM official_policy "
            "WHERE event_time>=? ORDER BY event_time DESC LIMIT 40", (cutoff,))
        for r in rows:
            try:
                secs = json.loads(r.get("affected_sectors") or "[]")
            except Exception:  # noqa: BLE001
                secs = []
            cats.append({
                "cat_id": f"policy:{r['policy_id']}", "kind": "policy",
                "title": r.get("title") or "", "direction": r.get("impact_direction") or "",
                "impact_level": r.get("impact_level") or 1,
                "sectors": secs, "event_time": r.get("event_time") or "",
                "extra": {"scope": r.get("scope"), "country": r.get("country"),
                          "doc_type": r.get("doc_type")},
            })
    except Exception:  # noqa: BLE001
        pass
    try:
        today = _today()
        rows = query(
            "SELECT term,heat,rise,fall,sectors FROM hot_term WHERE window_end=? "
            "ORDER BY heat DESC LIMIT 24", (today,))
        if not rows:
            latest = query("SELECT MAX(window_end) AS d FROM hot_term")
            if latest and latest[0].get("d"):
                rows = query(
                    "SELECT term,heat,rise,fall,sectors FROM hot_term WHERE window_end=? "
                    "ORDER BY heat DESC LIMIT 24", (latest[0]["d"],))
        for r in rows:
            try:
                packed = json.loads(r.get("sectors") or "[]")
            except Exception:  # noqa: BLE001
                packed = []
            names = []
            if isinstance(packed, list):
                for it in packed:
                    if isinstance(it, dict) and it.get("name"):
                        names.append(it["name"])
                    elif isinstance(it, str):
                        names.append(it)
            trend = "上升" if (r.get("rise") or 0) >= (r.get("fall") or 0) else "下降"
            cats.append({
                "cat_id": f"hot:{r['term']}", "kind": "hot_term",
                "title": r["term"], "direction": "", "impact_level": 3,
                "sectors": names[:8], "event_time": today,
                "extra": {"heat": r.get("heat"), "trend": trend},
            })
    except Exception:  # noqa: BLE001
        pass
    try:
        future = (_now() + timedelta(days=7)).strftime("%Y-%m-%d")
        rows = query(
            "SELECT item_id,title,event_time,affected_sectors,impact_level FROM intel_cache "
            "WHERE kind='calendar' AND event_time>=? AND event_time<=? AND impact_level>=4 "
            "ORDER BY event_time LIMIT 20", (_today(), future + " 23:59:59"))
        for r in rows:
            try:
                secs = json.loads(r.get("affected_sectors") or "[]")
            except Exception:  # noqa: BLE001
                secs = []
            cats.append({
                "cat_id": f"cal:{r['item_id']}", "kind": "calendar",
                "title": r.get("title") or "", "direction": "",
                "impact_level": r.get("impact_level") or 4,
                "sectors": secs, "event_time": r.get("event_time") or "",
                "extra": {},
            })
    except Exception:  # noqa: BLE001
        pass
    return cats[:60]


def _vol_dim(vr) -> float | None:
    if vr is None:
        return None
    v = float(vr)
    if v < 0.5:
        return 25.0
    if v < 0.8:
        return 40.0
    if v <= 1.5:
        return 70.0
    if v <= 3:
        return 90.0
    if v <= 5:
        return 70.0
    return 45.0


def _flow_dim(row: dict) -> float | None:
    if row.get("main_net_in") is None and row.get("main_buy_ratio") is None:
        return None
    net = row.get("main_net_in") or 0
    ratio = row.get("main_buy_ratio")
    s = 50.0 + max(-30.0, min(30.0, float(net) / 8000.0 * 20))
    if ratio is not None:
        s = s * 0.6 + float(ratio) * 0.4
    return _clamp(s)


def _sector_dim(net_yi) -> float | None:
    if net_yi is None:
        return None
    return _clamp(50.0 + float(net_yi) * 1.2)


def _macro_dim(boards: set[str], cats: list[dict]) -> tuple[float, list[str]]:
    if not boards:
        return 0.0, []
    hot_n = pol_n = cal_n = 0
    signed = 0.0
    hit_ids = []
    for c in cats:
        secs = {str(s) for s in (c.get("sectors") or []) if s}
        if not secs or boards.isdisjoint(secs):
            continue
        hit_ids.append(c["cat_id"])
        kind = c.get("kind")
        direction = c.get("direction") or ""
        sign = -1.0 if direction == "利空" else 1.0
        if kind == "hot_term":
            hot_n += 1
            signed += 15 * sign
        elif kind == "policy":
            pol_n += 1
            signed += 10 * sign
        elif kind == "calendar":
            cal_n += 1
            signed += 8 * sign
    if not hit_ids:
        return 0.0, []
    raw = 15 * min(3, hot_n) + 10 * min(3, pol_n) + 8 * min(2, cal_n)
    # 方向：利空记负后再映射到 0–100，50 为中性
    mapped = _clamp(50 + max(-40, min(40, signed * 0.4 + (raw - 20) * 0.5)))
    return mapped if mapped is not None else 50.0, hit_ids[:8]


def _weighted_score(vec: dict, weights: dict) -> float | None:
    num = den = 0.0
    for k, w in weights.items():
        ww = float(w or 0)
        if ww <= 0:
            continue
        val = vec.get(k)
        if val is None:
            continue
        num += float(val) * ww
        den += ww
    if den <= 0:
        return None
    return round(num / den, 1)


def _load_industry_flow() -> dict[str, float]:
    out = {}
    try:
        from . import sector as sector_svc
        for it in (sector_svc.get_flow("industry") or {}).get("items") or []:
            if it.get("name") is not None:
                out[it["name"]] = it.get("net_in_yi")
    except Exception:  # noqa: BLE001
        pass
    return out


def _load_concepts() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    try:
        for r in query("SELECT code, concept FROM concept_map WHERE concept IS NOT NULL LIMIT 80000"):
            out.setdefault(r["code"], [])
            if r["concept"] and r["concept"] not in out[r["code"]]:
                out[r["code"]].append(r["concept"])
    except Exception:  # noqa: BLE001
        pass
    return out


def run_engine(kind: str = "manual") -> dict:
    """采集→分析→快照。kind=eod/intraday/manual。"""
    ensure_tables()
    cfg = get_config()
    started = _now().isoformat(timespec="seconds")
    asof = _today()
    kind = kind if kind in ("eod", "intraday", "manual") else "manual"
    run_id = f"{asof}_{kind}" if kind != "manual" else f"{asof}_manual_{uuid.uuid4().hex[:8]}"
    domains = collect_domains(cfg)
    quote_ok = domains.get("quote", {}).get("available")
    metrics_ok = domains.get("metrics", {}).get("available")
    if not quote_ok or not metrics_ok:
        note = "核心域 quote/metrics 不可用，未写个股向量。"
        execute(
            "INSERT OR REPLACE INTO engine_run(run_id,asof,kind,started_at,finished_at,status,domains_json,brief_json,note)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (run_id, asof, kind, started, _now().isoformat(timespec="seconds"), "fail",
             json.dumps(domains, ensure_ascii=False), "{}", note),
        )
        return {"ok": False, "status": "fail", "run_id": run_id, "asof": asof, "note": note, "domains": domains}

    regime = market_regime()
    rotation = sector_rotation() if domains.get("industry", {}).get("available") else []
    cats = macro_catalysts(14) if any(domains.get(k, {}).get("available") for k in ("policy", "hot_terms", "calendar")) else []
    try:
        from . import holder_feature
        holder_feature.refresh_all_features()
    except Exception as exc:  # noqa: BLE001
        log.warning("持股特征刷新失败: %s", exc)
    hits_buy = strategy_svc.collect_hits("buy") if domains.get("strategy_hit", {}).get("available") else []
    hits_sell = strategy_svc.collect_hits("sell") if domains.get("strategy_hit", {}).get("available") else []
    buy_map: dict[str, list[str]] = {}
    sell_set: set[str] = set()
    for r in hits_buy:
        code = r.get("code")
        if not code:
            continue
        ids = [x for x in str(r.get("plan_id") or "").split(",") if x]
        buy_map[code] = ids or ["A"]
    for r in hits_sell:
        if r.get("code"):
            sell_set.add(r["code"])

    ind_flow = _load_industry_flow()
    concepts = _load_concepts()
    d_env = regime.get("d_env")
    missing = [k for k, v in domains.items()
               if not v.get("available") and v.get("error") != "域已关闭"]

    rows = query(
        """SELECT s.code, s.name, s.price, s.pct, s.volume_ratio, s.main_net_in, s.main_in, s.main_out,
                  s.float_mv, COALESCE(l.industry,'') AS industry,
                  m.buy_index, m.sentiment, m.dark_power, m.stabilize_score,
                  g.grade AS finance_grade
           FROM stock_snapshot s
           LEFT JOIN stock_list l ON l.code=s.code
           LEFT JOIN stock_metrics m ON m.code=s.code
           LEFT JOIN stock_finance_grade g ON g.code=s.code"""
    )
    stock_rows = []
    for r in rows:
        code = r["code"]
        industry = r.get("industry") or ""
        boards = set()
        if industry:
            boards.add(industry)
        for c in concepts.get(code, [])[:8]:
            boards.add(c)
        main_in, main_out = r.get("main_in"), r.get("main_out")
        ratio = None
        if main_in is not None and main_out is not None and (main_in + main_out) > 0:
            ratio = round(float(main_in) / (float(main_in) + float(main_out)) * 100.0, 2)
        flow_row = {"main_net_in": r.get("main_net_in"), "main_buy_ratio": ratio}
        grade = r.get("finance_grade")
        fin_missing = 0
        if grade in FIN_MAP:
            d_fin = FIN_MAP[grade]
        else:
            d_fin = 50.0
            fin_missing = 1
        d_macro, cat_ids = _macro_dim(boards, cats) if domains.get("policy", {}).get("available") or domains.get("hot_terms", {}).get("available") else (0.0, [])
        if not cat_ids:
            d_macro = 0.0
        vec = {
            "d_buy": _clamp(r["buy_index"]) if r.get("buy_index") is not None else None,
            "d_sent": _clamp(r["sentiment"]) if r.get("sentiment") is not None else None,
            "d_dark": _clamp(r["dark_power"]) if r.get("dark_power") is not None else None,
            "d_stab": _clamp(r["stabilize_score"]) if r.get("stabilize_score") is not None else None,
            "d_flow": _flow_dim(flow_row),
            "d_vol": _vol_dim(r.get("volume_ratio")),
            "d_fin": d_fin,
            "d_sector": _sector_dim(ind_flow.get(industry) if industry else None),
            "d_macro": d_macro,
            "d_ext": 0.0,
            "d_env": d_env,
        }
        hits = []
        if code in buy_map:
            hits.extend([{"side": "buy", "plan_id": p} for p in buy_map[code]])
        if code in sell_set:
            hits.append({"side": "sell", "plan_id": "sell"})
        bits = []
        if vec["d_buy"] is not None:
            bits.append(f"购买指数 {vec['d_buy']:.0f}")
        bits.append(f"财报 {grade}" if grade else "财报 —")
        if cat_ids:
            bits.append(f"宏观催化 {len(cat_ids)}")
        if hits:
            bits.append("策略命中")
        score = _weighted_score(vec, cfg["weights"])
        stock_rows.append((
            run_id, code, r.get("name") or "", r.get("price"), r.get("pct"), industry,
            vec["d_buy"], vec["d_sent"], vec["d_dark"], vec["d_stab"], vec["d_flow"],
            vec["d_vol"], vec["d_fin"], vec["d_sector"], vec["d_macro"], vec["d_ext"], vec["d_env"],
            fin_missing, score,
            json.dumps(hits, ensure_ascii=False),
            json.dumps(cat_ids, ensure_ascii=False),
            json.dumps(list(boards)[:12], ensure_ascii=False),
            " · ".join(bits),
        ))

    execute("DELETE FROM engine_stock WHERE run_id=?", (run_id,))
    execute("DELETE FROM engine_sector WHERE run_id=?", (run_id,))
    execute("DELETE FROM engine_catalyst WHERE run_id=?", (run_id,))
    executemany(
        "INSERT INTO engine_stock(run_id,code,name,price,pct,industry,"
        "d_buy,d_sent,d_dark,d_stab,d_flow,d_vol,d_fin,d_sector,d_macro,d_ext,d_env,"
        "fin_missing,score,hits_json,catalysts_json,boards_json,reason_bits)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        stock_rows,
    )
    sec_rows = [(run_id, it["dim"], it["name"], it.get("hot_score"), it.get("pct"),
                 it.get("net_in"), it.get("direction"),
                 json.dumps(it.get("tags") or [], ensure_ascii=False)) for it in rotation[:40]]
    if sec_rows:
        executemany(
            "INSERT INTO engine_sector(run_id,dim,name,hot_score,pct,net_in,direction,tags)"
            " VALUES(?,?,?,?,?,?,?,?)", sec_rows)
    cat_rows = [(run_id, c["cat_id"], c.get("kind"), c.get("title"), c.get("direction"),
                 c.get("impact_level"), json.dumps(c.get("sectors") or [], ensure_ascii=False),
                 c.get("event_time"), json.dumps(c.get("extra") or {}, ensure_ascii=False))
                for c in cats]
    if cat_rows:
        executemany(
            "INSERT INTO engine_catalyst(run_id,cat_id,kind,title,direction,impact_level,sectors_json,event_time,extra_json)"
            " VALUES(?,?,?,?,?,?,?,?,?)", cat_rows)

    key_facts = []
    for c in cats[:12]:
        extra = c.get("extra") or {}
        if c["kind"] == "policy":
            key_facts.append({"type": "policy", "title": c["title"], "scope": extra.get("scope"),
                              "country": extra.get("country"), "sectors": c.get("sectors") or []})
        elif c["kind"] == "hot_term":
            key_facts.append({"type": "hot_term", "term": c["title"], "heat": extra.get("heat"),
                              "trend": extra.get("trend")})
        else:
            key_facts.append({"type": "calendar", "title": c["title"], "event_time": c.get("event_time")})
    for it in rotation[:5]:
        key_facts.append({"type": "sector", "name": it["name"], "net_in_yi": it.get("net_in")})
    brief = {
        "asof": asof, "regime": {
            "label": regime.get("label"), "temp": regime.get("temp"),
            "volume": regime.get("volume"), "sentence": regime.get("sentence"),
        },
        "key_facts": key_facts[:30],
        "missing": missing,
        "disclaimer": "量化参考，不构成投资建议",
        "buy_hits": len(hits_buy), "sell_hits": len(hits_sell),
        "stocks": len(stock_rows),
    }
    status = "partial" if missing else "ok"
    note = ("缺失域：" + "、".join(missing)) if missing else ""
    execute(
        "INSERT OR REPLACE INTO engine_run(run_id,asof,kind,started_at,finished_at,status,domains_json,brief_json,note)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (run_id, asof, kind, started, _now().isoformat(timespec="seconds"), status,
         json.dumps(domains, ensure_ascii=False), json.dumps(brief, ensure_ascii=False), note),
    )
    n_tasks = upsert_signal_tasks(asof, run_id, hits_buy, hits_sell)
    track_open_tasks()
    factor_info = None
    if cfg.get("build_factor_daily") and kind != "intraday":
        try:
            from . import factor_frame
            factor_info = factor_frame.build_all()
        except Exception as exc:  # noqa: BLE001
            log.warning("按日因子生成失败: %s", exc)
            factor_info = {"ok": False, "error": str(exc)[:200]}
    return {
        "ok": True, "status": status, "run_id": run_id, "asof": asof,
        "stocks": len(stock_rows), "buy_hits": len(hits_buy), "sell_hits": len(hits_sell),
        "tasks": n_tasks, "missing": missing, "note": note, "regime": regime.get("label"),
        "factor": factor_info,
        "domains": domains,
    }


def latest_run(prefer_eod: bool = True) -> dict | None:
    ensure_tables()
    rows = query(
        "SELECT * FROM engine_run WHERE status IN ('ok','partial') "
        "ORDER BY finished_at DESC LIMIT 8")
    if not rows:
        return None
    if prefer_eod:
        for r in rows:
            if r.get("kind") == "eod":
                return dict(r)
    return dict(rows[0])


def status() -> dict:
    cfg = get_config()
    run = latest_run()
    out = {
        "ok": True, "config": cfg, "enabled": cfg["enabled"],
        "has_snapshot": bool(run),
        "run": None,
    }
    if run:
        try:
            domains = json.loads(run.get("domains_json") or "{}")
        except Exception:  # noqa: BLE001
            domains = {}
        try:
            brief = json.loads(run.get("brief_json") or "{}")
        except Exception:  # noqa: BLE001
            brief = {}
        out["run"] = {
            "run_id": run["run_id"], "asof": run["asof"], "kind": run["kind"],
            "status": run["status"], "finished_at": run["finished_at"],
            "note": run.get("note") or "", "domains": domains, "brief": brief,
        }
    return out


def get_brief() -> dict:
    run = latest_run()
    if not run:
        return {"ok": True, "empty": True, "empty_reason": "尚无引擎快照。请到设置→策略引擎配置点「跑一次引擎」。"}
    try:
        brief = json.loads(run.get("brief_json") or "{}")
    except Exception:  # noqa: BLE001
        brief = {}
    try:
        domains = json.loads(run.get("domains_json") or "{}")
    except Exception:  # noqa: BLE001
        domains = {}
    return {
        "ok": True, "empty": False, "run_id": run["run_id"], "asof": run["asof"],
        "kind": run["kind"], "status": run["status"], "note": run.get("note") or "",
        "brief": brief, "domains": domains, "regime": (brief.get("regime") or {}),
        "disclaimer": "量化参考，不构成投资建议",
    }


def get_signals(side: str = "buy", limit: int = 40) -> dict:
    run = latest_run()
    cfg = get_config()
    if not run:
        return {"ok": True, "items": [], "empty_reason": "策略引擎未产出信号包。可先在综合选股计算，或到设置→选股策略查看启用方案。",
                "enabled": strategy_svc.get_enabled(side)}
    if not cfg["consume"].get("smartpick_signals", True):
        return {"ok": True, "items": [], "empty_reason": "已关闭「策略命中页走信号包」。",
                "enabled": strategy_svc.get_enabled(side)}
    side = "sell" if side == "sell" else "buy"
    like = '%"side": "' + side + '"%'
    rows = query(
        "SELECT * FROM engine_stock WHERE run_id=? AND hits_json LIKE ? "
        "ORDER BY score DESC LIMIT ?", (run["run_id"], like, max(5, min(int(limit or 40), 80))))
    items = []
    for r in rows:
        try:
            hits = json.loads(r.get("hits_json") or "[]")
        except Exception:  # noqa: BLE001
            hits = []
        plan_ids = [h.get("plan_id") for h in hits if h.get("side") == side]
        items.append({
            "code": r["code"], "name": r["name"], "price": r["price"], "pct": r["pct"],
            "industry": r.get("industry") or "", "score": r.get("score"),
            "plan_id": ",".join(plan_ids),
            "hit_count": len(plan_ids),
            "plan_labels": [strategy_svc.plan_caption(p, side) for p in plan_ids if p],
            "reason": r.get("reason_bits") or "",
            "d_buy": r.get("d_buy"), "d_macro": r.get("d_macro"),
        })
    attach_signal_levels(items, side, run.get("asof"))
    return {
        "ok": True, "side": side, "run_id": run["run_id"], "asof": run["asof"],
        "items": items, "count": len(items),
        "enabled": strategy_svc.get_enabled(side),
        "empty_reason": "" if items else "当前快照中无该方向策略命中。",
        "disclaimer": "量化参考，不构成投资建议",
    }


def get_catalysts() -> dict:
    run = latest_run()
    cfg = get_config()
    if not run:
        return {"ok": True, "items": [], "empty_reason": "尚无引擎 Brief。原始数据仍在宏观情报里。"}
    if not cfg["consume"].get("smartpick_catalyst", True):
        return {"ok": True, "items": [], "empty_reason": "已关闭「宏观催化页走 Brief」。"}
    rows = query("SELECT * FROM engine_catalyst WHERE run_id=? ORDER BY impact_level DESC", (run["run_id"],))
    items = []
    for r in rows:
        try:
            secs = json.loads(r.get("sectors_json") or "[]")
        except Exception:  # noqa: BLE001
            secs = []
        try:
            extra = json.loads(r.get("extra_json") or "{}")
        except Exception:  # noqa: BLE001
            extra = {}
        items.append({
            "cat_id": r["cat_id"], "kind": r["kind"], "title": r["title"],
            "direction": r.get("direction") or "", "impact_level": r.get("impact_level"),
            "sectors": secs, "event_time": r.get("event_time") or "", **extra,
        })
    return {
        "ok": True, "run_id": run["run_id"], "asof": run["asof"], "items": items,
        "brief": get_brief().get("brief") or {},
        "empty_reason": "" if items else "窗口内无政策/热词/高星日历命中（不编造）。",
    }


def upsert_signal_tasks(asof: str, run_id: str, hits_buy: list, hits_sell: list) -> int:
    ensure_tables()
    n = 0
    for side, rows in (("buy", hits_buy), ("sell", hits_sell)):
        for r in rows:
            code = r.get("code")
            if not code:
                continue
            px = r.get("price")
            plan_id = str(r.get("plan_id") or "")[:40]
            exists = query(
                "SELECT id FROM signal_task WHERE asof=? AND code=? AND side=? AND plan_id=?",
                (asof, code, side, plan_id))
            if exists:
                continue
            if px is None or float(px) <= 0:
                execute(
                    "INSERT INTO signal_task(asof,code,name,side,plan_id,entry_px,stop_px,take_px,"
                    "expire_n,status,note,run_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (asof, code, r.get("name") or "", side, plan_id, None, None, None,
                     EXPIRE_N, "invalid", "无现价，任务无效", run_id))
                n += 1
                continue
            entry = float(px)
            if side == "buy":
                stop_px, take_px = round(entry * (1 - STOP_PCT), 4), round(entry * (1 + TAKE_PCT), 4)
            else:
                stop_px, take_px = round(entry * (1 + STOP_PCT), 4), round(entry * (1 - TAKE_PCT), 4)
            execute(
                "INSERT INTO signal_task(asof,code,name,side,plan_id,entry_px,stop_px,take_px,"
                "expire_n,status,note,run_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (asof, code, r.get("name") or "", side, plan_id, entry, stop_px, take_px,
                 EXPIRE_N, "open", "", run_id))
            n += 1
    return n


def _fwd_ret(bars: list[dict], entry: float, n: int) -> float | None:
    if entry is None or entry <= 0 or len(bars) <= n:
        return None
    close = bars[n].get("close")
    if close is None:
        return None
    return round((float(close) - float(entry)) / float(entry) * 100.0, 2)


def track_open_tasks() -> dict:
    """用后续日 K 跟踪。无下一根则收益为 null，不是 0%。同日高低都触碰记 ambiguous。"""
    ensure_tables()
    open_rows = query("SELECT * FROM signal_task WHERE status='open'")
    updated = 0
    for t in open_rows:
        code, asof, side = t["code"], t["asof"], t["side"]
        entry, stop_px, take_px = t.get("entry_px"), t.get("stop_px"), t.get("take_px")
        bars = query(
            "SELECT date,open,high,low,close FROM daily_kline WHERE code=? AND date>? ORDER BY date LIMIT 25",
            (code, asof))
        ret1 = _fwd_ret(bars, entry, 0) if bars else None
        ret5 = _fwd_ret(bars, entry, 4) if bars else None
        ret20 = _fwd_ret(bars, entry, 19) if bars else None
        status, exit_date, exit_px, note = t["status"], t.get("exit_date"), t.get("exit_px"), t.get("note") or ""
        expire_n = int(t.get("expire_n") or EXPIRE_N)
        if bars:
            for i, bar in enumerate(bars):
                hi, lo, close = bar.get("high"), bar.get("low"), bar.get("close")
                if hi is None or lo is None:
                    continue
                hit_stop = hit_take = False
                if side == "buy":
                    hit_stop = stop_px is not None and float(lo) <= float(stop_px)
                    hit_take = take_px is not None and float(hi) >= float(take_px)
                else:
                    hit_stop = stop_px is not None and float(hi) >= float(stop_px)
                    hit_take = take_px is not None and float(lo) <= float(take_px)
                if hit_stop and hit_take:
                    status, exit_date, exit_px = "invalid_ambiguous", bar["date"], close
                    note = "当日高低价同时触及止盈与止损，不选边"
                    break
                if hit_take:
                    status, exit_date, exit_px = "hit_take", bar["date"], take_px
                    break
                if hit_stop:
                    status, exit_date, exit_px = "hit_stop", bar["date"], stop_px
                    break
                if i + 1 >= expire_n:
                    status, exit_date, exit_px = "expired", bar["date"], close
                    note = "超过有效期，按收盘结束跟踪"
                    break
        execute(
            "UPDATE signal_task SET status=?, exit_date=?, exit_px=?, ret_1=?, ret_5=?, ret_20=?, note=? WHERE id=?",
            (status, exit_date, exit_px, ret1, ret5, ret20, note, t["id"]))
        updated += 1
    return {"ok": True, "updated": updated}


def list_signal_tasks(side: str = "", status: str = "", limit: int = 80) -> dict:
    ensure_tables()
    track_open_tasks()
    where = ["1=1"]
    params: list = []
    if side in ("buy", "sell"):
        where.append("side=?")
        params.append(side)
    if status:
        where.append("status=?")
        params.append(status)
    rows = query(
        f"SELECT * FROM signal_task WHERE {' AND '.join(where)} "
        "ORDER BY asof DESC, id DESC LIMIT ?", (*params, max(10, min(int(limit or 80), 200))))
    from . import backtest as backtest_svc
    from . import paper as paper_svc
    fst = backtest_svc.factor_status()
    return {
        "ok": True, "items": rows, "count": len(rows),
        "backtest_open": bool(fst.get("open")),
        "backtest_kind": "factor" if fst.get("open") else None,
        "backtest_reason": (
            "已有 factor_daily，可做技术因子回放（不是方案 A–H）。A–H 重放仍关闭：缺少 metrics_daily。"
            if fst.get("open") else
            (fst.get("reason") or "缺少按日因子表 factor_daily，回测入口关闭，避免用最新截面做假回测。")
        ),
        "ah_replay_open": False,
        "ah_replay_reason": backtest_svc.AH_REPLAY_REASON,
        "factor_rule": fst.get("rule") or "",
        "has_factor_daily": bool(fst.get("open")),
        "factor_rows": fst.get("rows") or 0,
        "paper": paper_svc.status(),
        "note": "跟踪收益仅用命中日之后的日 K。无下一根显示为空，不是 0%。高低同日触碰记 ambiguous。",
        "disclaimer": "量化参考，不构成投资建议",
    }


def attach_signal_levels(items: list[dict], side: str, asof: str | None = None) -> None:
    if not items:
        return
    ensure_tables()
    asof = asof or _today()
    codes = [r.get("code") for r in items if r.get("code")]
    if not codes:
        return
    ph = ",".join("?" * len(codes))
    rows = query(
        f"SELECT * FROM signal_task WHERE asof=? AND side=? AND code IN ({ph}) ORDER BY id DESC",
        (asof, side, *codes))
    by_code = {}
    for t in rows:
        by_code.setdefault(t["code"], t)
    for r in items:
        t = by_code.get(r.get("code"))
        if not t:
            px = r.get("price")
            if px:
                entry = float(px)
                r["entry_px"] = entry
                if side == "buy":
                    r["stop_px"] = round(entry * (1 - STOP_PCT), 4)
                    r["take_px"] = round(entry * (1 + TAKE_PCT), 4)
                else:
                    r["stop_px"] = round(entry * (1 + STOP_PCT), 4)
                    r["take_px"] = round(entry * (1 - TAKE_PCT), 4)
            r["task_status"] = "preview"
            r["ret_1"] = None
            r["ret_5"] = None
            r["ret_20"] = None
            continue
        r["entry_px"] = t.get("entry_px")
        r["stop_px"] = t.get("stop_px")
        r["take_px"] = t.get("take_px")
        r["task_status"] = t.get("status")
        r["ret_1"] = t.get("ret_1")
        r["ret_5"] = t.get("ret_5")
        r["ret_20"] = t.get("ret_20")


def maybe_auto_run(kind: str) -> dict | None:
    cfg = get_config()
    if not cfg.get("enabled"):
        return None
    if kind == "intraday" and not cfg.get("intraday"):
        return None
    try:
        return run_engine(kind)
    except Exception as exc:  # noqa: BLE001
        log.warning("引擎自动运行失败: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}


def vector_map(run_id: str) -> dict[str, dict]:
    out = {}
    for r in query(
        "SELECT code,score,reason_bits,d_buy,d_fin,fin_missing,hits_json FROM engine_stock WHERE run_id=?",
        (run_id,)):
        out[r["code"]] = dict(r)
    return out


def list_stocks(limit: int = 40) -> dict:
    run = latest_run()
    if not run:
        return {"ok": True, "items": [], "empty_reason": "尚无引擎快照。请到设置→策略引擎配置点「跑一次引擎」。"}
    limit = max(5, min(int(limit or 40), 80))
    rows = query(
        "SELECT code,name,price,pct,industry,score,d_buy,d_flow,d_fin,d_macro,d_env,"
        "fin_missing,reason_bits,hits_json FROM engine_stock WHERE run_id=? "
        "ORDER BY score IS NULL, score DESC LIMIT ?",
        (run["run_id"], limit))
    items = []
    for r in rows:
        try:
            hits = json.loads(r.get("hits_json") or "[]")
        except Exception:  # noqa: BLE001
            hits = []
        items.append({
            **{k: r.get(k) for k in (
                "code", "name", "price", "pct", "industry", "score",
                "d_buy", "d_flow", "d_fin", "d_macro", "d_env", "fin_missing", "reason_bits")},
            "hits": hits,
            "finance_grade": None if r.get("fin_missing") else None,
            "finance_display": "—" if r.get("fin_missing") else "有评级",
        })
    return {
        "ok": True, "run_id": run["run_id"], "asof": run["asof"], "kind": run["kind"],
        "items": items, "count": len(items),
        "disclaimer": "量化参考，不构成投资建议",
    }


def snapshot_view() -> dict:
    brief = get_brief()
    stocks = list_stocks(20)
    run = latest_run()
    sectors = []
    if run:
        sectors = query(
            "SELECT dim,name,hot_score,pct,net_in,direction FROM engine_sector WHERE run_id=? "
            "ORDER BY net_in DESC LIMIT 16", (run["run_id"],))
    return {
        "ok": True,
        "empty": brief.get("empty", True),
        "empty_reason": brief.get("empty_reason") or "",
        "run_id": brief.get("run_id"), "asof": brief.get("asof"),
        "kind": brief.get("kind"), "status": brief.get("status"),
        "note": brief.get("note") or "",
        "brief": brief.get("brief") or {},
        "domains": brief.get("domains") or {},
        "regime": brief.get("regime") or {},
        "stocks": stocks.get("items") or [],
        "sectors": sectors,
        "disclaimer": "量化参考，不构成投资建议",
    }


def stocks_for_board(board: str, limit: int = 20) -> dict:
    run = latest_run()
    name = (board or "").strip()
    if not run or not name:
        return {"ok": True, "items": [], "empty_reason": "无快照或未指定板块"}
    limit = max(5, min(int(limit or 20), 40))
    like = f"%{name}%"
    rows = query(
        "SELECT code,name,price,pct,industry,score,reason_bits FROM engine_stock "
        "WHERE run_id=? AND (industry=? OR boards_json LIKE ?) "
        "ORDER BY score IS NULL, score DESC LIMIT ?",
        (run["run_id"], name, like, limit))
    return {
        "ok": True, "board": name, "run_id": run["run_id"], "asof": run["asof"],
        "items": rows, "count": len(rows),
        "empty_reason": "" if rows else "该板块在当前快照中无个股向量。",
        "disclaimer": "量化参考，不构成投资建议",
    }
