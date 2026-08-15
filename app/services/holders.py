"""个股持股情况：股东户数、实控人、机构/个人结构、十大股东。

数据来自东方财富 F10 披露，缺数不编造。机构汇总来自 jgcc ORG_TYPE=00；
个人及其他 = 流通盘剩余（100 − 机构汇总），不是单独披露的零售户口径。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from ..cache import cache
from ..database import execute, query
from ..datasources import eastmoney

log = logging.getLogger("holders")
_TZ = ZoneInfo("Asia/Shanghai")
SOURCE = "东方财富F10（披露数据）"

# 与东方财富 RPT_MAIN_ORGHOLD / F9_ORGTYPE_NAME 对齐
ORG_TYPE_NAME = {
    "00": "机构汇总",
    "01": "基金",
    "02": "QFII",
    "03": "社保",
    "04": "券商",
    "05": "保险",
    "06": "信托",
    "07": "一般法人",
    "08": "银行",
    "09": "阳光私募",
    "10": "券商资管",
    "11": "企业年金",
    "12": "其他机构",
}

_ORG_TYPE_HINTS = (
    "公司", "基金", "保险", "银行", "信托", "证券", "资管", "集团", "合伙",
    "有限", "股份", "社保", "年金", "QFII", "中央结算", "结算有限", "投资",
    "资本", "控股", "企业", "大学", "协会", "政府", "国资", "财政",
    "HKSCC", "NOMINEES", "LIMITED", "LTD", "INC", "LLC", "PLC",
)
_PERSON_TYPES = {"个人", "自然人"}
_ORG_TYPES = {
    "基金", "券商", "保险", "保险公司", "保险产品", "信托", "社保", "QFII",
    "投资公司", "财务公司", "银行", "一般法人", "阳光私募", "券商资管",
    "企业年金", "其他机构", "机构",
}


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _date(v) -> str | None:
    if not v:
        return None
    s = str(v).strip()
    return s[:10] if len(s) >= 10 else s or None


def _num(v) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("%", "").strip()
    if s in ("不变", "新进", "退出", "--", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _round(v, n=2) -> float | None:
    x = _num(v)
    return None if x is None else round(x, n)


def _shares_txt(n) -> str | None:
    x = _num(n)
    if x is None:
        return None
    ax = abs(x)
    if ax >= 1e8:
        return f"{x / 1e8:.2f}亿"
    if ax >= 1e4:
        return f"{x / 1e4:.2f}万"
    return f"{int(round(x))}"


def _change_txt(raw, ratio) -> str | None:
    if raw in (None, ""):
        r = _round(ratio, 2)
        return None if r is None else f"{r:+.2f}%"
    s = str(raw).strip()
    if s in ("不变", "新进", "退出"):
        return s
    n = _num(s)
    if n is None:
        return s
    txt = _shares_txt(n)
    r = _round(ratio, 2)
    if txt and r is not None:
        return f"{txt}（{r:+.2f}%）"
    return txt or s


def _classify(name: str, holder_type: str | None) -> str:
    t = (holder_type or "").strip()
    if t in _PERSON_TYPES:
        return "个人"
    if t in _ORG_TYPES:
        return "机构"
    n = (name or "").strip()
    n_up = n.upper()
    if any(k in n or k in n_up for k in _ORG_TYPE_HINTS):
        return "机构"
    # 2～4 个汉字、无机构关键词，按个人标注；其余不猜
    if n and 2 <= len(n) <= 4 and all("\u4e00" <= ch <= "\u9fff" for ch in n):
        return "个人"
    return "未标注"


def _rows(raw) -> list[dict]:
    return [r for r in (raw or []) if isinstance(r, dict)]


def _holder_row(r: dict, *, float_holder: bool) -> dict | None:
    name = (r.get("HOLDER_NAME") or "").strip()
    if not name:
        return None
    holder_type = (r.get("HOLDER_TYPE") or r.get("HOLDER_NEWTYPE") or "").strip() or None
    if not holder_type and str(r.get("IS_HOLDORG") or "") == "1":
        holder_type = "机构"
    ratio = _round(r.get("FREE_HOLDNUM_RATIO") if float_holder else r.get("HOLD_NUM_RATIO"), 4)
    if ratio is None:
        ratio = _round(r.get("HOLD_NUM_RATIO"), 4)
    return {
        "rank": int(_num(r.get("HOLDER_RANK")) or 0) or None,
        "name": name,
        "kind": _classify(name, holder_type),
        "holder_type": holder_type,
        "shares_type": (r.get("SHARES_TYPE") or "").strip() or None,
        "shares": _num(r.get("HOLD_NUM")),
        "shares_txt": _shares_txt(r.get("HOLD_NUM")),
        "ratio": ratio,
        "change": _change_txt(r.get("HOLD_NUM_CHANGE"), r.get("CHANGE_RATIO")),
        "date": _date(r.get("END_DATE")),
    }


def parse_shareholders(raw: dict) -> dict:
    """把 F10 PageAjax 转成前端可用结构。空列表保持空，不填假数。"""
    raw = raw or {}
    gdrs = _rows(raw.get("gdrs"))
    counts = []
    for r in gdrs[:8]:
        qoq = _round(r.get("TOTAL_NUM_RATIO"), 2)
        ipo_qoq = qoq is not None and abs(qoq) > 200
        counts.append({
            "date": _date(r.get("END_DATE")),
            "holders": int(_num(r.get("HOLDER_TOTAL_NUM")) or 0) or None,
            "holders_qoq": None if ipo_qoq else qoq,
            "holders_qoq_note": "上市/首期，环比不可比" if ipo_qoq else None,
            "avg_free_shares": int(_num(r.get("AVG_FREE_SHARES")) or 0) or None,
            "avg_free_qoq": _round(r.get("AVG_FREESHARES_RATIO"), 2),
            "focus": (r.get("HOLD_FOCUS") or "").strip() or None,
            "avg_hold_amt": _round(r.get("AVG_HOLD_AMT"), 0),
            "top10_ratio": _round(r.get("HOLD_RATIO_TOTAL"), 2),
            "top10_float_ratio": _round(r.get("FREEHOLD_RATIO_TOTAL"), 2),
        })
    latest = counts[0] if counts else {}

    controllers = []
    for r in _rows(raw.get("sjkzr")):
        name = (r.get("HOLDER_NAME") or "").strip()
        if not name:
            continue
        controllers.append({"name": name, "ratio": _round(r.get("HOLD_RATIO"), 2)})

    org_rows = []
    inst_ratio = None
    inst_count = None
    inst_date = None
    for r in _rows(raw.get("jgcc")):
        code = str(r.get("ORG_TYPE") or "").zfill(2)[-2:]
        ratio = _round(r.get("TOTAL_SHARES_RATIO"), 4)
        item = {
            "org_type": code,
            "name": (r.get("ORG_TYPEName") or r.get("ORG_TYPE_NAME")
                     or ORG_TYPE_NAME.get(code, f"机构类型{code}")),
            "count": int(_num(r.get("TOTAL_ORG_NUM")) or 0) or None,
            "shares": _num(r.get("TOTAL_FREE_SHARES")),
            "shares_txt": _shares_txt(r.get("TOTAL_FREE_SHARES")),
            "float_ratio": ratio,
            "total_ratio": _round(r.get("ALL_SHARES_RATIO"), 4),
            "date": _date(r.get("REPORT_DATE")),
        }
        if code == "00":
            inst_ratio = ratio
            inst_count = item["count"]
            inst_date = item["date"]
            continue
        org_rows.append(item)
    org_rows.sort(key=lambda x: -(x.get("float_ratio") or 0))

    person_ratio = None
    if inst_ratio is not None:
        person_ratio = round(max(0.0, min(100.0, 100.0 - inst_ratio)), 2)

    top10 = [x for x in (_holder_row(r, float_holder=False) for r in _rows(raw.get("sdgd"))) if x]
    top10_float = [x for x in (_holder_row(r, float_holder=True) for r in _rows(raw.get("sdltgd"))) if x]

    funds = []
    for r in _rows(raw.get("jjcg"))[:12]:
        name = (r.get("HOLDER_NAME") or "").strip()
        if not name:
            continue
        funds.append({
            "name": name,
            "code": (r.get("FUND_CODE") or r.get("HOLDER_CODE") or "").strip() or None,
            "shares": _num(r.get("TOTAL_SHARES") or r.get("FREE_SHARES")),
            "shares_txt": _shares_txt(r.get("TOTAL_SHARES") or r.get("FREE_SHARES")),
            "ratio": _round(r.get("TOTALSHARES_RATIO") or r.get("FREESHARES_RATIO"), 4),
            "value_yi": _round((_num(r.get("HOLD_VALUE")) or 0) / 1e8, 2) if _num(r.get("HOLD_VALUE")) is not None else None,
            "date": _date(r.get("REPORT_DATE")),
        })

    unlocks = []
    for r in _rows(raw.get("xsjj")):
        unlocks.append({
            "date": _date(r.get("LIFT_DATE")),
            "shares": _num(r.get("LIFT_NUM")),
            "shares_txt": _shares_txt(r.get("LIFT_NUM")),
            "total_ratio": _round(r.get("TOTAL_SHARES_RATIO"), 2),
            "float_ratio": _round(r.get("UNLIMITED_A_SHARES_RATIO"), 2),
            "lift_type": (r.get("LIFT_TYPE") or "").strip() or None,
        })

    float_struct = None
    ltgf = _rows(raw.get("ltgf"))
    if ltgf:
        r = ltgf[0]
        float_struct = {
            "date": _date(r.get("END_DATE")),
            "limited_ratio": _round(r.get("LIMITED_SHARES_RATIO"), 2),
            "unlimited_ratio": _round(r.get("UNLIMITED_SHARES_RATIO"), 2),
        }

    asof = (
        latest.get("date") or inst_date
        or (top10[0]["date"] if top10 else None)
        or (top10_float[0]["date"] if top10_float else None)
    )
    has_data = bool(counts or controllers or org_rows or top10 or top10_float or funds or unlocks)
    return {
        "asof": asof,
        "controller": controllers,
        "counts": counts,
        "latest": latest,
        "institution_ratio": inst_ratio,
        "institution_count": inst_count,
        "person_ratio": person_ratio,
        "org_hold": org_rows,
        "top10": top10,
        "top10_float": top10_float,
        "funds": funds,
        "unlocks": unlocks,
        "float_struct": float_struct,
        "empty": not has_data,
    }


def _empty(code: str, note: str, offline: bool = False) -> dict:
    return {
        "code": code, "asof": None, "controller": [], "counts": [], "latest": {},
        "institution_ratio": None, "institution_count": None, "person_ratio": None,
        "org_hold": [], "top10": [], "top10_float": [], "funds": [], "unlocks": [],
        "float_struct": None, "empty": True, "offline": offline,
        "source": SOURCE, "note": note,
    }


def _load_db(code: str) -> dict | None:
    rows = query("SELECT payload, fetched_at FROM stock_holders WHERE code=?", (code,))
    if not rows:
        return None
    try:
        payload = json.loads(rows[0]["payload"])
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict):
        return None
    payload["offline"] = True
    payload["fetched_at"] = rows[0]["fetched_at"]
    payload["source"] = SOURCE
    payload["note"] = payload.get("note") or "网络暂不可用，展示上次缓存"
    return payload


def _persist(code: str, payload: dict) -> None:
    execute(
        "CREATE TABLE IF NOT EXISTS stock_holders ("
        "code TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at TEXT NOT NULL)"
    )
    execute(
        "INSERT OR REPLACE INTO stock_holders(code, payload, fetched_at) VALUES(?,?,?)",
        (code, json.dumps(payload, ensure_ascii=False), payload.get("fetched_at") or _now()),
    )


def get_holders(code: str) -> dict:
    """持股情况：F10 优先、数据中心兜底；成功缓存 24h，失败不长期当空数据。"""
    code = (code or "").strip().lower()
    if not code or not eastmoney.f10_code(code):
        return _empty(code, "该代码没有股东披露数据")
    if code.startswith(("sh000", "sz399", "bj899", "sh880")):
        return _empty(code, "指数没有股东持股披露")

    key = f"holders:v3:{code}"
    hit = cache.get(key)
    if hit is not None:
        return hit

    last_err: Exception | None = None
    parsed: dict | None = None
    try:
        raw = eastmoney.fetch_shareholders(code)
        parsed = parse_shareholders(raw)
    except Exception as exc:  # noqa: BLE001
        last_err = exc
        log.warning("持股拉取失败 %s: %s", code, exc)

    if parsed and not parsed.get("empty"):
        parsed["code"] = code
        parsed["offline"] = False
        parsed["fetched_at"] = _now()
        parsed["source"] = SOURCE
        parsed["note"] = (
            "机构占比为已披露机构持仓合计；个人及其他为流通盘剩余，"
            "不是单独公布的零售户口径。"
        )
        try:
            _persist(code, parsed)
        except Exception as exc:  # noqa: BLE001
            log.warning("持股落库失败 %s: %s", code, exc)
        cache.set(key, parsed, 86400)
        return parsed

    db = _load_db(code)
    if db and not db.get("empty"):
        cache.set(key, db, 300)
        return db

    if last_err:
        note = f"持股数据暂时拉不到（{last_err}）。已尝试东方财富 F10 与数据中心。"
    elif parsed and parsed.get("empty"):
        note = "该公司暂无股东披露数据"
    else:
        note = "暂无持股数据（披露接口不可用且本地无缓存）"
    empty = _empty(code, note, offline=bool(last_err))
    cache.set(key, empty, 45)
    return empty
