"""奇门遁甲预测选股：按所点时辰起盘，用季节旺相五行对照行业，再叠本地综合评分与策略门槛。

只从本地快照筛选，不足 50 只就返回实际数量，不拿休囚死或其他板块凑数。
民俗推算 + 量化过滤，不构成投资建议，也不写入智能选股涨跌名单。
"""
from __future__ import annotations

from datetime import date

from ..database import query
from . import qimen as qimen_svc
from . import rating
from . import wuxing
from .almanac import (
    _WANGXIANG,
    _season_of,
    resolve_almanac_date,
    shichen_index,
)
from .lunar import solar_to_lunar

_MIN_SCORE = 55.0
_MIN_BUY = 50.0
_LIMIT = 50
_POOL = 400

NOTE = (
    "奇门按所选时辰起时家盘；五行旺相按节气季节。个股来自本地行业五行归类"
    "且综合评分、购买指数达到门槛。不是奇门断涨跌，不构成投资建议。"
)


def _hour_ok(hour) -> int | None:
    if hour is None or hour == "":
        return None
    try:
        return int(hour) % 24
    except (TypeError, ValueError):
        return None


def predict(day: str = "", hour: int | None = None) -> dict:
    d = resolve_almanac_date(day)
    if (day or "").strip() and d is None:
        return {"ok": False, "error": "日期格式无效，请用 YYYY-MM-DD", "stocks": []}
    if d is None:
        return {"ok": False, "error": "日期格式无效，请用 YYYY-MM-DD", "stocks": []}
    hh = _hour_ok(hour)
    if hour is not None and hour != "" and hh is None:
        return {"ok": False, "error": "时辰小时无效", "stocks": []}
    if hh is None:
        hh = 12
    return predict_on(d, hh)


def predict_on(d: date, hour: int) -> dict:
    lunar = solar_to_lunar(d)
    plate = qimen_svc.plate(d, hour)
    season = _season_of(d)
    wx = dict(_WANGXIANG[season])
    favor = [wx["旺"], wx["相"]]
    industries = wuxing.industries_for(favor)
    ju = plate.get("ju") or {}
    context = {
        "date": d.isoformat(),
        "hour": int(hour) % 24,
        "zhi_index": shichen_index(hour),
        "lunar": (lunar.get("full") or lunar.get("text")) if lunar and lunar.get("ok") else "",
        "lunar_ok": bool(lunar and lunar.get("ok")),
        "lunar_note": "" if (lunar and lunar.get("ok")) else "农历超出对照表，奇门仍按公历节气起盘，不猜测农历。",
        "qimen": {
            "shichen": plate.get("shichen"),
            "hour_ganzhi": plate.get("hour_ganzhi"),
            "day_ganzhi": plate.get("day_ganzhi"),
            "yang": ju.get("yang"),
            "ju": ju.get("ju"),
            "yuan": ju.get("yuan"),
            "term": ju.get("term"),
            "zhi_fu_star": plate.get("zhi_fu_star"),
            "zhi_shi_door": plate.get("zhi_shi_door"),
        },
        "season": season,
        "wangxiang": wx,
        "favor": favor,
        "yin_yang": "阳遁" if ju.get("yang") else "阴遁",
        "industries": industries,
    }
    if not industries:
        return {
            "ok": True, **context, "stocks": [], "count": 0,
            "empty_reason": "旺相五行没有对应到本地行业词表，不编造板块个股。",
            "note": NOTE,
        }
    ph = ",".join("?" * len(industries))
    rows = query(
        f"SELECT s.*, l.industry, m.buy_index, m.sentiment AS senti, "
        f"m.dark_power, m.stabilize_score "
        f"FROM stock_snapshot s "
        f"JOIN stock_list l ON l.code=s.code "
        f"LEFT JOIN stock_metrics m ON m.code=s.code "
        f"WHERE s.price IS NOT NULL AND s.pct IS NOT NULL "
        f"AND s.name NOT LIKE '%ST%' AND s.name NOT LIKE '%退%' "
        f"AND l.industry IN ({ph}) "
        f"ORDER BY COALESCE(m.buy_index, 50) DESC, COALESCE(s.pct_d5, 0) DESC "
        f"LIMIT {_POOL}",
        tuple(industries),
    )
    wuxing.tags_for_list(rows)
    favor_set = set(favor)
    picked = []
    for r in rows or []:
        tags = [t for t in (r.get("wuxing") or []) if t in wuxing.WUXING]
        if not favor_set.intersection(tags):
            continue
        score, advice = rating.quick_score(r)
        if score < _MIN_SCORE:
            continue
        if advice == "减持":
            continue
        buy = r.get("buy_index")
        if buy is not None and float(buy) < _MIN_BUY:
            continue
        wx_state = []
        for t in tags:
            for st, el in wx.items():
                if el == t:
                    wx_state.append(f"{t}{st}")
        picked.append({
            "code": r["code"],
            "name": r["name"],
            "price": r.get("price"),
            "pct": r.get("pct"),
            "industry": r.get("industry") or "",
            "wuxing": tags,
            "wx_state": wx_state,
            "score": score,
            "advice": advice,
            "buy_index": buy,
            "stabilize_score": r.get("stabilize_score"),
            "volume_ratio": r.get("volume_ratio"),
            "main_net_in": r.get("main_net_in"),
        })
    picked.sort(key=lambda x: (
        0 if x["advice"] == "增持" else 1,
        -(x["score"] or 0),
        -(x["buy_index"] or 0),
        -(x.get("stabilize_score") or 0),
    ))
    stocks = picked[:_LIMIT]
    empty = ""
    if not stocks:
        empty = (
            f"{season}季旺{wx['旺']}相{wx['相']}的本地行业里，"
            f"没有同时达到综合评分≥{_MIN_SCORE:.0f}、策略非减持"
            f"{'、购买指数≥' + str(int(_MIN_BUY)) if True else ''} 的个股。"
            "不拿休囚死或其他板块凑数。"
        )
    return {
        "ok": True,
        **context,
        "min_score": _MIN_SCORE,
        "min_buy_index": _MIN_BUY,
        "pool": len(rows or []),
        "passed": len(picked),
        "count": len(stocks),
        "stocks": stocks,
        "empty_reason": empty,
        "note": NOTE,
    }
