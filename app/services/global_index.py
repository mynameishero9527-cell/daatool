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
    # 宽基
    ("sh510050", "宽基", "上证50"),
    ("sh510300", "宽基", "沪深300"),
    ("sh510500", "宽基", "中证500"),
    ("sh512100", "宽基", "中证1000"),
    ("sh588000", "宽基", "科创50"),
    ("sz159915", "宽基", "创业板指"),
    ("sz159949", "宽基", "创业板50"),
    # 金融
    ("sh512880", "行业", "证券公司"),
    ("sh512000", "行业", "券商"),
    ("sh512800", "行业", "银行"),
    ("sh512200", "行业", "房地产"),
    # 科技
    ("sh512480", "行业", "半导体"),
    ("sz159995", "行业", "芯片"),
    ("sh515000", "行业", "科技龙头"),
    ("sh515050", "行业", "5G通信"),
    ("sh515880", "行业", "通信设备"),
    ("sz159869", "行业", "游戏"),
    ("sh512980", "行业", "传媒"),
    # 医药消费
    ("sh512010", "行业", "医药卫生"),
    ("sh512170", "行业", "医疗器械"),
    ("sh512690", "行业", "白酒"),
    ("sh515170", "行业", "食品饮料"),
    ("sz159928", "行业", "中证消费"),
    ("sz159996", "行业", "家电"),
    ("sz159766", "行业", "旅游"),
    # 制造周期
    ("sh515030", "行业", "新能源车"),
    ("sh515790", "行业", "光伏产业"),
    ("sz159875", "行业", "新能源"),
    ("sh512660", "行业", "军工"),
    ("sh512710", "行业", "军工龙头"),
    ("sh515220", "行业", "煤炭"),
    ("sh512400", "行业", "有色金属"),
    ("sh515210", "行业", "钢铁"),
    ("sh516220", "行业", "化工"),
    ("sh512580", "行业", "环保"),
    ("sz159865", "行业", "养殖"),
    # 跨境
    ("sh513100", "跨境", "纳斯达克100"),
    ("sh513500", "跨境", "标普500"),
    ("sh513180", "跨境", "恒生科技"),
    ("sh513050", "跨境", "中概互联"),
    ("sz159920", "跨境", "恒生指数"),
    # 商品
    ("sh518880", "商品", "黄金"),
    ("sz159934", "商品", "黄金(华安)"),
    ("sz159985", "商品", "豆粕"),
    ("sz159981", "商品", "能源化工"),
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
    SELECT s.code, s.name, s.price, s.pct, s.pct_d5, s.pct_d20, s.pct_d60,
           s.float_mv, s.main_net_in, s.volume_ratio, l.industry,
           m.buy_index, m.sentiment, m.dark_power
    FROM stock_snapshot s
    JOIN stock_list l ON l.code = s.code
    LEFT JOIN stock_metrics m ON m.code = s.code
    WHERE s.price IS NOT NULL AND s.float_mv IS NOT NULL
      AND s.name NOT LIKE '%ST%' """


# 跟踪标的关键词 → (概念候选, 行业候选)，用于扩容 ETF 的持仓推算
_TRACK_KEYWORD_RULES: list[tuple[tuple, tuple]] = [
    (("证券", "券商"), ("证券", "非银金融")),
    (("银行",), ("银行", "银行")),
    (("房地产", "地产"), ("房地产", "房地产")),
    (("半导体", "芯片"), ("半导体", "电子")),
    (("科技",), ("人工智能", "计算机")),
    (("5G", "通信"), ("5G", "通信")),
    (("游戏",), ("游戏", "传媒")),
    (("传媒",), ("传媒", "传媒")),
    (("医药", "医疗"), ("创新药", "医药生物")),
    (("白酒", "食品", "消费"), ("白酒", "食品饮料")),
    (("家电",), ("家电", "家用电器")),
    (("旅游",), ("旅游", "社会服务")),
    (("新能源车",), ("新能源车", "汽车")),
    (("光伏",), ("光伏", "电力设备")),
    (("新能源", "能源化工"), ("新能源", "电力设备")),
    (("军工",), ("军工", "国防军工")),
    (("煤炭",), ("煤炭", "煤炭")),
    (("有色", "黄金"), ("黄金", "有色金属")),
    (("钢铁",), ("钢铁", "钢铁")),
    (("化工",), ("化工", "基础化工")),
    (("环保",), ("环保", "环保")),
    (("养殖", "豆粕"), ("养殖", "农林牧渔")),
]
_QDII_KEYWORDS = ("纳斯达克", "标普", "恒生", "中概")


def _rule_for(code: str, track: str):
    if code in _ETF_HOLDING_RULES:
        return _ETF_HOLDING_RULES[code]
    if any(k in track for k in _QDII_KEYWORDS):
        return None
    for keywords, pair in _TRACK_KEYWORD_RULES:
        if any(k in track for k in keywords):
            return ("sector", pair)
    if "中证1000" in track:
        return ("mv_range", (800, 1000))
    if "中证500" in track:
        return ("mv_range", (300, 500))
    if "创业板" in track:
        return ("board", "创业板")
    if "科创" in track:
        return ("board", "科创板")
    return ("mv_top", None)


def get_etf_holdings(code: str, limit: int = 20) -> dict:
    """ETF 近似持仓（FR5-06-3 / FR6-04-4）：按跟踪标的从本地数据推算权重，附指标列。"""
    from . import metrics as metrics_svc
    from . import rating as rating_svc

    track = next((t for c, _cat, t in ETF_CATALOG if c == code), "")
    rule = _rule_for(code, track)
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
        r["score"], r["advice"] = rating_svc.quick_score(r)
        r["volume_desc"] = rating_svc.volume_desc(r["volume_ratio"])
    return {"code": code, "track": track, "holdings": rows,
            "note": "近似持仓：按跟踪标的从本地市值/板块数据推算，非基金实际披露持仓"}


def get_etfs(filter_: str = "all", page: int = 1, page_size: int = 20) -> dict:
    """ETF 全景（FR6-04）：全部（分页）/ 上涨TOP20 / 下跌TOP20。"""
    codes = [c for c, *_ in ETF_CATALOG]
    quotes = market.get_quotes(codes)
    names = {r["code"]: r["name"] for r in query(
        f"SELECT code,name FROM stock_list WHERE code IN ({','.join('?' * len(codes))})", tuple(codes))}
    items = []
    for code, category, track in ETF_CATALOG:
        q = quotes.get(code) or {}
        items.append({
            "code": code, "category": category, "track": track,
            "name": q.get("name") or names.get(code) or code,
            "price": q.get("price"), "pct": q.get("pct"),
            "amount": q.get("amount"), "source": q.get("source"),
        })
    if filter_ == "top_up":
        items = sorted([i for i in items if i["pct"] is not None], key=lambda x: -x["pct"])[:20]
        return {"items": items, "total": len(items), "page": 1, "pages": 1, "filter": filter_}
    if filter_ == "top_down":
        items = sorted([i for i in items if i["pct"] is not None], key=lambda x: x["pct"])[:20]
        return {"items": items, "total": len(items), "page": 1, "pages": 1, "filter": filter_}
    total = len(items)
    page_size = page_size if page_size in (20, 50) else 20
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, pages))
    return {"items": items[(page - 1) * page_size: page * page_size],
            "total": total, "page": page, "pages": pages, "page_size": page_size, "filter": "all"}
