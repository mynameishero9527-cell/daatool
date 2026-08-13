"""全球指数服务：分区域指数（标注国家）、国内大A指数、主要 ETF。"""
from ..cache import cached
from ..config import TTL_GLOBAL_INDEX
from ..database import query
from ..datasources import sina
from . import market

# 区域 → [(sina符号, 名称, 国家/地区, 简介)]
GLOBAL_CATALOG: dict[str, list[tuple[str, str, str, str]]] = {
    "美国": [
        ("gb_dji", "道琼斯工业指数", "美国", "30家美国蓝筹工业股"),
        ("gb_ixic", "纳斯达克综合指数", "美国", "美国科技股风向标"),
        ("gb_inx", "标普500指数", "美国", "500家美国大型上市公司"),
    ],
    "欧洲": [
        ("int_ftse", "英国富时100", "英国", "伦敦交易所市值前100"),
        ("int_dax", "德国DAX30", "德国", "法兰克福30家蓝筹"),
        ("int_fchi", "法国CAC40", "法国", "巴黎40家龙头企业"),
    ],
    "亚太": [
        ("int_nikkei", "日经225", "日本", "东京225家代表企业"),
        ("int_kospi", "韩国KOSPI", "韩国", "韩国综合股价指数"),
        ("int_hangseng", "恒生指数", "中国香港", "港股大盘蓝筹"),
        ("int_sensex", "印度SENSEX30", "印度", "孟买30家权重股"),
        ("int_asx200", "澳洲标普200", "澳大利亚", "澳交所市值前200"),
        ("int_sti", "新加坡海峡时报", "新加坡", "新交所30家蓝筹"),
    ],
    "其他": [
        ("int_rts", "俄罗斯RTS", "俄罗斯", "莫斯科交易所美元计价指数"),
        ("int_bvsp", "巴西BOVESPA", "巴西", "圣保罗交易所大盘指数"),
        ("int_gsptse", "加拿大S&P/TSX", "加拿大", "多伦多交易所综合指数"),
    ],
}

# 国内主要 ETF：(带前缀代码, 分类, 跟踪标的)
ETF_CATALOG: list[tuple[str, str, str]] = [
    ("sh510300", "宽基", "沪深300"),
    ("sh510500", "宽基", "中证500"),
    ("sh588000", "宽基", "科创50"),
    ("sz159915", "宽基", "创业板指"),
    ("sh512880", "行业", "证券公司"),
    ("sh512480", "行业", "半导体"),
    ("sh512010", "行业", "医药卫生"),
    ("sh515030", "行业", "新能源车"),
    ("sh512660", "行业", "军工"),
    ("sh512690", "行业", "白酒"),
    ("sh513100", "跨境", "纳斯达克100"),
    ("sh513180", "跨境", "恒生科技"),
    ("sh518880", "商品", "黄金"),
    ("sz159985", "商品", "豆粕"),
]


def get_global() -> dict:
    """全球指数（含中国大陆分区，来自腾讯）。"""
    symbols = [sym for items in GLOBAL_CATALOG.values() for sym, *_ in items]

    def loader():
        try:
            return {"data": sina.fetch_global_indices(symbols), "offline": False}
        except Exception:  # noqa: BLE001
            return {"data": {}, "offline": True}

    fetched = cached("global:indices", TTL_GLOBAL_INDEX, loader)
    data = fetched["data"]

    regions = []
    cn = market.get_indices_overview()
    regions.append({
        "region": "中国大陆",
        "items": [{
            "symbol": q["code"], "name": q["name"], "country": "中国",
            "desc": "A股核心指数", "price": q.get("price"), "pct": q.get("pct"),
            "change": q.get("change"), "time": q.get("time"),
        } for q in cn],
    })
    for region, items in GLOBAL_CATALOG.items():
        rows = []
        for sym, name, country, desc in items:
            q = data.get(sym) or {}
            rows.append({
                "symbol": sym, "name": name, "country": country, "desc": desc,
                "price": q.get("price"), "pct": q.get("pct"),
                "change": q.get("change"), "time": q.get("time"),
            })
        regions.append({"region": region, "items": rows})
    return {"regions": regions, "offline": fetched["offline"]}


# ETF → 持仓推算规则：("mv_top",None) 全市场市值前N / ("mv_range",(a,b)) 市值排名区间
#                      / ("board",板块) / ("sector",(概念,行业)) / None=QDII无A股持仓
_ETF_HOLDING_RULES: dict[str, tuple | None] = {
    "sh510300": ("mv_top", None),
    "sh510500": ("mv_range", (300, 500)),
    "sh588000": ("board", "科创板"),
    "sz159915": ("board", "创业板"),
    "sh512880": ("sector", ("证券", "非银金融")),
    "sh512480": ("sector", ("半导体", "电子")),
    "sh512010": ("sector", ("创新药", "医药生物")),
    "sh515030": ("sector", ("新能源车", "电力设备")),
    "sh512660": ("sector", ("军工", "国防军工")),
    "sh512690": ("sector", ("白酒", "食品饮料")),
    "sh513100": None,
    "sh513180": None,
    "sh518880": ("sector", ("黄金", "有色金属")),
    "sz159985": ("sector", ("豆粕", "农林牧渔")),
}

_HOLDING_SELECT = """
    SELECT s.code, s.name, s.price, s.pct, s.float_mv, l.industry,
           m.buy_index, m.sentiment, m.dark_power
    FROM stock_snapshot s
    JOIN stock_list l ON l.code = s.code
    LEFT JOIN stock_metrics m ON m.code = s.code
    WHERE s.price IS NOT NULL AND s.float_mv IS NOT NULL
      AND s.name NOT LIKE '%ST%' """


def get_etf_holdings(code: str, limit: int = 20) -> dict:
    """ETF 近似持仓（FR5-06-3）：按跟踪标的从本地数据推算权重，附指标列。"""
    from . import metrics as metrics_svc
    from . import rating as rating_svc

    rule = _ETF_HOLDING_RULES.get(code, ("mv_top", None))
    track = next((t for c, _cat, t in ETF_CATALOG if c == code), "")
    if rule is None:
        return {"code": code, "track": track, "holdings": [],
                "note": "QDII 跨境 ETF，持仓为境外资产，无A股持仓数据"}

    kind, arg = rule
    if kind == "mv_top":
        rows = query(_HOLDING_SELECT + "ORDER BY s.float_mv DESC LIMIT ?", (limit,))
    elif kind == "mv_range":
        lo, hi = arg
        rows = query(_HOLDING_SELECT + "ORDER BY s.float_mv DESC LIMIT ? OFFSET ?",
                     (limit, lo))
    elif kind == "board":
        rows = query(_HOLDING_SELECT + "AND l.board = ? ORDER BY s.float_mv DESC LIMIT ?",
                     (arg, limit))
    else:  # sector
        concept, industry = arg
        rows = query(
            _HOLDING_SELECT + """AND (l.industry = ? OR s.code IN
                (SELECT code FROM concept_map WHERE concept LIKE ?))
                ORDER BY s.float_mv DESC LIMIT ?""",
            (industry, f"%{concept}%", limit))

    total_mv = sum(r["float_mv"] or 0 for r in rows) or 1
    for r in rows:
        r["weight"] = round((r["float_mv"] or 0) / total_mv * 100, 2)
        if r["sentiment"] is not None:
            r["sent_level"] = metrics_svc.sentiment_level(r["sentiment"])[0]
        if r["buy_index"] is not None:
            r["buy_level"] = metrics_svc.buy_index_level(r["buy_index"])[0]
        score = _quick_grade(r)
        r["advice"] = score
    return {"code": code, "track": track, "holdings": rows,
            "note": "近似持仓：按跟踪标的从本地市值/板块数据推算，非基金实际披露持仓"}


def _quick_grade(r: dict) -> str:
    bi = r.get("buy_index")
    if bi is None:
        return "-"
    return "增持" if bi >= 70 else "减持" if bi <= 35 else "保持不变"


def get_etfs() -> list[dict]:
    codes = [c for c, *_ in ETF_CATALOG]
    quotes = market.get_quotes(codes)
    names = {r["code"]: r["name"] for r in query(
        f"SELECT code,name FROM stock_list WHERE code IN ({','.join('?' * len(codes))})", tuple(codes))}
    out = []
    for code, category, track in ETF_CATALOG:
        q = quotes.get(code) or {}
        out.append({
            "code": code, "category": category, "track": track,
            "name": q.get("name") or names.get(code) or code,
            "price": q.get("price"), "pct": q.get("pct"),
            "amount": q.get("amount"), "source": q.get("source"),
        })
    return out
