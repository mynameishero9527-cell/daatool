"""板块资金流全景（FR3-01）与个股画像（FR3-04）。

板块资金全部由本地快照按 行业/概念 维度聚合计算，treemap 数据结构：
方块大小=成交额，颜色值=主力净流入率。
"""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from ..cache import cached
from ..database import executemany, query
from ..datasources import tencent
from . import metrics as metrics_svc
from . import rating

# 泛化标签型概念（不具题材意义），同步时剔除
_CONCEPT_BLACKLIST = ("融资融券", "转融券", "深股通", "沪股通", "富时罗素", "MSCI",
                      "标普", "样本股", "证金持股", "GDR", "同花顺", "昨日")

CONCEPT_TOP_N = 150


def sync_concepts() -> int:
    """同步活跃概念板块（按成交额前 N，剔除黑名单）及成分股映射。"""
    boards: list[dict] = []
    offset = 0
    while len(boards) < CONCEPT_TOP_N * 2 and offset < 400:
        page = tencent.fetch_concept_boards(offset=offset, count=100)
        if not page:
            break
        boards.extend(page)
        if len(page) < 100:
            break
        offset += 100
    boards = [b for b in boards
              if b["name"] and not any(k in b["name"] for k in _CONCEPT_BLACKLIST)][:CONCEPT_TOP_N]

    now = datetime.now().isoformat(timespec="seconds")
    executemany(
        "INSERT OR REPLACE INTO concept_board(concept,pct,turnover,updated_at) VALUES(?,?,?,?)",
        [(b["name"], b["pct"], b["turnover"], now) for b in boards])

    pairs = []
    for b in boards:
        page_offset = 0
        while True:
            try:
                codes = tencent.fetch_board_stocks(b["board_code"], offset=page_offset)
            except Exception:  # noqa: BLE001
                break
            pairs.extend((b["name"], c) for c in codes if c)
            if len(codes) < 200:
                break
            page_offset += 200
        time.sleep(0.05)
    if pairs:
        # 全量重建映射，避免残留过期概念
        from ..database import execute
        execute("DELETE FROM concept_map")
        executemany("INSERT OR IGNORE INTO concept_map(concept,code) VALUES(?,?)", pairs)
    return len(boards)


def _flow_sql(group_expr: str, join: str, having: str = "") -> str:
    return f"""
        SELECT {group_expr} AS name,
               COUNT(*) AS stocks,
               SUM(CASE WHEN s.pct > 0 THEN 1 ELSE 0 END) AS up,
               SUM(CASE WHEN s.pct < 0 THEN 1 ELSE 0 END) AS down,
               ROUND(SUM(s.amount) / 10000, 1) AS amount_yi,
               ROUND(SUM(s.main_net_in) / 10000, 2) AS net_in_yi,
               ROUND(AVG(s.pct), 2) AS pct
        FROM stock_snapshot s {join}
        WHERE s.amount IS NOT NULL AND {group_expr} != ''
        GROUP BY {group_expr} {having}
        ORDER BY SUM(s.amount) DESC"""


def get_flow(dim: str = "industry") -> dict:
    dim = dim if dim in ("industry", "concept") else "industry"

    def loader():
        if dim == "industry":
            rows = query(_flow_sql("l.industry", "JOIN stock_list l ON l.code = s.code"))
        else:
            rows = query(_flow_sql("c.concept", "JOIN concept_map c ON c.code = s.code",
                                   "HAVING COUNT(*) >= 5") + " LIMIT 80")
        items = []
        for r in rows:
            amount = r["amount_yi"] or 0
            net_in = r["net_in_yi"] or 0
            ratio = round(net_in / amount * 100, 2) if amount else 0  # 净流入率 %
            leader = query(
                f"""SELECT s.code, s.name, s.pct FROM stock_snapshot s
                    {"JOIN stock_list l ON l.code=s.code AND l.industry=?" if dim == "industry"
                     else "JOIN concept_map c ON c.code=s.code AND c.concept=?"}
                    ORDER BY s.pct DESC LIMIT 1""", (r["name"],))
            items.append({
                **r, "net_in_ratio": ratio,
                "leader": leader[0] if leader else None,
            })
        return {"dim": dim, "items": items,
                "updated_at": datetime.now().strftime("%H:%M:%S")}

    return cached(f"sector:flow:{dim}", 60, loader)


def get_sector_stocks(dim: str, name: str, limit: int = 30) -> list[dict]:
    join = ("JOIN stock_list l ON l.code = s.code AND l.industry = ?"
            if dim == "industry" else
            "JOIN concept_map c ON c.code = s.code AND c.concept = ?")
    rows = query(
        f"""SELECT s.code, s.name, s.price, s.pct, s.main_net_in, s.volume_ratio,
                   s.turnover_rate, m.buy_index, m.sentiment, m.dark_power
            FROM stock_snapshot s {join}
            LEFT JOIN stock_metrics m ON m.code = s.code
            WHERE s.price IS NOT NULL
            ORDER BY s.main_net_in DESC LIMIT ?""", (name, limit))
    for r in rows:
        if r["sentiment"] is not None:
            r["sent_level"] = metrics_svc.sentiment_level(r["sentiment"])[0]
    from . import finance as finance_svc
    finance_svc.attach_grades(rows)
    return rows


# ---------------- 板块推荐（FR8-06-1） ----------------

def get_sector_recommend(top_n: int = 10, stocks_per: int = 5) -> list[dict]:
    """强势板块 TOP10 + 每板块推荐个股 TOP5。"""
    def loader():
        from . import rating as rating_svc
        sectors = query(
            """SELECT l.industry AS name, ROUND(AVG(s.pct),2) AS pct,
                      ROUND(AVG(s.pct_d5),2) AS d5,
                      ROUND(SUM(s.main_net_in)/10000,1) AS net_in_yi,
                      COUNT(*) AS n
               FROM stock_snapshot s JOIN stock_list l ON l.code=s.code
               WHERE l.industry != '' AND s.pct IS NOT NULL
               GROUP BY l.industry""")
        for r in sectors:
            r["hot_score"] = round((r["pct"] or 0) * 3 + (r["d5"] or 0) * 1.5
                                   + min(max((r["net_in_yi"] or 0), -20), 20), 1)
        sectors.sort(key=lambda r: -r["hot_score"])
        out = []
        for sec in sectors[:top_n]:
            stocks = query(
                """SELECT s.code, s.name, s.price, s.pct, s.pct_d5, s.pct_d20, s.pct_d60,
                          s.main_net_in, s.volume_ratio, s.float_mv,
                          m.buy_index, m.sentiment
                   FROM stock_snapshot s
                   JOIN stock_list l ON l.code = s.code AND l.industry = ?
                   LEFT JOIN stock_metrics m ON m.code = s.code
                   WHERE s.price IS NOT NULL AND s.name NOT LIKE '%ST%'
                   ORDER BY (COALESCE(s.pct_d5,0) * 1.5 + COALESCE(s.main_net_in,0) / 5000.0
                             + (COALESCE(s.volume_ratio,1) - 1) * 8) DESC LIMIT ?""",
                (sec["name"], stocks_per))
            for st in stocks:
                st["score"], st["advice"] = rating_svc.quick_score(st)
                st["industry"] = sec["name"]
            from . import finance as finance_svc
            from . import wuxing
            wuxing.tags_for_list(stocks)
            finance_svc.attach_grades(stocks)
            out.append({**sec, "stocks": stocks})
        return out
    return cached("sector:recommend", 120, loader)


# ---------------- 板块周期阶段与季节性常识（FR7-05-3/4） ----------------

SEASONAL_KNOWLEDGE = {
    "食品饮料": "Q3中秋备货、Q4春节旺季为传统强势窗口；白酒动销数据是关键跟踪点",
    "煤炭": "冬储（11月-次年1月）与夏季用电高峰（7-8月）双旺季",
    "公用事业": "夏冬用电用气高峰受益；电价气价政策为催化",
    "家用电器": "夏季空调旺季（5-7月）+ 双11大促备货（9-10月）",
    "农林牧渔": "春耕（2-3月种业）、生猪周期独立于季节，关注能繁母猪存栏拐点",
    "建筑装饰": "春季开工季（3-4月）+ 年末赶工，基建订单集中披露期强势",
    "建筑材料": "随开工季节奏，水泥价格3-5月、9-11月双旺季",
    "社会服务": "暑期（7-8月）与法定长假（五一/十一）前为旅游酒店传统布局窗口",
    "传媒": "春节档/暑期档电影季，寒暑假游戏流水高峰",
    "纺织服饰": "换季备货（3月/9月）与出口订单季",
    "医药生物": "冬季流感季（11-1月）呼吸道用药需求上升；集采落地为压制因素",
    "国防军工": "年末订单确认季（Q4）与重大阅兵/航展催化",
    "电力设备": "夏季用电高峰前电网招标（4-6月）；光伏装机年末抢装",
    "汽车": "金九银十传统旺季；年末新能源抢装冲量",
    "银行": "年报分红季（4-6月）高股息配置窗口",
    "非银金融": "牛市初期弹性最大（成交额放大直接受益）",
    "房地产": "金三银四、金九银十销售季；政策宽松窗口敏感",
    "钢铁": "随基建地产开工季（3-5月、9-11月）",
    "有色金属": "跟随全球商品周期与美元流动性，季节性弱、事件性强",
    "石油石化": "冬季取暖油需求与OPEC会议节奏",
    "电子": "下半年消费电子新品季（9-11月苹果/华为发布带动果链）",
    "计算机": "年末订单验收季（Q4收入确认）；两会前政策主题活跃",
    "通信": "运营商资本开支披露期（3-4月）与新技术大会催化",
}


def get_sector_cycles() -> list[dict]:
    """全行业周期阶段：按多周期动量与位置判定 + 季节性常识。"""
    def loader():
        rows = query(
            """SELECT l.industry AS name, COUNT(*) AS n,
                      ROUND(AVG(s.pct), 2) AS pct,
                      ROUND(AVG(s.pct_d5), 2) AS d5,
                      ROUND(AVG(s.pct_d20), 2) AS d20,
                      ROUND(AVG(s.pct_d60), 2) AS d60,
                      ROUND(AVG(m.pos60), 3) AS pos60,
                      ROUND(SUM(s.main_net_in) / 10000, 1) AS net_in_yi
               FROM stock_snapshot s
               JOIN stock_list l ON l.code = s.code
               LEFT JOIN stock_metrics m ON m.code = s.code
               WHERE l.industry != '' AND s.pct IS NOT NULL
               GROUP BY l.industry ORDER BY d20 DESC""")
        out = []
        for r in rows:
            d20, d60, pos = r["d20"] or 0, r["d60"] or 0, r["pos60"] or 0.5
            d5 = r["d5"] or 0
            if d20 > 8 and d5 > 0:
                stage, css = "上升期", "level-4"
                desc = "多周期动量向上，资金关注度高"
            elif pos > 0.65 and d5 <= 0:
                stage, css = "高位滞涨", "level-3"
                desc = "涨幅居前但短线转弱，注意兑现节奏"
            elif d20 < -8 and d5 < 0:
                stage, css = "下降期", "level-1"
                desc = "调整趋势未止，等待企稳信号"
            elif pos < 0.35 and abs(d5) < 3:
                stage, css = "底部盘整", "level-2"
                desc = "低位缩量整理，可跟踪资金回流"
            else:
                stage, css = "震荡期", "level-2"
                desc = "方向未明，跟随大盘节奏"
            out.append({
                **r, "stage": stage, "stage_css": css, "stage_desc": desc,
                "seasonal": SEASONAL_KNOWLEDGE.get(r["name"], "季节性规律不显著，以事件与资金驱动为主"),
            })
        return out
    return cached("sector:cycles", 300, loader)


# ---------------- 个股画像（FR3-04） ----------------

def get_profile(code: str) -> dict:
    info = query("SELECT l.name, l.industry, l.board, s.float_mv, s.volume_ratio "
                 "FROM stock_list l LEFT JOIN stock_snapshot s ON s.code=l.code WHERE l.code=?",
                 (code,))
    if not info:
        return {"code": code, "industry": "", "concepts": [], "desc": "暂无画像数据（请先全量同步）"}
    r = info[0]
    concepts = [row["concept"] for row in query(
        """SELECT cm.concept FROM concept_map cm
           JOIN concept_board cb ON cb.concept = cm.concept
           WHERE cm.code=? ORDER BY cb.turnover DESC LIMIT 8""", (code,))]
    float_mv = r["float_mv"] or 0
    size = "大盘" if float_mv >= 500 else "中盘" if float_mv >= 100 else "小盘"
    vol_desc = rating.volume_desc(r["volume_ratio"])
    concept_txt = "、".join(concepts[:3]) if concepts else "暂无热门题材"
    desc = (f"{r['name']} 属于{r['industry'] or '未分类'}行业（{r['board']}），"
            f"涉及 {concept_txt} 等题材，流通市值 {float_mv:.0f} 亿（{size}股），当前{vol_desc}。")
    return {"code": code, "name": r["name"], "industry": r["industry"], "board": r["board"],
            "concepts": concepts, "size": size, "float_mv": float_mv,
            "volume_desc": vol_desc, "desc": desc}


# ---------------- 板块资金日频 + 横向直方图（FR10-03） ----------------

_FLOW_TZ = ZoneInfo("Asia/Shanghai")
RANGE_DAYS = {"1d": 1, "3d": 3, "5d": 5, "10d": 10, "20d": 20, "1m": 20, "3m": 60}
RANGE_LABELS = {
    "1d": "当天", "3d": "过去3天", "5d": "过去5天", "10d": "过去10天",
    "20d": "过去20天", "1m": "过去一月", "3m": "过去3个月",
}


def beijing_trade_date() -> str:
    return datetime.now(_FLOW_TZ).strftime("%Y-%m-%d")


def record_daily_flow() -> dict:
    """快照成功后 upsert 当日行业/概念资金。单位：万元。"""
    trade_date = beijing_trade_date()
    industry_rows = query(
        """SELECT l.industry AS name, COUNT(*) AS stocks,
                  SUM(s.main_net_in) AS net_in, SUM(s.amount) AS amount
           FROM stock_snapshot s JOIN stock_list l ON l.code = s.code
           WHERE s.amount IS NOT NULL AND l.industry != ''
           GROUP BY l.industry""")
    concept_rows = query(
        """SELECT c.concept AS name, COUNT(*) AS stocks,
                  SUM(s.main_net_in) AS net_in, SUM(s.amount) AS amount
           FROM stock_snapshot s JOIN concept_map c ON c.code = s.code
           WHERE s.amount IS NOT NULL AND c.concept != ''
           GROUP BY c.concept HAVING COUNT(*) >= 5""")
    pairs = [("industry", r) for r in industry_rows] + [("concept", r) for r in concept_rows]
    if pairs:
        executemany(
            """INSERT INTO sector_flow_daily(dim,name,trade_date,net_in,amount,stocks)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(dim,name,trade_date) DO UPDATE SET
                 net_in=excluded.net_in, amount=excluded.amount, stocks=excluded.stocks""",
            [(dim, r["name"], trade_date, r["net_in"] or 0, r["amount"] or 0, r["stocks"] or 0)
             for dim, r in pairs])
    return {"date": trade_date, "rows": len(pairs)}


def flow_history_stats() -> dict:
    rows = query(
        "SELECT dim, COUNT(DISTINCT trade_date) AS days, MIN(trade_date) AS first_d, "
        "MAX(trade_date) AS last_d FROM sector_flow_daily GROUP BY dim")
    by_dim = {r["dim"]: r for r in rows}
    return {
        "industry_days": (by_dim.get("industry") or {}).get("days", 0),
        "concept_days": (by_dim.get("concept") or {}).get("days", 0),
        "last_date": max((r["last_d"] for r in rows if r.get("last_d")), default=""),
        "first_date": min((r["first_d"] for r in rows if r.get("first_d")), default=""),
    }


def _flow_from_snapshot(dim: str, field: str) -> list[dict]:
    if field not in ("main_net_in", "main_net_in_d5"):
        field = "main_net_in"
    if dim == "industry":
        return query(
            f"""SELECT l.industry AS name, COUNT(*) AS stocks,
                       ROUND(SUM(s.{field}) / 10000.0, 2) AS net_in_yi,
                       ROUND(SUM(s.amount) / 10000.0, 1) AS amount_yi
                FROM stock_snapshot s JOIN stock_list l ON l.code = s.code
                WHERE s.amount IS NOT NULL AND l.industry != ''
                GROUP BY l.industry""")
    return query(
        f"""SELECT c.concept AS name, COUNT(*) AS stocks,
                   ROUND(SUM(s.{field}) / 10000.0, 2) AS net_in_yi,
                   ROUND(SUM(s.amount) / 10000.0, 1) AS amount_yi
            FROM stock_snapshot s JOIN concept_map c ON c.code = s.code
            WHERE s.amount IS NOT NULL AND c.concept != ''
            GROUP BY c.concept HAVING COUNT(*) >= 5""")


def _flow_from_daily(dim: str, target_days: int) -> tuple[list[dict], dict, str]:
    dates = query(
        "SELECT DISTINCT trade_date FROM sector_flow_daily WHERE dim=? "
        "ORDER BY trade_date DESC LIMIT ?", (dim, target_days))
    have = len(dates)
    if not have:
        return [], {"have": 0, "target": target_days, "dates": []}, \
            "尚无板块资金日频记录。当天/近5日可用快照；更长区间需快照同步后逐日落库，不会用涨跌幅填补。"
    date_list = [d["trade_date"] for d in dates]
    ph = ",".join("?" * len(date_list))
    rows = query(
        f"""SELECT name, SUM(net_in) AS net_in, SUM(amount) AS amount,
                   MAX(stocks) AS stocks, COUNT(DISTINCT trade_date) AS days
            FROM sector_flow_daily
            WHERE dim=? AND trade_date IN ({ph})
            GROUP BY name""",
        (dim, *date_list))
    items = [{
        "name": r["name"],
        "net_in_yi": round((r["net_in"] or 0) / 10000.0, 2),
        "amount_yi": round((r["amount"] or 0) / 10000.0, 1),
        "stocks": r["stocks"] or 0,
        "days": r["days"],
    } for r in rows]
    if have < target_days:
        note = (f"已累计 {have} 个交易日（目标 {target_days}），早期日期待落库。"
                "未用涨跌幅冒充资金流入。")
    else:
        note = f"已按近 {have} 个交易日板块资金合计（交易日）"
    return items, {"have": have, "target": target_days, "dates": date_list}, note


def get_flow_bar(dim: str = "industry", range_key: str = "1d",
                 sort: str = "inflow") -> dict:
    dim = dim if dim in ("industry", "concept") else "industry"
    range_key = range_key if range_key in RANGE_DAYS else "1d"
    sort = sort if sort in ("inflow", "outflow", "abs") else "inflow"
    target = RANGE_DAYS[range_key]
    try:
        today = beijing_trade_date()
        n = query("SELECT COUNT(*) AS n FROM sector_flow_daily WHERE trade_date=?", (today,))[0]["n"]
        if n == 0 and query("SELECT COUNT(*) AS n FROM stock_snapshot")[0]["n"]:
            record_daily_flow()
    except Exception:  # noqa: BLE001
        pass

    if range_key == "1d":
        items = _flow_from_snapshot(dim, "main_net_in")
        coverage = {"have": 1, "target": 1, "dates": [beijing_trade_date()]}
        note = "当天主力净流入，与资金全景当日口径一致"
    elif range_key == "5d":
        items = _flow_from_snapshot(dim, "main_net_in_d5")
        coverage = {"have": 5, "target": 5, "dates": []}
        note = "近5日主力净流入取自行情快照累计字段（交易日）"
    else:
        items, coverage, note = _flow_from_daily(dim, target)

    if sort == "outflow":
        items = sorted(items, key=lambda x: (x.get("net_in_yi") or 0))
    elif sort == "abs":
        items = sorted(items, key=lambda x: -abs(x.get("net_in_yi") or 0))
    else:
        items = sorted(items, key=lambda x: -(x.get("net_in_yi") or 0))

    return {
        "dim": dim, "range": range_key, "range_label": RANGE_LABELS[range_key],
        "target_days": target, "items": items, "coverage": coverage, "note": note,
        "sort": sort, "ranges": RANGE_LABELS,
    }


def get_flow_bar_stocks(dim: str, name: str, range_key: str = "1d",
                        limit: int = 50) -> dict:
    dim = dim if dim in ("industry", "concept") else "industry"
    range_key = range_key if range_key in RANGE_DAYS else "1d"
    limit = max(10, min(int(limit or 50), 200))
    join = ("JOIN stock_list l ON l.code = s.code AND l.industry = ?"
            if dim == "industry" else
            "JOIN concept_map c ON c.code = s.code AND c.concept = ?")
    if range_key == "5d":
        flow_col, stock_note = "s.main_net_in_d5", "个股贡献为近5日主力净流入"
    elif range_key == "1d":
        flow_col, stock_note = "s.main_net_in", "个股贡献为当日主力净流入"
    else:
        flow_col, stock_note = (
            "s.main_net_in",
            "个股区间资金仅当天/5日可精确，更长区间暂显示当日主力净流入",
        )
    rows = query(
        f"""SELECT s.code, s.name, s.price, s.pct, s.main_net_in, s.main_net_in_d5,
                   {flow_col} AS contrib, m.buy_index, m.sentiment, m.dark_power
            FROM stock_snapshot s {join}
            LEFT JOIN stock_metrics m ON m.code = s.code
            WHERE s.price IS NOT NULL
            ORDER BY contrib DESC NULLS LAST LIMIT ?""", (name, limit))
    for r in rows:
        if r.get("sentiment") is not None:
            r["sent_level"] = metrics_svc.sentiment_level(r["sentiment"])[0]
        if r.get("buy_index") is not None:
            r["buy_level"] = metrics_svc.buy_index_level(r["buy_index"])[0]
    from . import finance as finance_svc
    from . import wuxing
    wuxing.tags_for_list(rows)
    finance_svc.attach_grades(rows)
    return {"dim": dim, "name": name, "range": range_key, "items": rows,
            "note": stock_note, "total": len(rows)}
