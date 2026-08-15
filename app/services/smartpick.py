"""智能选股（FR11-01）：本地综合多维指标打分，AI 点评受开关与频率约束。"""
import hashlib
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from ..database import get_meta_json, query, set_meta_json
from . import metrics as metrics_svc
from . import rating as rating_svc
from . import screener

log = logging.getLogger("smartpick")
_TZ = ZoneInfo("Asia/Shanghai")

DEFAULT_WEIGHTS = {
    "buy_index": 20, "score": 15, "main_flow": 15, "volume": 10,
    "finance": 15, "stabilize": 8, "dark": 7, "sector_flow": 10,
}
WEIGHT_LABELS = {
    "buy_index": "购买指数", "score": "综合评分", "main_flow": "主力资金",
    "volume": "量能", "finance": "财报", "stabilize": "企稳",
    "dark": "暗盘", "sector_flow": "板块资金",
}
DEFAULT_POLICY = {"mode": "manual", "min_interval_sec": 600, "daily_cap": 12}

TEMPLATES = {
    "balanced": {
        "name": "均衡综合",
        "conditions": {"exclude_st": True},
        "hard": {"exclude_st": True},
        "weights": dict(DEFAULT_WEIGHTS),
    },
    "main_quality": {
        "name": "主力吸筹绩优",
        "conditions": {"exclude_st": True, "main_flow": ["inflow"], "finance_grade": ["A", "B"]},
        "hard": {"exclude_st": True, "need_finance": True, "need_main_in": True},
        "weights": {**DEFAULT_WEIGHTS, "main_flow": 25, "finance": 25, "buy_index": 15},
    },
    "breakout": {
        "name": "放量突破",
        "conditions": {"exclude_st": True, "volume_ratio": ["mild", "surge"],
                       "tech": ["break20_high"], "pct_today": ["gt5", "0to5"]},
        "hard": {"exclude_st": True, "need_volume": True},
        "weights": {**DEFAULT_WEIGHTS, "volume": 22, "buy_index": 22, "finance": 8},
    },
    "oversold": {
        "name": "超跌企稳",
        "conditions": {"exclude_st": True, "pct_d20": ["ltneg10", "neg20to0"], "tech": ["stabilized"]},
        "hard": {"exclude_st": True, "need_stabilize": True},
        "weights": {**DEFAULT_WEIGHTS, "stabilize": 22, "score": 20, "volume": 8},
    },
    "sector_res": {
        "name": "板块资金共振",
        "conditions": {"exclude_st": True, "main_flow": ["inflow"]},
        "hard": {"exclude_st": True, "need_main_in": True, "need_sector_in": True},
        "weights": {**DEFAULT_WEIGHTS, "sector_flow": 22, "main_flow": 22},
    },
    "custom": {
        "name": "自定义",
        "conditions": {"exclude_st": True},
        "hard": {"exclude_st": True},
        "weights": dict(DEFAULT_WEIGHTS),
    },
}


def _today() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def get_policy() -> dict:
    raw = get_meta_json("smartpick_ai_policy", {}) or {}
    out = dict(DEFAULT_POLICY)
    if raw.get("mode") in ("off", "manual", "auto"):
        out["mode"] = raw["mode"]
    try:
        sec = int(raw.get("min_interval_sec") or out["min_interval_sec"])
        if sec in (600, 1800, 3600):
            out["min_interval_sec"] = sec
    except (TypeError, ValueError):
        pass
    try:
        cap = int(raw.get("daily_cap") or out["daily_cap"])
        if cap in (5, 12, 20):
            out["daily_cap"] = cap
    except (TypeError, ValueError):
        pass
    return out


def save_policy(payload: dict) -> dict:
    cur = get_policy()
    if payload.get("mode") in ("off", "manual", "auto"):
        cur["mode"] = payload["mode"]
    if payload.get("min_interval_sec") in (600, 1800, 3600):
        cur["min_interval_sec"] = int(payload["min_interval_sec"])
    if payload.get("daily_cap") in (5, 12, 20):
        cur["daily_cap"] = int(payload["daily_cap"])
    set_meta_json("smartpick_ai_policy", cur)
    return {"ok": True, **cur, "usage": _usage()}


def _usage() -> dict:
    u = get_meta_json("smartpick_ai_usage", {}) or {}
    if u.get("date") != _today():
        return {"date": _today(), "count": 0, "last_ts": 0, "last_fp": ""}
    return {
        "date": u.get("date"),
        "count": int(u.get("count") or 0),
        "last_ts": float(u.get("last_ts") or 0),
        "last_fp": u.get("last_fp") or "",
    }


def _save_usage(u: dict) -> None:
    set_meta_json("smartpick_ai_usage", u)


def meta() -> dict:
    from . import finance as finance_svc
    st = finance_svc.stats()
    return {
        "templates": {k: {"id": k, "name": v["name"], "weights": v["weights"],
                          "hard": v["hard"]} for k, v in TEMPLATES.items()},
        "weights": DEFAULT_WEIGHTS,
        "weight_labels": WEIGHT_LABELS,
        "policy": get_policy(),
        "usage": _usage(),
        "finance_graded": st.get("graded", 0),
        "finance_universe": st.get("universe", 0),
        "finance_distribution": st.get("distribution") or {},
    }


def _industry_flow() -> dict[str, float]:
    rows = query(
        "SELECT l.industry AS name, ROUND(SUM(s.main_net_in)/10000.0, 2) AS net_in_yi "
        "FROM stock_snapshot s JOIN stock_list l ON l.code=s.code "
        "WHERE l.industry != '' AND s.main_net_in IS NOT NULL GROUP BY l.industry")
    return {r["name"]: r["net_in_yi"] or 0 for r in rows}


def _fin_score(grade: str | None) -> float:
    return {"A": 100.0, "B": 75.0, "C": 45.0, "D": 15.0}.get(grade or "", 50.0)


def _vol_score(vr) -> float:
    if vr is None:
        return 50.0
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


def _flow_dim(row: dict) -> float:
    net = row.get("main_net_in") or 0
    ratio = row.get("main_buy_ratio")
    s = 50.0 + max(-30.0, min(30.0, float(net) / 8000.0 * 20))
    if ratio is not None:
        s = s * 0.6 + float(ratio) * 0.4
    return max(0.0, min(100.0, s))


def _sector_dim(net_yi: float | None) -> float:
    if net_yi is None:
        return 50.0
    return max(0.0, min(100.0, 50.0 + float(net_yi) * 1.2))


def _composite(row: dict, weights: dict, sector_yi: float | None) -> tuple[float, str]:
    dims = {
        "buy_index": row.get("buy_index") if row.get("buy_index") is not None else 50,
        "score": row.get("score") if row.get("score") is not None else 50,
        "main_flow": _flow_dim(row),
        "volume": _vol_score(row.get("volume_ratio")),
        "finance": _fin_score(row.get("finance_grade")),
        "stabilize": row.get("stabilize_score") if row.get("stabilize_score") is not None else 50,
        "dark": row.get("dark_power") if row.get("dark_power") is not None else 50,
        "sector_flow": _sector_dim(sector_yi),
    }
    num = den = 0.0
    for k, w in weights.items():
        ww = float(w or 0)
        if ww <= 0 or k not in dims:
            continue
        num += float(dims[k]) * ww
        den += ww
    total = round(num / den, 1) if den else 0.0
    bits = []
    if row.get("buy_index") is not None:
        bits.append(f"购买指数 {row['buy_index']:.0f}")
    g = row.get("finance_grade")
    bits.append(f"财报 {g}" if g else "财报 —")
    if row.get("main_buy_ratio") is not None:
        bits.append(f"主力买比 {row['main_buy_ratio']:.0f}%")
    if sector_yi is not None:
        bits.append(f"所属行业当日净流入 {sector_yi:+.1f} 亿")
    return total, " · ".join(bits) or "多维综合"


def _apply_hard(items: list[dict], hard: dict, industry_flow: dict) -> list[dict]:
    out = []
    for r in items:
        if hard.get("exclude_st") and ("ST" in (r.get("name") or "") or "退" in (r.get("name") or "")):
            continue
        if hard.get("need_main_in") and not ((r.get("main_net_in") or 0) > 0):
            continue
        if hard.get("need_finance") and not r.get("finance_grade"):
            continue
        if hard.get("need_stabilize") and r.get("stabilize_score") is None:
            continue
        if hard.get("need_volume") and (r.get("volume_ratio") or 0) < 1.5:
            continue
        if hard.get("need_sector_in"):
            yi = industry_flow.get(r.get("industry") or "")
            if yi is None or yi <= 0:
                continue
        out.append(r)
    return out


def _ranges_ok(r: dict, ranges: dict) -> bool:
    def _n(key, default=None):
        v = ranges.get(key)
        if v in (None, ""):
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    lo, hi = _n("buy_index_min"), _n("buy_index_max")
    if lo is not None and (r.get("buy_index") is None or r["buy_index"] < lo):
        return False
    if hi is not None and (r.get("buy_index") is None or r["buy_index"] > hi):
        return False
    lo, hi = _n("main_buy_ratio_min"), _n("main_buy_ratio_max")
    if lo is not None and (r.get("main_buy_ratio") is None or r["main_buy_ratio"] < lo):
        return False
    if hi is not None and (r.get("main_buy_ratio") is None or r["main_buy_ratio"] > hi):
        return False
    lo, hi = _n("volume_ratio_min"), _n("volume_ratio_max")
    if lo is not None and (r.get("volume_ratio") is None or r["volume_ratio"] < lo):
        return False
    if hi is not None and (r.get("volume_ratio") is None or r["volume_ratio"] > hi):
        return False
    return True


def _fingerprint(template: str, items: list[dict]) -> str:
    codes = ",".join(i.get("code", "") for i in items[:15])
    raw = f"{template}|{codes}"
    return hashlib.md5(raw.encode()).hexdigest()


def _can_call_ai(policy: dict, usage: dict, fp: str) -> tuple[bool, str]:
    if policy["mode"] == "off":
        return False, "off"
    if usage["count"] >= policy["daily_cap"]:
        return False, "cap"
    now = time.time()
    if usage["last_fp"] == fp and now - (usage["last_ts"] or 0) < policy["min_interval_sec"]:
        return False, "duplicate"
    if now - (usage["last_ts"] or 0) < policy["min_interval_sec"]:
        return False, "interval"
    return True, ""


def _ai_config() -> dict:
    from ..database import get_meta_json
    return get_meta_json("ai_config", {}) or {}


def comment(items: list[dict], template_name: str, summary: str,
            force: bool = False) -> dict:
    """force=True 表示手动按钮，仍受关闭/上限/去重约束，但 interval 在手动时可放宽为仅去重。"""
    policy = get_policy()
    usage = _usage()
    fp = _fingerprint(template_name, items)
    if policy["mode"] == "off":
        return {"skipped": True, "reason": "off", "text": "", "ai": False}
    if not items:
        return {"skipped": True, "reason": "empty", "text": "", "ai": False}
    cfg = _ai_config()
    from .ai import normalize_api_base
    if not (cfg.get("api_key") and normalize_api_base(cfg.get("api_base", "") or "")):
        return {"skipped": True, "reason": "unconfigured", "text": "",
                "ai": False, "error": "未配置大模型，请到 AI 分析页填写并测试连通"}
    ok, reason = _can_call_ai(policy, usage, fp)
    if force and reason == "interval":
        ok, reason = True, ""
    if force and reason == "duplicate":
        ok, reason = False, "duplicate"
    if not force and policy["mode"] != "auto":
        return {"skipped": True, "reason": "manual", "text": "", "ai": False}
    if not ok:
        return {"skipped": True, "reason": reason, "text": "", "ai": False}
    try:
        from . import ai as ai_svc
        top = "；".join(
            f"{i['name']}({i['code']}) 综合分{i.get('smart_score')} 财报{i.get('finance_grade') or '—'} "
            f"购买指数{i.get('buy_index')} 主力买比{i.get('main_buy_ratio')}"
            for i in items[:15])
        text = ai_svc._call_llm(
            cfg,
            f"策略「{template_name}」。条件：{summary}。前15只：{top}",
            "根据下列本地量化结果，用不超过180字指出3只更值得跟踪的股票及理由，并给一句风险提示。禁止收益承诺。",
        )
        usage.update(date=_today(), count=usage["count"] + 1, last_ts=time.time(), last_fp=fp)
        _save_usage(usage)
        return {"skipped": False, "reason": "", "text": text + ai_svc.DISCLAIMER,
                "ai": True, "error": "", "usage": usage}
    except Exception as exc:  # noqa: BLE001
        log.warning("智能选股AI点评失败: %s", exc)
        return {"skipped": False, "reason": "error", "text": "", "ai": False,
                "error": str(exc)[:240], "usage": usage}


def run(payload: dict) -> dict:
    template = payload.get("template") or "balanced"
    if template not in TEMPLATES:
        template = "custom"
    tpl = TEMPLATES[template]
    conditions = dict(tpl["conditions"])
    user_cond = payload.get("conditions") or {}
    for k, v in user_cond.items():
        if v not in (None, "", []):
            conditions[k] = v
    if payload.get("semantic"):
        parsed = screener.parse_semantic(payload["semantic"])
        for k, v in (parsed.get("conditions") or {}).items():
            if k == "exclude_st":
                conditions[k] = v
            elif isinstance(v, list):
                conditions.setdefault(k, [])
                for x in v:
                    if x not in conditions[k]:
                        conditions[k].append(x)
            else:
                conditions[k] = v
        semantic_summary = parsed.get("summary") or ""
    else:
        semantic_summary = ""

    hard = dict(tpl.get("hard") or {})
    hard.update(payload.get("hard") or {})
    weights = dict(tpl.get("weights") or DEFAULT_WEIGHTS)
    for k, v in (payload.get("weights") or {}).items():
        if k in DEFAULT_WEIGHTS:
            try:
                weights[k] = float(v)
            except (TypeError, ValueError):
                pass
    ranges = payload.get("ranges") or {}

    result = screener.run(conditions, limit=200)
    items = result.get("items") or []
    industry_flow = _industry_flow()
    for r in items:
        if r.get("score") is None:
            r["score"], r["advice"] = rating_svc.quick_score(r)
        metrics_svc.attach_flow_fields(r)
        r["sector_net_in_yi"] = industry_flow.get(r.get("industry") or "")
    items = _apply_hard(items, hard, industry_flow)
    items = [r for r in items if _ranges_ok(r, ranges)]
    for r in items:
        r["smart_score"], r["local_reason"] = _composite(r, weights, r.get("sector_net_in_yi"))
    items.sort(key=lambda x: x.get("smart_score") or 0, reverse=True)
    items = items[:100]
    from . import finance as finance_svc
    from . import wuxing
    wuxing.tags_for_list(items)
    finance_svc.attach_grades(items)
    metrics_svc.attach_flow_list(items)

    st = finance_svc.stats()
    local = (f"策略「{tpl['name']}」命中 {len(items)} 只。"
             + (f" 语义：{semantic_summary}。" if semantic_summary else "")
             + f" 财报已评级 {st.get('graded', 0)}/{st.get('universe', 0)}。"
             + " 综合分为本地加权，不构成投资建议。")
    if not items and hard.get("need_finance"):
        local += " 当前绩优硬性条件要求已评级，覆盖率低时结果可能为空，可改「均衡综合」或先重建财报。"

    ai_block = {"skipped": True, "reason": "not_requested", "text": "", "ai": False}
    policy = get_policy()
    if payload.get("want_ai") or policy["mode"] == "auto":
        ai_block = comment(items, tpl["name"], local, force=bool(payload.get("want_ai")))

    return {
        "template": template, "template_name": tpl["name"],
        "items": items, "total": len(items),
        "local_summary": local,
        "weights": weights, "hard": hard,
        "ai": ai_block, "policy": policy, "usage": _usage(),
        "finance_graded": st.get("graded", 0),
        "finance_universe": st.get("universe", 0),
    }
