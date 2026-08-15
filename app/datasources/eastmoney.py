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


_F10_SOURCE = "东方财富F10"
_F10_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://emweb.securities.eastmoney.com/",
    "Accept": "application/json,text/plain,*/*",
}
_DC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://data.eastmoney.com/",
    "Accept": "application/json,text/plain,*/*",
}
_F10_SHAREHOLDER = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={code}"
)
_DC_WEB = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_DC_SEC_HOST = "https://datacenter.eastmoney.com/securities/api/data/v1/get"


def f10_code(code: str) -> str | None:
    """本地 sh600519 → 东方财富 F10 的 SH600519。"""
    s = (code or "").strip().lower()
    if len(s) == 8 and s[:2] in ("sh", "sz", "bj") and s[2:].isdigit():
        return s[:2].upper() + s[2:]
    return None


def _security_code(code: str) -> str | None:
    s = (code or "").strip().lower()
    if len(s) >= 6 and s[-6:].isdigit():
        return s[-6:]
    return None


def _shareholder_raw_ok(raw: dict | None) -> bool:
    if not isinstance(raw, dict) or not raw:
        return False
    for key in ("gdrs", "sdgd", "sdltgd", "jgcc", "sjkzr", "jjcg"):
        val = raw.get(key)
        if isinstance(val, list) and val:
            return True
    return False


def _json_get(source: str, url: str, headers: dict, timeout: float = 8.0,
              params: dict | None = None) -> dict:
    """独立短连接，避免拖垮板块资金用的东方财富熔断。"""
    last: Exception | None = None
    for attempt in range(2):
        try:
            with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = _parse_json_maybe_jsonp(resp.text)
                if not data:
                    try:
                        data = resp.json()
                    except Exception:  # noqa: BLE001
                        data = {}
                if isinstance(data, dict):
                    return data
                return {}
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.2 * (attempt + 1))
    if last:
        log.warning("%s 请求失败 %s: %s", source, url.split("?")[0], last)
        raise last
    return {}


def _dc_rows(report: str, filt: str, sort: str, st: str, pz: int = 20) -> list[dict]:
    last: Exception | None = None
    specs = (
        (_DC_WEB, {"reportName": report, "columns": "ALL", "pageNumber": 1, "pageSize": pz,
                   "sortColumns": sort, "sortTypes": st, "filter": filt,
                   "source": "WEB", "client": "WEB"}),
        (_DC_SEC_HOST, {"reportName": report, "columns": "ALL", "pageNumber": 1, "pageSize": pz,
                        "sortColumns": sort, "sortTypes": st, "filter": filt,
                        "source": "HSF10", "client": "PC"}),
    )
    for url, params in specs:
        try:
            body = _json_get(_F10_SOURCE, url, _DC_HEADERS, timeout=8.0, params=params)
        except Exception as exc:  # noqa: BLE001
            last = exc
            continue
        rows = ((body.get("result") or {}).get("data")) or []
        if isinstance(rows, list) and rows:
            return [r for r in rows if isinstance(r, dict)]
    if last:
        raise last
    return []


def _latest_date(rows: list[dict], field: str = "END_DATE") -> str | None:
    dates = []
    for r in rows:
        s = str(r.get(field) or "").strip()
        if len(s) >= 10:
            dates.append(s[:10])
    return max(dates) if dates else None


def _same_day(rows: list[dict], day: str | None, field: str = "END_DATE") -> list[dict]:
    if not day:
        return rows
    return [r for r in rows if str(r.get(field) or "").startswith(day)]


def _fetch_f10_pageajax(em: str) -> dict:
    url = _F10_SHAREHOLDER.format(code=em)
    return _json_get(_F10_SOURCE, url, _F10_HEADERS, timeout=5.0)


def _fetch_f10_datacenter(digits: str) -> dict:
    """data.eastmoney.com 股东户数/十大股东/机构持仓，F10 页面被拦时的备源。"""
    filt = f'(SECURITY_CODE="{digits}")'
    gdrs = _dc_rows("RPT_F10_EH_HOLDERNUM", filt, "END_DATE", "-1", 8)
    holders = _dc_rows("RPT_F10_EH_HOLDERS", filt, "END_DATE,HOLDER_RANK", "-1,1", 30)
    hold_day = _latest_date(holders)
    if hold_day:
        holders = _same_day(holders, hold_day) or holders[:10]
        more = _dc_rows(
            "RPT_F10_EH_HOLDERS",
            f'{filt}(END_DATE=\'{hold_day}\')',
            "HOLDER_RANK", "1", 15,
        )
        if more:
            holders = more
    free = _dc_rows(
        "RPT_F10_EH_FREEHOLDERS",
        f'{filt}(IS_MAX_REPORTDATE="1")',
        "HOLDER_RANK", "1", 15,
    )
    if not free:
        free = _dc_rows("RPT_F10_EH_FREEHOLDERS", filt, "END_DATE,HOLDER_RANK", "-1,1", 20)
        free = _same_day(free, _latest_date(free))
    org = _dc_rows("RPT_MAIN_ORGHOLD", filt, "REPORT_DATE,ORG_TYPE", "-1,1", 30)
    org_day = _latest_date(org, "REPORT_DATE")
    org = _same_day(org, org_day, "REPORT_DATE")
    jgcc = []
    for r in org:
        jgcc.append({
            "ORG_TYPE": r.get("ORG_TYPE"),
            "ORG_TYPEName": r.get("ORG_TYPE_NAME"),
            "TOTAL_ORG_NUM": r.get("HOULD_NUM") or r.get("TYPE_NUM"),
            "TOTAL_FREE_SHARES": r.get("FREE_SHARES") or r.get("TOTAL_SHARES"),
            "TOTAL_SHARES_RATIO": r.get("FREESHARES_RATIO") or r.get("TOTALSHARES_RATIO"),
            "ALL_SHARES_RATIO": r.get("TOTALSHARES_RATIO") or r.get("FREESHARES_RATIO"),
            "REPORT_DATE": r.get("REPORT_DATE"),
        })
    ctrl = _dc_rows(
        "RPT_F10_EH_FREEHOLDERS",
        f'{filt}(IS_SJKZR="1")',
        "END_DATE", "-1", 8,
    )
    ctrl_day = _latest_date(ctrl)
    sjkzr = []
    seen: set[str] = set()
    for r in _same_day(ctrl, ctrl_day):
        name = (r.get("HOLDER_NAME") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        sjkzr.append({"HOLDER_NAME": name, "HOLD_RATIO": r.get("HOLD_RATIO")})
    funds = []
    if org_day:
        funds = _dc_rows(
            "RPT_MAIN_ORGHOLDDETAIL",
            f'{filt}(REPORT_DATE=\'{org_day}\')',
            "TOTALSHARES_RATIO", "-1", 12,
        )
    return {
        "gdrs": gdrs,
        "sdgd": holders,
        "sdltgd": free,
        "jgcc": jgcc,
        "sjkzr": sjkzr,
        "jjcg": funds,
        "xsjj": [],
        "ltgf": [],
    }


def fetch_shareholders(code: str) -> dict:
    """股东研究。优先 F10 一页 JSON，失败改走数据中心接口。缺数返回空 dict。"""
    em = f10_code(code)
    digits = _security_code(code)
    if not em or not digits:
        return {}
    errors: list[str] = []
    try:
        raw = _fetch_f10_pageajax(em)
        if _shareholder_raw_ok(raw):
            return raw
        if raw:
            errors.append("F10页面无股东字段")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"F10页面: {exc}")
    try:
        raw = _fetch_f10_datacenter(digits)
        if _shareholder_raw_ok(raw):
            return raw
        if raw:
            errors.append("数据中心无股东字段")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"数据中心: {exc}")
    if errors:
        log.warning("股东数据均失败 %s: %s", em, " | ".join(errors))
        raise RuntimeError("；".join(errors))
    return {}
