"""紫微斗数流日示意盘。

缺出生年月日时与五行局，不能排本命盘。
此处只用当日农历日安紫微、所选时支为命宫，作流日示意。
"""
from __future__ import annotations

from .almanac import ZHI

PALACE_NAMES = ["命宫", "兄弟", "夫妻", "子女", "财帛", "疾厄",
                "迁移", "交友", "官禄", "田宅", "福德", "父母"]
# 从紫微宫逆行
_ZIWEI_REV = ["紫微", "天机", None, "太阳", "武曲", "天同", None, "廉贞"]
# 从天府宫顺行
_TIANFU_FWD = ["天府", "太阴", "贪狼", "巨门", "天相", "天梁", "七杀", None, None, None, "破军"]

# 年干四化（流年干示意，不是生年四化）
_SIHUA = {
    "甲": [("廉贞", "禄"), ("破军", "权"), ("武曲", "科"), ("太阳", "忌")],
    "乙": [("天机", "禄"), ("天梁", "权"), ("紫微", "科"), ("太阴", "忌")],
    "丙": [("天同", "禄"), ("天机", "权"), ("文昌", "科"), ("廉贞", "忌")],
    "丁": [("太阴", "禄"), ("天同", "权"), ("天机", "科"), ("巨门", "忌")],
    "戊": [("贪狼", "禄"), ("太阴", "权"), ("右弼", "科"), ("天机", "忌")],
    "己": [("武曲", "禄"), ("贪狼", "权"), ("天梁", "科"), ("文曲", "忌")],
    "庚": [("太阳", "禄"), ("武曲", "权"), ("太阴", "科"), ("天同", "忌")],
    "辛": [("巨门", "禄"), ("太阳", "权"), ("文曲", "科"), ("文昌", "忌")],
    "壬": [("天梁", "禄"), ("紫微", "权"), ("左辅", "科"), ("武曲", "忌")],
    "癸": [("破军", "禄"), ("巨门", "权"), ("太阴", "科"), ("贪狼", "忌")],
}

STAR_WX = {
    "紫微": "土", "天机": "木", "太阳": "火", "武曲": "金", "天同": "水", "廉贞": "火",
    "天府": "土", "太阴": "水", "贪狼": "木", "巨门": "水", "天相": "水", "天梁": "土",
    "七杀": "金", "破军": "水",
}


def day_chart(lunar_day: int | None, hour_zhi: str, year_gan: str) -> dict:
    """流日盘。农历日缺失则只给星名表，不落宫。"""
    zhi_i = ZHI.index(hour_zhi) if hour_zhi in ZHI else 6
    palaces = []
    for k in range(12):
        zi = (zhi_i + k) % 12
        palaces.append({
            "zhi": ZHI[zi],
            "name": PALACE_NAMES[k],
            "stars": [],
        })
    note = (
        "缺出生信息与五行局，不能排本命盘。"
        "本图以所选时支为命宫、农历日安紫微，仅为流日示意。"
    )
    if not lunar_day or lunar_day < 1:
        return {
            "ok": True, "empty": True, "palaces": palaces,
            "sihua": _SIHUA.get(year_gan, []),
            "stars": [{"name": n, "wuxing": STAR_WX[n]} for n in STAR_WX],
            "note": note + " 当日无农历对照，不猜测落宫。",
        }
    # 寅起一，顺数农历日 → 紫微落支（简化，不是完整安星）
    ziwei_zhi = (2 + (int(lunar_day) - 1)) % 12
    start = next(i for i, p in enumerate(palaces) if ZHI.index(p["zhi"]) == ziwei_zhi)
    for off, name in enumerate(_ZIWEI_REV):
        if not name:
            continue
        palaces[(start - off) % 12]["stars"].append(name)
    fu_zhi = (ziwei_zhi + 6) % 12
    fu_i = next(i for i, p in enumerate(palaces) if ZHI.index(p["zhi"]) == fu_zhi)
    for off, name in enumerate(_TIANFU_FWD):
        if not name:
            continue
        palaces[(fu_i + off) % 12]["stars"].append(name)
    gan = year_gan[0] if year_gan else ""
    sihua = _SIHUA.get(gan, [])
    hua_map = {n: h for n, h in sihua}
    for p in palaces:
        p["stars_txt"] = "、".join(p["stars"]) or "—"
        p["hua"] = [hua_map[s] for s in p["stars"] if s in hua_map]
    return {
        "ok": True,
        "empty": False,
        "ming_zhi": hour_zhi,
        "ziwei_zhi": ZHI[ziwei_zhi],
        "lunar_day": int(lunar_day),
        "palaces": palaces,
        "sihua": [{"star": n, "hua": h} for n, h in sihua],
        "sihua_note": "按年柱天干作流年四化示意，不是生年四化。",
        "stars": [{"name": n, "wuxing": STAR_WX[n]} for n in STAR_WX],
        "note": note,
    }
