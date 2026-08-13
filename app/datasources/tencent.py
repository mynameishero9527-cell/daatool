"""腾讯财经数据源（P0）：实时行情、K线、分时、全市场排行（含主力资金）。"""
import re

from .base import tracked_get

SOURCE = "腾讯财经"

_QT_URL = "https://qt.gtimg.cn/q={codes}"
_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},{period},,,{count},qfq"
_MINUTE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/minute/query?code={code}"
_M5_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},m5,,{count}"
_RANK_URL = (
    "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
    "?board_code=aStock&sort_type={sort}&direct={direct}&offset={offset}&count={count}"
)
_INDUSTRY_URL = (
    "https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank"
    "?board_type=hy&sort_type=price&direct=down&offset=0&count=100"
)
_CONCEPT_URL = (
    "https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank"
    "?board_type=gn&sort_type=turnover&direct=down&offset={offset}&count={count}"
)
_BOARD_STOCKS_URL = (
    "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
    "?board_code={board}&sort_type=price&direct=down&offset={offset}&count={count}"
)


def _f(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_quotes(codes: list[str]) -> dict[str, dict]:
    """批量实时行情。codes 形如 sz300432 / sh600519 / sh000001。"""
    if not codes:
        return {}
    resp = tracked_get(SOURCE, _QT_URL.format(codes=",".join(codes)))
    text = resp.content.decode("gbk", errors="replace")
    result: dict[str, dict] = {}
    for match in re.finditer(r'v_(\w+)="([^"]*)"', text):
        code, raw = match.group(1), match.group(2)
        p = raw.split("~")
        if len(p) < 50 or not p[3]:
            continue
        result[code] = {
            "code": code,
            "name": p[1],
            "price": _f(p[3]),
            "prev_close": _f(p[4]),
            "open": _f(p[5]),
            "volume": _f(p[6]),          # 手
            "time": p[30],
            "change": _f(p[31]),
            "pct": _f(p[32]),
            "high": _f(p[33]),
            "low": _f(p[34]),
            "amount": _f(p[37]),         # 万元
            "turnover_rate": _f(p[38]),
            "pe": _f(p[39]),
            "amplitude": _f(p[43]),
            "float_mv": _f(p[44]),       # 亿
            "total_mv": _f(p[45]),       # 亿
            "pb": _f(p[46]),
            "volume_ratio": _f(p[49]),
            "source": SOURCE,
        }
    return result


def fetch_kline(code: str, period: str = "day", count: int = 320) -> list[list]:
    """前复权K线。period: day/week/month。返回 [[date,open,close,high,low,volume],...]"""
    resp = tracked_get(SOURCE, _KLINE_URL.format(code=code, period=period, count=count))
    data = resp.json()
    node = data.get("data", {}).get(code, {})
    rows = node.get(f"qfq{period}") or node.get(period) or []
    out = []
    for r in rows:
        if len(r) >= 6:
            out.append([r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])])
    return out


def fetch_m5_kline(code: str, count: int = 240) -> list[list]:
    """5分钟K线（近5个交易日约240根）。返回 [[hhmm标签, open, close, high, low, volume],...]"""
    resp = tracked_get(SOURCE, _M5_URL.format(code=code, count=count))
    data = resp.json()
    rows = data.get("data", {}).get(code, {}).get("m5") or []
    out = []
    for r in rows:
        if len(r) >= 6:
            ts = str(r[0])  # 202608130935
            label = f"{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}" if len(ts) >= 12 else ts
            out.append([label, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])])
    return out


def fetch_minute(code: str) -> dict:
    """当日分时。返回 {date, prev_close, points: [[hhmm, price, cum_volume], ...]}"""
    resp = tracked_get(SOURCE, _MINUTE_URL.format(code=code))
    data = resp.json().get("data", {}).get(code, {})
    minute = data.get("data", {})
    points = []
    for line in minute.get("data", []):
        seg = line.split()
        if len(seg) >= 3:
            points.append([seg[0], float(seg[1]), float(seg[2])])
    qt = data.get("qt", {}).get(code, [])
    prev_close = float(qt[4]) if len(qt) > 4 and qt[4] else None
    return {"date": minute.get("date", ""), "prev_close": prev_close, "points": points}


def fetch_orderbook(code: str) -> dict | None:
    """盘口：外盘/内盘 + 五档委托（委比）。"""
    resp = tracked_get(SOURCE, _QT_URL.format(codes=code))
    text = resp.content.decode("gbk", errors="replace")
    m = re.search(r'v_(\w+)="([^"]*)"', text)
    if not m:
        return None
    p = m.group(2).split("~")
    if len(p) < 29:
        return None
    bids = [( _f(p[9 + i * 2]), _f(p[10 + i * 2]) ) for i in range(5)]
    asks = [( _f(p[19 + i * 2]), _f(p[20 + i * 2]) ) for i in range(5)]
    bid_vol = sum(v or 0 for _, v in bids)
    ask_vol = sum(v or 0 for _, v in asks)
    total = bid_vol + ask_vol
    outer, inner = _f(p[7]), _f(p[8])
    of_total = (outer or 0) + (inner or 0)
    return {
        "code": m.group(1), "name": p[1], "price": _f(p[3]),
        "outer": outer, "inner": inner,
        "outer_ratio": round((outer or 0) / of_total, 4) if of_total else None,
        "in_out_ratio": round((outer or 0) / inner, 2) if inner else None,
        "bids": bids, "asks": asks,
        "bid_vol": bid_vol, "ask_vol": ask_vol,
        "order_ratio": round((bid_vol - ask_vol) / total * 100, 2) if total else None,  # 委比%
        "time": p[30],
    }


def fetch_industries() -> list[dict]:
    """申万一级行业板块列表（含板块涨跌幅与领涨股）。"""
    resp = tracked_get(SOURCE, _INDUSTRY_URL)
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"industry api code={payload.get('code')}")
    out = []
    for r in payload.get("data", {}).get("rank_list", []) or []:
        lzg = r.get("lzg") or {}
        out.append({
            "board_code": r.get("code", ""),
            "name": r.get("name", ""),
            "pct": _f(r.get("zdf")),
            "turnover_rate": _f(r.get("hsl")),
            "leader_code": lzg.get("code", ""),
            "leader_name": lzg.get("name", ""),
            "leader_pct": _f(lzg.get("zdf")),
        })
    return out


def fetch_concept_boards(offset: int = 0, count: int = 100) -> list[dict]:
    """概念板块列表（按成交额排序）。"""
    resp = tracked_get(SOURCE, _CONCEPT_URL.format(offset=offset, count=count))
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"concept api code={payload.get('code')}")
    out = []
    for r in payload.get("data", {}).get("rank_list", []) or []:
        out.append({
            "board_code": r.get("code", ""),
            "name": r.get("name", ""),
            "pct": _f(r.get("zdf")),
            "turnover": _f(r.get("turnover")),
        })
    return out


def fetch_board_stocks(board_code: str, offset: int = 0, count: int = 200) -> list[str]:
    """板块成分股代码列表。"""
    resp = tracked_get(SOURCE, _BOARD_STOCKS_URL.format(board=board_code, offset=offset, count=count))
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"board stocks api code={payload.get('code')}")
    return [r.get("code", "") for r in payload.get("data", {}).get("rank_list", []) or []]


def fetch_rank_page(sort: str = "price", direct: str = "down", offset: int = 0, count: int = 200) -> list[dict]:
    """全市场 A 股排行页（含主力资金、多周期涨跌幅、估值）。"""
    resp = tracked_get(SOURCE, _RANK_URL.format(sort=sort, direct=direct, offset=offset, count=count))
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"rank api code={payload.get('code')}")
    rows = payload.get("data", {}).get("rank_list", []) or []
    out = []
    for r in rows:
        out.append({
            "code": r.get("code", ""),
            "name": r.get("name", ""),
            "price": _f(r.get("zxj")),
            "pct": _f(r.get("zdf")),
            "turnover_rate": _f(r.get("hsl")),
            "volume_ratio": _f(r.get("lb")),
            "pe_ttm": _f(r.get("pe_ttm")),
            "pb": _f(r.get("pn")),
            "float_mv": _f(r.get("ltsz")),
            "total_mv": _f(r.get("zsz")),
            "main_net_in": _f(r.get("zljlr")),
            "main_in": _f(r.get("zllr")),
            "main_out": _f(r.get("zllc")),
            "main_in_d5": _f(r.get("zllr_d5")),
            "main_out_d5": _f(r.get("zllc_d5")),
            "pct_d5": _f(r.get("zdf_d5")),
            "pct_d10": _f(r.get("zdf_d10")),
            "pct_d20": _f(r.get("zdf_d20")),
            "pct_d60": _f(r.get("zdf_d60")),
            "amount": _f(r.get("turnover")),
        })
    return out
