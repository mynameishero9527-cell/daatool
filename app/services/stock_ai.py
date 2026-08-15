"""个股右键 AI 简明诊断 / 五行判定：成功结果落本地，失败不覆盖。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from ..database import execute, query
from . import ai as ai_svc
from . import market as market_svc

log = logging.getLogger("stock_ai")
_TZ = ZoneInfo("Asia/Shanghai")

KIND_DIAGNOSE = "diagnose"
KIND_WUXING = "wuxing"


def _now() -> str:
    return datetime.now(_TZ).replace(tzinfo=None).isoformat(timespec="seconds")


def ensure_tables() -> None:
    execute(
        "CREATE TABLE IF NOT EXISTS stock_ai_brief ("
        "code TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,"
        "updated_at TEXT NOT NULL, PRIMARY KEY (code, kind))"
    )


def _empty(kind: str = KIND_DIAGNOSE) -> dict:
    return {
        "ok": True,
        "kind": kind,
        "applied": False,
        "ai": False,
        "text": "",
        "source": "",
        "updated_at": "",
        "analyzed_at": "",
        "error": "",
        "hint": "",
        "kept": False,
        "tags": [],
        "model": "",
    }


def _norm(code: str) -> str:
    return market_svc.normalize_code(code) or (code or "").strip().lower()


def load_kind(code: str, kind: str) -> dict | None:
    ensure_tables()
    code = _norm(code)
    if not code or kind not in (KIND_DIAGNOSE, KIND_WUXING):
        return None
    try:
        rows = query(
            "SELECT payload, updated_at FROM stock_ai_brief WHERE code=? AND kind=?",
            (code, kind),
        )
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    try:
        data = json.loads(rows[0].get("payload") or "{}")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or not (data.get("text") or "").strip():
        return None
    stamp = rows[0].get("updated_at") or data.get("analyzed_at") or data.get("updated_at") or ""
    tags = data.get("tags") if isinstance(data.get("tags"), list) else []
    tags = [str(t) for t in tags if t]
    return {
        "applied": True,
        "ai": bool(data.get("ai")),
        "kind": kind,
        "text": data.get("text") or "",
        "source": data.get("source") or "",
        "updated_at": stamp,
        "analyzed_at": data.get("analyzed_at") or stamp,
        "error": "",
        "hint": "",
        "kept": False,
        "tags": tags,
        "model": data.get("model") or "",
    }


def save_kind(code: str, kind: str, payload: dict) -> dict:
    ensure_tables()
    code = _norm(code)
    now = _now()
    body = dict(payload or {})
    body["analyzed_at"] = now
    body["updated_at"] = now
    body["kind"] = kind
    execute(
        "INSERT OR REPLACE INTO stock_ai_brief(code, kind, payload, updated_at) VALUES(?,?,?,?)",
        (code, kind, json.dumps(body, ensure_ascii=False), now),
    )
    return load_kind(code, kind) or _empty(kind)


def load_brief(code: str) -> dict:
    return load_kind(code, KIND_DIAGNOSE) or _empty(KIND_DIAGNOSE)


def load_wuxing(code: str) -> dict:
    return load_kind(code, KIND_WUXING) or _empty(KIND_WUXING)


def bundle(code: str) -> dict:
    """个股分析页一次取齐：简明诊断 + 五行判定。"""
    code = _norm(code)
    return {
        "ok": True,
        "code": code,
        "brief": load_brief(code),
        "wuxing": load_wuxing(code),
    }


def _keep(prev: dict | None, kind: str, error: str = "", hint: str = "",
          source: str = "", text: str = "") -> dict:
    if prev and (prev.get("text") or "").strip():
        return {
            **prev,
            "ok": True,
            "applied": False,
            "kept": True,
            "error": error,
            "hint": hint,
        }
    body = (text or "").strip()
    if body and ai_svc.DISCLAIMER.strip() not in body:
        body = body + ai_svc.DISCLAIMER
    out = _empty(kind)
    out.update({
        "ok": True,
        "text": body,
        "source": source,
        "error": error,
        "hint": hint,
        "kept": False,
    })
    return out


def analyze_brief(code: str) -> dict:
    """右键/手动：个股简明诊断。LLM 成功必落库；失败不覆盖上次成功结果。"""
    code = _norm(code)
    if not code:
        return {**_empty(KIND_DIAGNOSE), "ok": False, "error": "未指定股票"}
    prev = load_kind(code, KIND_DIAGNOSE)
    result = ai_svc.analyze("stock", code)
    text = (result.get("text") or "").strip()
    is_ai = bool(result.get("ai"))
    err = result.get("error") or ""
    hint = result.get("hint") or ""
    source = result.get("source") or ""

    if is_ai and len(text) >= 20:
        saved = save_kind(code, KIND_DIAGNOSE, {
            "text": text,
            "source": source,
            "ai": True,
            "model": (ai_svc.get_config(masked=False).get("model") or ""),
        })
        return {
            **saved,
            "ok": True,
            "applied": True,
            "ai": True,
            "kept": False,
            "error": "",
            "hint": "",
            "code": code,
            "wuxing": load_wuxing(code),
        }

    # 未配置或本地兜底：没有旧的大模型结果时也落库，便于回显；有旧 LLM 则不覆盖
    prev_is_ai = bool(prev and prev.get("ai"))
    if text and not prev_is_ai and not err:
        saved = save_kind(code, KIND_DIAGNOSE, {
            "text": text,
            "source": source,
            "ai": False,
            "model": "",
        })
        return {
            **saved,
            "ok": True,
            "applied": True,
            "ai": False,
            "kept": False,
            "error": err,
            "hint": hint,
            "code": code,
            "wuxing": load_wuxing(code),
        }
    kept = _keep(prev, KIND_DIAGNOSE, error=err or "未写入简明诊断",
                 hint=hint or "失败不覆盖已保存结果。", source=source, text=text)
    kept["wuxing"] = load_wuxing(code)
    kept["code"] = code
    return kept


def save_wuxing_result(code: str, result: dict) -> dict:
    """五行右键：仅在大模型成功（或已回填标签）时写入文本，失败不覆盖。"""
    code = _norm(code)
    if not code:
        return _empty(KIND_WUXING)
    prev = load_kind(code, KIND_WUXING)
    text = (result.get("text") or "").strip()
    source = result.get("source") or ""
    is_ai = "AI大模型" in source
    err = result.get("error") or ""
    if is_ai and text and not err:
        saved = save_kind(code, KIND_WUXING, {
            "text": text if ai_svc.DISCLAIMER.strip() in text else text + ai_svc.DISCLAIMER,
            "source": source,
            "ai": True,
            "tags": result.get("tags") or [],
            "applied_tags": bool(result.get("applied")),
        })
        return saved
    if prev:
        return prev
    return _empty(KIND_WUXING)
