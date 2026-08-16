"""大六壬课体简推（民俗推算）。

月将按中气太阳过宫；天盘月将加时；四课按日干寄宫。
十二神将按日干阴阳贵人顺逆。三传仅在单一下贼时简推，否则不硬断。
不是官方六壬历书，不作断事依据。
"""
from __future__ import annotations

from datetime import date

from .almanac import GAN, SOLAR_TERMS, ZHI, day_ganzhi, hour_ganzhi, shichen_index
from .lunar import solar_to_lunar

# 中气 → 月将
_ZHONGQI_JIANG = {
    "雨水": "亥", "春分": "戌", "谷雨": "酉", "小满": "申",
    "夏至": "未", "大暑": "午", "处暑": "巳", "秋分": "辰",
    "霜降": "卯", "小雪": "寅", "冬至": "丑", "大寒": "子",
}
# 日干寄宫
_GAN_HOME = {
    "甲": "寅", "乙": "辰", "丙": "巳", "丁": "未", "戊": "巳",
    "己": "未", "庚": "申", "辛": "戌", "壬": "亥", "癸": "丑",
}
# 昼贵 / 夜贵
_GUI = {
    "甲": ("丑", "未"), "戊": ("丑", "未"), "庚": ("丑", "未"),
    "乙": ("子", "申"), "己": ("子", "申"),
    "丙": ("亥", "酉"), "丁": ("亥", "酉"),
    "壬": ("卯", "巳"), "癸": ("卯", "巳"),
    "辛": ("午", "寅"),
}
_YANG_GAN = set("甲丙戊庚壬")
_SHENJIANG = ["贵人", "螣蛇", "朱雀", "六合", "勾陈", "青龙",
              "天空", "白虎", "太常", "玄武", "太阴", "天后"]
_WX = {"子": "水", "丑": "土", "寅": "木", "卯": "木", "辰": "土", "巳": "火",
       "午": "火", "未": "土", "申": "金", "酉": "金", "戌": "土", "亥": "水"}
_KE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}
NOTE = "大六壬为民俗推算：月将取中气过宫，四课按日干寄宫。不是官方历书，不构成投资建议。"


def yuejiang(d: date) -> dict:
    items: list[tuple[date, str]] = []
    for y in (d.year - 1, d.year):
        for name, m, dd in SOLAR_TERMS:
            if name not in _ZHONGQI_JIANG:
                continue
            try:
                items.append((date(y, m, dd), name))
            except ValueError:
                continue
    items.sort()
    last = items[0]
    for dt, name in items:
        if dt <= d:
            last = (dt, name)
        else:
            break
    jiang = _ZHONGQI_JIANG[last[1]]
    return {
        "zhi": jiang,
        "term": last[1],
        "term_date": last[0].isoformat(),
        "days_into": (d - last[0]).days,
    }


def _tian_pan(jiang: str, hour_zhi: str) -> list[str]:
    """月将加于时支，顺布十二支。返回地盘子起的天盘支。"""
    j = ZHI.index(jiang)
    h = ZHI.index(hour_zhi)
    tian = [""] * 12
    for k in range(12):
        tian[(h + k) % 12] = ZHI[(j + k) % 12]
    return tian


def _shang(tian: list[str], zhi: str) -> str:
    return tian[ZHI.index(zhi)]


def for_datetime(d: date, hour: int) -> dict:
    hg = hour_ganzhi(d, hour)
    dgz = day_ganzhi(d)
    gan, zhi = dgz[0], dgz[1]
    hour_zhi = hg["ganzhi"][1]
    yj = yuejiang(d)
    tian = _tian_pan(yj["zhi"], hour_zhi)
    di_tian = [{"di": ZHI[i], "tian": tian[i], "wx": _WX[tian[i]]} for i in range(12)]
    gan_home = _GAN_HOME[gan]
    ke1_shang = _shang(tian, gan_home)
    ke2_shang = _shang(tian, ke1_shang)
    ke3_shang = _shang(tian, zhi)
    ke4_shang = _shang(tian, ke3_shang)
    kes = [
        {"name": "一课", "xia": gan_home, "shang": ke1_shang, "note": f"干阳{gan}寄{gan_home}"},
        {"name": "二课", "xia": ke1_shang, "shang": ke2_shang, "note": "干阳上神"},
        {"name": "三课", "xia": zhi, "shang": ke3_shang, "note": f"日支{zhi}"},
        {"name": "四课", "xia": ke3_shang, "shang": ke4_shang, "note": "支阳上神"},
    ]
    day = shichen_index(hour) in (3, 4, 5, 6, 7, 8)  # 卯-申为昼
    gui_day, gui_night = _GUI[gan]
    gui = gui_day if day else gui_night
    yang = gan in _YANG_GAN
    gods = []
    start = ZHI.index(gui)
    for i, name in enumerate(_SHENJIANG):
        pos = (start + i) % 12 if yang else (start - i) % 12
        gods.append({"god": name, "zhi": ZHI[pos], "wx": _WX[ZHI[pos]]})
    thief = []
    for k in kes:
        w_xia, w_shang = _WX[k["xia"]], _WX[k["shang"]]
        if _KE.get(w_xia) == w_shang:
            thief.append(k)
    san_chuan = []
    san_note = "四课无单一下贼，不硬断三传（比用/涉害未全演）。"
    if len(thief) == 1:
        chu = thief[0]["shang"]
        zhong = _shang(tian, chu)
        mo = _shang(tian, zhong)
        san_chuan = [
            {"name": "初传", "zhi": chu},
            {"name": "中传", "zhi": zhong},
            {"name": "末传", "zhi": mo},
        ]
        san_note = "单一下贼取贼克：初传取所贼上神，中末递取其上神。简推。"
    lunar = solar_to_lunar(d)
    return {
        "ok": True,
        "kind": "大六壬",
        "date": d.isoformat(),
        "hour": int(hour) % 24,
        "zhi_index": hg["zhi_index"],
        "day_ganzhi": f"{dgz}日",
        "hour_ganzhi": hg["text"],
        "lunar": (lunar or {}).get("text") or "",
        "lunar_ok": bool(lunar and lunar.get("ok")),
        "yuejiang": yj,
        "tianpan": di_tian,
        "sike": kes,
        "gui_ren": gui,
        "gui_day": day,
        "yang_gui": yang,
        "shenjiang": gods,
        "san_chuan": san_chuan,
        "san_chuan_note": san_note,
        "note": NOTE,
    }


def plates_for_day(d: date) -> list[dict]:
    hours = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22]
    return [for_datetime(d, h) for h in hours]
