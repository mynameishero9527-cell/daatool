"""FastAPI 路由：所有接口为同步函数，由框架线程池执行，保证事件循环不被阻塞。"""
from fastapi import APIRouter, Query

from . import scheduler
from .cache import cache
from .database import get_meta
from .datasources.base import HEALTH
from .services import (
    commodity, global_index, kline, macro, market, rating, recommend, stocklist,
)

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
    return {"quote": quotes.get(norm), "rating": rating.score_stock(norm)}


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


@router.post("/system/source-toggle")
def source_toggle(name: str):
    h = HEALTH.get(name)
    if not h:
        return {"ok": False, "error": "未知数据源"}
    h.enabled = not h.enabled
    return {"ok": True, "enabled": h.enabled}
