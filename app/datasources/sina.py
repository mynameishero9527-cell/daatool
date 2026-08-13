"""新浪财经数据源（P1）：行情备源、全球指数、大宗商品、7x24 快讯。"""
import re

from .base import tracked_get

SOURCE = "新浪财经"

_HQ_URL = "https://hq.sinajs.cn/list={codes}"
_NEWS_URL = "https://zhibo.sina.com.cn/api/zhibo/feed?page={page}&page_size={size}&zhibo_id=152"
_HQ_HEADERS = {"Referer": "https://finance.sina.com.cn"}


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_raw(codes: list[str]) -> dict[str, list[str]]:
    resp = tracked_get(SOURCE, _HQ_URL.format(codes=",".join(codes)), headers=_HQ_HEADERS)
    text = resp.content.decode("gbk", errors="replace")
    out = {}
    for m in re.finditer(r'var hq_str_(\w+)="([^"]*)"', text):
        out[m.group(1)] = m.group(2).split(",")
    return out


def fetch_quotes(codes: list[str]) -> dict[str, dict]:
    """A股实时行情备源。codes 形如 sz300432。"""
    raw = _fetch_raw(codes)
    result = {}
    for code, p in raw.items():
        if len(p) < 32 or not p[3]:
            continue
        price, prev_close = _f(p[3]), _f(p[2])
        pct = round((price - prev_close) / prev_close * 100, 2) if price and prev_close else None
        result[code] = {
            "code": code,
            "name": p[0],
            "price": price,
            "prev_close": prev_close,
            "open": _f(p[1]),
            "high": _f(p[4]),
            "low": _f(p[5]),
            "volume": (_f(p[8]) or 0) / 100,       # 股 → 手
            "amount": (_f(p[9]) or 0) / 10000,     # 元 → 万元
            "change": round(price - prev_close, 3) if price and prev_close else None,
            "pct": pct,
            "time": f"{p[30]} {p[31]}",
            "turnover_rate": None, "pe": None, "pb": None,
            "amplitude": None, "float_mv": None, "total_mv": None, "volume_ratio": None,
            "source": SOURCE,
        }
    return result


def fetch_global_indices(symbols: list[str]) -> dict[str, dict]:
    """全球指数。gb_xxx（美股指数）与 int_xxx（国际指数）两种格式。"""
    raw = _fetch_raw(symbols)
    result = {}
    for sym, p in raw.items():
        if sym.startswith("gb_") and len(p) >= 8:
            result[sym] = {
                "symbol": sym, "name": p[0],
                "price": _f(p[1]), "pct": _f(p[2]), "change": _f(p[4]),
                "time": p[3],
            }
        elif sym.startswith("int_") and len(p) >= 4:
            result[sym] = {
                "symbol": sym, "name": p[0],
                "price": _f(p[1]), "change": _f(p[2]), "pct": _f(p[3]),
                "time": "",
            }
    return result


def fetch_commodities(symbols: list[str]) -> dict[str, dict]:
    """国际大宗商品（hf_ 前缀）。"""
    raw = _fetch_raw(symbols)
    result = {}
    for sym, p in raw.items():
        if not sym.startswith("hf_") or len(p) < 14 or not p[0]:
            continue
        price = _f(p[0])
        prev = _f(p[7])  # 昨收
        pct = round((price - prev) / prev * 100, 2) if price and prev else None
        result[sym] = {
            "symbol": sym,
            "name": p[13],
            "price": price,
            "prev_close": prev,
            "high": _f(p[4]),
            "low": _f(p[5]),
            "pct": pct,
            "time": f"{p[12]} {p[6]}",
        }
    return result


def fetch_news(page: int = 1, size: int = 50) -> list[dict]:
    """新浪财经 7x24 快讯。"""
    resp = tracked_get(SOURCE, _NEWS_URL.format(page=page, size=size))
    data = resp.json()
    items = (
        data.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
    ) or []
    out = []
    for it in items:
        text = it.get("rich_text", "") or ""
        out.append({
            "id": str(it.get("id", "")),
            "text": text,
            "time": it.get("create_time", ""),
            "tags": [t.get("name", "") for t in (it.get("tag") or [])],
        })
    return out
