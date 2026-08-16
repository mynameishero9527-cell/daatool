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


FUND_TOP_N = 10


def _parse_funds(rows: list[dict]) -> list[dict]:
    """基金持股：按持股数量降序取前 10。ORG_TYPE 若有则只保留基金(01)。"""
    funds = []
    seen: set[str] = set()
    for r in rows:
        raw_ot = str(r.get("ORG_TYPE") or "").strip()
        org_type = raw_ot.zfill(2)[-2:] if raw_ot else ""
        if org_type and org_type != "01":
            continue
        name = (r.get("HOLDER_NAME") or "").strip()
        if not name:
            continue
        key = (r.get("FUND_CODE") or r.get("HOLDER_CODE") or name).strip()
        if key in seen:
            continue
        seen.add(key)
        shares = _num(r.get("TOTAL_SHARES") or r.get("FREE_SHARES") or r.get("HOLD_NUM"))
        funds.append({
            "name": name,
            "code": (r.get("FUND_CODE") or r.get("HOLDER_CODE") or "").strip() or None,
            "shares": shares,
            "shares_txt": _shares_txt(shares) if shares is not None else _shares_txt(
                r.get("TOTAL_SHARES") or r.get("FREE_SHARES") or r.get("HOLD_NUM")),
            "ratio": _round(r.get("TOTALSHARES_RATIO") or r.get("FREESHARES_RATIO")
                            or r.get("HOLD_NUM_RATIO"), 4),
            "value_yi": _round((_num(r.get("HOLD_VALUE")) or 0) / 1e8, 2)
            if _num(r.get("HOLD_VALUE")) is not None else None,
            "date": _date(r.get("REPORT_DATE") or r.get("END_DATE")),
            "org_type": org_type or "01",
        })
    funds.sort(key=lambda x: (
        x.get("shares") is None,
        -(x.get("shares") or 0),
        x.get("ratio") is None,
        -(x.get("ratio") or 0),
    ))
    out = funds[:FUND_TOP_N]
    for i, item in enumerate(out, 1):
        item["rank"] = i
    return out


def _rerank_saved_funds(funds: list) -> list[dict]:
    items = [dict(x) for x in funds if isinstance(x, dict) and (x.get("name") or "").strip()]
    items.sort(key=lambda x: (
        x.get("shares") is None,
        -(x.get("shares") or 0),
        x.get("ratio") is None,
        -(x.get("ratio") or 0),
    ))
    out = items[:FUND_TOP_N]
    for i, item in enumerate(out, 1):
        item["rank"] = i
    return out


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

    funds = _parse_funds(_rows(raw.get("jjcg")))

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
        "org_types": [
            {"org_type": x["org_type"], "name": x["name"], "count": x.get("count")}
            for x in org_rows
        ],
        "top10": top10,
        "top10_float": top10_float,
        "funds": funds,
        "funds_note": "按持股数量降序取前10（数量缺失时按占股本比）。缺披露不编造。",
        "unlocks": unlocks,
        "float_struct": float_struct,
        "empty": not has_data,
    }


def _empty(code: str, note: str, offline: bool = False) -> dict:
    return {
        "code": code, "asof": None, "controller": [], "counts": [], "latest": {},
        "institution_ratio": None, "institution_count": None, "person_ratio": None,
        "org_hold": [], "org_types": [], "top10": [], "top10_float": [],
        "funds": [], "funds_note": "", "unlocks": [],
        "float_struct": None, "empty": True, "offline": offline,
        "source": SOURCE, "note": note,
        "ai": _empty_ai(),
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
    payload.setdefault("org_types", [
        {"org_type": x.get("org_type"), "name": x.get("name"), "count": x.get("count")}
        for x in (payload.get("org_hold") or [])
    ])
    payload.setdefault("funds_note", "按持股数量降序取前10（数量缺失时按占股本比）。缺披露不编造。")
    if isinstance(payload.get("funds"), list) and payload["funds"]:
        payload["funds"] = _rerank_saved_funds(payload["funds"])
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
    try:
        from . import holder_feature
        holder_feature.upsert_from_payload(code, payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("持股特征落库失败 %s: %s", code, exc)


def get_holders(code: str) -> dict:
    """持股情况：F10 优先、数据中心兜底；成功缓存 24h，失败不长期当空数据。"""
    code = (code or "").strip().lower()
    if not code or not eastmoney.f10_code(code):
        return _with_ai(code, _empty(code, "该代码没有股东披露数据"))
    if code.startswith(("sh000", "sz399", "bj899", "sh880")):
        return _with_ai(code, _empty(code, "指数没有股东持股披露"))

    key = f"holders:v4:{code}"
    hit = cache.get(key)
    if hit is not None:
        return _with_ai(code, hit)

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
        return _with_ai(code, parsed)

    db = _load_db(code)
    if db and not db.get("empty"):
        cache.set(key, db, 300)
        return _with_ai(code, db)

    if last_err:
        note = f"持股数据暂时拉不到（{last_err}）。已尝试东方财富 F10 与数据中心。"
    elif parsed and parsed.get("empty"):
        note = "该公司暂无股东披露数据"
    else:
        note = "暂无持股数据（披露接口不可用且本地无缓存）"
    empty = _empty(code, note, offline=bool(last_err))
    cache.set(key, empty, 45)
    return _with_ai(code, empty)


def _empty_ai() -> dict:
    return {
        "applied": False, "text": "", "source": "", "updated_at": "",
        "analyzed_at": "", "asof": "", "error": "", "hint": "", "kept": False,
        "keywords": [],
    }


def load_holder_ai(code: str) -> dict | None:
    code = (code or "").strip().lower()
    if not code:
        return None
    try:
        rows = query("SELECT payload, updated_at FROM stock_holder_ai WHERE code=?", (code,))
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    try:
        data = json.loads(rows[0].get("payload") or "{}")
    except Exception:  # noqa: BLE001
        return None
    kws = data.get("keywords") if isinstance(data.get("keywords"), list) else []
    kws = [str(x).strip() for x in kws if str(x).strip()]
    if not isinstance(data, dict) or (not (data.get("text") or "").strip() and not kws):
        return None
    stamp = rows[0].get("updated_at") or data.get("analyzed_at") or data.get("updated_at") or ""
    return {
        "applied": True,
        "text": data.get("text") or "",
        "source": data.get("source") or "",
        "updated_at": stamp,
        "analyzed_at": data.get("analyzed_at") or stamp,
        "asof": data.get("asof") or "",
        "error": "",
        "hint": "",
        "kept": False,
        "keywords": kws[:16],
    }


def save_holder_ai(code: str, payload: dict) -> dict:
    now = _now()
    body = dict(payload)
    body["analyzed_at"] = now
    body["updated_at"] = now
    execute(
        "CREATE TABLE IF NOT EXISTS stock_holder_ai ("
        "code TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    execute(
        "INSERT OR REPLACE INTO stock_holder_ai(code, payload, updated_at) VALUES(?,?,?)",
        (code, json.dumps(body, ensure_ascii=False), now),
    )
    return load_holder_ai(code) or _empty_ai()


def _with_ai(code: str, data: dict) -> dict:
    out = dict(data or {})
    out["ai"] = load_holder_ai(code) or _empty_ai()
    try:
        from . import stock_ai as stock_ai_svc
        out["brief"] = stock_ai_svc.load_brief(code)
        out["wuxing_ai"] = stock_ai_svc.load_wuxing(code)
    except Exception:  # noqa: BLE001
        out["brief"] = {"applied": False, "text": "", "source": "", "analyzed_at": ""}
        out["wuxing_ai"] = {"applied": False, "text": "", "source": "", "analyzed_at": ""}
    return out


def _holder_ai_context(snap: dict) -> str:
    """只用已披露字段拼上下文，缺项写「未披露」，不编股东户数或基金名单。"""
    L = snap.get("latest") or {}
    lines = [
        f"代码 {snap.get('code') or ''}，报告期 {snap.get('asof') or '未披露'}",
        f"股东户数 {L.get('holders') if L.get('holders') is not None else '未披露'}"
        + (f"，环比 {L.get('holders_qoq')}%" if L.get("holders_qoq") is not None
           else f"，环比 {L.get('holders_qoq_note') or '未披露'}"),
        f"人均流通股 {L.get('avg_free_shares') if L.get('avg_free_shares') is not None else '未披露'}"
        f"，集中度 {L.get('focus') or '未披露'}"
        f"，十大股东合计 {L.get('top10_ratio') if L.get('top10_ratio') is not None else '未披露'}%",
    ]
    inst, person = snap.get("institution_ratio"), snap.get("person_ratio")
    lines.append(
        f"机构占流通 {inst if inst is not None else '未披露'}%"
        f"（{snap.get('institution_count') if snap.get('institution_count') is not None else '未披露'} 家）"
        f"，个人及其他 {person if person is not None else '未披露'}%"
    )
    org_bits = []
    for r in snap.get("org_hold") or []:
        org_bits.append(
            f"{r.get('name')} {r.get('count') if r.get('count') is not None else '-'}家"
            f"/{r.get('shares_txt') or '-'} / 流通{r.get('float_ratio') if r.get('float_ratio') is not None else '-'}%"
        )
    lines.append("机构构成：" + ("；".join(org_bits) if org_bits else "未披露"))
    funds = snap.get("funds") or []
    if funds:
        bits = []
        for r in funds[:10]:
            bits.append(
                f"{r.get('rank') or ''} {r.get('name')} 持股{r.get('shares_txt') or '未披露'}"
                f" 占股本{r.get('ratio') if r.get('ratio') is not None else '未披露'}%"
            )
        lines.append("基金持股数量前10：" + "；".join(bits))
    else:
        lines.append("基金持股数量前10：未披露")
    top = []
    for r in (snap.get("top10") or [])[:6]:
        top.append(f"{r.get('name')} {r.get('kind') or ''} {r.get('ratio') if r.get('ratio') is not None else '-'}%")
    lines.append("十大股东摘要：" + ("、".join(top) if top else "未披露"))
    ctrl = "、".join(
        (c.get("name") or "") + (f" {c.get('ratio')}%" if c.get("ratio") is not None else "")
        for c in (snap.get("controller") or [])[:3]
    )
    lines.append("实控人：" + (ctrl or "未披露"))
    return "\n".join(lines)


def _local_holder_text(snap: dict) -> str:
    ctx = _holder_ai_context(snap)
    return (
        "【本地规则】以下仅复述已披露持股数据，不是大模型研判，未改写已保存的 AI 结果。\n"
        + ctx
        + "\n可在 AI 分析页配置大模型后，在本页点击「更新AI分析」回填。"
    )


def analyze_holder_ai(code: str) -> dict:
    """手动调用：用持股快照让大模型分析筹码结构。成功才落库并更新时间戳；失败保留旧结果。"""
    from . import ai as ai_svc
    from . import market as market_svc
    code = market_svc.normalize_code(code) or (code or "").strip().lower()
    if not code:
        return {**_empty_ai(), "ok": False, "configured": False, "error": "未指定股票"}
    snap = get_holders(code)
    prev = load_holder_ai(code)
    local_text = _local_holder_text(snap)
    cfg = ai_svc.get_config(masked=False)
    base_url = ai_svc.normalize_api_base(cfg.get("api_base", "") or "")
    configured = bool(cfg.get("api_key") and base_url)

    def _keep(error: str = "", hint: str = "", source: str = "", text: str = "") -> dict:
        if prev:
            return {
                **prev,
                "ok": True,
                "applied": False,
                "ai": False,
                "kept": True,
                "configured": configured,
                "error": error,
                "hint": hint,
            }
        body = (text or local_text).strip()
        if ai_svc.DISCLAIMER.strip() not in body:
            body = body + ai_svc.DISCLAIMER
        return {
            **_empty_ai(),
            "ok": True,
            "applied": False,
            "ai": False,
            "kept": False,
            "configured": configured,
            "source": source or "本地规则（未配置AI大模型）",
            "text": body,
            "error": error,
            "hint": hint,
            "asof": snap.get("asof") or "",
        }

    if not configured:
        return _keep(
            error="未配置AI大模型",
            hint="到 AI 分析页填写地址、密钥、模型并测试连通后再点「更新AI分析」。未改写已保存结果。",
            source="本地规则（未配置AI大模型）",
            text=local_text,
        )
    name = ""
    try:
        rows = query("SELECT name FROM stock_list WHERE code=?", (code,))
        name = (rows[0]["name"] if rows else "") or ""
    except Exception:  # noqa: BLE001
        name = ""
    context = (
        f"股票 {name}（{code}）。\n"
        f"{_holder_ai_context(snap)}\n"
        "口径：机构占比为已披露机构持仓合计；个人及其他为流通盘剩余。"
        "未披露的数字不要编造。"
    )
    task = (
        "请基于以上已披露持股数据，分析筹码结构："
        "股东户数变化含义、机构构成偏好、基金持股数量前10的集中度、主要风险。"
        "分点，300字内。禁止收益承诺，不确定就写「披露不足」。"
    )
    try:
        raw = ai_svc._call_llm(cfg, context, task)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        err = ai_svc.format_llm_error(exc, base_url)
        kept = _keep(
            error=err,
            hint=ai_svc._hint_for_error(err),  # noqa: SLF001
            source="本地规则分析（大模型未调用）",
            text=local_text + f"\n（大模型未调用：{err}）",
        )
        return kept
    text = (raw or "").strip()
    if len(text) < 20:
        return _keep(
            error="模型返回过短，未覆盖已保存结果",
            hint="可稍后重试「更新AI分析」。",
            source=f"AI大模型（{cfg.get('model') or '默认'}）",
            text=local_text,
        )
    saved = save_holder_ai(code, {
        "text": text + ai_svc.DISCLAIMER,
        "source": f"AI大模型（{cfg.get('model') or '默认'}）",
        "model": cfg.get("model") or "",
        "asof": snap.get("asof") or "",
        "keywords": (prev or {}).get("keywords") or [],
    })
    return {
        **saved,
        "ok": True,
        "applied": True,
        "ai": True,
        "kept": False,
        "configured": True,
        "error": "",
        "hint": "",
    }


def get_holder_ai(code: str) -> dict:
    from . import market as market_svc
    code = market_svc.normalize_code(code) or (code or "").strip().lower()
    if not code:
        return {**_empty_ai(), "ok": False, "error": "未指定股票"}
    ov = load_holder_ai(code) or _empty_ai()
    return {**ov, "ok": True, "code": code}
