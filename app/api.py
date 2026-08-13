"""FastAPI 路由：所有接口为同步函数，由框架线程池执行，保证事件循环不被阻塞。"""
from fastapi import APIRouter, Query

from . import scheduler
from .cache import cache
from .database import get_meta
from .datasources.base import HEALTH
from .services import (
    commodity, darkpool, global_index, kline, macro, market, rating, recommend,
    screener, stocklist,
)
from .services import metrics as metrics_svc
from .database import query as db_query

router = APIRouter(prefix="/api")


# ---------------- 行情看板 ----------------

@router.get("/dashboard")
def dashboard():
    return {
        "indices": market.get_indices_overview(),
        "stats": market.market_stats(),
        "movers": market.top_movers(5),
        "watchlist": market.get_watchlist(),
    }


@router.get("/quote")
def quote(codes: str):
    code_list = [market.normalize_code(c) or c for c in codes.split(",") if c.strip()]
    return market.get_quotes(code_list)


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


@router.get("/analysis")
def analysis(code: str):
    norm = market.normalize_code(code) or code
    quotes = market.get_quotes([norm])
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
        "quote": quotes.get(norm),
        "rating": rating.score_stock(norm),
        "metrics": metrics2,
        "dark": darkpool.get_dark_power(norm),
    }


# ---------------- 宏观情报 ----------------

@router.get("/macro/news")
def macro_news(limit: int = 60):
    return macro.get_news(limit)


@router.get("/macro/policies")
def macro_policies(limit: int = 40):
    return macro.get_policies(limit)


@router.get("/macro/major")
def macro_major(limit: int = 20):
    return macro.get_major_events(limit)


@router.get("/macro/calendar")
def macro_calendar(months: int = Query(3, ge=1, le=3)):
    return macro.get_calendar(months)


# ---------------- 大宗商品 / 全球指数 ----------------

@router.get("/commodities")
def commodities(categories: str = ""):
    cats = [c for c in categories.split(",") if c] or None
    return {"catalog": list(commodity.CATALOG.keys()), "items": commodity.get_quotes(cats)}


@router.post("/commodities/watch")
def commodities_watch(symbol: str):
    return commodity.toggle_watch(symbol)


@router.get("/global-indices")
def global_indices():
    return global_index.get_global()


@router.get("/etfs")
def etfs():
    return global_index.get_etfs()


# ---------------- 个股推荐 ----------------

@router.get("/recommend")
def recommend_board(board: str = "composite", limit: int = Query(50, le=100)):
    return {"boards": recommend.BOARDS, **recommend.get_board(board, limit)}


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
        int(payload.get("impact_level", 3)), payload.get("note", ""))


@router.post("/macro/custom-event/delete")
def macro_delete_event(event_id: int):
    return macro.delete_custom_event(event_id)


# ---------------- 系统 / 设置 ----------------

@router.get("/system/status")
def system_status():
    return {
        "sources": [h.stats() for h in HEALTH.values()],
        "cache": cache.stats(),
        "jobs": scheduler.status(),
        "sync": stocklist.sync_state(),
        "last_realtime_refresh": get_meta("last_realtime_refresh", "-"),
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


@router.post("/system/source-toggle")
def source_toggle(name: str):
    h = HEALTH.get(name)
    if not h:
        return {"ok": False, "error": "未知数据源"}
    h.enabled = not h.enabled
    return {"ok": True, "enabled": h.enabled}
