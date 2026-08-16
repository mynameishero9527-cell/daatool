"""宏观情报服务：实时快讯、影响程度评估、政策追踪、未来事件日历。"""
import hashlib
import json
from datetime import date, datetime, timedelta

from ..cache import cached
from ..config import TTL_NEWS
from ..database import execute, executemany, query
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
    # 关注度评分（FR5-01-2）：等级50% + 方向强度20% + 板块覆盖15% + 政策属性15%
    score = round(min(100.0,
                      level * 10  # 10-50
                      + min(abs(bull - bear), 4) * 5
                      + min(len(sectors), 3) * 5
                      + (15 if is_policy else 0) + 20), 0)
    category = "政策" if is_policy else "快讯"
    brief = (f"该消息属{category}类，影响程度{_LEVEL_DESC[level]}（关注度 {score:.0f}），"
             f"方向{direction}"
             + (f"，或将波及 {'、'.join(sectors[:4])} 等板块" if sectors else "，暂未识别到明确受影响板块")
             + "。")
    # 评论热度描述（FR7-02-1，规则生成）
    heat_word = "高" if score >= 70 else "中等" if score >= 45 else "一般"
    commentary = (
        f"机构与论坛关注度{heat_word}（热度 {score:.0f}）"
        + (f"，短线或{'催化' if direction == '利好' else '压制' if direction == '利空' else '扰动'}"
           f" {'、'.join(sectors[:2])} 板块" if sectors else "")
        + ("；政策类消息建议跟踪后续细则落地" if is_policy else "")
        + "（规则生成，仅供参考）")
    return {
        "impact_level": level, "impact_desc": _LEVEL_DESC[level],
        "impact_direction": direction, "affected_sectors": sectors,
        "region": region, "is_policy": is_policy,
        "score": score, "brief": brief, "commentary": commentary,
    }


def get_news_range(days: int = 0, limit: int = 80, policy_only: bool = False) -> list[dict]:
    """时间范围筛选（FR8-02-1）：days=1 当日 / 3 / 5 / 10 / 0 全部近端。历史来自本地事件库。"""
    get_news(120)  # 先刷新最新一批入库
    from datetime import datetime as _dt, timedelta as _td
    where = "event_id LIKE 'news_%'"
    params: list = []
    if days:
        cutoff = (_dt.now() - _td(days=days)).strftime("%Y-%m-%d 00:00:00")
        where += " AND event_time >= ?"
        params.append(cutoff)
    rows = query(f"SELECT * FROM macro_event WHERE {where} ORDER BY event_time DESC LIMIT ?",
                 (*params, limit * 3))
    out = []
    for r in rows:
        item = {"id": r["event_id"], "text": r["summary"], "time": r["event_time"],
                **assess_impact(r["summary"] or ""), "source": "本地事件库", "tags": []}
        if policy_only and not item["is_policy"]:
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out


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
        **assess_impact(r["summary"] or ""),
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


def _generate_events(days: int) -> list[dict]:
    """未来 days 天内的重大事件（常规日程预估 + 年度周期事件 + 用户自定义）。"""
    today = date.today()
    end = today + timedelta(days=days)
    months_span = days // 28 + 2
    events: list[dict] = []

    def add(day: date, title: str, category: str, region: str, level: int, note: str = ""):
        if today <= day <= end:
            events.append({
                "id": hashlib.md5(f"{day}{title}".encode()).hexdigest()[:12],
                "date": day.isoformat(), "title": title, "category": category,
                "region": region, "impact_level": level,
                "impact_desc": _LEVEL_DESC[level], "note": note or "日程为常规发布时间预估",
            })

    for i in range(months_span):
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
        # FOMC 会议（一年 8 次，取会议月第三个周三）
        if m in (1, 3, 4, 6, 7, 9, 10, 12):
            add(_third_wednesday(y, m), "美联储 FOMC 议息会议", "货币政策", "美国", 5)
        # 政治局会议（季度末月常规研究经济工作）
        if m in (4, 7, 10, 12):
            add(date(y, m, 28), "中央政治局会议（研究经济工作）", "财政政策", "中国", 5)
        # 财报季
        for (fm, title) in ((4, "A股年报/一季报密集披露期"), (8, "A股中报密集披露期"),
                            (10, "A股三季报密集披露期"), (1, "A股年报业绩预告密集期")):
            if m == fm:
                add(date(y, m, 25 if fm != 1 else 20), title, "公司事件", "中国", 3)
        # OPEC+ 部长级会议（月度初）
        add(date(y, m, 4), "OPEC+ 产量政策会议窗口", "地缘政治", "全球", 3)

    # 固定年度大事
    for y in (today.year, today.year + 1):
        add(date(y, 3, 5), "全国两会开幕（政府工作报告）", "财政政策", "中国", 5)
        add(date(y, 12, 11), "中央经济工作会议（定调次年）", "财政政策", "中国", 5)

    # 节气与节日（FR6-05）
    from . import almanac
    for ev in almanac.get_festival_events(today, end):
        events.append({
            "id": hashlib.md5(f"{ev['date']}{ev['title']}".encode()).hexdigest()[:12],
            **{k: ev[k] for k in ("date", "title", "category", "region", "impact_level", "note")},
            "impact_desc": _LEVEL_DESC.get(ev["impact_level"], "较小"),
            "sectors": ev.get("sectors", ""),
        })

    # 用户自定义事件
    for r in query("SELECT * FROM custom_event WHERE date>=? AND date<=? ", (today.isoformat(), end.isoformat())):
        events.append({
            "id": f"custom_{r['event_id']}", "date": r["date"], "title": r["title"],
            "category": r["category"], "region": r["region"],
            "impact_level": r["impact_level"], "impact_desc": _LEVEL_DESC.get(r["impact_level"], "中等"),
            "note": r["note"] or "用户自定义事件", "custom": True,
            "sectors": r.get("sectors") or "",
        })

    # 去重（同日同标题）并排序
    seen, out = set(), []
    for e in sorted(events, key=lambda x: (x["date"], -x["impact_level"])):
        key = (e["date"], e["title"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def get_calendar(months: int = 3) -> list[dict]:
    """未来 1-6 个月重大事件日历。"""
    return _generate_events(max(1, min(months, 6)) * 31)


# ---------------- 多时间跨度展望（FR2-05） ----------------

HORIZONS = {
    "week": (7, "未来一周"), "month": (31, "未来一月"),
    "quarter": (92, "未来三月"), "half": (183, "未来半年"),
}

_SECTOR_HINTS = {
    "货币政策": ["贵金属", "券商", "银行"], "财政政策": ["基建", "大消费"],
    "经济数据": ["指数权重", "周期"], "地缘政治": ["能源", "军工", "黄金股"],
    "公司事件": ["业绩主线"], "行业监管": ["受监管行业"],
}


def get_outlook(horizon: str = "week") -> dict:
    days, label = HORIZONS.get(horizon, HORIZONS["week"])
    events = _generate_events(days)
    critical = [e for e in events if e["impact_level"] >= 5]
    top3 = sorted(events, key=lambda e: (-e["impact_level"], e["date"]))[:3]
    sectors: list[str] = []
    for e in top3:
        for s in _SECTOR_HINTS.get(e["category"], []):
            if s not in sectors:
                sectors.append(s)

    # 叙事综述
    if events:
        top_txt = "；".join(
            f"{e['date'][5:]} {e['title']}（{e['impact_desc']}·{e['region']}）" for e in top3)
        summary = (
            f"{label}共 {len(events)} 个重点事件，其中极重大 {len(critical)} 个。"
            f"影响最大的事件：{top_txt}。"
            f"建议重点关注板块：{('、'.join(sectors[:5])) if sectors else '暂无明确主线'}。"
            f"风险提示：事件密集窗口波动可能加大，留意议息与政策会议的预期差。"
        )
    else:
        summary = f"{label}暂无收录的重大事件。"

    # 分组：周档按日，月档按周，季/半年按月
    groups: dict[str, list[dict]] = {}
    for e in events:
        d = date.fromisoformat(e["date"])
        if horizon == "week":
            key = f"{e['date'][5:]}（{'周一周二周三周四周五周六周日'[d.weekday()*2:d.weekday()*2+2]}）"
        elif horizon == "month":
            week_no = (d - date.today()).days // 7 + 1
            key = f"第{week_no}周"
        else:
            key = f"{d.year}年{d.month}月"
        groups.setdefault(key, []).append(e)

    return {
        "horizon": horizon, "label": label, "days": days,
        "summary": summary, "total": len(events), "critical": len(critical),
        "focus_sectors": sectors[:5],
        "groups": [{"name": k, "events": v} for k, v in groups.items()],
    }


# ---------------- 预期事件详情（FR5-02） ----------------

# 事件关键词 → (利好板块, 利空板块, 利好基准概率%)
_EVENT_IMPACT_MAP: list[tuple[tuple, list[str], list[str], int]] = [
    (("LPR", "降息", "降准", "MLF"), ["券商", "银行", "房地产", "贵金属"], ["无明显利空"], 60),
    (("FOMC", "议息", "美联储"), ["贵金属", "黄金"], ["半导体", "人工智能", "科技成长"], 48),
    (("CPI", "PPI",), ["食品饮料", "农林牧渔", "大消费"], ["高估值成长"], 50),
    (("PMI",), ["基建", "钢铁", "机械设备"], ["无明显利空"], 52),
    (("非农",), ["出口链"], ["贵金属", "黄金"], 48),
    (("两会", "政治局", "经济工作会议"), ["基建", "大消费", "券商", "科技自主"], ["无明显利空"], 62),
    (("财报", "业绩", "年报", "季报"), ["绩优白马", "食品饮料"], ["业绩暴雷高风险股"], 50),
    (("OPEC", "原油", "产量"), ["石油石化", "油气开采"], ["航空", "物流"], 55),
    (("社零", "工业增加值"), ["大消费", "家用电器"], ["无明显利空"], 52),
]

# 板块词 → 申万行业 + 概念（快讯/政策标签与日历事件共用）
_SECTOR_SPEC: dict[str, dict[str, list[str]]] = {
    "半导体": {"concepts": ["芯片概念"], "industries": ["电子"]},
    "新能源": {"concepts": ["新能源车", "光伏概念", "储能概念", "锂电池概念", "风电概念"],
             "industries": ["电力设备"]},
    "医药": {"concepts": [], "industries": ["医药生物"]},
    "地产": {"concepts": [], "industries": ["房地产"]},
    "房地产": {"concepts": [], "industries": ["房地产"]},
    "金融": {"concepts": [], "industries": ["银行", "非银金融"]},
    "银行": {"concepts": [], "industries": ["银行"]},
    "券商": {"concepts": ["证券"], "industries": ["非银金融"]},
    "能源": {"concepts": [], "industries": ["石油石化", "煤炭", "公用事业"]},
    "有色": {"concepts": [], "industries": ["有色金属"]},
    "有色金属": {"concepts": [], "industries": ["有色金属"]},
    "汽车": {"concepts": [], "industries": ["汽车"]},
    "军工": {"concepts": ["军工"], "industries": ["国防军工"]},
    "国防军工": {"concepts": ["军工"], "industries": ["国防军工"]},
    "消费": {"concepts": [], "industries": ["食品饮料", "商贸零售", "家用电器", "美容护理", "社会服务"]},
    "大消费": {"concepts": [], "industries": ["食品饮料", "商贸零售", "家用电器"]},
    "科技": {"concepts": ["人工智能", "数据中心"], "industries": ["计算机", "通信"]},
    "科技成长": {"concepts": ["人工智能"], "industries": ["计算机"]},
    "科技自主": {"concepts": ["国产替代"], "industries": ["计算机", "电子"]},
    "人工智能": {"concepts": ["人工智能"], "industries": ["计算机"]},
    "贵金属": {"concepts": ["黄金"], "industries": ["有色金属"]},
    "黄金": {"concepts": ["黄金"], "industries": ["有色金属"]},
    "石油石化": {"concepts": [], "industries": ["石油石化"]},
    "油气开采": {"concepts": [], "industries": ["石油石化"]},
    "航空": {"concepts": ["航空"], "industries": ["交通运输"]},
    "物流": {"concepts": ["物流"], "industries": ["交通运输"]},
    "基建": {"concepts": ["基建"], "industries": ["建筑装饰", "建筑材料"]},
    "钢铁": {"concepts": [], "industries": ["钢铁"]},
    "机械设备": {"concepts": [], "industries": ["机械设备"]},
    "食品饮料": {"concepts": [], "industries": ["食品饮料"]},
    "农林牧渔": {"concepts": ["农业"], "industries": ["农林牧渔"]},
    "家用电器": {"concepts": ["家电"], "industries": ["家用电器"]},
    "出口链": {"concepts": ["跨境电商"], "industries": ["轻工制造", "纺织服饰"]},
    "绩优白马": {"concepts": ["基金重仓"], "industries": ["食品饮料"]},
    "高估值成长": {"concepts": ["人工智能"], "industries": ["计算机"]},
    "指数权重": {"concepts": [], "industries": []},
}

_STOCK_COLS = """s.code, s.name, s.price, s.pct, s.main_net_in, s.volume_ratio,
                 s.turnover_rate, s.main_in, s.main_out, s.amount,
                 m.buy_index, m.sentiment, m.dark_power, s.float_mv,
                 l.industry"""


def _resolve_sector(sec: str) -> tuple[list[str], list[str]]:
    spec = _SECTOR_SPEC.get(sec)
    if spec:
        return list(spec.get("industries") or []), list(spec.get("concepts") or [])
    if query("SELECT 1 FROM stock_list WHERE industry = ? LIMIT 1", (sec,)):
        return [sec], []
    if query("SELECT 1 FROM concept_map WHERE concept = ? LIMIT 1", (sec,)):
        return [], [sec]
    return [], [sec]


def _not_in(codes: list[str]) -> tuple[str, list]:
    if not codes:
        return "", []
    return " AND s.code NOT IN (" + ",".join("?" * len(codes)) + ")", list(codes)


def _fetch_by_concepts(concepts: list[str], limit: int, exclude: list[str], like: bool = False) -> list[dict]:
    if not concepts or limit <= 0:
        return []
    extra, params_ex = _not_in(exclude)
    if like:
        cond = " OR ".join(["c.concept LIKE ?" for _ in concepts])
        params = [f"%{c}%" for c in concepts] + params_ex + [limit]
        where = f"({cond})"
    else:
        ph = ",".join("?" * len(concepts))
        params = list(concepts) + params_ex + [limit]
        where = f"c.concept IN ({ph})"
    return query(
        f"""SELECT DISTINCT {_STOCK_COLS}
            FROM stock_snapshot s
            JOIN concept_map c ON c.code = s.code AND {where}
            LEFT JOIN stock_list l ON l.code = s.code
            LEFT JOIN stock_metrics m ON m.code = s.code
            WHERE s.price IS NOT NULL {extra}
            ORDER BY s.main_net_in DESC LIMIT ?""",
        tuple(params),
    )


def _fetch_by_industries(industries: list[str], limit: int, exclude: list[str]) -> list[dict]:
    if not industries or limit <= 0:
        return []
    extra, params_ex = _not_in(exclude)
    ph = ",".join("?" * len(industries))
    return query(
        f"""SELECT {_STOCK_COLS}
            FROM stock_snapshot s
            JOIN stock_list l ON l.code = s.code AND l.industry IN ({ph})
            LEFT JOIN stock_metrics m ON m.code = s.code
            WHERE s.price IS NOT NULL {extra}
            ORDER BY s.main_net_in DESC LIMIT ?""",
        (*industries, *params_ex, limit),
    )


def _fetch_top_mv(limit: int, exclude: list[str]) -> list[dict]:
    extra, params_ex = _not_in(exclude)
    return query(
        f"""SELECT {_STOCK_COLS}
            FROM stock_snapshot s
            LEFT JOIN stock_list l ON l.code = s.code
            LEFT JOIN stock_metrics m ON m.code = s.code
            WHERE s.price IS NOT NULL {extra}
            ORDER BY s.float_mv DESC LIMIT ?""",
        (*params_ex, limit),
    )


def _annotate_rows(rows: list[dict], sector: str) -> list[dict]:
    from . import metrics as metrics_svc
    from . import rating as rating_svc
    from . import wuxing
    for r in rows:
        r["sector"] = sector
        if r.get("buy_index") is not None:
            r["buy_level"], r["buy_action"] = metrics_svc.buy_index_level(r["buy_index"])
        else:
            r["buy_level"] = r["buy_action"] = None
        r["score"], r["advice"] = rating_svc.quick_score(r)
    wuxing.tags_for_list(rows)
    from . import finance as finance_svc
    finance_svc.attach_grades(rows)
    metrics_svc.attach_flow_list(rows)
    return rows


def _stocks_for_sectors(sectors: list[str], limit: int = 30) -> list[dict]:
    """由板块词反查本地个股（概念优先、行业兜底），按主力净流入排序，返回 20–50 只。"""
    limit = max(20, min(int(limit or 30), 50))
    names = [s.strip() for s in sectors
             if s and s.strip() and s.strip() not in ("无明显利空", "未映射影响板块")]
    if not names:
        return []
    seen, out = set(), []
    nsec = len(names)
    per = limit if nsec == 1 else max(8, (limit + nsec - 1) // nsec)
    for sec in names:
        need = min(per, limit - len(out))
        if need <= 0:
            break
        industries, concepts = _resolve_sector(sec)
        rows: list[dict] = []
        if concepts:
            rows = _fetch_by_concepts(concepts, need, list(seen), like=False)
            if not rows:
                rows = _fetch_by_concepts(concepts, need, list(seen), like=True)
        if len(rows) < need and industries:
            rows.extend(_fetch_by_industries(industries, need - len(rows),
                                             list(seen) + [r["code"] for r in rows]))
        if not rows:
            rows = _fetch_by_concepts([sec], need, list(seen), like=True)
        if not rows and industries:
            rows = _fetch_by_industries(industries, need, list(seen))
        if not rows and sec == "指数权重":
            rows = _fetch_top_mv(need, list(seen))
        for r in _annotate_rows(rows, sec):
            if r["code"] in seen:
                continue
            seen.add(r["code"])
            out.append(r)
            if len(out) >= limit:
                return out[:limit]
    return out[:limit]


def get_event_detail(title: str, bull_override: str = "", bear_override: str = "",
                     limit: int = 30) -> dict:
    """预期事件详情：利好/利空板块、利好概率、相关个股（20-50 只）。

    bull_override/bear_override：逗号分隔板块名，用于节气/节日/板块事件/快讯标签直接传参。
    """
    limit = max(20, min(int(limit or 30), 50))
    bull, bear, base_prob = [], ["无明显利空"], 50
    mapped = False
    if bull_override:
        bull = [s.strip() for s in bull_override.replace("、", ",").split(",") if s.strip()]
        bear = ([s.strip() for s in bear_override.replace("、", ",").split(",") if s.strip()]
                or ["无明显利空"])
        base_prob = 55
        mapped = bool(bull)
    else:
        for keywords, b1, b2, prob in _EVENT_IMPACT_MAP:
            if any(k in title for k in keywords):
                bull, bear, base_prob = b1, b2, prob
                mapped = True
                break
    if not mapped:
        return {
            "title": title,
            "bull_sectors": ["未映射影响板块"], "bear_sectors": ["无明显利空"],
            "bull_prob": 50,
            "prob_note": "该事件未配置影响板块，不回退指数权重个股",
            "stocks": [], "unmapped": True,
            "disclaimer": "板块映射与概率为规则化预估，仅供参考，不构成投资建议",
        }

    # 周期阶段修正
    prob = base_prob
    stage_note = ""
    try:
        from . import cycle as cycle_svc
        stage = cycle_svc.get_cycle().get("stage", "")
        adj = {"牛市主升": 6, "震荡上行": 3, "底部构筑": 1, "高位震荡": -2,
               "调整下行": -4, "熊市寻底": -5}.get(stage, 0)
        prob = max(20, min(80, prob + adj))
        stage_note = f"当前处于「{stage}」阶段，概率修正 {adj:+d}%"
    except Exception:  # noqa: BLE001
        pass

    bull_valid = [s for s in bull if s != "无明显利空"]
    stocks = _stocks_for_sectors(bull_valid, limit=limit)
    return {
        "title": title,
        "bull_sectors": bull, "bear_sectors": bear,
        "bull_prob": prob,
        "prob_note": f"类别先验 {base_prob}%；{stage_note}" if stage_note else f"类别先验 {base_prob}%",
        "stocks": stocks,
        "disclaimer": "板块映射与概率为规则化预估，仅供参考，不构成投资建议",
    }


def add_custom_event(day: str, title: str, level: int = 3, note: str = "",
                     sectors: str = "") -> dict:
    try:
        date.fromisoformat(day)
    except ValueError:
        return {"ok": False, "error": "日期格式应为 YYYY-MM-DD"}
    if not title.strip():
        return {"ok": False, "error": "标题不能为空"}
    sec = (sectors or "").replace("，", ",").replace("、", ",").strip()
    execute("INSERT INTO custom_event(date,title,impact_level,note,sectors) VALUES(?,?,?,?,?)",
            (day, title.strip(), max(1, min(5, level)), note, sec))
    return {"ok": True}


def delete_custom_event(event_id: int) -> dict:
    execute("DELETE FROM custom_event WHERE event_id=?", (event_id,))
    return {"ok": True}


def _save_intel_rows(rows: list[tuple]) -> None:
    if not rows:
        return
    executemany(
        """INSERT OR REPLACE INTO intel_cache(
             item_id,kind,title,summary,event_time,region,category,impact_level,
             impact_direction,affected_sectors,source,raw_json,fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    links = []
    for rec in rows:
        item_id, _kind, _title, _summary, event_time = rec[0], rec[1], rec[2], rec[3], rec[4]
        try:
            sectors = json.loads(rec[9] or "[]")
        except Exception:  # noqa: BLE001
            sectors = []
        for s in sectors:
            if s:
                links.append((str(s), item_id, event_time))
    if links:
        executemany(
            "INSERT OR REPLACE INTO intel_sector(sector,item_id,event_time) VALUES(?,?,?)",
            links)


def sync_intel() -> dict:
    """拉取快讯/日历/板块事件写入本地情报库，供离线分析。"""
    now = datetime.now().isoformat(timespec="seconds")
    news_n = 0
    db_news = []
    for page in range(1, 5):
        try:
            items = sina.fetch_news(page=page, size=50)
        except Exception:  # noqa: BLE001
            break
        if not items:
            break
        rows = []
        for it in items:
            impact = assess_impact(it["text"])
            kind = "policy" if impact["is_policy"] else "news"
            rows.append((
                f"news_{it['id']}", kind, (it["text"] or "")[:80], it["text"],
                it["time"], impact["region"],
                "政策" if impact["is_policy"] else "快讯",
                impact["impact_level"], impact["impact_direction"],
                json.dumps(impact["affected_sectors"], ensure_ascii=False),
                "新浪财经", json.dumps({"tags": it.get("tags") or []}, ensure_ascii=False), now,
            ))
            db_news.append((
                f"news_{it['id']}", (it["text"] or "")[:80], it["text"],
                "政策" if impact["is_policy"] else "快讯", impact["region"],
                impact["impact_level"], impact["impact_direction"],
                json.dumps(impact["affected_sectors"], ensure_ascii=False),
                it["time"], it["time"], "新浪财经",
            ))
        _save_intel_rows(rows)
        news_n += len(rows)
    if db_news:
        executemany(
            "INSERT OR REPLACE INTO macro_event(event_id,title,summary,category,region,"
            "impact_level,impact_direction,affected_sectors,event_time,publish_time,source)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)", db_news)

    cal_n = 0
    cal_rows = []
    for ev in _generate_events(93):
        sectors = []
        if ev.get("sectors"):
            sectors = [s.strip() for s in str(ev["sectors"]).replace("、", ",").split(",") if s.strip()]
        cal_rows.append((
            f"cal_{ev['id']}", "calendar", ev["title"], ev.get("note") or "",
            ev["date"], ev.get("region") or "", ev.get("category") or "",
            ev.get("impact_level") or 3, "", json.dumps(sectors, ensure_ascii=False),
            "本地日历", json.dumps(ev, ensure_ascii=False, default=str), now,
        ))
        cal_n += 1
    _save_intel_rows(cal_rows)

    from . import almanac
    sec_rows = []
    for ev in almanac.get_sector_events(12):
        sectors = [s.strip() for s in str(ev.get("sectors") or "").replace("、", ",").split(",") if s.strip()]
        key = hashlib.md5(f"{ev['date']}{ev['title']}".encode()).hexdigest()[:16]
        sec_rows.append((
            f"sev_{key}", "sector_event", ev["title"], ev.get("cycle_desc") or "",
            ev["date"], ev.get("city") or "", "板块事件",
            ev.get("impact_level") or 3, "", json.dumps(sectors, ensure_ascii=False),
            "板块周期", json.dumps(ev, ensure_ascii=False, default=str), now,
        ))
    _save_intel_rows(sec_rows)
    from ..database import set_meta
    set_meta("intel_last_sync", now)
    return {"ok": True, "news": news_n, "calendar": cal_n,
            "sector_events": len(sec_rows), "stats": intel_stats()}


def intel_stats() -> dict:
    try:
        rows = query("SELECT kind, COUNT(*) AS n FROM intel_cache GROUP BY kind")
        total = query("SELECT COUNT(*) AS n FROM intel_cache")[0]["n"]
        sectors = query("SELECT COUNT(DISTINCT sector) AS n FROM intel_sector")[0]["n"]
    except Exception:  # noqa: BLE001
        return {"total": 0, "by_kind": {}, "sectors": 0}
    from ..database import get_meta
    extra = {}
    try:
        extra["official_policy"] = query(
            "SELECT COUNT(*) AS n FROM official_policy")[0]["n"]
        extra["hot_terms"] = query(
            "SELECT COUNT(*) AS n FROM hot_term WHERE window_end=?",
            (datetime.now().strftime("%Y-%m-%d"),))[0]["n"]
        extra["policy_last_sync"] = get_meta("official_policy_last_sync", "从未")
        extra["hot_last_sync"] = get_meta("hot_terms_last_sync", "从未")
    except Exception:  # noqa: BLE001
        extra.update({"official_policy": 0, "hot_terms": 0,
                      "policy_last_sync": "从未", "hot_last_sync": "从未"})
    return {
        "total": total, "by_kind": {r["kind"]: r["n"] for r in rows},
        "sectors": sectors, "last_sync": get_meta("intel_last_sync", "从未"),
        **extra,
    }


def list_intel(kind: str = "", sector: str = "", days: int = 0, limit: int = 80) -> dict:
    limit = max(10, min(int(limit or 80), 200))
    where = ["1=1"]
    params: list = []
    if kind:
        where.append("kind=?")
        params.append(kind)
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        where.append("event_time>=?")
        params.append(cutoff)
    if sector:
        rows = query(
            f"""SELECT c.* FROM intel_cache c JOIN intel_sector s ON s.item_id=c.item_id
                WHERE {' AND '.join(where)} AND s.sector=?
                ORDER BY c.event_time DESC LIMIT ?""",
            (*params, sector, limit))
    else:
        rows = query(
            f"SELECT * FROM intel_cache WHERE {' AND '.join(where)} "
            "ORDER BY event_time DESC LIMIT ?", (*params, limit))
    items = []
    for r in rows:
        try:
            sectors = json.loads(r.get("affected_sectors") or "[]")
        except Exception:  # noqa: BLE001
            sectors = []
        items.append({
            "id": r["item_id"], "kind": r["kind"], "title": r["title"],
            "summary": r["summary"], "time": r["event_time"], "date": (r["event_time"] or "")[:10],
            "region": r["region"], "category": r["category"],
            "impact_level": r["impact_level"] or 1,
            "impact_direction": r["impact_direction"] or "",
            "sectors": "、".join(sectors), "affected_sectors": sectors,
            "source": r["source"], "cached": True,
        })
    return {"items": items, "count": len(items), "stats": intel_stats()}


_WEEKDAY_CN = "一二三四五六日"


def event_ymd(value: str) -> str:
    """只取日历日，避免带时分秒时按日无法合并。"""
    s = (value or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return ""


def _week_bounds(ymd: str) -> tuple[str, str]:
    d = date.fromisoformat(ymd)
    start = d - timedelta(days=d.weekday())
    end = start + timedelta(days=6)
    return start.isoformat(), end.isoformat()


def _split_sectors(raw) -> list[str]:
    if isinstance(raw, list):
        parts = raw
    else:
        text = str(raw or "")
        for sep in ("，", "、", "/", "|"):
            text = text.replace(sep, ",")
        parts = text.split(",")
    out: list[str] = []
    seen: set[str] = set()
    skip = {"", "未映射影响板块", "政策", "A股", "大盘"}
    for p in parts:
        n = str(p.get("name") or "").strip() if isinstance(p, dict) else str(p or "").strip()
        if n in skip or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def group_sector_events(items: list[dict], dim: str = "day") -> list[dict]:
    """把事件合并进日/周/月时间桶，并汇总该桶内板块。"""
    dim = dim if dim in ("day", "week", "month") else "day"
    buckets: dict[str, dict] = {}
    for ev in items or []:
        ymd = event_ymd(str(ev.get("date") or ""))
        if dim == "week" and ymd:
            start, end = _week_bounds(ymd)
            key = f"w:{start}"
            label = f"{start} ~ {end[5:]} 当周"
            span = f"{start} ~ {end}"
        elif dim == "month" and ymd:
            key = f"m:{ymd[:7]}"
            label = f"{ymd[:4]}年{int(ymd[5:7])}月"
            span = ymd[:7]
        elif ymd:
            key = f"d:{ymd}"
            wd = _WEEKDAY_CN[date.fromisoformat(ymd).weekday()]
            label = f"{ymd} 周{wd}"
            span = ymd
        else:
            key = "none"
            label = "日期未标注"
            span = ""
        g = buckets.get(key)
        if g is None:
            g = {
                "key": key, "label": label, "range": span, "dim": dim,
                "items": [], "sectors": [],
            }
            buckets[key] = g
        g["items"].append(ev)
        seen = set(g["sectors"])
        for sec in _split_sectors(ev.get("sectors") or ev.get("affected_sectors") or ""):
            if sec not in seen:
                seen.add(sec)
                g["sectors"].append(sec)
    keys = [k for k in buckets if k != "none"]
    keys.sort(reverse=True)
    if "none" in buckets:
        keys.append("none")
    out = []
    for k in keys:
        g = buckets[k]
        g["items"].sort(key=lambda e: (e.get("date") or "", e.get("title") or ""), reverse=True)
        g["count"] = len(g["items"])
        g["sectors_text"] = "、".join(g["sectors"])
        out.append(g)
    return out


def get_sector_intel(months: int = 12, group: str = "day") -> dict:
    """板块事件 = 周期大事件 + 本地缓存里带板块标签的快讯/政策。"""
    from . import almanac
    events = almanac.get_sector_events(months)
    cached = list_intel(limit=100)
    news_ev = []
    for it in cached["items"]:
        if it["kind"] not in ("news", "policy") or not it.get("affected_sectors"):
            continue
        news_ev.append({
            "id": it.get("id") or "",
            "date": event_ymd(it.get("date") or it.get("time") or ""),
            "title": it["title"],
            "city": it.get("region") or "",
            "sectors": it.get("sectors") or "",
            "cycle_desc": it.get("summary") or "",
            "impact_level": it.get("impact_level") or 2,
            "source": "本地情报缓存",
            "kind": it["kind"],
        })
    for ev in events:
        ev.setdefault("source", "板块周期")
        ev.setdefault("kind", "sector_event")
        ev["date"] = event_ymd(ev.get("date") or "")
        if not ev.get("id"):
            key = hashlib.md5(f"{ev.get('date') or ''}{ev.get('title') or ''}".encode()).hexdigest()[:12]
            ev["id"] = key
    merged = events + news_ev
    merged.sort(key=lambda e: e.get("date") or "", reverse=True)
    dim = group if group in ("day", "week", "month") else "day"
    groups = group_sector_events(merged, dim)
    return {
        "items": merged, "groups": groups, "group": dim,
        "count": len(merged), "stats": intel_stats(),
        "note": "已按日/周/月把同一时段事件与影响板块合并展示。周期大事件与已缓存快讯均来自本地库，断网也可回看",
    }
