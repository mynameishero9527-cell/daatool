"""大宗商品服务：分类品种目录、实时报价、关注列表。"""
from datetime import datetime

from ..cache import cached
from ..config import TTL_COMMODITY
from ..database import execute, query
from ..datasources import sina

# 分类 → [(sina符号, 显示名, 单位, 关联A股板块)]
CATALOG: dict[str, list[tuple[str, str, str, str]]] = {
    "能源": [
        ("hf_CL", "WTI原油", "美元/桶", "石油石化"),
        ("hf_OIL", "布伦特原油", "美元/桶", "石油石化"),
        ("hf_NG", "天然气", "美元/mmBtu", "燃气"),
    ],
    "贵金属": [
        ("hf_GC", "COMEX黄金", "美元/盎司", "黄金股"),
        ("hf_SI", "COMEX白银", "美元/盎司", "贵金属"),
        ("hf_PL", "铂金", "美元/盎司", "贵金属"),
    ],
    "有色金属": [
        ("hf_HG", "COMEX铜", "美分/磅", "有色金属"),
        ("hf_AHD", "LME铝", "美元/吨", "有色金属"),
        ("hf_ZSD", "LME锌", "美元/吨", "有色金属"),
        ("hf_NID", "LME镍", "美元/吨", "有色金属"),
        ("hf_SND", "LME锡", "美元/吨", "有色金属"),
        ("hf_PBD", "LME铅", "美元/吨", "有色金属"),
    ],
    "化工农产品": [
        ("hf_TRB", "橡胶", "日元/千克", "化工"),
        ("hf_S", "美豆", "美分/蒲式耳", "农业"),
        ("hf_C", "美玉米", "美分/蒲式耳", "农业"),
        ("hf_CT", "美棉花", "美分/磅", "纺织"),
        ("hf_SM", "美豆粕", "美元/短吨", "饲料"),
        ("hf_W", "美小麦", "美分/蒲式耳", "农业"),
    ],
}

_META = {sym: (name, unit, sector) for items in CATALOG.values() for sym, name, unit, sector in items}


def get_quotes(categories: list[str] | None = None) -> list[dict]:
    cats = [c for c in (categories or list(CATALOG)) if c in CATALOG]
    symbols = [sym for c in cats for sym, *_ in CATALOG[c]]
    if not symbols:
        return []

    def loader():
        try:
            return {"data": sina.fetch_commodities(symbols), "offline": False}
        except Exception:  # noqa: BLE001
            return {"data": {}, "offline": True}

    fetched = cached("commodity:" + ",".join(sorted(cats)), TTL_COMMODITY, loader)
    data = fetched["data"]
    watched = {r["symbol"] for r in query("SELECT symbol FROM commodity_watch")}
    out = []
    for cat in cats:
        for sym, name, unit, sector in CATALOG[cat]:
            q = data.get(sym) or {}
            out.append({
                "symbol": sym, "name": name, "category": cat, "unit": unit,
                "related_sector": sector,
                "price": q.get("price"), "pct": q.get("pct"),
                "high": q.get("high"), "low": q.get("low"), "time": q.get("time"),
                "watched": sym in watched, "offline": fetched["offline"],
            })
    out.sort(key=lambda x: (not x["watched"],))
    return out


def toggle_watch(symbol: str) -> dict:
    if symbol not in _META:
        return {"ok": False, "error": "未知品种"}
    rows = query("SELECT 1 FROM commodity_watch WHERE symbol=?", (symbol,))
    if rows:
        execute("DELETE FROM commodity_watch WHERE symbol=?", (symbol,))
        return {"ok": True, "watched": False}
    execute("INSERT INTO commodity_watch(symbol,created_at) VALUES(?,?)",
            (symbol, datetime.now().isoformat(timespec="seconds")))
    return {"ok": True, "watched": True}
