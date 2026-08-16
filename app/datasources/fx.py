"""各国汇率数据源：即时价走新浪/东财，官方日线走欧洲央行 Frankfurter。

不编造：空包跳过、周末无 ECB 价不插值、离岸人民币不拿在岸价冒充。
"""
from __future__ import annotations

import logging
import re
from datetime import date

from . import sina
from .base import tracked_get

log = logging.getLogger("fx")

SINA_SOURCE = "新浪财经"
EM_SOURCE = "东方财富"
ECB_SOURCE = "欧洲央行"

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FRANKFURTER = "https://api.frankfurter.app/{start}..{end}?from=CNY&to={to}"
_EM_ULIST = (
    "https://push2delay.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2"
    "&secids={secids}&fields=f2,f3,f4,f12,f14,f18"
)
_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://quote.eastmoney.com/forex/USDCNY.html",
}


def _f(value) -> float | None:
    try:
        if value is None or value == "" or value == "-":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _looks_time(s: str) -> bool:
    return bool(_TIME_RE.match((s or "").strip()))


def _looks_date(s: str) -> bool:
    return bool(_DATE_RE.match((s or "").strip()))


def _plausible_rate(v: float | None) -> bool:
    return v is not None and 1e-6 < abs(v) < 1e6


def parse_sina_fx_fields(symbol: str, fields: list[str]) -> dict | None:
    """解析新浪 fx_s* 报价。空串或无数返回 None，不编造。"""
    if not fields or all(not str(x or "").strip() for x in fields):
        return None
    name = ""
    quote_time = ""
    nums: list[float] = []
    for raw in fields:
        s = str(raw or "").strip()
        if not s:
            continue
        if _looks_time(s):
            quote_time = s
            continue
        if _looks_date(s):
            if not quote_time:
                quote_time = s
            continue
        v = _f(s)
        if _plausible_rate(v):
            nums.append(v)
        elif v is None and not name and not s.replace(".", "").replace("-", "").isdigit():
            name = s
    if not nums:
        return None
    last = nums[0]
    prev = None
    for v in nums[1:]:
        if abs(v - last) < 1e-12:
            continue
        if 0.5 * abs(last) <= abs(v) <= 2.0 * abs(last):
            prev = v
            break
    change = round(last - prev, 6) if prev is not None else None
    pct = round((last - prev) / prev * 100, 4) if prev not in (None, 0) else None
    return {
        "symbol": symbol,
        "name": name,
        "rate": last,
        "prev": prev,
        "change": change,
        "pct": pct,
        "time": quote_time,
        "source": SINA_SOURCE,
    }


def fetch_sina_fx(symbols: list[str]) -> dict[str, dict]:
    """新浪外汇即时价。某品种空包则跳过。"""
    codes = [s for s in symbols if s]
    if not codes:
        return {}
    raw = sina._fetch_raw(codes)  # noqa: SLF001 — 复用已有行情通道
    out: dict[str, dict] = {}
    for sym in codes:
        parsed = parse_sina_fx_fields(sym, raw.get(sym) or [])
        if parsed:
            out[sym] = parsed
    return out


def parse_em_fx_diff(rows) -> dict[str, dict]:
    """东财 ulist 外汇。f2 最新、f18 昨收、f3 涨跌幅、f4 涨跌额。缺数字段不编。"""
    out: dict[str, dict] = {}
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        code = str(r.get("f12") or "").strip().upper()
        last = _f(r.get("f2"))
        if not code or not _plausible_rate(last):
            continue
        prev = _f(r.get("f18"))
        change = _f(r.get("f4"))
        pct = _f(r.get("f3"))
        if change is None and last is not None and prev not in (None, 0):
            change = round(last - prev, 6)
        if pct is None and last is not None and prev not in (None, 0):
            pct = round((last - prev) / prev * 100, 4)
        out[code] = {
            "symbol": code,
            "name": str(r.get("f14") or "").strip(),
            "rate": last,
            "prev": prev,
            "change": change,
            "pct": pct,
            "time": "",
            "source": EM_SOURCE,
        }
    return out


def fetch_eastmoney_fx(secids: list[str]) -> dict[str, dict]:
    ids = [s for s in secids if s]
    if not ids:
        return {}
    resp = tracked_get(
        EM_SOURCE,
        _EM_ULIST.format(secids=",".join(ids)),
        headers=_EM_HEADERS,
    )
    data = (resp.json() or {}).get("data") or {}
    return parse_em_fx_diff(data.get("diff") or [])


def parse_frankfurter_cny_timeseries(payload: dict, currencies: list[str]) -> list[dict]:
    """from=CNY 的 timeseries：官方公布日才有点，缺日不插值。

    rates[date][USD] = 1 人民币兑多少美元 → 存 USDCNY = 1/该值。
    不生成 USDCNH。
    """
    out: list[dict] = []
    if not isinstance(payload, dict):
        return out
    rates = payload.get("rates")
    if not isinstance(rates, dict):
        return out
    want = {c.upper() for c in currencies if c and c.upper() not in ("CNY", "CNH")}
    for day, mapping in rates.items():
        if not _looks_date(str(day)) or not isinstance(mapping, dict):
            continue
        try:
            date.fromisoformat(str(day))
        except ValueError:
            continue
        for cur in want:
            raw = mapping.get(cur)
            v = _f(raw)
            if v is None or v <= 0:
                continue
            out.append({
                "pair": f"{cur}CNY",
                "trade_date": str(day)[:10],
                "rate": 1.0 / v,
                "source": ECB_SOURCE,
            })
    out.sort(key=lambda r: (r["pair"], r["trade_date"]))
    return out


def fetch_frankfurter_range(start: date, end: date, currencies: list[str]) -> list[dict]:
    """欧洲央行参考价（Frankfurter 转发）。无 key。周末/假日无点则没有。"""
    to = ",".join(sorted({c.upper() for c in currencies if c and c.upper() not in ("CNY", "CNH")}))
    if not to:
        return []
    url = _FRANKFURTER.format(start=start.isoformat(), end=end.isoformat(), to=to)
    resp = tracked_get(ECB_SOURCE, url, timeout=20.0)
    return parse_frankfurter_cny_timeseries(resp.json() or {}, currencies)
