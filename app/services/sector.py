"""板块资金流全景（FR3-01）与个股画像（FR3-04）。

板块资金全部由本地快照按 行业/概念 维度聚合计算，treemap 数据结构：
方块大小=成交额，颜色值=主力净流入率。
"""
import time
from datetime import datetime

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
    return rows


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
