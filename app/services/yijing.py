"""易经八卦与六爻卜卦（民俗推算）。

日课用梅花易数：上卦取年+月+日，下卦再加时辰，动爻取总和。
卜卦按钮用三钱法连卜六次，六爻数字做排列后只匹配本地已有代码。
不是官方周易断事，不构成投资建议。
"""
from __future__ import annotations

import itertools
import secrets
from datetime import date

from ..database import query
from .almanac import resolve_almanac_date, shichen_index
from .lunar import solar_to_lunar

TRIGRAMS = ("乾", "兑", "离", "震", "巽", "坎", "艮", "坤")
TRI_WX = {"乾": "金", "兑": "金", "离": "火", "震": "木",
          "巽": "木", "坎": "水", "艮": "土", "坤": "土"}
# 自下而上三爻：1阳0阴 → 卦名
_TRI_BITS = {
    (1, 1, 1): "乾", (1, 1, 0): "兑", (1, 0, 1): "离", (1, 0, 0): "震",
    (0, 1, 1): "巽", (0, 1, 0): "坎", (0, 0, 1): "艮", (0, 0, 0): "坤",
}
# 文王序：上卦、下卦
_HEX = [
    (1, "乾", "乾", "乾", "元亨利贞，刚健运行"),
    (2, "坤", "坤", "坤", "厚德载物，顺承而行"),
    (3, "屯", "坎", "震", "起始艰难，宜守不宜躁"),
    (4, "蒙", "艮", "坎", "启蒙求教，勿妄进"),
    (5, "需", "坎", "乾", "有所待，不可强求"),
    (6, "讼", "乾", "坎", "争讼耗神，宜和解"),
    (7, "师", "坤", "坎", "众行有律，慎用刚"),
    (8, "比", "坎", "坤", "亲辅得众，择善而从"),
    (9, "小畜", "巽", "乾", "密云不雨，积小成"),
    (10, "履", "乾", "兑", "如履虎尾，敬慎则吉"),
    (11, "泰", "坤", "乾", "天地交，小往大来"),
    (12, "否", "乾", "坤", "闭塞不通，宜守静"),
    (13, "同人", "乾", "离", "与人和同，出门有功"),
    (14, "大有", "离", "乾", "所有既大，宜谦受益"),
    (15, "谦", "坤", "艮", "谦尊而光，有终"),
    (16, "豫", "震", "坤", "顺时而乐，勿耽逸"),
    (17, "随", "兑", "震", "随时而动，从善如流"),
    (18, "蛊", "艮", "巽", "振弊革新，先甲后甲"),
    (19, "临", "坤", "兑", "以上临下，教思无穷"),
    (20, "观", "巽", "坤", "观而化，先自省"),
    (21, "噬嗑", "离", "震", "啮合除梗，明罚"),
    (22, "贲", "艮", "离", "文饰得宜，不可过"),
    (23, "剥", "艮", "坤", "阴盛剥阳，宜止"),
    (24, "复", "坤", "震", "一阳来复，反复其道"),
    (25, "无妄", "乾", "震", "无妄则亨，勿贪非分"),
    (26, "大畜", "艮", "乾", "积蓄其德，止健"),
    (27, "颐", "艮", "震", "自养养人，慎言语"),
    (28, "大过", "兑", "巽", "栋桡过重，独立不惧"),
    (29, "坎", "坎", "坎", "习坎有孚，行险而不失"),
    (30, "离", "离", "离", "附丽得明，宜中正"),
    (31, "咸", "兑", "艮", "二气感应，以虚受人"),
    (32, "恒", "震", "巽", "恒久不易，立不易方"),
    (33, "遁", "乾", "艮", "退避得时，勿恋栈"),
    (34, "大壮", "震", "乾", "雷在天上，壮而勿用壮"),
    (35, "晋", "离", "坤", "明出地上，昼日三接"),
    (36, "明夷", "坤", "离", "明入地中，用晦而明"),
    (37, "家人", "巽", "离", "正家而天下定"),
    (38, "睽", "离", "兑", "乖异之中求同"),
    (39, "蹇", "坎", "艮", "见险而止，反身修德"),
    (40, "解", "震", "坎", "险以动，赦过宥罪"),
    (41, "损", "艮", "兑", "损下益上，惩忿窒欲"),
    (42, "益", "巽", "震", "损上益下，迁善改过"),
    (43, "夬", "兑", "乾", "决而和，露其疵"),
    (44, "姤", "乾", "巽", "遇而不交，女壮勿用"),
    (45, "萃", "兑", "坤", "聚而相见，除戎器"),
    (46, "升", "坤", "巽", "积小高大，顺势而升"),
    (47, "困", "兑", "坎", "困而不失其所亨"),
    (48, "井", "坎", "巽", "井养不穷，改邑不改井"),
    (49, "革", "兑", "离", "水火相息，顺天应人"),
    (50, "鼎", "离", "巽", "以木巽火，亨饪养贤"),
    (51, "震", "震", "震", "洊雷，恐惧修省"),
    (52, "艮", "艮", "艮", "时止则止，不获其身"),
    (53, "渐", "巽", "艮", "循序而进，女归吉"),
    (54, "归妹", "震", "兑", "征凶无攸利，宜正位"),
    (55, "丰", "震", "离", "宜日中，勿忧宜照"),
    (56, "旅", "离", "艮", "旅贞吉，柔得中"),
    (57, "巽", "巽", "巽", "申命行事，柔皆顺"),
    (58, "兑", "兑", "兑", "丽泽相益，朋友讲习"),
    (59, "涣", "巽", "坎", "风行水上，散而能聚"),
    (60, "节", "坎", "兑", "苦节不可贞，中正"),
    (61, "中孚", "巽", "兑", "信及豚鱼，虚中"),
    (62, "小过", "震", "艮", "可小事不可大事"),
    (63, "既济", "坎", "离", "初吉终乱，思患预防"),
    (64, "未济", "离", "坎", "未济而亨，慎辨物"),
]
HEX_BY_TRI = {(u, l): {"num": n, "name": name, "upper": u, "lower": l, "brief": brief}
              for n, name, u, l, brief in _HEX}
YAO_NAME = {6: "老阴", 7: "少阳", 8: "少阴", 9: "老阳"}

NOTE = "易经为民俗文化参考，卦辞是简述不是官方断语，不构成投资建议。"


def _mei_tri(n: int) -> str:
    return TRIGRAMS[(int(n) - 1) % 8]


def _hex_of(upper: str, lower: str) -> dict:
    rec = HEX_BY_TRI.get((upper, lower))
    if rec:
        return dict(rec)
    return {"num": 0, "name": f"{upper}{lower}", "upper": upper, "lower": lower, "brief": "未入文王序表"}


def _trigram_from_bits(bits: tuple[int, int, int]) -> str:
    return _TRI_BITS.get(tuple(1 if b else 0 for b in bits), "坤")


def _yao_bits(yaos: list[dict], changing: bool = False) -> list[int]:
    out = []
    for y in yaos:
        yang = bool(y.get("yang"))
        if changing and y.get("changing"):
            yang = not yang
        out.append(1 if yang else 0)
    return out


def hexagram_from_yaos(yaos: list[dict]) -> dict:
    """六爻自下而上成卦；老阴老阳取变卦。"""
    if len(yaos) != 6:
        return {"ok": False, "error": "须卜六次得六爻"}
    bits = _yao_bits(yaos, False)
    chg = _yao_bits(yaos, True)
    lower = _trigram_from_bits((bits[0], bits[1], bits[2]))
    upper = _trigram_from_bits((bits[3], bits[4], bits[5]))
    base = _hex_of(upper, lower)
    changed = None
    if any(y.get("changing") for y in yaos):
        cl = _trigram_from_bits((chg[0], chg[1], chg[2]))
        cu = _trigram_from_bits((chg[3], chg[4], chg[5]))
        changed = _hex_of(cu, cl)
    return {
        "ok": True,
        "ben": base,
        "bian": changed,
        "upper": upper, "lower": lower,
        "upper_wx": TRI_WX.get(upper, ""),
        "lower_wx": TRI_WX.get(lower, ""),
        "changing_count": sum(1 for y in yaos if y.get("changing")),
    }


def yao_from_coins(coins: list[int]) -> dict:
    """三钱：2背3字，和为 6老阴 7少阳 8少阴 9老阳。"""
    if len(coins) != 3 or any(c not in (2, 3) for c in coins):
        raise ValueError("须三枚正反钱")
    total = int(sum(coins))
    yang = total in (7, 9)
    changing = total in (6, 9)
    bit = (coins[0] - 2) + (coins[1] - 2) * 2 + (coins[2] - 2) * 4
    return {
        "coins": list(coins),
        "sum": total,
        "yao": total,
        "name": YAO_NAME[total],
        "yang": yang,
        "changing": changing,
        "digit": bit,  # 0-7，来自三钱正反
    }


def one_yao(rng=None) -> dict:
    pick = rng.randrange if rng is not None else secrets.randbelow
    coins = [2 + int(pick(2)) for _ in range(3)]
    return yao_from_coins(coins)


def _perm_codes(digits: list[int]) -> list[str]:
    raw = [str(int(x) % 10) for x in digits]
    if len(raw) != 6:
        return []
    out = {"".join(raw), "".join(reversed(raw))}
    for p in set(itertools.permutations(raw)):
        out.add("".join(p))
        if len(out) >= 720:
            break
    return sorted(out)


def codes_from_yaos(yaos: list[dict]) -> dict:
    """六组爻数做排列，生成候选六位数字。"""
    yao_digits = [int(y.get("yao") or 0) % 10 for y in yaos]
    bit_digits = [int(y.get("digit") or 0) % 10 for y in yaos]
    codes = []
    seen = set()
    for seq in (yao_digits, bit_digits):
        for c in _perm_codes(seq):
            if c not in seen:
                seen.add(c)
                codes.append(c)
    return {
        "yao_digits": yao_digits,
        "bit_digits": bit_digits,
        "candidates": codes,
        "candidate_count": len(codes),
    }


def match_stock_codes(digits_list: list[str]) -> list[dict]:
    """只查本地 stock_list。对不上的号码不生成个股。"""
    nums = []
    seen = set()
    for raw in digits_list or []:
        s = "".join(ch for ch in str(raw) if ch.isdigit())
        if len(s) != 6 or s in seen:
            continue
        seen.add(s)
        nums.append(s)
    if not nums:
        return []
    matched = []
    for i in range(0, len(nums), 200):
        chunk = nums[i:i + 200]
        ph = ",".join("?" * len(chunk))
        rows = query(
            f"SELECT l.code, l.name, COALESCE(l.industry,'') AS industry, "
            f"s.price, s.pct, s.volume_ratio, s.main_net_in, m.buy_index "
            f"FROM stock_list l "
            f"LEFT JOIN stock_snapshot s ON s.code=l.code "
            f"LEFT JOIN stock_metrics m ON m.code=l.code "
            f"WHERE substr(l.code, 3) IN ({ph})",
            tuple(chunk),
        )
        matched.extend(rows or [])
    out, got = [], set()
    for r in matched:
        code = r.get("code") or ""
        if not code or code in got:
            continue
        got.add(code)
        out.append({
            "code": code,
            "name": r.get("name") or "",
            "industry": r.get("industry") or "",
            "price": r.get("price"),
            "pct": r.get("pct"),
            "volume_ratio": r.get("volume_ratio"),
            "buy_index": r.get("buy_index"),
            "digits": (code[2:] if len(code) >= 8 else code),
        })
    return out


def for_datetime(d: date, hour: int) -> dict:
    """梅花易数日课：有农历用农历年，否则只用公历年月日，不猜农历。"""
    lunar = solar_to_lunar(d)
    if lunar and lunar.get("ok"):
        year_n = int(lunar["year"])
        month_n = int(lunar["month"])
        day_n = int(lunar["day"])
        lunar_note = f"农历{lunar.get('text') or ''}"
        used_lunar = True
    else:
        year_n, month_n, day_n = d.year, d.month, d.day
        lunar_note = "农历超出对照表，梅花只用公历年月日，不猜测"
        used_lunar = False
    zhi_i = shichen_index(hour)
    shi = zhi_i + 1
    up_n = (year_n + month_n + day_n) % 8 or 8
    low_n = (year_n + month_n + day_n + shi) % 8 or 8
    dong = (year_n + month_n + day_n + shi) % 6 or 6
    upper, lower = _mei_tri(up_n), _mei_tri(low_n)
    ben = _hex_of(upper, lower)
    bagua = [{"name": n, "wuxing": TRI_WX[n], "mei": i + 1} for i, n in enumerate(TRIGRAMS)]
    return {
        "ok": True,
        "kind": "梅花易数日课",
        "date": d.isoformat(),
        "hour": int(hour) % 24,
        "zhi_index": zhi_i,
        "used_lunar": used_lunar,
        "lunar_note": lunar_note,
        "upper_num": up_n, "lower_num": low_n, "dong_yao": dong,
        "ben": ben,
        "upper": upper, "lower": lower,
        "upper_wx": TRI_WX[upper], "lower_wx": TRI_WX[lower],
        "bagua": bagua,
        "jiugong_map": "后天八卦：巽东南、离正南、坤西南、震正东、兑正西、艮东北、坎正北、乾西北。中宫不用卦。",
        "note": NOTE,
    }


def plates_for_day(d: date) -> list[dict]:
    hours = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22]
    return [for_datetime(d, h) for h in hours]


def result_from_yaos(yaos: list[dict], d: date | None = None, hour: int | None = None) -> dict:
    gua = hexagram_from_yaos(yaos)
    pack = codes_from_yaos(yaos)
    stocks = match_stock_codes(pack["candidates"])
    lines = []
    for i, y in enumerate(yaos, 1):
        lines.append({
            "idx": i,
            "name": y.get("name"),
            "yao": y.get("yao"),
            "digit": y.get("digit"),
            "yang": bool(y.get("yang")),
            "changing": bool(y.get("changing")),
            "coins": y.get("coins") or [],
        })
    return {
        "ok": True,
        "kind": "三钱六爻",
        "date": d.isoformat() if d else "",
        "hour": hour,
        "yaos": lines,
        "gua": gua,
        "yao_digits": pack["yao_digits"],
        "bit_digits": pack["bit_digits"],
        "candidate_count": pack["candidate_count"],
        "matched_count": len(stocks),
        "unmatched_count": max(0, pack["candidate_count"] - len(stocks)),
        "stocks": stocks,
        "empty_reason": "" if stocks else "六个爻数排列后的号码在本地股票列表没有对应代码，未匹配的不显示。",
        "method": "三钱法连卜六次（自下而上）。6老阴、7少阳、8少阴、9老阳；老阴老阳为动爻。六位数字取爻数(6-9)与三钱正反(0-7)两组，做全排列后只对照本地代码。",
        "note": NOTE,
    }


def cast_six(d: date | None = None, hour: int | None = None, rng=None) -> dict:
    yaos = [one_yao(rng) for _ in range(6)]
    return result_from_yaos(yaos, d, hour)


def divination(day: str = "", hour: int | None = None, rng=None) -> dict:
    d = resolve_almanac_date(day) if (day or "").strip() else None
    if (day or "").strip() and d is None:
        return {"ok": False, "error": "日期格式无效，请用 YYYY-MM-DD", "stocks": []}
    hh = None
    if hour is not None:
        try:
            hh = int(hour) % 24
        except (TypeError, ValueError):
            return {"ok": False, "error": "时辰小时无效", "stocks": []}
    return cast_six(d, hh, rng)
