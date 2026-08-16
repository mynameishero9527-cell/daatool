"""公历转农历（1900-01-31～2100-12-31）。

月大小与闰月采用通用农历编码表（JJonline calendar.js 同源），
不是官方黄历颁发；超出区间返回 None，不猜测。
"""
from __future__ import annotations

from datetime import date

# 1900-2100 每年闰月与十二月大小（201 项）
_LUNAR_INFO = [
    0x04BD8, 0x04AE0, 0x0A570, 0x054D5, 0x0D260, 0x0D950, 0x16554, 0x056A0, 0x09AD0, 0x055D2,
    0x04AE0, 0x0A5B6, 0x0A4D0, 0x0D250, 0x1D255, 0x0B540, 0x0D6A0, 0x0ADA2, 0x095B0, 0x14977,
    0x04970, 0x0A4B0, 0x0B4B5, 0x06A50, 0x06D40, 0x1AB54, 0x02B60, 0x09570, 0x052F2, 0x04970,
    0x06566, 0x0D4A0, 0x0EA50, 0x06E95, 0x05AD0, 0x02B60, 0x186E3, 0x092E0, 0x1C8D7, 0x0C950,
    0x0D4A0, 0x1D8A6, 0x0B550, 0x056A0, 0x1A5B4, 0x025D0, 0x092D0, 0x0D2B2, 0x0A950, 0x0B557,
    0x06CA0, 0x0B550, 0x15355, 0x04DA0, 0x0A5B0, 0x14573, 0x052B0, 0x0A9A8, 0x0E950, 0x06AA0,
    0x0AEA6, 0x0AB50, 0x04B60, 0x0AAE4, 0x0A570, 0x05260, 0x0F263, 0x0D950, 0x05B57, 0x056A0,
    0x096D0, 0x04DD5, 0x04AD0, 0x0A4D0, 0x0D4D4, 0x0D250, 0x0D558, 0x0B540, 0x0B6A0, 0x195A6,
    0x095B0, 0x049B0, 0x0A974, 0x0A4B0, 0x0B27A, 0x06A50, 0x06D40, 0x0AF46, 0x0AB60, 0x09570,
    0x04AF5, 0x04970, 0x064B0, 0x074A3, 0x0EA50, 0x06B58, 0x055C0, 0x0AB60, 0x096D5, 0x092E0,
    0x0C960, 0x0D954, 0x0D4A0, 0x0DA50, 0x07552, 0x056A0, 0x0ABB7, 0x025D0, 0x092D0, 0x0CAB5,
    0x0A950, 0x0B4A0, 0x0BAA4, 0x0AD50, 0x055D9, 0x04BA0, 0x0A5B0, 0x15176, 0x052B0, 0x0A930,
    0x07954, 0x06AA0, 0x0AD50, 0x05B52, 0x04B60, 0x0A6E6, 0x0A4E0, 0x0D260, 0x0EA65, 0x0D530,
    0x05AA0, 0x076A3, 0x096D0, 0x04AFB, 0x04AD0, 0x0A4D0, 0x1D0B6, 0x0D250, 0x0D520, 0x0DD45,
    0x0B5A0, 0x056D0, 0x055B2, 0x049B0, 0x0A577, 0x0A4B0, 0x0AA50, 0x1B255, 0x06D20, 0x0ADA0,
    0x14B63, 0x09370, 0x049F8, 0x04970, 0x064B0, 0x168A6, 0x0EA50, 0x06B20, 0x1A6C4, 0x0AAE0,
    0x0A2E0, 0x0D2E3, 0x0C960, 0x0D557, 0x0D4A0, 0x0DA50, 0x05D55, 0x056A0, 0x0A6D0, 0x055D4,
    0x052D0, 0x0A9B8, 0x0A950, 0x0B4A0, 0x0B6A6, 0x0AD50, 0x055A0, 0x0ABA4, 0x0A5B0, 0x052B0,
    0x0B273, 0x06930, 0x07337, 0x06AA0, 0x0AD50, 0x14B55, 0x04B60, 0x0A570, 0x054E4, 0x0D160,
    0x0E968, 0x0D520, 0x0DAA0, 0x16AA6, 0x056D0, 0x04AE0, 0x0A9D4, 0x0A2D0, 0x0D150, 0x0F252,
    0x0D520,
]
_BASE = date(1900, 1, 31)  # 农历 1900 正月初一
_MONTHS = "正二三四五六七八九十冬腊"
_DAY10 = "一二三四五六七八九十"


def _info(year: int) -> int:
    return _LUNAR_INFO[year - 1900]


def _leap_month(year: int) -> int:
    return _info(year) & 0xF


def _leap_days(year: int) -> int:
    if not _leap_month(year):
        return 0
    return 30 if _info(year) & 0x10000 else 29


def _month_days(year: int, month: int) -> int:
    if month < 1 or month > 12:
        return 0
    return 30 if _info(year) & (0x10000 >> month) else 29


def _year_days(year: int) -> int:
    total = 348
    mask = 0x8000
    while mask > 0x8:
        total += 1 if _info(year) & mask else 0
        mask >>= 1
    return total + _leap_days(year)


def lunar_day_name(day: int) -> str:
    if day < 1 or day > 30:
        return ""
    if day <= 10:
        return "初" + _DAY10[day - 1]
    if day < 20:
        return "十" + _DAY10[day - 11]
    if day == 20:
        return "二十"
    if day < 30:
        return "廿" + _DAY10[day - 21]
    return "三十"


def lunar_month_name(month: int, leap: bool = False) -> str:
    if month < 1 or month > 12:
        return ""
    return ("闰" if leap else "") + _MONTHS[month - 1] + "月"


def solar_to_lunar(d: date) -> dict | None:
    """公历转农历。超出对照表返回 None，不猜日期。"""
    if not isinstance(d, date) or d < _BASE or d > date(2100, 12, 31):
        return None
    if len(_LUNAR_INFO) != 201:
        return None
    offset = (d - _BASE).days
    year = 1900
    while year < 2101 and offset > 0:
        span = _year_days(year)
        offset -= span
        if offset < 0:
            offset += span
            break
        year += 1
    if year > 2100:
        return None
    leap = _leap_month(year)
    is_leap = False
    month = 1
    i = 1
    while i < 13 and offset > 0:
        if leap and i == leap + 1 and not is_leap:
            i -= 1
            is_leap = True
            span = _leap_days(year)
        else:
            span = _month_days(year, i)
        if is_leap and i == leap + 1:
            is_leap = False
        offset -= span
        i += 1
    if offset == 0 and leap and i == leap + 1:
        if is_leap:
            is_leap = False
        else:
            is_leap = True
            i -= 1
    if offset < 0:
        if is_leap and i - 1 == leap:
            span = _leap_days(year)
        else:
            span = _month_days(year, i - 1 if i > 1 else 1)
        offset += span
        i -= 1
        if leap and i == leap and not is_leap:
            is_leap = True
    month = i
    day = offset + 1
    if month < 1 or month > 12 or day < 1 or day > 30:
        return None
    mname = lunar_month_name(month, is_leap)
    dname = lunar_day_name(day)
    return {
        "ok": True,
        "year": year,
        "month": month,
        "day": day,
        "leap": is_leap,
        "month_name": mname,
        "day_name": dname,
        "text": f"{mname}{dname}",
    }
