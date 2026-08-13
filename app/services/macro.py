"""宏观情报服务：实时快讯、影响程度评估、政策追踪、未来事件日历。"""
import hashlib
import json
from datetime import date, datetime, timedelta

from ..cache import cached
from ..config import TTL_NEWS
from ..database import executemany, query
from ..datasources import sina

# ---------------- 影响程度关键词规则（详见产品文档 §5.3） ----------------

_LEVEL_RULES: list[tuple[int, list[str]]] = [
    (5, ["战争", "开战", "军事冲突", "宣布加息", "宣布降息", "降准", "全面降息", "紧急状态",
         "制裁升级", "石油禁运", "熔断", "崩盘", "违约", "系统性风险"]),
    (4, ["加息", "降息", "美联储", "央行", "CPI", "PPI", "GDP", "非农", "监管新规",
         "反垄断", "关税", "地缘", "国务院", "证监会", "重磅政策", "印花税"]),
    (3, ["政策", "发改委", "财政部", "工信部", "规划", "补贴", "限购", "IPO", "退市",
         "北向资金", "外资", "汇率", "人民币"]),
    (2, ["业绩", "财报", "回购", "增持", "减持", "中标", "合作", "签约", "投产"]),
]

_LEVEL_DESC = {5: "极重大", 4: "重大", 3: "中等", 2: "较小", 1: "轻微"}

_BULL_WORDS = ["利好", "上涨", "增长", "超预期", "回暖", "降准", "降息", "补贴", "支持",
               "扶持", "复苏", "创新高", "回购", "增持", "中标", "突破"]
_BEAR_WORDS = ["利空", "下跌", "下滑", "不及预期", "衰退", "加息", "制裁", "处罚", "调查",
               "违约", "减持", "退市", "亏损", "风险", "警示", "下调"]

_SECTOR_KEYWORDS = {
    "半导体": ["芯片", "半导体", "光刻", "晶圆"],
    "新能源": ["锂电", "光伏", "储能", "新能源", "风电", "充电桩"],
    "医药": ["医药", "疫苗", "创新药", "医疗", "集采"],
    "地产": ["地产", "房地产", "楼市", "房贷"],
    "金融": ["银行", "券商", "保险", "金融"],
    "能源": ["原油", "石油", "煤炭", "天然气", "电力"],
    "有色": ["黄金", "铜", "铝", "稀土", "有色"],
    "汽车": ["汽车", "车企", "智能驾驶"],
    "军工": ["军工", "国防", "航天"],
    "消费": ["白酒", "消费", "零售", "食品"],
    "科技": ["人工智能", "AI", "算力", "数据中心", "机器人"],
}

_REGION_KEYWORDS = {
    "美国": ["美国", "美联储", "白宫", "纳斯达克", "美股", "非农"],
    "欧洲": ["欧洲", "欧盟", "欧元区", "德国", "法国", "英国"],
    "亚太": ["日本", "韩国", "印度", "东南亚"],
    "中国": ["中国", "国内", "央行", "国务院", "证监会", "A股", "发改委", "财政部"],
}


def assess_impact(text: str) -> dict:
    """对一条消息文本做影响评估：等级、方向、区域、受影响板块。"""
    level = 1
    for lv, words in _LEVEL_RULES:
        if any(w in text for w in words):
            level = max(level, lv)
    bull = sum(1 for w in _BULL_WORDS if w in text)
    bear = sum(1 for w in _BEAR_WORDS if w in text)
    direction = "利好" if bull > bear else "利空" if bear > bull else "中性"
    sectors = [s for s, kws in _SECTOR_KEYWORDS.items() if any(k in text for k in kws)]
    region = "全球"
    for reg, kws in _REGION_KEYWORDS.items():
        if any(k in text for k in kws):
            region = reg
            break
    is_policy = any(w in text for w in ("政策", "央行", "国务院", "证监会", "发改委", "财政部",
                                        "监管", "新规", "美联储", "关税", "降准", "降息", "加息"))
    return {
        "impact_level": level, "impact_desc": _LEVEL_DESC[level],
        "impact_direction": direction, "affected_sectors": sectors,
        "region": region, "is_policy": is_policy,
    }


def get_news(limit: int = 60) -> list[dict]:
    """实时快讯 + 影响评估，成功后落库供离线回看。"""
    def loader():
        try:
            items = sina.fetch_news(page=1, size=limit)
        except Exception:  # noqa: BLE001
            return _news_from_db(limit)
        out = []
        db_rows = []
        for it in items:
            impact = assess_impact(it["text"])
            row = {**it, **impact, "source": "新浪财经", "offline": False}
            out.append(row)
            db_rows.append((
                f"news_{it['id']}", it["text"][:80], it["text"],
                "政策" if impact["is_policy"] else "快讯", impact["region"],
                impact["impact_level"], impact["impact_direction"],
                json.dumps(impact["affected_sectors"], ensure_ascii=False),
                it["time"], it["time"], "新浪财经",
            ))
        if db_rows:
            executemany(
                "INSERT OR REPLACE INTO macro_event(event_id,title,summary,category,region,"
                "impact_level,impact_direction,affected_sectors,event_time,publish_time,source)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)", db_rows)
        return out
    return cached("macro:news", TTL_NEWS, loader) or []


def _news_from_db(limit: int) -> list[dict]:
    rows = query(
        "SELECT * FROM macro_event WHERE event_id LIKE 'news_%' ORDER BY event_time DESC LIMIT ?",
        (limit,))
    return [{
        "id": r["event_id"], "text": r["summary"], "time": r["event_time"],
        "impact_level": r["impact_level"], "impact_desc": _LEVEL_DESC.get(r["impact_level"], "轻微"),
        "impact_direction": r["impact_direction"],
        "affected_sectors": json.loads(r["affected_sectors"] or "[]"),
        "region": r["region"], "is_policy": r["category"] == "政策",
        "source": "本地数据库", "offline": True, "tags": [],
    } for r in rows]


def get_policies(limit: int = 40) -> list[dict]:
    return [n for n in get_news(120) if n.get("is_policy")][:limit]


def get_major_events(limit: int = 20) -> list[dict]:
    """影响评估专题：等级 >= 3 的事件。"""
    return [n for n in get_news(120) if n.get("impact_level", 1) >= 3][:limit]


# ---------------- 未来事件日历（预估日程） ----------------

def _third_wednesday(year: int, month: int) -> date:
    d = date(year, month, 1)
    offset = (2 - d.weekday()) % 7  # 周三
    return d + timedelta(days=offset + 14)


def get_calendar(months: int = 3) -> list[dict]:
    """未来 1-3 个月重大事件日历（按常规日程预估）。"""
    months = max(1, min(months, 3))
    today = date.today()
    events: list[dict] = []

    def add(day: date, title: str, category: str, region: str, level: int, note: str = ""):
        if today <= day <= today + timedelta(days=months * 31):
            events.append({
                "id": hashlib.md5(f"{day}{title}".encode()).hexdigest()[:12],
                "date": day.isoformat(), "title": title, "category": category,
                "region": region, "impact_level": level,
                "impact_desc": _LEVEL_DESC[level], "note": note or "日程为常规发布时间预估",
            })

    for i in range(months + 1):
        m = (today.month - 1 + i) % 12 + 1
        y = today.year + (today.month - 1 + i) // 12
        try:
            add(date(y, m, 9), "中国 CPI/PPI 数据发布", "经济数据", "中国", 4)
            add(date(y, m, 15), "中国 MLF 操作与经济数据（工业增加值/社零）", "货币政策", "中国", 3)
            add(date(y, m, 20), "中国 LPR 报价公布", "货币政策", "中国", 4)
            add(date(y, m, 13), "美国 CPI 数据发布", "经济数据", "美国", 4)
            add(date(y, m, 1), "中国官方制造业 PMI 公布", "经济数据", "中国", 3)
            first = date(y, m, 1)
            first_friday = first + timedelta(days=(4 - first.weekday()) % 7)
            add(first_friday, "美国非农就业报告", "经济数据", "美国", 4)
        except ValueError:
            continue

    # FOMC 会议（一年 8 次，取近似月份的第三个周三）
    for i in range(months + 1):
        m = (today.month - 1 + i) % 12 + 1
        y = today.year + (today.month - 1 + i) // 12
        if m in (1, 3, 4, 6, 7, 9, 10, 12):
            add(_third_wednesday(y, m), "美联储 FOMC 议息会议", "货币政策", "美国", 5)

    # 财报季提示
    for (m, title) in ((4, "A股年报/一季报密集披露期"), (8, "A股中报密集披露期"), (10, "A股三季报密集披露期")):
        for i in range(months + 1):
            mm = (today.month - 1 + i) % 12 + 1
            yy = today.year + (today.month - 1 + i) // 12
            if mm == m:
                add(date(yy, mm, 25), title, "公司事件", "中国", 3)

    events.sort(key=lambda e: e["date"])
    return events
