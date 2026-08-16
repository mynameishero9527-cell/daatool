"""板块资金流全景（FR3-01）与个股画像（FR3-04）。

板块资金全部由本地快照按 行业/概念 维度聚合计算，treemap 数据结构：
方块大小=成交额，颜色值=主力净流入率。
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

from ..cache import cached
from ..database import execute, executemany, get_meta, query, set_meta
from ..datasources import eastmoney, sina, tencent
from . import market as market_svc
from . import metrics as metrics_svc
from . import rating

log = logging.getLogger("sector")

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
                   s.turnover_rate, s.main_in, s.main_out, s.amount,
                   m.buy_index, m.sentiment, m.dark_power
            FROM stock_snapshot s {join}
            LEFT JOIN stock_metrics m ON m.code = s.code
            WHERE s.price IS NOT NULL
            ORDER BY s.main_net_in DESC LIMIT ?""", (name, limit))
    for r in rows:
        if r["sentiment"] is not None:
            r["sent_level"] = metrics_svc.sentiment_level(r["sentiment"])[0]
    from . import finance as finance_svc
    finance_svc.attach_grades(rows)
    metrics_svc.attach_flow_list(rows)
    return rows


# ---------------- 板块推荐（FR8-06-1） ----------------

def industry_heat_map() -> dict:
    """行业热度：当日涨跌×3 + 5日×1.5 + 主力净流入（亿元，截断±20）。"""
    rows = query(
        """SELECT l.industry AS name,
                  ROUND(AVG(s.pct), 2) AS pct,
                  ROUND(AVG(s.pct_d5), 2) AS d5,
                  ROUND(SUM(s.main_net_in) / 10000.0, 1) AS net_in_yi
           FROM stock_snapshot s JOIN stock_list l ON l.code = s.code
           WHERE l.industry != '' AND s.pct IS NOT NULL
           GROUP BY l.industry"""
    )
    out = {}
    for r in rows:
        out[r["name"]] = round(
            (r["pct"] or 0) * 3 + (r["d5"] or 0) * 1.5
            + min(max((r["net_in_yi"] or 0), -20), 20), 1)
    return out


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
                          s.main_in, s.main_out, s.amount,
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
            metrics_svc.attach_flow_list(stocks)
            out.append({**sec, "stocks": stocks})
        return out
    return cached("sector:recommend:v2", 120, loader)


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


# 12个月板块季节性强弱：由 SEASONAL_KNOWLEDGE 归纳，标注常识，不是官方政策。
MONTH_STRONG = {
    1: [("煤炭", "冬储与供暖旺季"), ("公用事业", "冬季用电用气高峰"), ("医药生物", "流感季用药需求"),
        ("石油石化", "取暖油需求"), ("传媒", "春节档影视前瞻")],
    2: [("食品饮料", "春节消费旺季"), ("农林牧渔", "春耕种业窗口"), ("传媒", "春节档"),
        ("社会服务", "春节出行"), ("计算机", "两会前政策主题活跃")],
    3: [("农林牧渔", "春耕启动"), ("建筑装饰", "春季开工季"), ("建筑材料", "水泥价格旺季起点"),
        ("房地产", "金三银四销售季"), ("通信", "运营商资本开支披露期"), ("钢铁", "基建开工带动")],
    4: [("银行", "年报分红配置窗口"), ("电力设备", "夏季用电前电网招标"), ("建筑装饰", "开工季延续"),
        ("社会服务", "五一长假前瞻"), ("通信", "资本开支披露"), ("家用电器", "空调旺季备货")],
    5: [("家用电器", "空调销售旺季"), ("公用事业", "夏季用电预热"), ("社会服务", "五一消费"),
        ("电力设备", "电网招标"), ("建筑材料", "开工旺季")],
    6: [("家用电器", "空调旺季"), ("公用事业", "迎峰度夏"), ("银行", "分红季"),
        ("电力设备", "招标与装机"), ("食品饮料", "啤酒饮料消费")],
    7: [("煤炭", "夏季用电高峰"), ("公用事业", "迎峰度夏"), ("社会服务", "暑期旅游"),
        ("传媒", "暑期档电影与游戏"), ("家用电器", "空调旺季收官")],
    8: [("煤炭", "夏季用电"), ("社会服务", "暑期旅游"), ("传媒", "暑期档"),
        ("食品饮料", "中秋备货前瞻"), ("公用事业", "高温用电")],
    9: [("汽车", "金九银十"), ("食品饮料", "中秋备货旺季"), ("电子", "消费电子新品季"),
        ("房地产", "金九银十销售季"), ("纺织服饰", "换季备货"), ("建筑材料", "秋季开工")],
    10: [("汽车", "银十旺季"), ("电子", "新品与双11备货"), ("社会服务", "十一黄金周"),
         ("国防军工", "年末订单确认季启动"), ("钢铁", "秋季开工"), ("家用电器", "双11大促备货")],
    11: [("煤炭", "冬储启动"), ("医药生物", "流感季"), ("电子", "消费电子旺季"),
         ("国防军工", "航展与订单"), ("公用事业", "供暖季"), ("石油石化", "取暖油")],
    12: [("煤炭", "冬储延续"), ("国防军工", "年末订单确认"), ("计算机", "年末订单验收"),
         ("电力设备", "光伏年末抢装"), ("汽车", "新能源年末冲量"), ("食品饮料", "春节备货启动")],
}
MONTH_WEAK = {
    1: [("家用电器", "空调淡季"), ("建筑装饰", "开工季未到"), ("社会服务", "暑期旅游淡季")],
    2: [("煤炭", "取暖尾声将至"), ("建筑材料", "开工尚未放量"), ("汽车", "金九银十未到")],
    3: [("煤炭", "供暖季结束"), ("医药生物", "流感季回落"), ("社会服务", "春节出行结束")],
    4: [("煤炭", "淡季"), ("石油石化", "取暖需求回落"), ("传媒", "春节档结束")],
    5: [("食品饮料", "春节动销结束"), ("煤炭", "传统淡季"), ("房地产", "金三银四尾声")],
    6: [("房地产", "销售淡季"), ("建筑装饰", "开工空档"), ("医药生物", "流感季已过")],
    7: [("食品饮料", "中秋备货尚未"), ("房地产", "销售淡季"), ("纺织服饰", "换季未到")],
    8: [("汽车", "金九尚未"), ("电子", "新品季未到"), ("银行", "分红季结束")],
    9: [("社会服务", "暑期旅游结束"), ("煤炭", "夏季用电回落"), ("家用电器", "空调旺季结束")],
    10: [("公用事业", "夏季用电结束"), ("传媒", "暑期档结束"), ("农林牧渔", "春耕已过")],
    11: [("家用电器", "空调淡季"), ("社会服务", "十一假期结束"), ("建筑装饰", "开工放缓")],
    12: [("社会服务", "旅游淡季"), ("家用电器", "空调淡季"), ("纺织服饰", "换季结束")],
}


def get_month_board_cycles() -> dict:
    """12个月板块季节性强弱 + 当月行业动量。历史月份不用涨跌幅编造。"""
    def loader():
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        cur = now.month
        live_up, live_down = [], []
        try:
            for r in get_sector_cycles():
                name = r.get("name") or ""
                if not name:
                    continue
                item = {"name": name, "stage": r.get("stage"), "d20": r.get("d20"), "d5": r.get("d5")}
                if r.get("stage") == "上升期":
                    live_up.append(item)
                elif r.get("stage") == "下降期":
                    live_down.append(item)
        except Exception:  # noqa: BLE001
            live_up, live_down = [], []
        months = []
        for m in range(1, 13):
            rec = {
                "month": m,
                "label": f"{m}月",
                "is_current": m == cur,
                "strong": [{"name": n, "why": w} for n, w in MONTH_STRONG.get(m, ())],
                "weak": [{"name": n, "why": w} for n, w in MONTH_WEAK.get(m, ())],
                "live_strong": live_up[:10] if m == cur else [],
                "live_weak": live_down[:10] if m == cur else [],
            }
            months.append(rec)
        return {
            "current_month": cur,
            "asof": now.strftime("%Y-%m-%d"),
            "note": "季节性板块周期为交易常识归纳，不是官方政策或预测保证。"
                    "当月「当前动量」来自行业5/20/60日均涨幅与位置，不用单日涨跌幅编造其余月份。",
            "months": months,
        }
    return cached("sector:month-cycles", 300, loader)


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


# ---------------- 板块资金日频 + 横向直方图 + 日走势（FR10-03 / FR11-03） ----------------

_FLOW_TZ = ZoneInfo("Asia/Shanghai")
RANGE_DAYS = {"1d": 1, "3d": 3, "5d": 5, "10d": 10, "20d": 20, "1m": 20, "3m": 60}
RANGE_LABELS = {
    "1d": "当天", "3d": "过去3天", "5d": "过去5天", "10d": "过去10天",
    "20d": "过去20天", "1m": "过去一月", "3m": "过去3个月",
}
# 日走势横轴粒度：与直方图「时间范围」独立。lookback 为最多取多少个交易日再重采样。
TREND_GRAINS = {
    "1d":  {"label": "日", "lookback": 60, "kind": "day", "n": 1},
    "5d":  {"label": "5日", "lookback": 60, "kind": "ndays", "n": 5},
    "1w":  {"label": "周", "lookback": 130, "kind": "week", "n": 5},
    "12d": {"label": "12日", "lookback": 120, "kind": "ndays", "n": 12},
    "20d": {"label": "20日", "lookback": 120, "kind": "ndays", "n": 20},
    "1m":  {"label": "月", "lookback": 250, "kind": "month", "n": 20},
}


def beijing_trade_date() -> str:
    return datetime.now(_FLOW_TZ).strftime("%Y-%m-%d")


def snapshot_asof_date() -> str:
    """账本日 = 快照 MAX(updated_at) 的日期，而不是日历今天。"""
    rows = query("SELECT MAX(updated_at) AS t FROM stock_snapshot")
    raw = ((rows[0]["t"] if rows else "") or "").strip()
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    return beijing_trade_date()


def _valid_day(day: str, asof: str) -> str:
    d = (day or "").strip()
    if len(d) == 10 and d[4] == "-" and d[7] == "-" and d <= asof:
        return d
    return ""


def record_daily_flow() -> dict:
    """按快照 asof 日 upsert 行业/概念资金（万元），并清掉晚于 asof 的错账。"""
    asof = snapshot_asof_date()
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
            [(dim, r["name"], asof, r["net_in"] or 0, r["amount"] or 0, r["stocks"] or 0)
             for dim, r in pairs])
    deleted = 0
    try:
        before = query("SELECT COUNT(*) AS n FROM sector_flow_daily WHERE trade_date > ?", (asof,))
        deleted = (before[0]["n"] if before else 0) or 0
        if deleted:
            execute("DELETE FROM sector_flow_daily WHERE trade_date > ?", (asof,))
    except Exception:  # noqa: BLE001
        deleted = 0
    return {"date": asof, "rows": len(pairs), "deleted_future": deleted}


def _ensure_ledger() -> str:
    asof = snapshot_asof_date()
    try:
        if query("SELECT COUNT(*) AS n FROM stock_snapshot")[0]["n"]:
            record_daily_flow()
            asof = snapshot_asof_date()
    except Exception:  # noqa: BLE001
        pass
    return asof


def flow_history_stats() -> dict:
    rows = query(
        "SELECT dim, COUNT(DISTINCT trade_date) AS days, MIN(trade_date) AS first_d, "
        "MAX(trade_date) AS last_d FROM sector_flow_daily GROUP BY dim")
    by_dim = {r["dim"]: r for r in rows}
    lasts = [r["last_d"] for r in rows if r.get("last_d")]
    firsts = [r["first_d"] for r in rows if r.get("first_d")]
    return {
        "industry_days": (by_dim.get("industry") or {}).get("days", 0),
        "concept_days": (by_dim.get("concept") or {}).get("days", 0),
        "remote_hy_days": (by_dim.get("remote_hy") or {}).get("days", 0),
        "remote_gn_days": (by_dim.get("remote_gn") or {}).get("days", 0),
        "em_hy_days": (by_dim.get("em_hy") or {}).get("days", 0),
        "em_gn_days": (by_dim.get("em_gn") or {}).get("days", 0),
        "last_date": max(lasts, default=""),
        "first_date": min(firsts, default=""),
        "remote_last": max(
            ((by_dim.get("remote_hy") or {}).get("last_d") or "",
             (by_dim.get("remote_gn") or {}).get("last_d") or ""),
            default=""),
    }


# 新浪旧行业分类 → 本地申万一级，供点折线联动直方图
REMOTE_TO_SW = {
    "电子信息": "电子", "电子器件": "电子", "有色金属": "有色金属",
    "机械行业": "机械设备", "仪器仪表": "机械设备", "纺织机械": "机械设备",
    "食品行业": "食品饮料", "玻璃行业": "建筑材料", "水泥行业": "建筑材料",
    "建筑建材": "建筑材料", "钢铁行业": "钢铁", "煤炭行业": "煤炭",
    "房地产": "房地产", "汽车制造": "汽车", "摩托车": "汽车",
    "家电行业": "家用电器", "电器行业": "家用电器",
    "化工行业": "基础化工", "化纤行业": "基础化工", "农药化肥": "基础化工",
    "塑料制品": "基础化工", "环保行业": "环保", "石油行业": "石油石化",
    "综合行业": "综合", "纺织行业": "纺织服饰", "服装鞋类": "纺织服饰",
    "公路桥梁": "交通运输", "船舶制造": "交通运输", "飞机制造": "国防军工",
    "医疗器械": "医药生物", "酒店旅游": "社会服务",
    "供水供气": "公用事业", "发电设备": "电力设备",
    "造纸行业": "轻工制造", "家具行业": "轻工制造", "印刷包装": "轻工制造",
    "陶瓷行业": "轻工制造",
}


def pull_remote_flow() -> dict:
    """独立数据源：拉取新浪板块资金并写入 remote_hy / remote_gn，不覆盖本地成分股账本。"""
    asof = snapshot_asof_date()
    hy = sina.fetch_board_moneyflow(0, pages=1, num=80)
    gn = sina.fetch_board_moneyflow(1, pages=3, num=80)
    pairs = [("remote_hy", r) for r in hy] + [("remote_gn", r) for r in gn]
    if pairs:
        payload = [(dim, r["name"], asof, r["net_in"], r["amount"], 0, "sina", r.get("board_code") or "")
                   for dim, r in pairs]
        try:
            executemany(
                """INSERT INTO sector_flow_daily(dim,name,trade_date,net_in,amount,stocks,source,board_code)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(dim,name,trade_date) DO UPDATE SET
                     net_in=excluded.net_in, amount=excluded.amount,
                     source=excluded.source, board_code=excluded.board_code""",
                payload)
        except Exception:  # noqa: BLE001
            executemany(
                """INSERT INTO sector_flow_daily(dim,name,trade_date,net_in,amount,stocks)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(dim,name,trade_date) DO UPDATE SET
                     net_in=excluded.net_in, amount=excluded.amount""",
                [(dim, r["name"], asof, r["net_in"], r["amount"], 0) for dim, r in pairs])
    set_meta("sector_flow_remote_sync", datetime.now(_FLOW_TZ).isoformat(timespec="seconds"))
    return {"date": asof, "industry": len(hy), "concept": len(gn), "source": "sina", "rows": len(pairs)}


def _em_distinct_days(em_dim: str) -> int:
    return query("SELECT COUNT(DISTINCT trade_date) AS n FROM sector_flow_daily WHERE dim=?",
                 (em_dim,))[0]["n"]


def pull_em_flow_history(kind: str = "industry", lookback: int = 40,
                         force: bool = False) -> dict:
    """拉取东方财富板块资金日K写入 em_hy/em_gn。按真实主力净流入落库，不用涨跌幅填日。"""
    kind = "concept" if kind == "concept" else "industry"
    em_dim = "em_gn" if kind == "concept" else "em_hy"
    asof = snapshot_asof_date()
    have = _em_distinct_days(em_dim)
    today_n = query("SELECT COUNT(*) AS n FROM sector_flow_daily WHERE dim=? AND trade_date=?",
                    (em_dim, asof))[0]["n"]
    last_sync = get_meta("sector_flow_em_sync") or ""
    recent = False
    if last_sync:
        try:
            ts = datetime.fromisoformat(last_sync)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_FLOW_TZ)
            recent = (datetime.now(_FLOW_TZ) - ts).total_seconds() < 1800
        except Exception:  # noqa: BLE001
            recent = False
    if today_n > 0 and not force and (have >= 5 or recent):
        return {"skipped": True, "dim": em_dim, "days": have, "today": today_n, "rows": 0}
    boards = eastmoney.fetch_board_list(kind, pz=200)
    if kind == "industry":
        local = {r["industry"] for r in query(
            "SELECT DISTINCT industry FROM stock_list WHERE industry!=''")}
        matched = [b for b in boards if b["name"] in local]
        extra = [b for b in boards if b["name"] not in local][:8]
        boards = (matched + extra) if matched else boards[:36]
    else:
        local = {r["concept"] for r in query("SELECT DISTINCT concept FROM concept_map")}
        matched = [b for b in boards if b["name"] in local]
        boards = matched[:24] if matched else boards[:24]
    boards = boards[:40]
    lookback = max(10, min(int(lookback or 40), 80))
    today_payload = [
        (em_dim, b["name"], asof, b["net_in"], b.get("amount") or 0, 0, "eastmoney", b["board_code"])
        for b in boards
    ]

    def write(rows: list[tuple]) -> None:
        if not rows:
            return
        try:
            executemany(
                """INSERT INTO sector_flow_daily(dim,name,trade_date,net_in,amount,stocks,source,board_code)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(dim,name,trade_date) DO UPDATE SET
                     net_in=excluded.net_in, amount=COALESCE(excluded.amount, sector_flow_daily.amount),
                     source=excluded.source, board_code=excluded.board_code""",
                rows)
        except Exception:  # noqa: BLE001
            executemany(
                """INSERT INTO sector_flow_daily(dim,name,trade_date,net_in,amount,stocks)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(dim,name,trade_date) DO UPDATE SET net_in=excluded.net_in""",
                [(a, b, c, d, e, f) for a, b, c, d, e, f, *_ in rows])

    write(today_payload)
    payload: list[tuple] = []
    ok = fail = 0
    sample = []
    if boards:
        try:
            sample = eastmoney.fetch_board_fflow_history(boards[0]["board_code"], lookback)
        except Exception as exc:  # noqa: BLE001
            log.warning("板块资金日K探测失败: %s", exc)
    if len(sample) >= 2:
        def one(board: dict) -> tuple[dict, list]:
            return board, eastmoney.fetch_board_fflow_history(board["board_code"], lookback)

        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = [pool.submit(one, b) for b in boards]
            for fut in as_completed(futs):
                try:
                    board, hist = fut.result()
                    ok += 1
                    for p in hist:
                        payload.append((
                            em_dim, board["name"], p["trade_date"], p["net_in"], 0, 0,
                            "eastmoney", board["board_code"],
                        ))
                except Exception as exc:  # noqa: BLE001
                    fail += 1
                    log.warning("板块资金日K失败: %s", exc)
        write(payload)
    elif sample:
        ok = 1
        write([(em_dim, boards[0]["name"], p["trade_date"], p["net_in"], 0, 0,
                "eastmoney", boards[0]["board_code"]) for p in sample])
    days = _em_distinct_days(em_dim)
    set_meta("sector_flow_em_sync", datetime.now(_FLOW_TZ).isoformat(timespec="seconds"))
    return {"dim": em_dim, "boards": ok or len(boards), "fail": fail, "rows": len(today_payload) + len(payload),
            "days": days, "date": asof, "source": "eastmoney"}


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


def _flow_from_daily(dim: str, target_days: int, asof: str, day: str = "") -> tuple[list[dict], dict, str]:
    if day:
        rows = query(
            """SELECT name, net_in, amount, stocks FROM sector_flow_daily
               WHERE dim=? AND trade_date=?""", (dim, day))
        items = [{
            "name": r["name"],
            "net_in_yi": round((r["net_in"] or 0) / 10000.0, 2),
            "amount_yi": round((r["amount"] or 0) / 10000.0, 1),
            "stocks": r["stocks"] or 0,
            "days": 1,
        } for r in rows]
        have = 1 if items else 0
        note = (f"直方图为账本 {day} 当日横截面"
                if have else f"账本没有 {day} 的记录，未用涨跌幅填补")
        return items, {"have": have, "target": 1, "dates": [day] if have else []}, note

    dates = query(
        "SELECT DISTINCT trade_date FROM sector_flow_daily WHERE dim=? AND trade_date<=? "
        "ORDER BY trade_date DESC LIMIT ?", (dim, asof, target_days))
    have = len(dates)
    if not have:
        return [], {"have": 0, "target": target_days, "dates": []}, \
            "尚无板块资金日频记录。请先全量同步；不会用涨跌幅填补空白日。"
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
        note = (f"账本已覆盖 {have}/{target_days} 个交易日（{date_list[-1]}～{date_list[0]}），"
                "空白日不填 0、不用涨跌幅冒充资金。")
    else:
        note = f"直方图为近 {have} 个交易日账本合计（{date_list[-1]}～{date_list[0]}）"
    return items, {"have": have, "target": target_days, "dates": date_list}, note


def _attach_snap_ref(dim: str, items: list[dict]) -> None:
    """快照字段只作对照，不参与柱高。"""
    m1 = {r["name"]: r for r in _flow_from_snapshot(dim, "main_net_in")}
    m5 = {r["name"]: r for r in _flow_from_snapshot(dim, "main_net_in_d5")}
    for it in items:
        s1 = m1.get(it["name"]) or {}
        s5 = m5.get(it["name"]) or {}
        it["snap_d1_yi"] = s1.get("net_in_yi")
        it["snap_d5_yi"] = s5.get("net_in_yi")


def _reconcile(asof: str) -> dict:
    """行业合计：账本 asof 日 vs 最新快照，用于校验联动准确性。"""
    snap = query(
        """SELECT ROUND(SUM(s.main_net_in) / 10000.0, 2) AS yi
           FROM stock_snapshot s JOIN stock_list l ON l.code = s.code
           WHERE s.amount IS NOT NULL AND l.industry != ''""")
    led = query(
        """SELECT ROUND(SUM(net_in) / 10000.0, 2) AS yi
           FROM sector_flow_daily WHERE dim='industry' AND trade_date=?""", (asof,))
    snap_yi = (snap[0]["yi"] if snap else None)
    led_yi = (led[0]["yi"] if led else None)
    match = False
    if snap_yi is not None and led_yi is not None:
        match = abs((snap_yi or 0) - (led_yi or 0)) < 0.02
    return {
        "asof": asof,
        "calendar_today": beijing_trade_date(),
        "snap_industry_net_yi": snap_yi,
        "ledger_industry_net_yi": led_yi,
        "match": match,
        "note": "行业合计账本应等于最新快照主力净流入；概念因一股多概念会重复计入，不作对账。",
    }


def _sort_flow_items(items: list[dict], sort: str) -> list[dict]:
    if sort == "outflow":
        return sorted(items, key=lambda x: (x.get("net_in_yi") or 0))
    if sort == "abs":
        return sorted(items, key=lambda x: -abs(x.get("net_in_yi") or 0))
    return sorted(items, key=lambda x: -(x.get("net_in_yi") or 0))


def _apply_flow_filters(items: list[dict], direction: str, min_stocks: int, q: str) -> list[dict]:
    q = (q or "").strip().lower()
    out = []
    for it in items:
        v = it.get("net_in_yi") or 0
        if direction == "in" and v <= 0:
            continue
        if direction == "out" and v >= 0:
            continue
        if min_stocks and (it.get("stocks") or 0) < min_stocks:
            continue
        if q and q not in str(it.get("name") or "").lower():
            continue
        out.append(it)
    return out


def get_flow_bar(dim: str = "industry", range_key: str = "1d",
                 sort: str = "inflow", day: str = "",
                 direction: str = "", min_stocks: int = 0, q: str = "") -> dict:
    dim = dim if dim in ("industry", "concept") else "industry"
    range_key = range_key if range_key in RANGE_DAYS else "1d"
    sort = sort if sort in ("inflow", "outflow", "abs") else "inflow"
    direction = direction if direction in ("in", "out", "") else ""
    min_stocks = max(0, min(int(min_stocks or 0), 200))
    q = (q or "").strip()
    target = RANGE_DAYS[range_key]
    asof = _ensure_ledger()
    day = _valid_day(day, asof)

    items, coverage, note = _flow_from_daily(dim, target, asof, day)
    _attach_snap_ref(dim, items)
    value_source = "ledger"
    if not day and range_key == "5d":
        for it in items:
            it["ledger_net_in_yi"] = it.get("net_in_yi")
            if it.get("snap_d5_yi") is not None:
                it["net_in_yi"] = it["snap_d5_yi"]
        value_source = "snapshot_d5"
        note = ("直方图柱高已切到「近5日」快照累计主力净流入，与「当天」账本不是同一口径。"
                "下方日走势横轴独立，不跟直方图时间范围绑死。")
    elif not day and range_key == "1d":
        value_source = "ledger_1d"
    items = _sort_flow_items(items or [], sort)
    before = len(items)
    items = _apply_flow_filters(items, direction, min_stocks, q)
    rec = _reconcile(asof)
    if day:
        extra = f" · 当前查看 {day} 当日横截面"
    else:
        extra = f" · 账本日 {asof} · 范围 {RANGE_LABELS[range_key]}"
        if rec["calendar_today"] != asof:
            extra += f"（日历 {rec['calendar_today']}，以快照日为准）"
    if rec.get("match") and range_key == "1d":
        extra += " · 行业账本与快照对账一致"
    if before != len(items):
        extra += f" · 筛选后 {len(items)}/{before} 个板块"
    return {
        "dim": dim, "range": range_key, "range_label": RANGE_LABELS[range_key],
        "target_days": target, "items": items, "coverage": coverage,
        "note": note + extra, "sort": sort, "ranges": RANGE_LABELS,
        "count": len(items), "asof": asof, "day": day,
        "calendar_today": rec["calendar_today"], "reconcile": rec,
        "source": value_source, "value_source": value_source,
        "filters": {"dir": direction, "min_stocks": min_stocks, "q": q},
    }


def _short_md(d: str) -> str:
    return d[5:] if len(d) >= 10 else d


def _bucket_trade_dates(dates: list[str], grain: str) -> list[dict]:
    """把交易日列表收成横轴桶。缺日不补 0。"""
    spec = TREND_GRAINS[grain]
    kind, n = spec["kind"], spec["n"]
    dates = sorted(d for d in dates if d)
    if not dates:
        return []
    buckets: list[dict] = []

    def add(chunk: list[str], label: str) -> None:
        buckets.append({"label": label, "start": chunk[0], "end": chunk[-1], "days": chunk})

    if kind == "day":
        for d in dates:
            add([d], _short_md(d))
        return buckets
    if kind == "ndays":
        remainder = len(dates) % n
        i = 0
        if remainder:
            chunk = dates[:remainder]
            label = _short_md(chunk[-1]) if len(chunk) == 1 else f"{_short_md(chunk[0])}~{_short_md(chunk[-1])}"
            add(chunk, label)
            i = remainder
        while i < len(dates):
            chunk = dates[i:i + n]
            label = f"{_short_md(chunk[0])}~{_short_md(chunk[-1])}" if len(chunk) > 1 else _short_md(chunk[-1])
            add(chunk, label)
            i += n
        return buckets
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for d in dates:
        if kind == "week":
            dt = datetime.strptime(d, "%Y-%m-%d")
            iso = dt.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
        else:
            key = d[:7]
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(d)
    for key in order:
        add(groups[key], key)
    return buckets


def get_flow_trend(dim: str = "industry", name: str = "",
                   grain: str = "1d", top_n: int = 0,
                   direction: str = "", min_stocks: int = 0, q: str = "",
                   range_key: str = "1d") -> dict:
    """板块资金折线：横轴按 grain（日/5日/周/12日/20日/月），纵轴为该桶主力净流入合计（亿）。

    不跟直方图的「当天/过去N天」绑死；缺日不填 0、不用涨跌幅冒充资金。
    """
    dim = dim if dim in ("industry", "concept") else "industry"
    grain = grain if grain in TREND_GRAINS else "1d"
    name = (name or "").strip()
    direction = direction if direction in ("in", "out", "") else ""
    min_stocks = max(0, min(int(min_stocks or 0), 200))
    q = (q or "").strip().lower()
    spec = TREND_GRAINS[grain]
    default_n = 12 if dim == "industry" else 8
    top_n = max(3, min(int(top_n or default_n), 80))
    asof = _ensure_ledger()
    em_dim = "em_hy" if dim == "industry" else "em_gn"
    remote_dim = "remote_hy" if dim == "industry" else "remote_gn"
    em_days = _em_distinct_days(em_dim)
    if em_days < 2:
        try:
            pull_em_flow_history(dim, lookback=min(spec["lookback"], 40))
            em_days = _em_distinct_days(em_dim)
        except Exception as exc:  # noqa: BLE001
            log.warning("拉取板块资金日K失败: %s", exc)
    remote_n = query("SELECT COUNT(*) AS n FROM sector_flow_daily WHERE dim=?", (remote_dim,))[0]["n"]
    if not remote_n:
        try:
            pull_remote_flow()
            remote_n = query("SELECT COUNT(*) AS n FROM sector_flow_daily WHERE dim=?", (remote_dim,))[0]["n"]
        except Exception:  # noqa: BLE001
            remote_n = 0
    if em_days >= 1:
        use_dim, source_name = em_dim, "eastmoney"
    elif remote_n:
        use_dim, source_name = remote_dim, "sina"
    else:
        use_dim, source_name = dim, "local_ledger"
    try:
        market_svc.record_market_volume(asof)
    except Exception as exc:  # noqa: BLE001
        log.warning("大A量能落库失败: %s", exc)
    flow_dates = [d["trade_date"] for d in query(
        "SELECT DISTINCT trade_date FROM sector_flow_daily WHERE dim=? AND trade_date<=? "
        "ORDER BY trade_date DESC LIMIT ?", (use_dim, asof, spec["lookback"]))]
    flow_dates.reverse()
    cal_n = spec["lookback"] if len(flow_dates) >= 5 else min(spec["lookback"], 20)
    cal = [r["trade_date"] for r in query(
        "SELECT trade_date FROM market_volume_daily WHERE trade_date<=? "
        "ORDER BY trade_date DESC LIMIT ?", (asof, cal_n))]
    cal.reverse()
    date_list = cal if len(cal) >= 2 else flow_dates
    buckets = _bucket_trade_dates(date_list, grain)
    x_labels = [b["label"] for b in buckets]
    dim_label = "题材概念" if dim == "concept" else "行业板块"
    lines: list[dict] = []
    have = len(flow_dates)
    if date_list:
        ph = ",".join("?" * len(date_list))
        rows = query(
            f"""SELECT name, trade_date, net_in, stocks FROM sector_flow_daily
                WHERE dim=? AND trade_date IN ({ph})""",
            (use_dim, *date_list))
        by_name: dict[str, dict] = {}
        stocks_map: dict[str, int] = {}
        for r in rows:
            by_name.setdefault(r["name"], {})[r["trade_date"]] = round((r["net_in"] or 0) / 10000.0, 2)
            stocks_map[r["name"]] = max(stocks_map.get(r["name"], 0), r["stocks"] or 0)
        scored = []
        for nm, pts in by_name.items():
            vals = [pts[d] for d in date_list if d in pts]
            ssum = sum(vals)
            if direction == "in" and ssum <= 0:
                continue
            if direction == "out" and ssum >= 0:
                continue
            if min_stocks and not use_dim.startswith(("remote", "em")) and stocks_map.get(nm, 0) < min_stocks:
                continue
            if q and q not in nm.lower():
                continue
            scored.append((nm, ssum, abs(ssum)))
        scored.sort(key=lambda x: -x[2])
        picked = [x[0] for x in scored[:top_n]]
        if name and name not in picked:
            picked = [name] + picked[: top_n - 1]
        elif name:
            picked = [name] + [n for n in picked if n != name]
        for nm in picked:
            pts = by_name.get(nm) or {}
            data = []
            for b in buckets:
                vals = [pts[d] for d in b["days"] if d in pts]
                data.append(round(sum(vals), 2) if vals else None)
            ssum = round(sum(v for v in data if v is not None), 2)
            local_name = REMOTE_TO_SW.get(nm, nm) if use_dim.startswith("remote") else nm
            lines.append({"name": nm, "data": data, "sum_yi": ssum, "selected": nm == name,
                          "local_name": local_name or nm})
    vol_map: dict[str, dict] = {}
    if date_list:
        ph = ",".join("?" * len(date_list))
        for r in query(
            f"""SELECT trade_date, amount_yi, volume, pct FROM market_volume_daily
                WHERE trade_date IN ({ph})""", tuple(date_list)):
            vol_map[r["trade_date"]] = r
    vol_series, amt_series, vol_up = [], [], []
    for b in buckets:
        vols = [vol_map[d]["volume"] for d in b["days"]
                if d in vol_map and vol_map[d].get("volume") is not None]
        amts = [vol_map[d]["amount_yi"] for d in b["days"]
                if d in vol_map and vol_map[d].get("amount_yi") is not None]
        last = next((vol_map[d] for d in reversed(b["days"]) if d in vol_map), None)
        vol_series.append(round(sum(vols) / 1e8, 2) if vols else None)
        amt_series.append(round(sum(amts), 1) if amts else None)
        vol_up.append(bool(last and (last.get("pct") or 0) >= 0))
    latest_vol = query("SELECT * FROM market_volume_daily ORDER BY trade_date DESC LIMIT 5")
    latest = latest_vol[0] if latest_vol else {}
    vol5 = [r["volume"] for r in latest_vol if r.get("volume")]
    vol_ratio = None
    if latest.get("volume") and len(vol5) >= 2:
        avg = sum(vol5[1:]) / max(1, len(vol5) - 1)
        if avg:
            vol_ratio = round(latest["volume"] / avg, 2)
    kpis = {
        "sum_net_yi": round(sum(l["sum_yi"] for l in lines), 2) if lines else 0,
        "last_date": date_list[-1] if date_list else "",
        "days_have": have,
        "days_target": spec["lookback"],
        "line_count": len(lines),
        "bucket_count": len(buckets),
        "grain": grain,
        "grain_label": spec["label"],
        "market_amount_yi": latest.get("amount_yi"),
        "sh_amount_yi": latest.get("sh_amount_yi"),
        "sz_amount_yi": latest.get("sz_amount_yi"),
        "bj_amount_yi": latest.get("bj_amount_yi"),
        "market_volume_yi": round((latest.get("volume") or 0) / 1e8, 2) if latest.get("volume") else None,
        "vol_ratio": vol_ratio,
        "market_asof": latest.get("trade_date") or "",
    }
    y_desc = "当日主力净流入（亿）" if grain == "1d" else f"该{spec['label']}周期内主力净流入合计（亿）"
    src_map = {"eastmoney": "东方财富板块资金日K", "sina": "新浪板块资金独立源",
               "local_ledger": "本地成分股账本"}
    src_label = src_map.get(source_name, source_name)
    title = f"「{name}」及对照板块 · 横轴{spec['label']}" if name else f"{dim_label}资金走势 · 横轴{spec['label']}"
    notes = [
        f"走势数据源：{src_label}",
        f"上图=每个{dim_label}一条资金折线，下图=大A量能（中证全指成交量，亿手）",
        f"账本覆盖 {have} 个交易日（最多取近 {spec['lookback']} 日再按{spec['label']}重采样）",
        "缺日不填 0、不用涨跌幅冒充资金；点折线可选中该板块",
    ]
    if have < 2:
        notes.append("板块资金日K目前主要覆盖最新交易日，下图先用大A近20日量能铺时间轴；历史接口恢复后折线自动拉长")
    return {
        "dim": dim, "name": name, "title": title,
        "grain": grain, "grain_label": spec["label"],
        "grains": {k: v["label"] for k, v in TREND_GRAINS.items()},
        "range": range_key, "range_label": RANGE_LABELS.get(range_key, range_key),
        "asof": asof, "calendar_today": beijing_trade_date(),
        "coverage": {"have": have, "target": spec["lookback"], "dates": date_list},
        "dates": x_labels, "buckets": [{"label": b["label"], "start": b["start"], "end": b["end"],
                                       "days": len(b["days"])} for b in buckets],
        "lines": lines, "kpis": kpis, "y_name": y_desc,
        "market_vol": {"volume": vol_series, "amount": amt_series, "up": vol_up,
                       "unit": "亿手", "name": "大A量能"},
        "note": "。".join(notes) + "。",
        "source": source_name, "ledger_dim": use_dim,
    }


def get_flow_bar_stocks(dim: str, name: str, range_key: str = "1d",
                        limit: int = 50) -> dict:
    dim = dim if dim in ("industry", "concept") else "industry"
    range_key = range_key if range_key in RANGE_DAYS else "1d"
    limit = max(10, min(int(limit or 50), 200))
    asof = snapshot_asof_date()
    join = ("JOIN stock_list l ON l.code = s.code AND l.industry = ?"
            if dim == "industry" else
            "JOIN concept_map c ON c.code = s.code AND c.concept = ?")
    if range_key == "5d":
        flow_col = "s.main_net_in_d5"
        stock_note = "个股贡献为最新快照近5日主力净流入"
    elif range_key == "1d":
        flow_col = "s.main_net_in"
        stock_note = "个股贡献为最新快照当日主力净流入"
    else:
        flow_col = "s.main_net_in"
        stock_note = "个股区间资金仅当天/5日可精确到票，更长区间暂显示当日主力净流入"
    stock_note += f"。个股明细为最新快照，直方图/日走势为板块日频账本（账本日 {asof}）"
    rows = query(
        f"""SELECT s.code, s.name, s.price, s.pct, s.main_net_in, s.main_net_in_d5,
                   s.volume_ratio, s.main_in, s.main_out, s.amount,
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
    metrics_svc.attach_flow_list(rows)
    return {"dim": dim, "name": name, "range": range_key, "items": rows,
            "note": stock_note, "total": len(rows), "asof": asof}
