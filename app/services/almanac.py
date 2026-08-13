"""黄历与节气节日服务（FR6-05）：干支纪年、五行、时辰方位、24节气、国家/农历节日。

干支锚点：1949-10-01 为甲子日（史实锚点）；年干支以春节为界。
节气/农历节日日期采用 2026-2027 通用日期表（±1日近似，界面标注）。
"""
from datetime import date, timedelta

GAN = "甲乙丙丁戊己庚辛壬癸"
ZHI = "子丑寅卯辰巳午未申酉戌亥"
ZODIAC = "鼠牛虎兔龙蛇马羊猴鸡狗猪"
GAN_WUXING = {"甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
              "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水"}
ZHI_WUXING = {"子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土", "巳": "火",
              "午": "火", "未": "土", "申": "金", "酉": "金", "戌": "土", "亥": "水"}
# 民俗口诀：甲乙东北是财神，丙丁向在西南寻，戊己正北坐方位，庚辛正东去安身，壬癸原来正南坐
CAISHEN = {"甲": "东北", "乙": "东北", "丙": "西南", "丁": "西南", "戊": "正北",
           "己": "正北", "庚": "正东", "辛": "正东", "壬": "正南", "癸": "正南"}
SHICHEN = [
    ("子时 23-01", "正北"), ("丑时 01-03", "东北"), ("寅时 03-05", "东北"),
    ("卯时 05-07", "正东"), ("辰时 07-09", "东南"), ("巳时 09-11", "东南"),
    ("午时 11-13", "正南"), ("未时 13-15", "西南"), ("申时 15-17", "西南"),
    ("酉时 17-19", "正西"), ("戌时 19-21", "西北"), ("亥时 21-23", "西北"),
]

_ANCHOR = date(1949, 10, 1)  # 甲子日
SPRING_FESTIVAL = {2025: date(2025, 1, 29), 2026: date(2026, 2, 17), 2027: date(2027, 2, 6)}

# 24节气通用日期表（月, 日）
SOLAR_TERMS = [
    ("小寒", 1, 5), ("大寒", 1, 20), ("立春", 2, 4), ("雨水", 2, 19),
    ("惊蛰", 3, 5), ("春分", 3, 20), ("清明", 4, 5), ("谷雨", 4, 20),
    ("立夏", 5, 5), ("小满", 5, 21), ("芒种", 6, 6), ("夏至", 6, 21),
    ("小暑", 7, 7), ("大暑", 7, 23), ("立秋", 8, 7), ("处暑", 8, 23),
    ("白露", 9, 7), ("秋分", 9, 23), ("寒露", 10, 8), ("霜降", 10, 23),
    ("立冬", 11, 7), ("小雪", 11, 22), ("大雪", 12, 7), ("冬至", 12, 21),
]

_TERM_SECTORS = {
    "立春": ("农林牧渔、种业", "春耕行情启动窗口"),
    "清明": ("旅游、白酒", "假期消费小周期"),
    "立夏": ("电力、啤酒饮料", "夏季用电与消暑消费预热"),
    "夏至": ("电力、空调家电", "高温周期开启"),
    "立秋": ("白酒、农业", "中秋备货旺季前瞻，提前3-4周布局惯例"),
    "秋分": ("农业、物流", "秋收与双节物流旺季"),
    "立冬": ("燃气、煤炭、供暖", "供暖季启动"),
    "冬至": ("燃气、煤炭、医药", "寒潮消费与冬季用能高峰"),
}

# 节日：(名称, 公历日期dict{年:日期}或固定(月,日), 影响板块, 周期描述)
FESTIVALS_FIXED = [
    ("元旦", (1, 1), "旅游、零售", "跨年消费小高峰"),
    ("劳动节", (5, 1), "旅游、免税、影视", "五一长假消费，提前2-3周布局旅游酒店惯例"),
    ("国庆节", (10, 1), "旅游、免税、影视", "十一黄金周，提前3-4周布局大消费惯例"),
]
FESTIVALS_LUNAR = {  # 近似日期表
    2026: [("春节", date(2026, 2, 17), "食品饮料、影视、旅游", "春节消费旺季，提前1个月白酒零售行情惯例"),
           ("元宵节", date(2026, 3, 3), "食品、零售", "节庆消费收尾"),
           ("端午节", date(2026, 6, 19), "食品、旅游", "小长假消费"),
           ("七夕", date(2026, 8, 19), "黄金珠宝、零售", "礼赠消费小周期"),
           ("中秋节", date(2026, 9, 25), "白酒、食品、旅游", "双节备货旺季，白酒动销关键窗口"),
           ("重阳节", date(2026, 10, 18), "医药、养老", "银发经济关注点")],
    2027: [("腊八", date(2027, 1, 15), "食品", "年货季启动"),
           ("除夕", date(2027, 2, 5), "食品饮料、影视", "春节档影视与年夜饭消费"),
           ("春节", date(2027, 2, 6), "食品饮料、影视、旅游", "春节消费旺季（日期近似）"),
           ("元宵节", date(2027, 2, 20), "食品、零售", "节庆消费收尾（日期近似）")],
}


def day_ganzhi(d: date) -> str:
    idx = (d - _ANCHOR).days % 60
    return GAN[idx % 10] + ZHI[idx % 12]


def year_ganzhi(d: date) -> tuple[str, str]:
    year = d.year
    spring = SPRING_FESTIVAL.get(year)
    if spring and d < spring:
        year -= 1
    return GAN[(year - 4) % 10] + ZHI[(year - 4) % 12], ZODIAC[(year - 4) % 12]


def month_ganzhi(d: date) -> str:
    """月支以节气月近似（立春起寅月），月干按五虎遁。"""
    # 找到当前所处节气月（以每月第一个节气为界）
    boundaries = []
    for y in (d.year - 1, d.year):
        for i, (_name, m, day) in enumerate(SOLAR_TERMS):
            if i % 2 == 0:  # 节（每月第一个）
                boundaries.append((date(y, m, day), i // 2))
    boundaries.sort()
    month_idx = 11  # 默认丑月
    for bd, idx in boundaries:
        if d >= bd:
            month_idx = idx
    # idx0=小寒→丑月(1)，立春idx1→寅月(2)...
    zhi_idx = (month_idx + 1) % 12
    ygz, _ = year_ganzhi(d)
    ygan_idx = GAN.index(ygz[0])
    # 五虎遁：甲己之年丙作首（寅月起丙）
    first_gan = {0: 2, 5: 2, 1: 4, 6: 4, 2: 6, 7: 6, 3: 8, 8: 8, 4: 0, 9: 0}[ygan_idx]
    months_from_yin = (zhi_idx - 2) % 12
    return GAN[(first_gan + months_from_yin) % 10] + ZHI[zhi_idx]


# 九宫方位（洛书后天八卦）
JIUGONG = [
    ("巽·东南", "离·正南", "坤·西南"),
    ("震·正东", "中宫", "兑·正西"),
    ("艮·东北", "坎·正北", "乾·西北"),
]

# 季节 → 五行旺相休囚死（春木夏火秋金冬水，土旺四季末）
_WANGXIANG = {
    "春": {"旺": "木", "相": "火", "休": "水", "囚": "金", "死": "土"},
    "夏": {"旺": "火", "相": "土", "休": "木", "囚": "水", "死": "金"},
    "秋": {"旺": "金", "相": "水", "休": "土", "囚": "火", "死": "木"},
    "冬": {"旺": "水", "相": "木", "休": "金", "囚": "土", "死": "火"},
}


def _season_of(d: date) -> str:
    md = (d.month, d.day)
    if (2, 4) <= md < (5, 5):
        return "春"
    if (5, 5) <= md < (8, 7):
        return "夏"
    if (8, 7) <= md < (11, 7):
        return "秋"
    return "冬"


def _day_summary(d: date) -> dict:
    """某日的干支/节气/节日速览（用于明日预览）。"""
    dgz = day_ganzhi(d)
    term = next((n for n, m, dd in SOLAR_TERMS if m == d.month and dd == d.day), None)
    festival = None
    for name, md, *_ in FESTIVALS_FIXED:
        if (d.month, d.day) == md:
            festival = name
    for name, fd, *_ in FESTIVALS_LUNAR.get(d.year, []):
        if fd == d:
            festival = name
    return {"date": d.isoformat(), "weekday": "周" + "一二三四五六日"[d.weekday()],
            "day_ganzhi": f"{dgz}日", "solar_term": term, "festival": festival,
            "caishen": CAISHEN[dgz[0]]}


def get_almanac(d: date | None = None) -> dict:
    d = d or date.today()
    ygz, zodiac = year_ganzhi(d)
    dgz = day_ganzhi(d)
    mgz = month_ganzhi(d)
    day_gan = dgz[0]
    term_today = next((n for n, m, dd in SOLAR_TERMS if m == d.month and dd == d.day), None)
    season = _season_of(d)
    wx = _WANGXIANG[season]
    return {
        "date": d.isoformat(),
        "weekday": "周" + "一二三四五六日"[d.weekday()],
        "year_ganzhi": f"{ygz}年", "zodiac": zodiac,
        "month_ganzhi": f"{mgz}月", "day_ganzhi": f"{dgz}日",
        "wuxing": f"日干{day_gan}属{GAN_WUXING[day_gan]}，日支{dgz[1]}属{ZHI_WUXING[dgz[1]]}",
        "caishen": CAISHEN[day_gan],
        "shichen": [{"name": n, "direction": dr} for n, dr in SHICHEN],
        "solar_term": term_today,
        "season": season,
        "wangxiang": wx,
        "wangxiang_text": f"{season}季：{wx['旺']}旺、{wx['相']}相、{wx['休']}休、{wx['囚']}囚、{wx['死']}死",
        "jiugong": [list(row) for row in JIUGONG],
        "tomorrow": _day_summary(d + timedelta(days=1)),
        "note": "干支按1949-10-01甲子日推算；节气/农历为通用近似日期；方位五行为民俗文化参考",
    }


def get_festival_events(start: date, end: date) -> list[dict]:
    """节气 + 节日事件（供事件日历合并）。"""
    events = []
    for y in range(start.year, end.year + 1):
        for name, m, dd in SOLAR_TERMS:
            try:
                day = date(y, m, dd)
            except ValueError:
                continue
            if start <= day <= end:
                sectors, cycle_desc = _TERM_SECTORS.get(name, ("农林牧渔", "季节性周期参考"))
                events.append({
                    "date": day.isoformat(), "title": f"{name}（节气）", "category": "节气",
                    "region": "中国", "impact_level": 2,
                    "note": f"或利好 {sectors}；{cycle_desc}（日期为通用近似）",
                    "sectors": sectors,
                })
        for name, md, sectors, cycle_desc in FESTIVALS_FIXED:
            day = date(y, md[0], md[1])
            if start <= day <= end:
                events.append({
                    "date": day.isoformat(), "title": f"{name}（法定节日）", "category": "节日",
                    "region": "中国", "impact_level": 3,
                    "note": f"或利好 {sectors}；{cycle_desc}", "sectors": sectors,
                })
        for name, day, sectors, cycle_desc in FESTIVALS_LUNAR.get(y, []):
            if start <= day <= end:
                events.append({
                    "date": day.isoformat(), "title": f"{name}（农历节日）", "category": "节日",
                    "region": "中国", "impact_level": 3,
                    "note": f"或利好 {sectors}；{cycle_desc}", "sectors": sectors,
                })
    return events


# ---------------- 板块周期大事件（FR6-06） ----------------

SECTOR_EVENTS = [
    ("中国航天发射窗口", "monthly", 15, "酒泉/西昌/文昌", "军工、卫星导航、商业航天", "发射密集期前1-2周航天板块关注度上升"),
    ("珠海航展", 11, 10, "珠海", "军工、航空、无人机", "航展前1个月军工订单预期发酵"),
    ("中国国际进口博览会", 11, 5, "上海", "跨境电商、物流、消费", "进博会前2周会展与贸易链活跃"),
    ("广交会（春季）", 4, 15, "广州", "出口链、轻工、家电", "外贸订单风向标"),
    ("广交会（秋季）", 10, 15, "广州", "出口链、轻工、家电", "外贸订单风向标"),
    ("世界人工智能大会", 7, 6, "上海", "人工智能、算力、机器人", "AI主题催化窗口，前2-3周预热"),
    ("中国国际大数据产业博览会", 5, 26, "贵阳", "大数据、云计算", "数字经济主题窗口"),
    ("CES 国际消费电子展", 1, 7, "拉斯维加斯", "消费电子、苹果概念、AI硬件", "全球科技新品风向标"),
    ("MWC 世界移动通信大会", 3, 2, "巴塞罗那", "通信、5G、手机产业链", "通信新技术催化"),
    ("世界机器人大会", 8, 20, "北京", "机器人、减速器、伺服", "机器人主题集中催化"),
    ("服贸会", 9, 2, "北京", "服务贸易、会展、数字经济", "政策与订单发布窗口"),
    ("华为开发者大会", 6, 21, "东莞", "华为概念、鸿蒙、软件", "生态链新品催化"),
    ("苹果秋季发布会", 9, 10, "库比蒂诺", "苹果概念、消费电子", "果链备货与新品行情惯例"),
    ("G20 峰会季", 11, 18, "轮值国", "基建出海、贸易链", "国际合作与关税议题窗口"),
    ("APEC 峰会", 11, 12, "轮值经济体", "港口航运、贸易链", "区域贸易催化"),
    ("联合国气候大会 COP", 11, 30, "轮值国", "新能源、环保、碳中和", "碳主题政策催化"),
    ("冬季达沃斯论坛", 1, 20, "达沃斯", "全球金融、贸易", "全球宏观议题风向"),
]


def get_sector_events(months: int = 12) -> list[dict]:
    today = date.today()
    end = today + timedelta(days=months * 31)
    out = []
    for name, month, day, city, sectors, cycle_desc in SECTOR_EVENTS:
        if month == "monthly":
            for i in range(months):
                m = (today.month - 1 + i) % 12 + 1
                y = today.year + (today.month - 1 + i) // 12
                try:
                    dt = date(y, m, day)
                except ValueError:
                    continue
                if today <= dt <= end:
                    out.append({"date": dt.isoformat(), "title": name, "city": city,
                                "sectors": sectors, "cycle_desc": cycle_desc, "impact_level": 3})
        else:
            for y in (today.year, today.year + 1):
                try:
                    dt = date(y, month, day)
                except ValueError:
                    continue
                if today <= dt <= end:
                    out.append({"date": dt.isoformat(), "title": name, "city": city,
                                "sectors": sectors, "cycle_desc": cycle_desc, "impact_level": 4})
    out.sort(key=lambda e: e["date"])
    return out
