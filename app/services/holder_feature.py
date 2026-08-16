"""从已缓存 stock_holders JSON 物化方案 I/J 特征。不打 HTTP，不从十大股东猜户数。"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..database import execute, executemany, query

log = logging.getLogger("holder_feature")
_TZ = ZoneInfo("Asia/Shanghai")

UNLOCK_SELL_DAYS = 10
UNLOCK_FLOAT_MIN = 3.0
HOLDERS_QOQ_SELL = 5.0
UNLOCK_BUY_LAG = 5


def _today() -> date:
    return datetime.now(_TZ).date()


def weekday_days_between(d0: date, d1: date) -> int:
    """d0→d1 的工作日数（剔除周末；法定节假日未内置）。同日为 0。"""
    if d1 == d0:
        return 0
    step = 1 if d1 > d0 else -1
    n = 0
    cur = d0
    while cur != d1:
        cur += timedelta(days=step)
        if cur.weekday() < 5:
            n += step
    return n


def _parse_day(raw) -> date | None:
    s = str(raw or "").strip()[:10]
    if len(s) < 10:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def ensure_tables() -> None:
    execute(
        "CREATE TABLE IF NOT EXISTS holder_feature ("
        "code TEXT PRIMARY KEY,"
        "asof TEXT,"
        "holders_qoq REAL,"
        "institution_ratio REAL,"
        "institution_count INTEGER,"
        "unlock_date TEXT,"
        "unlock_float_ratio REAL,"
        "unlock_days_to INTEGER,"
        "last_unlock_date TEXT,"
        "last_unlock_days_ago INTEGER,"
        "has_institution INTEGER DEFAULT 0)"
    )


def features_from_payload(code: str, payload: dict, asof_today: date | None = None) -> dict:
    """只读规范化持股字段。十大股东名单不参与户数推断。"""
    payload = payload or {}
    latest = payload.get("latest") if isinstance(payload.get("latest"), dict) else {}
    holders_qoq = latest.get("holders_qoq")
    try:
        holders_qoq = None if holders_qoq is None else float(holders_qoq)
    except (TypeError, ValueError):
        holders_qoq = None
    inst_ratio = payload.get("institution_ratio")
    inst_count = payload.get("institution_count")
    try:
        inst_ratio = None if inst_ratio is None else float(inst_ratio)
    except (TypeError, ValueError):
        inst_ratio = None
    try:
        inst_count = None if inst_count is None else int(inst_count)
    except (TypeError, ValueError):
        inst_count = None
    has_institution = 1 if (inst_ratio is not None or inst_count is not None) else 0
    today = asof_today or _today()
    future: list[dict] = []
    past: list[dict] = []
    for u in payload.get("unlocks") or []:
        if not isinstance(u, dict):
            continue
        d = _parse_day(u.get("date"))
        if d is None:
            continue
        try:
            ratio = None if u.get("float_ratio") is None else float(u.get("float_ratio"))
        except (TypeError, ValueError):
            ratio = None
        rec = {"d": d, "days": weekday_days_between(today, d), "float_ratio": ratio}
        if d >= today:
            future.append(rec)
        else:
            past.append(rec)
    nearest_future = None
    if future:
        nearest_future = min(future, key=lambda x: (x["days"], -(x.get("float_ratio") or 0)))
    nearest_past = max(past, key=lambda x: x["d"]) if past else None
    return {
        "code": code,
        "asof": (payload.get("asof") or today.isoformat())[:10],
        "holders_qoq": holders_qoq,
        "institution_ratio": inst_ratio,
        "institution_count": inst_count,
        "unlock_date": nearest_future["d"].isoformat() if nearest_future else None,
        "unlock_float_ratio": nearest_future.get("float_ratio") if nearest_future else None,
        "unlock_days_to": nearest_future["days"] if nearest_future else None,
        "last_unlock_date": nearest_past["d"].isoformat() if nearest_past else None,
        "last_unlock_days_ago": abs(nearest_past["days"]) if nearest_past else None,
        "has_institution": has_institution,
    }


def upsert_from_payload(code: str, payload: dict, asof_today: date | None = None) -> dict:
    ensure_tables()
    feat = features_from_payload(code, payload, asof_today)
    execute(
        "INSERT OR REPLACE INTO holder_feature("
        "code,asof,holders_qoq,institution_ratio,institution_count,"
        "unlock_date,unlock_float_ratio,unlock_days_to,"
        "last_unlock_date,last_unlock_days_ago,has_institution)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            feat["code"], feat["asof"], feat["holders_qoq"], feat["institution_ratio"],
            feat["institution_count"], feat["unlock_date"], feat["unlock_float_ratio"],
            feat["unlock_days_to"], feat["last_unlock_date"], feat["last_unlock_days_ago"],
            feat["has_institution"],
        ),
    )
    return feat


def refresh_all_features(asof_today: date | None = None) -> dict:
    """只扫已缓存 stock_holders，不请求网络。无缓存则特征表为空，I/J 零命中。"""
    ensure_tables()
    try:
        rows = query("SELECT code, payload FROM stock_holders")
    except Exception:  # noqa: BLE001
        return {"ok": True, "rows": 0, "note": "无持股缓存表"}
    n = 0
    batch = []
    today = asof_today or _today()
    for r in rows:
        try:
            payload = json.loads(r.get("payload") or "{}")
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(payload, dict):
            continue
        feat = features_from_payload(r["code"], payload, today)
        batch.append((
            feat["code"], feat["asof"], feat["holders_qoq"], feat["institution_ratio"],
            feat["institution_count"], feat["unlock_date"], feat["unlock_float_ratio"],
            feat["unlock_days_to"], feat["last_unlock_date"], feat["last_unlock_days_ago"],
            feat["has_institution"],
        ))
        n += 1
    if batch:
        executemany(
            "INSERT OR REPLACE INTO holder_feature("
            "code,asof,holders_qoq,institution_ratio,institution_count,"
            "unlock_date,unlock_float_ratio,unlock_days_to,"
            "last_unlock_date,last_unlock_days_ago,has_institution)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            batch,
        )
    return {"ok": True, "rows": n}


def feature_count() -> int:
    ensure_tables()
    try:
        return int((query("SELECT COUNT(*) AS n FROM holder_feature")[0] or {}).get("n") or 0)
    except Exception:  # noqa: BLE001
        return 0


def near_unlock_codes(days: int = UNLOCK_SELL_DAYS, min_float: float = UNLOCK_FLOAT_MIN) -> set[str] | None:
    """未来 days 个工作日内、解禁占流通≥min_float 的代码。无特征行则返回 None（规则跳过）。"""
    if feature_count() <= 0:
        return None
    rows = query(
        "SELECT code FROM holder_feature WHERE unlock_days_to IS NOT NULL "
        "AND unlock_days_to BETWEEN 0 AND ? AND unlock_float_ratio IS NOT NULL "
        "AND unlock_float_ratio >= ?",
        (int(days), float(min_float)),
    )
    return {r["code"] for r in rows if r.get("code")}
