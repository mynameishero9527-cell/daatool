"""大宗商品服务：分类品种目录、实时报价、关注列表。"""
from datetime import datetime

from ..cache import cached
from ..config import TTL_COMMODITY
from ..database import execute, query
from ..datasources import sina

# 分类 → [(sina符号, 显示名, 单位, 关联A股板块, K线符号[global:CL / inner:RB0 / 空=无K线])]
CATALOG: dict[str, list[tuple[str, str, str, str, str]]] = {
    "能源": [
        ("hf_CL", "WTI原油", "美元/桶", "石油石化", "CL"),
        ("hf_OIL", "布伦特原油", "美元/桶", "石油石化", "OIL"),
        ("hf_NG", "天然气", "美元/mmBtu", "燃气", "NG"),
    ],
    "贵金属": [
        ("hf_GC", "COMEX黄金", "美元/盎司", "黄金股", "GC"),
        ("hf_SI", "COMEX白银", "美元/盎司", "贵金属", "SI"),
        ("hf_PL", "铂金", "美元/盎司", "贵金属", "PL"),
    ],
    "有色金属": [
        ("hf_HG", "COMEX铜", "美分/磅", "有色金属", "HG"),
        ("hf_AHD", "LME铝", "美元/吨", "有色金属", "AHD"),
        ("hf_ZSD", "LME锌", "美元/吨", "有色金属", "ZSD"),
        ("hf_NID", "LME镍", "美元/吨", "有色金属", "NID"),
        ("hf_SND", "LME锡", "美元/吨", "有色金属", "SND"),
        ("hf_PBD", "LME铅", "美元/吨", "有色金属", "PBD"),
    ],
    "黑色系": [
        ("nf_RB0", "螺纹钢", "元/吨", "钢铁", "RB0"),
        ("nf_HC0", "热卷", "元/吨", "钢铁", "HC0"),
        ("nf_I0", "铁矿石", "元/吨", "钢铁", "I0"),
        ("nf_J0", "焦炭", "元/吨", "煤炭", "J0"),
        ("nf_JM0", "焦煤", "元/吨", "煤炭", "JM0"),
    ],
    "化工": [
        ("hf_TRB", "日胶", "日元/千克", "化工", "TRB"),
        ("nf_TA0", "PTA", "元/吨", "化工", "TA0"),
        ("nf_MA0", "甲醇", "元/吨", "化工", "MA0"),
        ("nf_PP0", "聚丙烯", "元/吨", "化工", "PP0"),
        ("nf_RU0", "沪胶", "元/吨", "化工", "RU0"),
    ],
    "农产品": [
        ("hf_S", "美豆", "美分/蒲式耳", "农业", "S"),
        ("hf_C", "美玉米", "美分/蒲式耳", "农业", "C"),
        ("hf_CT", "美棉花", "美分/磅", "纺织", "CT"),
        ("nf_M0", "豆粕", "元/吨", "饲料", "M0"),
        ("nf_SR0", "白糖", "元/吨", "食品", "SR0"),
        ("nf_AP0", "苹果", "元/吨", "农业", "AP0"),
    ],
    "国内金属": [
        ("nf_CU0", "沪铜", "元/吨", "有色金属", "CU0"),
        ("nf_AL0", "沪铝", "元/吨", "有色金属", "AL0"),
        ("nf_ZN0", "沪锌", "元/吨", "有色金属", "ZN0"),
        ("nf_AU0", "沪金", "元/克", "黄金股", "AU0"),
        ("nf_AG0", "沪银", "元/千克", "贵金属", "AG0"),
    ],
}

_META = {sym: (name, unit, sector, ksym)
         for items in CATALOG.values() for sym, name, unit, sector, ksym in items}


def get_quotes(categories: list[str] | None = None) -> list[dict]:
    cats = [c for c in (categories or list(CATALOG)) if c in CATALOG]
    hf_symbols = [sym for c in cats for sym, *_ in CATALOG[c] if sym.startswith("hf_")]
    nf_symbols = [sym for c in cats for sym, *_ in CATALOG[c] if sym.startswith("nf_")]
    if not hf_symbols and not nf_symbols:
        return []

    def loader():
        data: dict = {}
        offline = False
        try:
            if hf_symbols:
                data.update(sina.fetch_commodities(hf_symbols))
            if nf_symbols:
                data.update(sina.fetch_domestic_futures(nf_symbols))
        except Exception:  # noqa: BLE001
            offline = not data
        return {"data": data, "offline": offline}

    fetched = cached("commodity:" + ",".join(sorted(cats)), TTL_COMMODITY, loader)
    data = fetched["data"]
    watched = {r["symbol"] for r in query("SELECT symbol FROM commodity_watch")}
    out = []
    for cat in cats:
        for sym, name, unit, sector, ksym in CATALOG[cat]:
            q = data.get(sym) or {}
            out.append({
                "symbol": sym, "name": name, "category": cat, "unit": unit,
                "related_sector": sector, "kline_symbol": ksym,
                "price": q.get("price"), "pct": q.get("pct"),
                "high": q.get("high"), "low": q.get("low"), "time": q.get("time"),
                "watched": sym in watched, "offline": fetched["offline"],
            })
    out.sort(key=lambda x: (not x["watched"],))
    return out


def _aggregate(rows: list[list], period: str) -> list[list]:
    """日K 本地聚合为 周K/月K。"""
    if period == "day" or not rows:
        return rows
    out, bucket, key = [], None, None
    for r in rows:
        d = r[0]
        k = d[:7] if period == "month" else f"{d[:4]}-W{__import__('datetime').date.fromisoformat(d).isocalendar()[1]:02d}"
        if k != key:
            if bucket:
                out.append(bucket)
            bucket = [d, r[1], r[2], r[3], r[4], r[5]]
            key = k
        else:
            bucket[0] = d           # 以周期内最后一日为标签
            bucket[2] = r[2]        # close
            bucket[3] = max(bucket[3], r[3])
            bucket[4] = min(bucket[4], r[4])
            bucket[5] += r[5]
    if bucket:
        out.append(bucket)
    return out


def get_kline(symbol: str, period: str = "day") -> dict:
    """商品K线：日/周/月 + MA5/10/20 + 成交量。symbol 为 sina 行情符号（hf_CL / nf_RB0）。"""
    meta = _META.get(symbol)
    if not meta or not meta[3]:
        return {"symbol": symbol, "error": "该品种暂不支持K线"}
    name, unit, _sector, ksym = meta
    period = period if period in ("day", "week", "month") else "day"

    def loader():
        try:
            rows = sina.fetch_futures_kline(ksym, inner=symbol.startswith("nf_"), count=280)
            return {"rows": rows, "offline": False}
        except Exception:  # noqa: BLE001
            return {"rows": [], "offline": True}

    data = cached(f"ckline:{symbol}", 3600, loader)
    rows = _aggregate(data["rows"], period)[-160:]
    closes = [r[2] for r in rows]

    def ma(n):
        return [round(sum(closes[max(0, i + 1 - n):i + 1]) / n, 3) if i + 1 >= n else None
                for i in range(len(closes))]

    return {
        "symbol": symbol, "name": name, "unit": unit, "period": period,
        "dates": [r[0] for r in rows],
        "kline": [[r[1], r[2], r[3], r[4]] for r in rows],
        "volumes": [r[5] for r in rows],
        "ma": {5: ma(5), 10: ma(10), 20: ma(20)},
        "offline": data["offline"],
    }


# 商品关联板块 → (概念候选, 行业候选)
_RELATED_LOOKUP = {
    "石油石化": ("石油", "石油石化"), "燃气": ("天然气", "公用事业"),
    "黄金股": ("黄金", "有色金属"), "贵金属": ("黄金", "有色金属"),
    "有色金属": ("有色", "有色金属"), "钢铁": ("钢铁", "钢铁"), "煤炭": ("煤炭", "煤炭"),
    "化工": ("化工", "基础化工"), "农业": ("农业", "农林牧渔"),
    "纺织": ("纺织", "纺织服饰"), "饲料": ("饲料", "农林牧渔"), "食品": ("食糖", "食品饮料"),
}


def get_related_stocks(symbol: str, page: int = 1, page_size: int = 20) -> dict:
    """商品关联的大A个股列表（FR5-04），分页，含购买指数/情绪/评分。"""
    meta = _META.get(symbol)
    if not meta:
        return {"items": [], "total": 0, "page": 1, "pages": 1, "sector": ""}
    sector = meta[2]
    concept, industry = _RELATED_LOOKUP.get(sector, (sector, sector))
    from . import metrics as metrics_svc
    from . import rating as rating_svc

    rows = query(
        """SELECT s.code, s.name, s.price, s.pct, s.pct_d5, s.pct_d20, s.pct_d60,
                  s.main_net_in, s.volume_ratio, s.float_mv,
                  s.main_in, s.main_out, s.amount,
                  l.industry, m.buy_index, m.sentiment, m.dark_power
           FROM stock_snapshot s
           JOIN stock_list l ON l.code = s.code
           LEFT JOIN stock_metrics m ON m.code = s.code
           WHERE s.price IS NOT NULL AND (l.industry = ? OR s.code IN
               (SELECT code FROM concept_map WHERE concept LIKE ?))
           ORDER BY s.float_mv DESC LIMIT 100""",
        (industry, f"%{concept}%"))
    for r in rows:
        if r["sentiment"] is not None:
            r["sent_level"] = metrics_svc.sentiment_level(r["sentiment"])[0]
        r["volume_desc"] = rating_svc.volume_desc(r["volume_ratio"])
        r["score"], r["advice"] = rating_svc.quick_score(r)
    total = len(rows)
    page_size = page_size if page_size in (10, 20, 50) else 20
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, pages))
    page_items = rows[(page - 1) * page_size: page * page_size]
    from . import finance as finance_svc
    finance_svc.attach_grades(page_items)
    metrics_svc.attach_flow_list(page_items)
    return {"items": page_items,
            "total": total, "page": page, "pages": pages, "page_size": page_size,
            "sector": sector}


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
