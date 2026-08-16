"""黄历预测推荐本地回显：只保存易经卜卦 / 奇门遁甲实际筛出的本地个股。

不编造代码，不写入智能选股上涨/下跌名单。清理只走手动接口。
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from ..database import execute, executemany, query

_TZ = ZoneInfo("Asia/Shanghai")

KINDS = {
    "yijing": {"id": "yijing", "prefix": "YJ", "label": "易经卜卦推测"},
    "qimen": {"id": "qimen", "prefix": "QM", "label": "奇门遁甲预测"},
}
SORTS = (
    {"id": "predicted_at", "name": "预测时间", "sql": "b.predicted_at"},
    {"id": "batch_no", "name": "批次号", "sql": "b.batch_no"},
    {"id": "kind", "name": "预测类型", "sql": "b.kind"},
    {"id": "wuxing", "name": "五行", "sql": "s.wuxing"},
    {"id": "score", "name": "综合评分", "sql": "s.score"},
    {"id": "name", "name": "名称", "sql": "s.name"},
    {"id": "code", "name": "代码", "sql": "s.code"},
    {"id": "finance_grade", "name": "财报", "sql": "s.finance_grade"},
)
_SORT_SQL = {s["id"]: s["sql"] for s in SORTS}
NOTE = (
    "这里只回显黄历里「易经卜卦 / 奇门遁甲预测」已经筛出的本地个股，"
    "不编造名单，也不写入上涨/下跌预测。"
)
DISCLAIMER = "民俗推算 + 量化过滤，仅供参考，不构成投资建议。"


def _now() -> datetime:
    return datetime.now(_TZ)


def _json_dump(val) -> str:
    return json.dumps(val if val is not None else [], ensure_ascii=False)


def _json_load(raw, default=None):
    if raw in (None, ""):
        return [] if default is None else default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return [] if default is None else default


def _norm_kind(kind: str) -> str | None:
    k = (kind or "").strip().lower()
    if k in KINDS:
        return k
    if k in ("yj", "divine", "divination", "易经", "卜卦"):
        return "yijing"
    if k in ("qm", "qimen-predict", "奇门", "遁甲"):
        return "qimen"
    return None


def _alloc_batch_no(kind: str, when: datetime) -> str:
    prefix = KINDS[kind]["prefix"]
    stamp = when.strftime("%Y%m%d-%H%M%S")
    for i in range(1, 100):
        no = f"{prefix}-{stamp}" if i == 1 else f"{prefix}-{stamp}-{i:02d}"
        rows = query("SELECT 1 AS n FROM forecast_batch WHERE batch_no=?", (no,))
        if not rows:
            return no
    return f"{prefix}-{stamp}-{when.microsecond:06d}"


def _wuxing_of(payload: dict, kind: str) -> list[str]:
    if kind == "yijing":
        raw = payload.get("gua_wuxing") or []
    else:
        raw = payload.get("favor") or []
    out = []
    for x in raw:
        s = str(x or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _summary_of(payload: dict, kind: str) -> str:
    if kind == "yijing":
        gua = payload.get("gua") or {}
        ben = (gua.get("ben") or {}).get("name") or ""
        return f"本卦{ben}" if ben else "易经六爻"
    qm = payload.get("qimen") or {}
    season = payload.get("season") or ""
    favor = "、".join(_wuxing_of(payload, "qimen"))
    return f"{season}季旺相{favor} {qm.get('shichen') or ''}".strip()


def record(kind: str, payload: dict | None) -> dict | None:
    """把一次预测的真实个股写入本地。无个股或失败不建批次。"""
    kid = _norm_kind(kind)
    if not kid or not isinstance(payload, dict) or not payload.get("ok"):
        return None
    stocks = [s for s in (payload.get("stocks") or []) if s.get("code") and s.get("name")]
    if not stocks:
        return None
    when = _now()
    batch_no = _alloc_batch_no(kid, when)
    predicted_at = when.strftime("%Y-%m-%d %H:%M:%S")
    wx = _wuxing_of(payload, kid)
    extra = {
        "gua": payload.get("gua") if kid == "yijing" else None,
        "qimen": payload.get("qimen") if kid == "qimen" else None,
        "hot_industries": payload.get("hot_industries") or [],
        "industries": payload.get("industries") or [],
        "empty_reason": payload.get("empty_reason") or "",
    }
    execute(
        "INSERT INTO forecast_batch(batch_no,kind,kind_label,predicted_at,almanac_date,hour,"
        "wuxing,summary,extra,stock_count) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            batch_no, kid, KINDS[kid]["label"], predicted_at,
            str(payload.get("date") or "")[:10],
            payload.get("hour"),
            _json_dump(wx),
            _summary_of(payload, kid),
            _json_dump(extra),
            len(stocks),
        ),
    )
    rows = []
    seen = set()
    for s in stocks:
        code = str(s.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        rows.append((
            batch_no, code, str(s.get("name") or "").strip() or code,
            str(s.get("industry") or ""),
            _json_dump(s.get("wuxing") or []),
            str(s.get("finance_grade") or ""),
            s.get("score"), s.get("advice"), s.get("buy_index"),
            s.get("price"), s.get("pct"),
            _json_dump(s.get("wx_state") or []),
        ))
    if rows:
        executemany(
            "INSERT OR REPLACE INTO forecast_stock("
            "batch_no,code,name,industry,wuxing,finance_grade,score,advice,"
            "buy_index,price,pct,wx_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        execute("UPDATE forecast_batch SET stock_count=? WHERE batch_no=?", (len(rows), batch_no))
    payload["batch_no"] = batch_no
    payload["predicted_at"] = predicted_at
    payload["kind_label"] = KINDS[kid]["label"]
    return {
        "batch_no": batch_no,
        "predicted_at": predicted_at,
        "kind": kid,
        "kind_label": KINDS[kid]["label"],
        "count": len(rows),
        "wuxing": wx,
    }


def list_batches(kind: str = "") -> list[dict]:
    kid = _norm_kind(kind) if kind else None
    sql = (
        "SELECT batch_no, kind, kind_label, predicted_at, almanac_date, hour, "
        "wuxing, summary, stock_count FROM forecast_batch"
    )
    params: tuple = ()
    if kid:
        sql += " WHERE kind=?"
        params = (kid,)
    sql += " ORDER BY predicted_at DESC, batch_no DESC"
    out = []
    for r in query(sql, params):
        r["wuxing"] = _json_load(r.get("wuxing"))
        out.append(r)
    return out


def list_page(
    kind: str = "",
    batch_no: str = "",
    sort: str = "predicted_at",
    order: str = "desc",
) -> dict:
    kid = _norm_kind(kind) if kind else None
    sort_id = sort if sort in _SORT_SQL else "predicted_at"
    direction = "ASC" if str(order or "").lower() == "asc" else "DESC"
    where = ["1=1"]
    params: list = []
    if kid:
        where.append("b.kind=?")
        params.append(kid)
    batch_no = (batch_no or "").strip()
    if batch_no:
        where.append("b.batch_no=?")
        params.append(batch_no)
    sql = (
        "SELECT s.code, s.name, s.industry, s.wuxing, s.finance_grade, s.score, "
        "s.advice, s.buy_index, s.price, s.pct, s.wx_state, "
        "b.batch_no, b.kind, b.kind_label, b.predicted_at, b.almanac_date, b.hour, "
        "b.wuxing AS batch_wuxing, b.summary "
        "FROM forecast_stock s JOIN forecast_batch b ON b.batch_no=s.batch_no "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY {_SORT_SQL[sort_id]} {direction}, b.predicted_at DESC, s.code"
    )
    items = []
    for r in query(sql, tuple(params)):
        items.append({
            "code": r.get("code"),
            "name": r.get("name"),
            "industry": r.get("industry") or "",
            "wuxing": _json_load(r.get("wuxing")),
            "wx_state": _json_load(r.get("wx_state")),
            "finance_grade": r.get("finance_grade") or "",
            "score": r.get("score"),
            "advice": r.get("advice") or "",
            "buy_index": r.get("buy_index"),
            "price": r.get("price"),
            "pct": r.get("pct"),
            "batch_no": r.get("batch_no"),
            "kind": r.get("kind"),
            "kind_label": r.get("kind_label"),
            "predicted_at": r.get("predicted_at"),
            "almanac_date": r.get("almanac_date") or "",
            "hour": r.get("hour"),
            "batch_wuxing": _json_load(r.get("batch_wuxing")),
            "summary": r.get("summary") or "",
        })
    empty = ""
    if not items:
        empty = (
            "暂无已保存的预测推荐。请先在本页黄历里点「易经卜卦」或「奇门遁甲预测」，"
            "有匹配个股才会写入本地。"
        )
    return {
        "ok": True,
        "side": "forecast",
        "items": items,
        "count": len(items),
        "batches": list_batches(kid or ""),
        "kinds": [{"id": k, "name": v["label"]} for k, v in KINDS.items()],
        "sorts": [{"id": s["id"], "name": s["name"]} for s in SORTS],
        "sort": sort_id,
        "order": direction.lower(),
        "kind": kid or "",
        "batch_no": batch_no,
        "note": NOTE,
        "empty_reason": empty,
        "disclaimer": DISCLAIMER,
    }


def clear(batch_no: str = "", kind: str = "", clear_all: bool = False) -> dict:
    """手动清理。必须明确批次、类型或全部，避免误删。"""
    batch_no = (batch_no or "").strip()
    kid = _norm_kind(kind) if kind else None
    if clear_all:
        n = query("SELECT COUNT(*) AS n FROM forecast_stock")[0]["n"]
        execute("DELETE FROM forecast_stock")
        execute("DELETE FROM forecast_batch")
        return {"ok": True, "cleared": int(n or 0), "scope": "all"}
    if batch_no:
        n = query("SELECT COUNT(*) AS n FROM forecast_stock WHERE batch_no=?", (batch_no,))[0]["n"]
        execute("DELETE FROM forecast_stock WHERE batch_no=?", (batch_no,))
        execute("DELETE FROM forecast_batch WHERE batch_no=?", (batch_no,))
        return {"ok": True, "cleared": int(n or 0), "scope": "batch", "batch_no": batch_no}
    if kid:
        rows = query("SELECT batch_no FROM forecast_batch WHERE kind=?", (kid,))
        nos = [r["batch_no"] for r in rows]
        n = 0
        if nos:
            marks = ",".join("?" * len(nos))
            n = query(
                f"SELECT COUNT(*) AS n FROM forecast_stock WHERE batch_no IN ({marks})",
                tuple(nos),
            )[0]["n"]
            execute(f"DELETE FROM forecast_stock WHERE batch_no IN ({marks})", tuple(nos))
            execute("DELETE FROM forecast_batch WHERE kind=?", (kid,))
        return {"ok": True, "cleared": int(n or 0), "scope": "kind", "kind": kid}
    return {"ok": False, "error": "请指定批次号、预测类型或全部清理", "cleared": 0}
