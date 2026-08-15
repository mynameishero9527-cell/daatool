"""新浪财经数据源（P1）：行情备源、全球指数、大宗商品、7x24 快讯。"""
import re

from .base import tracked_get

SOURCE = "新浪财经"

_HQ_URL = "https://hq.sinajs.cn/list={codes}"
_NEWS_URL = "https://zhibo.sina.com.cn/api/zhibo/feed?page={page}&page_size={size}&zhibo_id=152"
_FINANCE_URL = (
    "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
    "?paperCode={code}&source=gjzb&type=0&page=1&num={num}"
)
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


def fetch_domestic_futures(symbols: list[str]) -> dict[str, dict]:
    """国内商品期货主力合约（nf_ 前缀）。"""
    raw = _fetch_raw(symbols)
    result = {}
    for sym, p in raw.items():
        if not sym.startswith("nf_") or len(p) < 15 or not p[8]:
            continue
        price = _f(p[8])
        prev_settle = _f(p[10])
        pct = round((price - prev_settle) / prev_settle * 100, 2) if price and prev_settle else None
        result[sym] = {
            "symbol": sym, "name": p[0],
            "price": price, "prev_close": prev_settle,
            "open": _f(p[2]), "high": _f(p[3]), "low": _f(p[4]),
            "pct": pct, "time": f"{p[17] if len(p) > 17 else ''} {p[1]}",
        }
    return result


_GLOBAL_KLINE_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_=/"
    "GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={symbol}"
)
_INNER_KLINE_URL = (
    "https://stock.finance.sina.com.cn/futures/api/jsonp.php/var%20_=/"
    "InnerFuturesNewService.getDailyKLine?symbol={symbol}"
)


def fetch_futures_kline(symbol: str, inner: bool, count: int = 250) -> list[list]:
    """商品期货日K：[[date,open,close,high,low,volume],...]（取最近 count 根）。"""
    import json as _json
    url = (_INNER_KLINE_URL if inner else _GLOBAL_KLINE_URL).format(symbol=symbol)
    resp = tracked_get(SOURCE, url, headers=_HQ_HEADERS)
    text = resp.text
    start, end = text.find("(["), text.rfind("])")
    if start < 0 or end < 0:
        return []
    rows = _json.loads(text[start + 1:end + 1])
    out = []
    for r in rows[-count:]:
        try:
            if inner:
                out.append([r["d"], float(r["o"]), float(r["c"]), float(r["h"]), float(r["l"]), float(r["v"])])
            else:
                out.append([r["date"], float(r["open"]), float(r["close"]),
                            float(r["high"]), float(r["low"]), float(r["volume"])])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch_finance_reports(code: str, num: int = 6) -> list[dict]:
    """财务报告关键指标（真实披露数据）。code 为 6 位数字代码。

    返回按报告期倒序：[{report_date, report_name, revenue, revenue_yoy,
                       net_profit, net_profit_yoy, parent_profit, parent_profit_yoy}]
    """
    resp = tracked_get(SOURCE, _FINANCE_URL.format(code=code, num=num), headers=_HQ_HEADERS)
    data = resp.json().get("result", {}).get("data") or {}
    dates = data.get("report_date") or []
    reports = data.get("report_list") or {}
    field_map = {"BIZTOTINCO": "revenue", "NETPROFIT": "net_profit", "PARENETP": "parent_profit"}
    out = []
    for d in dates:
        key = d.get("date_value", "")
        rep = reports.get(key) or {}
        row = {"report_date": key, "report_name": d.get("date_description", "")}
        for item in rep.get("data", []):
            field = field_map.get(item.get("item_field"))
            if not field:
                continue
            try:
                row[field] = float(item.get("item_value"))
            except (TypeError, ValueError):
                row[field] = None
            tongbi = item.get("item_tongbi")
            row[f"{field}_yoy"] = round(tongbi * 100, 2) if isinstance(tongbi, (int, float)) else None
        if "revenue" in row or "net_profit" in row:
            out.append(row)
    return out


_BKZJ_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "MoneyFlow.ssl_bkzj_bk?page={page}&num={num}&sort=netamount&asc=0&fenlei={fenlei}"
)
_BKZJ_HEADERS = {"Referer": "https://vip.stock.finance.sina.com.cn/moneyflow/"}


def fetch_board_moneyflow(fenlei: int = 0, pages: int = 3, num: int = 80) -> list[dict]:
    """新浪板块资金流向（独立源）。fenlei=0 行业，1 概念。net_in 单位：万元。"""
    out: list[dict] = []
    seen: set[str] = set()
    for page in range(1, max(1, pages) + 1):
        resp = tracked_get(
            SOURCE, _BKZJ_URL.format(page=page, num=num, fenlei=int(fenlei)),
            headers=_BKZJ_HEADERS)
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            break
        for r in rows:
            name = (r.get("name") or "").strip()
            cat = (r.get("category") or "").strip()
            key = cat or name
            if not name or key in seen:
                continue
            seen.add(key)
            net = _f(r.get("netamount"))
            turnover = _f(r.get("turnover"))
            # netamount 为元；turnover 约为亿元
            out.append({
                "name": name,
                "board_code": cat,
                "net_in": round(net / 10000.0, 2) if net is not None else 0.0,
                "amount": round(turnover * 10000.0, 2) if turnover is not None else 0.0,
                "leader": r.get("ts_name") or "",
            })
        if len(rows) < num:
            break
    return out


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
