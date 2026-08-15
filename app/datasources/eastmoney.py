"""东方财富：板块主力资金日K、全市场成交额。"""
import json
import logging
import time

import httpx

from .base import tracked_get

SOURCE = "东方财富"
log = logging.getLogger("eastmoney")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://data.eastmoney.com/bkzj/hy.html",
}
_CLIST = (
    "https://push2delay.eastmoney.com/api/qt/clist/get"
    "?fid=f62&po=1&pz={pz}&pn=1&np=1&fltt=2&invt=2&fs={fs}&fields=f12,f14,f62,f6"
)
_FFLOW_PATH = (
    "/api/qt/stock/fflow/daykline/get?lmt={lmt}&klt=101&secid=90.{code}"
    "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
    "&ut=b2884a393a59ad64002292a3e90d46a5"
)
_FFLOW_HOSTS = (
    "https://push2his.eastmoney.com",
    "https://94.push2his.eastmoney.com",
    "https://88.push2his.eastmoney.com",
    "https://push2delay.eastmoney.com",
)
_ULIST = (
    "https://push2delay.eastmoney.com/api/qt/ulist.np/get?fltt=2"
    "&secids=1.000001,0.399001,0.899050,1.000985"
    "&fields=f12,f13,f14,f6"
)
FS_HY = "m:90+t:2"
FS_GN = "m:90+t:3"


def _f(v) -> float | None:
    try:
        if v is None or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_board_list(kind: str = "industry", pz: int = 200) -> list[dict]:
    """板块资金榜。kind=industry 行业 / concept 概念。net_in 万元，amount 万元。"""
    fs = FS_HY if kind != "concept" else FS_GN
    resp = tracked_get(SOURCE, _CLIST.format(pz=max(20, min(int(pz or 200), 500)), fs=fs),
                       headers=_HEADERS)
    data = (resp.json() or {}).get("data") or {}
    rows = data.get("diff") or []
    out = []
    seen: set[str] = set()
    for r in rows:
        name = (r.get("f14") or "").strip()
        code = (r.get("f12") or "").strip()
        if not name or not code or name in seen:
            continue
        seen.add(name)
        net = _f(r.get("f62"))
        amt = _f(r.get("f6"))
        out.append({
            "name": name,
            "board_code": code,
            "net_in": round(net / 10000.0, 2) if net is not None else 0.0,  # 元 → 万元
            "amount": round(amt / 10000.0, 2) if amt is not None else 0.0,
        })
    return out


def _parse_json_maybe_jsonp(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {}
    if raw[0] not in "{[":
        l, r = raw.find("("), raw.rfind(")")
        if l >= 0 and r > l:
            raw = raw[l + 1:r]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _his_get(url: str) -> dict:
    """独立短连接拉日K，避免长连接被对端掐掉。"""
    last: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=8.0, headers=_HEADERS, follow_redirects=True) as client:
                resp = client.get(url, params={"cb": "jQuery"})
                resp.raise_for_status()
                data = _parse_json_maybe_jsonp(resp.text)
                if data:
                    return data
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.3 * (attempt + 1))
    if last:
        raise last
    return {}


def fetch_board_fflow_history(board_code: str, lookback: int = 40) -> list[dict]:
    """板块主力资金日K。net_in 万元。不把涨跌幅当资金。"""
    code = (board_code or "").strip()
    if not code:
        return []
    lmt = max(5, min(int(lookback or 40), 120))
    best: list[dict] = []
    last_exc: Exception | None = None
    for host in _FFLOW_HOSTS:
        url = host + _FFLOW_PATH.format(lmt=lmt, code=code)
        try:
            body = _his_get(url)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
        parsed = _parse_fflow_klines(((body.get("data") or {}).get("klines") or []))
        if len(parsed) > len(best):
            best = parsed
        if len(best) >= min(5, lmt):
            break
    if not best and last_exc:
        log.warning("板块资金日K %s 失败: %s", code, last_exc)
    return best


def _parse_fflow_klines(klines: list) -> list[dict]:
    out = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 2:
            continue
        day = parts[0].strip()
        if len(day) < 10:
            continue
        net = _f(parts[1])
        close = _f(parts[11]) if len(parts) > 11 else None
        pct = _f(parts[12]) if len(parts) > 12 else None
        out.append({
            "trade_date": day[:10],
            "net_in": round(net / 10000.0, 2) if net is not None else 0.0,
            "close": close,
            "pct": pct,
        })
    return out


def fetch_market_amounts() -> dict:
    """全A/沪/深/北成交额（亿元）。优先中证全指作为全A量能总额。"""
    resp = tracked_get(SOURCE, _ULIST, headers=_HEADERS)
    rows = ((resp.json() or {}).get("data") or {}).get("diff") or []
    by = {str(r.get("f12") or ""): r for r in rows}

    def yi(code: str) -> float | None:
        yuan = _f((by.get(code) or {}).get("f6"))
        return round(yuan / 1e8, 1) if yuan else None

    sh, sz, bj, csi = yi("000001"), yi("399001"), yi("899050"), yi("000985")
    total = csi if csi else round((sh or 0) + (sz or 0) + (bj or 0), 1)
    return {
        "amount_yi": total, "sh_amount_yi": sh, "sz_amount_yi": sz,
        "bj_amount_yi": bj, "csi_amount_yi": csi, "source": SOURCE,
    }


_F10_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://emweb.securities.eastmoney.com/",
    "Accept": "application/json,text/plain,*/*",
}
_F10_SHAREHOLDER = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={code}"
)


def f10_code(code: str) -> str | None:
    """本地 sh600519 → 东方财富 F10 的 SH600519。"""
    s = (code or "").strip().lower()
    if len(s) == 8 and s[:2] in ("sh", "sz", "bj") and s[2:].isdigit():
        return s[:2].upper() + s[2:]
    return None


def fetch_shareholders(code: str) -> dict:
    """F10 股东研究一次性 JSON：户数、实控人、机构构成、十大股东等。缺数返回空 dict。"""
    em = f10_code(code)
    if not em:
        return {}
    url = _F10_SHAREHOLDER.format(code=em)
    last: Exception | None = None
    for attempt in range(3):
        try:
            resp = tracked_get(SOURCE, url, headers=_F10_HEADERS, timeout=12.0)
            data = resp.json()
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.3 * (attempt + 1))
    if last:
        log.warning("F10 股东研究 %s 失败: %s", em, last)
        raise last
    return {}
