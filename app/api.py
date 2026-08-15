"""FastAPI 路由：所有接口为同步函数，由框架线程池执行，保证事件循环不被阻塞。"""
from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import HTMLResponse

from . import scheduler
from .cache import cache
from .database import get_meta
from .datasources.base import HEALTH
from . import scheduler as sched_mod
from .services import (
    ai, alerts, announcement, attribution, commodity, cycle, darkpool, finance,
    forecast, global_index, holders, hot_terms, intel_ai, kline, knowledge, macro, market, ranks, rating,
    intelpick, recommend, screener, sector, smartpick, stock_ai, stocklist, strategy, wuxing,
)
from .services import metrics as metrics_svc
from .services import policy_archive
from .services import engine, engine_blueprint
from .database import query as db_query

router = APIRouter(prefix="/api")

_API_INDEX = {
    "name": "A股量化工具 API",
    "ui": "/",
    "docs": "/docs",
    "redoc": "/redoc",
    "openapi": "/openapi.json",
    "health": "/api/system/status",
    "dashboard": "/api/dashboard",
}

_API_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>A股量化工具 · API</title>
<style>
body{font-family:-apple-system,sans-serif;background:#0d1117;color:#dbe4f0;padding:40px 24px;max-width:640px;margin:0 auto;line-height:1.6}
a{color:#4a9eff;text-decoration:none} a:hover{text-decoration:underline}
.card{background:#161b22;border:1px solid #30363d;padding:18px 20px;border-radius:10px;margin:14px 0}
h1{font-size:22px;margin:0 0 8px} p{margin:8px 0} .muted{color:#8b9bb4;font-size:13px}
</style></head><body>
<h1>A股量化工具 · 后端入口</h1>
<p class="muted">你打开的是 API 地址，不是前端页面。</p>
<div class="card">
  <p><a href="/">打开前端界面</a></p>
  <p><a href="/docs">Swagger 接口文档（可直接调试）</a></p>
  <p><a href="/redoc">ReDoc 文档</a></p>
  <p><a href="/api/system/status">健康检查 JSON</a></p>
  <p><a href="/api/dashboard">看板数据 JSON</a></p>
  <p><a href="/openapi.json">OpenAPI JSON</a></p>
</div>
</body></html>
"""


def _api_root(request: Request):
    """浏览器打开 /api 给导航页；curl / 脚本拿到 JSON。"""
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return HTMLResponse(_API_HTML)
    return _API_INDEX


@router.get("", include_in_schema=False)
def api_root(request: Request):
    return _api_root(request)


@router.get("/", include_in_schema=False)
def api_root_slash(request: Request):
    return _api_root(request)


# ---------------- 行情看板 ----------------

@router.get("/dashboard")
def dashboard():
    return {
        "indices": market.get_indices_overview(),
        "stats": market.market_stats(),
        "movers": market.top_movers(20),
        "watchlist": market.get_watchlist(),
    }


@router.get("/alerts")
def get_alerts(limit: int = Query(50, le=100)):
    return alerts.get_alerts(limit)


@router.get("/alerts/buy-points")
def alerts_buy_points(limit: int = Query(12, le=40)):
    return alerts.get_buy_points(limit)


@router.get("/alerts/sell-points")
def alerts_sell_points(limit: int = Query(12, le=40)):
    return alerts.get_sell_points(limit)


@router.get("/strategy/plans")
def strategy_plans():
    return strategy.get_config()


@router.post("/strategy/enable")
def strategy_enable(payload: dict = Body(default={})):
    saved = strategy.set_enabled(
        ids=(payload or {}).get("ids"),
        buy_ids=(payload or {}).get("buy_ids"),
        sell_ids=(payload or {}).get("sell_ids"),
    )
    try:
        alerts.scan_all()
    except Exception:  # noqa: BLE001
        pass
    cfg = strategy.get_config()
    cfg["saved"] = saved
    cfg["ok"] = True
    return cfg


@router.get("/market/minute")
def market_minute(code: str = "sh000001"):
    return kline.get_minute(market.normalize_code(code) or code)


@router.get("/market/forecast")
def market_forecast():
    return forecast.get_forecast()


@router.get("/quote")
def quote(codes: str):
    code_list = [market.normalize_code(c) or c for c in codes.split(",") if c.strip()]
    return market.get_quotes(code_list)


@router.get("/watchlist")
def watchlist_get():
    return market.get_watchlist()


@router.post("/watchlist/add")
def watchlist_add(code: str):
    return market.add_watch(code)


@router.post("/watchlist/remove")
def watchlist_remove(code: str):
    return market.remove_watch(code)


@router.post("/watchlist/pin")
def watchlist_pin(code: str):
    return market.toggle_pin(code)


# ---------------- 个股分析 ----------------

@router.get("/search")
def search(q: str, limit: int = 20):
    results = stocklist.search(q, limit)
    if not results:
        norm = market.normalize_code(q)
        if norm:
            quotes = market.get_quotes([norm])
            if norm in quotes:
                q0 = quotes[norm]
                results = [{"code": norm, "name": q0.get("name"), "market": norm[:2].upper(),
                            "board": "-", "price": q0.get("price"), "pct": q0.get("pct")}]
    return results


@router.get("/kline")
def get_kline(code: str, period: str = "day", count: int = Query(320, le=800)):
    norm = market.normalize_code(code) or code
    if period == "minute":
        return {"code": norm, "period": "minute", **kline.get_minute(norm)}
    return kline.get_kline(norm, period, count)


@router.get("/kline/fund")
def get_fund_kline(code: str, period: str = "day", refresh: int = 0):
    """个股主力资金K。缺数返回 empty_reason，不用涨跌幅冒充。"""
    if refresh:
        kline.invalidate_fund_cache(code)
    return kline.get_fund_kline(code, period)


@router.post("/kline/fund/sync")
def sync_fund_kline(code: str, lookback: int = Query(240, ge=20, le=500)):
    """多源拉取个股主力资金历史并写入本地 SQLite。"""
    return kline.sync_fund_history(code, lookback)


@router.get("/analysis")
def analysis(code: str):
    norm = market.normalize_code(code) or code
    quotes = market.get_quotes([norm])
    raw = quotes.get(norm)
    info = db_query("SELECT name, board FROM stock_list WHERE code=?", (norm,))
    name = ((info[0]["name"] if info else "") or (raw or {}).get("name") or "")
    board = info[0]["board"] if info else ""
    quote = market.attach_price_limits(raw, norm, name, board)
    if raw is None and quote.get("price") is None and quote.get("up_limit") is None:
        quote = None
    rows = db_query("SELECT * FROM stock_metrics WHERE code=?", (norm,))
    m = rows[0] if rows else None
    metrics2 = None
    if m:
        buy_lv, buy_act = metrics_svc.buy_index_level(m["buy_index"] or 0)
        sent_lv, sent_desc = metrics_svc.sentiment_level(m["sentiment"] or 50)
        metrics2 = {
            "buy_index": m["buy_index"], "buy_level": buy_lv, "buy_action": buy_act,
            "sentiment": m["sentiment"], "sent_level": sent_lv, "sent_desc": sent_desc,
            "stabilize_score": m["stabilize_score"],
            "gates": [bool(m[f"stab_g{i}"]) for i in (1, 2, 3, 4)],
            "rsi14": m["rsi14"], "pos60": m["pos60"], "drawdown60": m["drawdown60"],
            "bias20": m["bias20"], "ma_bull": bool(m["ma_bull"]),
            "divergence": m["divergence"], "updated_at": m["updated_at"],
        }
    return {
        "quote": quote,
        "rating": rating.score_stock(norm),
        "metrics": metrics2,
        "dark": darkpool.get_dark_power(norm),
        "pull_smash": darkpool.get_pull_smash(norm),
        "attribution": attribution.get_attribution(norm),
    }


# ---------------- 宏观情报 ----------------

@router.get("/macro/news")
def macro_news(limit: int = 60, days: int = 0):
    if days:
        return macro.get_news_range(days, limit)
    return macro.get_news(limit)


@router.get("/macro/policies")
def macro_policies(limit: int = 40, days: int = 0):
    if days:
        return macro.get_news_range(days, limit, policy_only=True)
    return macro.get_policies(limit)


@router.get("/macro/major")
def macro_major(limit: int = 20):
    return macro.get_major_events(limit)


@router.get("/macro/calendar")
def macro_calendar(months: int = Query(3, ge=1, le=6)):
    return macro.get_calendar(months)


# ---------------- 大宗商品 / 全球指数 ----------------

@router.get("/commodities")
def commodities(categories: str = ""):
    cats = [c for c in categories.split(",") if c] or None
    return {"catalog": list(commodity.CATALOG.keys()), "items": commodity.get_quotes(cats)}


@router.get("/commodities/kline")
def commodities_kline(symbol: str, period: str = "day"):
    return commodity.get_kline(symbol, period)


@router.get("/commodities/related")
def commodities_related(symbol: str, page: int = 1, page_size: int = Query(20, le=50)):
    return commodity.get_related_stocks(symbol, page, page_size)


@router.get("/etf/holdings")
def etf_holdings(code: str, limit: int = Query(20, le=50)):
    return global_index.get_etf_holdings(code, limit)


@router.get("/macro/event-detail")
def macro_event_detail(title: str, bull: str = "", bear: str = "",
                       limit: int = Query(30, ge=20, le=50)):
    return macro.get_event_detail(title, bull, bear, limit=limit)


@router.get("/macro/almanac")
def macro_almanac(day: str = Query("", alias="date"), span: int = Query(7, ge=0, le=31)):
    from .services import almanac
    d = almanac.resolve_almanac_date(day)
    if d is None:
        return {"ok": False, "error": "日期格式无效，请用 YYYY-MM-DD"}
    payload = almanac.get_almanac(d, persist=True)
    try:
        stored = almanac.prefetch_almanac_range(d, span)
        payload["stored"] = True
        payload["stored_days"] = len(stored)
    except Exception:
        payload["stored_days"] = 1 if payload.get("stored") else 0
    return payload


@router.post("/macro/almanac/sync")
def macro_almanac_sync(day: str = Query("", alias="date"), span: int = Query(7, ge=0, le=31)):
    """把选定日及前后 span 天的干支/黄道写入本地 SQLite。"""
    from .services import almanac
    d = almanac.resolve_almanac_date(day)
    if d is None:
        return {"ok": False, "error": "日期格式无效，请用 YYYY-MM-DD"}
    stored = almanac.prefetch_almanac_range(d, span)
    payload = almanac.get_almanac(d, persist=True)
    return {
        "ok": True,
        "date": payload["date"],
        "stored": True,
        "stored_days": len(stored),
        "span": span,
        "year_ganzhi": payload["year_ganzhi"],
        "month_ganzhi": payload["month_ganzhi"],
        "day_ganzhi": payload["day_ganzhi"],
        "huangdao": payload["huangdao"],
    }


@router.get("/macro/sector-events")
def macro_sector_events(months: int = Query(12, le=12), group: str = Query("day")):
    return macro.get_sector_intel(months, group)


@router.get("/macro/intel")
def macro_intel(kind: str = "", sector: str = "", days: int = 0, limit: int = Query(80, le=200)):
    return macro.list_intel(kind, sector, days, limit)


@router.post("/macro/intel-sync")
def macro_intel_sync():
    out = macro.sync_intel()
    try:
        out["official_policy"] = policy_archive.sync_official_policy("incremental")
    except Exception as exc:  # noqa: BLE001
        out["official_policy_error"] = str(exc)[:200]
    try:
        out["hot_terms"] = hot_terms.rebuild_hot_terms()
    except Exception as exc:  # noqa: BLE001
        out["hot_terms_error"] = str(exc)[:200]
    return out


@router.get("/macro/official-policy")
def macro_official_policy(scope: str = "", country: str = "", doc_type: str = "",
                          days: int = Query(180, ge=1, le=183),
                          limit: int = Query(80, le=200)):
    return policy_archive.list_official_policy(scope, country, doc_type, days, limit)


@router.post("/macro/official-policy-sync")
def macro_official_policy_sync(mode: str = "incremental"):
    if mode not in ("incremental", "backfill"):
        mode = "incremental"
    return policy_archive.sync_official_policy(mode)


@router.get("/macro/hot-terms")
def macro_hot_terms(kind: str = ""):
    return hot_terms.list_hot_terms(kind)


@router.post("/macro/hot-terms-rebuild")
def macro_hot_terms_rebuild():
    return hot_terms.rebuild_hot_terms()


@router.get("/macro/hot-term-sectors")
def macro_hot_term_sectors(term: str):
    return hot_terms.hot_term_sectors(term)


@router.get("/macro/hot-sector-stocks")
def macro_hot_sector_stocks(sector: str, limit: int = Query(20, ge=20, le=50)):
    return hot_terms.hot_sector_stocks(sector, limit)


@router.post("/macro/hot-term-ai")
def macro_hot_term_ai(payload: dict = Body(default={})):
    """右键 AI：分析热词利好/利空并回填；revert=true 还原词库。失败不覆盖。"""
    body = payload or {}
    term = (body.get("term") or "").strip()
    if body.get("revert"):
        return hot_terms.revert_hot_term_ai(term)
    return hot_terms.analyze_hot_term_ai(term)


@router.post("/macro/hot-terms-ai-batch")
def macro_hot_terms_ai_batch(payload: dict = Body(default={})):
    """一键回填当前热词列表的利好/利空。未配置或解析失败不覆盖。"""
    body = payload or {}
    return hot_terms.analyze_hot_terms_ai_batch(body.get("kind") or "", body.get("terms"))


@router.get("/macro/intel-ai/index")
def macro_intel_ai_index():
    """宏观条目已分析徽章索引。"""
    return intel_ai.index()


@router.get("/macro/intel-ai")
def macro_intel_ai_get(item_key: str = ""):
    """读取已保存的情报 AI 分析，不调用大模型。"""
    return intel_ai.get_detail(item_key)


@router.post("/macro/intel-ai")
def macro_intel_ai_analyze(payload: dict = Body(default={})):
    """右键：利好/利空回填、解读保存、或提取关键词。失败不覆盖。"""
    return intel_ai.analyze(payload or {})


@router.get("/macro/hot-intel")
def macro_hot_intel(source: str = "", limit: int = Query(80, le=200),
                    sort: str = "heat", order: str = "desc"):
    """热门信息：全部已落库 AI 词库/板块。默认热度倒序，可换维度。"""
    return intel_ai.list_hot_intel(source, limit, sort, order)


@router.post("/commodities/watch")
def commodities_watch(symbol: str):
    return commodity.toggle_watch(symbol)


@router.get("/global-indices")
def global_indices():
    return global_index.get_global()


@router.get("/etfs")
def etfs(filter: str = "all", page: int = 1, page_size: int = Query(20, le=50)):
    return global_index.get_etfs(filter, page, page_size)


# ---------------- 个股推荐 ----------------

@router.get("/recommend")
def recommend_board(board: str = "composite", page: int = 1,
                    page_size: int = Query(20, le=100), advice: str = "",
                    min_score: float = 0, vol_filter: str = "", order_by: str = "",
                    mv_filter: str = "", turn_filter: str = "",
                    finance_grade: str = ""):
    return {"boards": recommend.BOARDS,
            **recommend.get_board(board, page=page, page_size=page_size, advice=advice,
                                  min_score=min_score, vol_filter=vol_filter, order_by=order_by,
                                  mv_filter=mv_filter, turn_filter=turn_filter,
                                  finance_grade=finance_grade)}


# ---------------- 板块资金 / 画像 / 财务 / 周期（3.0） ----------------

@router.get("/sector/flow")
def sector_flow(dim: str = "industry"):
    return sector.get_flow(dim)


@router.get("/sector/stocks")
def sector_stocks(dim: str, name: str, limit: int = Query(30, le=100)):
    return sector.get_sector_stocks(dim, name, limit)


@router.get("/sector/flow-bar")
def sector_flow_bar(dim: str = "industry",
                    span: str = Query("1d", alias="range"),
                    sort: str = "inflow",
                    day: str = "",
                    direction: str = Query("", alias="dir"),
                    min_stocks: int = 0,
                    q: str = ""):
    return sector.get_flow_bar(dim, span, sort, day, direction, min_stocks, q)


@router.get("/sector/flow-trend")
def sector_flow_trend(dim: str = "industry", name: str = "",
                      grain: str = Query("1d"),
                      span: str = Query("1d", alias="range"),
                      top_n: int = Query(0, le=80),
                      direction: str = Query("", alias="dir"),
                      min_stocks: int = 0,
                      q: str = ""):
    return sector.get_flow_trend(dim, name, grain, top_n, direction, min_stocks, q,
                                 range_key=span)


@router.post("/sector/flow-sync")
def sector_flow_sync():
    local = sector.record_daily_flow()
    remote = sector.pull_remote_flow()
    em_hy = sector.pull_em_flow_history("industry", force=True)
    em_gn = sector.pull_em_flow_history("concept", force=True)
    vol = market.record_market_volume(local.get("date") or "")
    return {"ok": True, "local": local, "remote": remote, "em_hy": em_hy, "em_gn": em_gn,
            "market_vol": vol, "stats": sector.flow_history_stats()}


@router.get("/sector/flow-bar/stocks")
def sector_flow_bar_stocks(dim: str, name: str,
                           span: str = Query("1d", alias="range"),
                           limit: int = Query(50, le=200)):
    return sector.get_flow_bar_stocks(dim, name, span, limit)


@router.get("/profile")
def profile(code: str):
    norm = market.normalize_code(code) or code
    return sector.get_profile(norm)


@router.get("/finance")
def finance_report(code: str):
    norm = market.normalize_code(code) or code
    return finance.get_finance(norm)


@router.get("/holders")
def holders_report(code: str):
    norm = market.normalize_code(code) or code
    return holders.get_holders(norm)


@router.get("/holders/ai")
def holders_ai_get(code: str):
    """读取已保存的持股 AI 分析，不调用大模型。"""
    return holders.get_holder_ai(code)


@router.post("/holders/ai")
def holders_ai_analyze(payload: dict = Body(default={})):
    """手动更新持股 AI 分析。成功才落库并刷新时间戳；失败保留旧结果。"""
    return holders.analyze_holder_ai((payload or {}).get("code") or "")


@router.get("/market/cycle")
def market_cycle():
    return cycle.get_cycle()


# ---------------- 个股筛选器（FR2-01） ----------------

@router.get("/screener/meta")
def screener_meta():
    return screener.meta()


@router.post("/screener/run")
def screener_run(conditions: dict):
    return screener.run(conditions, limit=int(conditions.get("limit", 100)))


@router.get("/screener/plans")
def screener_plans():
    return screener.list_plans()


@router.post("/screener/plans")
def screener_save_plan(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "方案名不能为空"}
    return screener.save_plan(name, payload.get("conditions") or {})


@router.post("/screener/plans/delete")
def screener_delete_plan(plan_id: int):
    return screener.delete_plan(plan_id)


# ---------------- 暗盘指标（FR2-06） ----------------

@router.get("/dark/power")
def dark_power(code: str):
    norm = market.normalize_code(code) or code
    return darkpool.get_dark_power(norm)


@router.get("/dark/orderflow")
def dark_orderflow(code: str):
    norm = market.normalize_code(code) or code
    return darkpool.get_orderflow(norm) or {"error": "盘口数据暂不可用"}


# ---------------- 情绪 / 展望（FR2-04/05） ----------------

@router.get("/sentiment/market")
def sentiment_market():
    return metrics_svc.market_sentiment()


@router.get("/macro/outlook")
def macro_outlook(horizon: str = "week"):
    return macro.get_outlook(horizon)


@router.post("/macro/custom-event")
def macro_add_event(payload: dict):
    return macro.add_custom_event(
        payload.get("date", ""), payload.get("title", ""),
        int(payload.get("impact_level", 3)), payload.get("note", ""),
        payload.get("sectors", ""))


@router.post("/macro/custom-event/delete")
def macro_delete_event(event_id: int):
    return macro.delete_custom_event(event_id)


# ---------------- 常识 / 公告 / 板块周期 / AI（7.0） ----------------

@router.get("/knowledge")
def get_knowledge(q: str = ""):
    return knowledge.get_knowledge(q)


async def _json_obj(request: Request) -> dict:
    """空 body / 非 JSON 不抛 422，右键更新才能稳定调用。"""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


@router.post("/knowledge/ai")
async def knowledge_ai(request: Request, term: str = Query(""), section: str = Query("")):
    """右键 AI 更新股票常识 / 选股票小技巧。失败不覆盖已保存解释。"""
    body = await _json_obj(request)
    try:
        return knowledge.ai_update(
            term=(body.get("term") or term or "").strip(),
            section=(body.get("section") or section or "").strip(),
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False, "applied": False, "kept": True,
            "error": str(exc)[:300],
            "hint": "未覆盖已有解释。可到 AI 分析页检查配置后再试。",
            "text": "更新失败，已保留原解释。",
        }


@router.post("/knowledge/revert")
async def knowledge_revert(request: Request, term: str = Query("")):
    body = await _json_obj(request)
    try:
        return knowledge.revert_knowledge((body.get("term") or term or "").strip())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "applied": False, "error": str(exc)[:300]}


@router.get("/announcements")
def get_announcements(code: str = "", limit: int = Query(60, le=100)):
    return announcement.get_announcements(code, limit)


@router.get("/sector/cycles")
def sector_cycles():
    return sector.get_sector_cycles()


@router.get("/sector/month-cycles")
def sector_month_cycles():
    return sector.get_month_board_cycles()


@router.get("/ai/config")
def ai_config():
    return ai.get_config()


@router.post("/ai/config")
def ai_save_config(payload: dict):
    return ai.save_config(payload.get("api_base", ""), payload.get("api_key", ""),
                          payload.get("model", ""))


@router.post("/ai/test")
def ai_test():
    return ai.test_connection()


@router.post("/ai/analyze")
def ai_analyze(payload: dict):
    mode = payload.get("mode", "market")
    code = payload.get("code", "")
    if mode == "stock" and code:
        return stock_ai.analyze_brief(code)
    return ai.analyze(mode, code, payload.get("question", ""))


@router.get("/ai/brief")
def ai_brief_get(code: str):
    """读取已保存的个股简明诊断与五行判定，不调用大模型。"""
    return stock_ai.bundle(code)


@router.post("/ai/brief")
def ai_brief_post(payload: dict = Body(default={})):
    """手动更新个股简明诊断。成功才落库；失败保留旧结果。"""
    return stock_ai.analyze_brief((payload or {}).get("code") or "")


@router.post("/ai/pick")
def ai_pick(payload: dict):
    return ai.pick_stocks(payload.get("description", ""))


@router.post("/ai/wuxing")
def ai_wuxing(payload: dict):
    d = ai.classify_wuxing(payload.get("code", ""))
    saved = stock_ai.save_wuxing_result(payload.get("code", ""), d)
    return {**d, "saved": bool(saved.get("applied")), "saved_at": saved.get("analyzed_at") or ""}


@router.get("/engine/blueprint")
def engine_blueprint_get():
    """策略引擎设计对象 + 当前配置/快照状态。"""
    return engine_blueprint.blueprint()


@router.get("/engine/config")
def engine_config_get():
    return {"ok": True, **engine.get_config()}


@router.post("/engine/config")
def engine_config_save(payload: dict = Body(default={})):
    return engine.save_config(payload or {})


@router.post("/engine/run")
def engine_run_post(payload: dict = Body(default={})):
    kind = (payload or {}).get("kind") or "manual"
    if kind not in ("eod", "intraday", "manual"):
        kind = "manual"
    return engine.run_engine(kind)


@router.get("/engine/status")
def engine_status_get():
    return engine.status()


@router.get("/engine/brief")
def engine_brief_get():
    return engine.get_brief()


@router.get("/engine/signals")
def engine_signals_get(side: str = "buy", limit: int = Query(40, le=80)):
    return engine.get_signals(side, limit)


@router.get("/engine/catalysts")
def engine_catalysts_get():
    return engine.get_catalysts()


@router.get("/engine/stocks")
def engine_stocks_get(limit: int = Query(40, le=80), board: str = ""):
    if board:
        return engine.stocks_for_board(board, limit)
    return engine.list_stocks(limit)


@router.get("/engine/snapshot")
def engine_snapshot_get():
    return engine.snapshot_view()


@router.get("/engine/tasks")
def engine_tasks_get(side: str = "", status: str = "", limit: int = Query(80, le=200)):
    return engine.list_signal_tasks(side, status, limit)


@router.get("/intelpick")
def intelpick_page(side: str = "up"):
    """智能选股：股价未来涨跌方向分析。本轮返回菜单骨架，不编造个股名单。"""
    return intelpick.get_page(side)


@router.get("/smartpick/meta")
def smartpick_meta():
    return smartpick.meta()


@router.post("/smartpick/run")
def smartpick_run(payload: dict):
    return smartpick.run(payload or {})


@router.get("/smartpick/ai-policy")
def smartpick_policy_get():
    return {"ok": True, **smartpick.get_policy(), "usage": smartpick._usage()}


@router.post("/smartpick/ai-policy")
def smartpick_policy_save(payload: dict):
    return smartpick.save_policy(payload or {})


@router.post("/smartpick/ai-comment")
def smartpick_ai_comment(payload: dict):
    return smartpick.comment(
        payload.get("items") or [],
        payload.get("template_name") or "策略选股",
        payload.get("summary") or "",
        force=True)


# ---------------- 榜单 / 板块推荐 / 五行 / 语义筛选（8.0） ----------------

@router.get("/ranks")
def get_ranks(type: str = "limit_up", limit: int = Query(50, le=100)):
    return ranks.get_rank(type, limit)


@router.get("/sector/recommend")
def sector_recommend():
    return sector.get_sector_recommend()


@router.get("/wuxing")
def wuxing_get(code: str):
    norm = market.normalize_code(code) or code
    return wuxing.get_tags(norm)


@router.post("/wuxing/set")
def wuxing_set(payload: dict):
    norm = market.normalize_code(payload.get("code", "")) or payload.get("code", "")
    return wuxing.set_tags(norm, payload.get("tags", []))


@router.post("/screener/parse")
def screener_parse(payload: dict):
    return screener.parse_semantic(payload.get("text", ""))


@router.post("/system/job-toggle")
def job_toggle(job_id: str):
    return sched_mod.toggle_job(job_id)


@router.post("/system/job-interval")
def job_interval(payload: dict):
    return sched_mod.set_job_interval(payload.get("job_id", ""), int(payload.get("minutes", 0)))


# ---------------- 系统 / 设置 ----------------

@router.get("/system/status")
def system_status():
    return {
        "sources": [h.stats() for h in HEALTH.values()],
        "cache": cache.stats(),
        "jobs": scheduler.status(),
        "sync": stocklist.sync_state(),
        "last_realtime_refresh": get_meta("last_realtime_refresh", "-"),
        "finance": finance.stats(),
        "sector_flow": sector.flow_history_stats(),
        "intel": macro.intel_stats(),
    }


@router.post("/system/sync")
def system_sync():
    return stocklist.full_sync()


@router.get("/system/sync-state")
def system_sync_state():
    return stocklist.sync_state()


@router.post("/system/clear-cache")
def system_clear_cache():
    cache.clear()
    return {"ok": True}


@router.post("/system/rebuild-metrics")
def system_rebuild_metrics(include_kline: bool = True):
    import threading
    threading.Thread(target=metrics_svc.rebuild_all, args=(include_kline,), daemon=True).start()
    return {"ok": True, "message": "指标重建已在后台启动"}


@router.get("/system/metrics-state")
def system_metrics_state():
    return metrics_svc.state()


@router.post("/system/rebuild-finance-grades")
def system_rebuild_finance(max_fetch: int = Query(300, le=800)):
    import threading
    threading.Thread(target=finance.rebuild_all, args=(max_fetch,), daemon=True).start()
    return {"ok": True, "message": "财报评级重建已在后台启动"}


@router.get("/system/finance-state")
def system_finance_state():
    return finance.state()


@router.get("/system/verify-kline")
def system_verify_kline():
    """数据校验（FR5-03-2）：比对日K末根与实时行情。"""
    report = []
    codes = ["sh600519", "sz300432", "sh000001"]
    quotes = market.get_quotes(codes)
    for code in codes:
        try:
            k = kline.get_kline(code, "day", 5)
            q = quotes.get(code) or {}
            if not k["dates"] or q.get("price") is None:
                report.append({"code": code, "ok": False, "msg": "数据不足"})
                continue
            last_close = k["kline"][-1][1]
            diff = abs(last_close - q["price"]) / q["price"] * 100
            report.append({
                "code": code, "name": q.get("name"), "ok": diff < 0.5,
                "kline_date": k["dates"][-1], "kline_close": last_close,
                "realtime": q["price"], "diff_pct": round(diff, 3),
                "kline_high": k["kline"][-1][2], "quote_high": q.get("high"),
            })
        except Exception as exc:  # noqa: BLE001
            report.append({"code": code, "ok": False, "msg": str(exc)[:100]})
    return {"report": report, "passed": all(r.get("ok") for r in report)}


@router.post("/system/source-toggle")
def source_toggle(name: str):
    h = HEALTH.get(name)
    if not h:
        return {"ok": False, "error": "未知数据源"}
    h.enabled = not h.enabled
    return {"ok": True, "enabled": h.enabled}
