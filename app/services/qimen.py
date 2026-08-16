"""时家奇门排盘（民俗推算）。

阴阳遁以冬至/夏至为界；局数用节气三元表；元按交节日起五日一元。
不是官方奇门历书，不作断事依据。
"""
from __future__ import annotations

from datetime import date

from .almanac import GAN, SOLAR_TERMS, ZHI, day_ganzhi, hour_ganzhi

# 洛书九宫，与黄历九宫同一视觉顺序
PALACES = [
    (4, "巽·东南"), (9, "离·正南"), (2, "坤·西南"),
    (3, "震·正东"), (5, "中宫"), (7, "兑·正西"),
    (8, "艮·东北"), (1, "坎·正北"), (6, "乾·西北"),
]
YI_QI = ["戊", "己", "庚", "辛", "壬", "癸", "丁", "丙", "乙"]
STARS = {1: "天蓬", 2: "天芮", 3: "天冲", 4: "天辅", 5: "天禽",
         6: "天心", 7: "天柱", 8: "天任", 9: "天英"}
DOORS = {1: "休", 2: "死", 3: "伤", 4: "杜", 5: "死", 6: "开", 7: "惊", 8: "生", 9: "景"}
GODS = ["值符", "腾蛇", "太阴", "六合", "白虎", "玄武", "九地", "九天"]
# 八神/八门飞宫（后天八卦，跳过中五）
BAGUA_YANG = [1, 8, 3, 4, 9, 2, 7, 6]
BAGUA_YIN = list(reversed(BAGUA_YANG))

# 节气 → (阳遁?, 上中下元局数)
TERM_JU: dict[str, tuple[bool, tuple[int, int, int]]] = {
    "冬至": (True, (1, 7, 4)), "小寒": (True, (2, 8, 5)), "大寒": (True, (3, 9, 6)),
    "立春": (True, (8, 5, 2)), "雨水": (True, (9, 6, 3)), "惊蛰": (True, (1, 7, 4)),
    "春分": (True, (3, 9, 6)), "清明": (True, (4, 1, 7)), "谷雨": (True, (5, 2, 8)),
    "立夏": (True, (4, 1, 7)), "小满": (True, (5, 2, 8)), "芒种": (True, (6, 3, 9)),
    "夏至": (False, (9, 3, 6)), "小暑": (False, (8, 2, 5)), "大暑": (False, (7, 1, 4)),
    "立秋": (False, (2, 5, 8)), "处暑": (False, (1, 4, 7)), "白露": (False, (9, 3, 6)),
    "秋分": (False, (7, 1, 4)), "寒露": (False, (6, 9, 3)), "霜降": (False, (5, 8, 2)),
    "立冬": (False, (6, 9, 3)), "小雪": (False, (5, 8, 2)), "大雪": (False, (4, 7, 1)),
}


def _gz_index(gz: str) -> int:
    for i in range(60):
        if GAN[i % 10] + ZHI[i % 12] == gz[:2]:
            return i
    return 0


def _xun_shou_yi(gz: str) -> str:
    """甲子戊、甲戌己、甲申庚、甲午辛、甲辰壬、甲寅癸。"""
    return ["戊", "己", "庚", "辛", "壬", "癸"][_gz_index(gz) // 10]


def last_solar_term(d: date) -> tuple[str, date]:
    items: list[tuple[date, str]] = []
    for y in (d.year - 1, d.year):
        for name, m, dd in SOLAR_TERMS:
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
    return last[1], last[0]


def ju_of(d: date) -> dict:
    name, term_date = last_solar_term(d)
    yang, jus = TERM_JU[name]
    yuan = min(2, max(0, (d - term_date).days // 5))
    return {
        "term": name,
        "term_date": term_date.isoformat(),
        "yang": yang,
        "yuan": ("上元", "中元", "下元")[yuan],
        "ju": jus[yuan],
        "days_into": (d - term_date).days,
    }


def _di_pan(ju: int, yang: bool) -> dict[int, str]:
    pos: dict[int, str] = {}
    for i, name in enumerate(YI_QI):
        p = (ju - 1 + i) % 9 + 1 if yang else (ju - 1 - i) % 9 + 1
        pos[p] = name
    return pos


def _fly(start: int, yang: bool, n: int = 9) -> list[int]:
    seq = []
    p = start
    for _ in range(n):
        seq.append(p)
        p = p + 1 if yang else p - 1
        if p > 9:
            p = 1
        if p < 1:
            p = 9
    return seq


def _bagua_fly(start: int, yang: bool) -> list[int]:
    order = BAGUA_YANG if yang else BAGUA_YIN
    if start == 5:
        start = 2  # 中寄坤
    if start not in order:
        start = order[0]
    i = order.index(start)
    return [order[(i + k) % 8] for k in range(8)]


def plate(d: date, hour: int) -> dict:
    """指定公历日 + 北京时间小时的时家奇门盘。"""
    ju = ju_of(d)
    hg = hour_ganzhi(d, hour)
    dgz = day_ganzhi(d)
    yang, jn = ju["yang"], ju["ju"]
    di = _di_pan(jn, yang)
    hour_gan = hg["ganzhi"][0]
    xun_yi = _xun_shou_yi(hg["ganzhi"])
    use_gan = xun_yi if hour_gan == "甲" else hour_gan
    zhi_fu_gong = next((p for p, yi in di.items() if yi == xun_yi), jn)
    shi_gan_gong = next((p for p, yi in di.items() if yi == use_gan), zhi_fu_gong)
    star_home = {p: STARS[p] for p in range(1, 10)}
    zhi_fu_star = star_home[jn]
    star_fly = _fly(zhi_fu_gong, yang)
    dest_fly = _fly(shi_gan_gong, yang)
    tian_star = {}
    tian_yi = {}
    for a, b in zip(star_fly, dest_fly):
        tian_star[b] = star_home[a]
        tian_yi[b] = di[a]
    zhi_shi_door = DOORS[jn]
    door_home = {p: DOORS[p] for p in range(1, 10)}
    door_path = _bagua_fly(zhi_fu_gong, yang)
    dest_path = _bagua_fly(shi_gan_gong, yang)
    tian_door = {5: "中"}
    for a, b in zip(door_path, dest_path):
        tian_door[b] = door_home[a]
    gods = {}
    for i, p in enumerate(_bagua_fly(shi_gan_gong, yang)):
        gods[p] = GODS[i]
    gods[5] = "—"
    cells = []
    for p, label in PALACES:
        cells.append({
            "palace": p,
            "label": label,
            "di": di.get(p, ""),
            "tian": tian_yi.get(p, di.get(p, "")),
            "star": tian_star.get(p, star_home.get(p, "")),
            "door": tian_door.get(p, ""),
            "god": gods.get(p, ""),
            "zhi_fu": p == shi_gan_gong,
        })
    grid = [cells[0:3], cells[3:6], cells[6:9]]
    return {
        "ok": True,
        "kind": "时家奇门",
        "date": d.isoformat(),
        "hour": int(hour) % 24,
        "hour_ganzhi": hg["text"],
        "shichen": hg["name"],
        "zhi_index": hg["zhi_index"],
        "day_ganzhi": f"{dgz}日",
        "ju": ju,
        "zhi_fu_gong": shi_gan_gong,
        "zhi_fu_star": zhi_fu_star,
        "zhi_shi_door": zhi_shi_door,
        "xun_shou": xun_yi,
        "grid": grid,
        "note": "时家奇门：冬至后阳遁、夏至后阴遁；局数按节气三元，元按交节五日一切。民俗推算，不是官方历书。",
    }


def plates_for_day(d: date) -> list[dict]:
    """十二时辰各一盘（每辰取该辰中间小时：子用 0 点）。"""
    hours = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22]
    return [plate(d, h) for h in hours]
