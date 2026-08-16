"""本地模拟账本：快照价记一笔，T+1 才允许卖出。默认关，不连接券商。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ..database import execute, query

_TZ = ZoneInfo("Asia/Shanghai")


def _now() -> str:
    return datetime.now(_TZ).replace(tzinfo=None).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d")


def ensure_tables() -> None:
    execute(
        "CREATE TABLE IF NOT EXISTS paper_lot ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "code TEXT NOT NULL, name TEXT, side TEXT NOT NULL,"
        "qty REAL, price REAL, asof TEXT NOT NULL,"
        "created_at TEXT, status TEXT, note TEXT, linked_id INTEGER)"
    )


def enabled() -> bool:
    try:
        from . import engine as engine_svc
        return bool(engine_svc.get_config().get("paper_enabled"))
    except Exception:  # noqa: BLE001
        return False


def status() -> dict:
    ensure_tables()
    on = enabled()
    n = int((query("SELECT COUNT(*) AS n FROM paper_lot")[0] or {}).get("n") or 0)
    return {
        "ok": True,
        "enabled": on,
        "lots": n,
        "note": (
            "已启用本地模拟账本。价格用当时快照现价，T+1 后才允许记卖出。不连接券商，不自动跟策略下单。"
            if on else
            "模拟账本默认关闭。到设置→策略引擎配置打开「本地模拟账本」后，可在策略选股结果上记一笔。不连接券商。"
        ),
    }


def list_lots(limit: int = 80) -> dict:
    ensure_tables()
    rows = query(
        "SELECT * FROM paper_lot ORDER BY id DESC LIMIT ?",
        (max(5, min(int(limit or 80), 200)),),
    )
    st = status()
    return {**st, "items": rows, "count": len(rows)}


def fill(code: str, side: str, qty: float = 100, price: float | None = None,
         name: str = "", asof: str | None = None) -> dict:
    ensure_tables()
    if not enabled():
        return {
            "ok": False,
            "error": "模拟账本未启用。到设置→策略引擎配置打开「本地模拟账本」。不连接券商。",
        }
    code = (code or "").strip()
    if not code:
        return {"ok": False, "error": "缺少股票代码"}
    side = "sell" if side == "sell" else "buy"
    asof = (asof or _today())[:10]
    try:
        qty = float(qty or 100)
    except (TypeError, ValueError):
        qty = 100.0
    if qty <= 0:
        return {"ok": False, "error": "数量必须为正"}
    if price is None:
        snap = query("SELECT price, name FROM stock_snapshot WHERE code=?", (code,))
        if snap:
            price = snap[0].get("price")
            name = name or (snap[0].get("name") or "")
    try:
        price = None if price is None else float(price)
    except (TypeError, ValueError):
        price = None
    if price is None or price <= 0:
        return {"ok": False, "error": "无快照现价，不编造成交价"}
    if side == "buy":
        execute(
            "INSERT INTO paper_lot(code,name,side,qty,price,asof,created_at,status,note,linked_id)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (code, name or "", "buy", qty, price, asof, _now(), "open", "模拟买入，非券商成交", None),
        )
        rid = (query(
            "SELECT id FROM paper_lot WHERE code=? AND side='buy' ORDER BY id DESC LIMIT 1",
            (code,),
        ) or [{}])[0].get("id")
        return {"ok": True, "id": rid, "side": "buy", "code": code, "price": price, "asof": asof,
                "note": "已记模拟买入。当日不可卖出（T+1）。不连接券商。"}
    opens = query(
        "SELECT * FROM paper_lot WHERE code=? AND side='buy' AND status='open' ORDER BY asof, id",
        (code,),
    )
    if not opens:
        return {"ok": False, "error": "没有可卖的模拟持仓"}
    lot = None
    for row in opens:
        if str(row.get("asof") or "") < asof:
            lot = row
            break
    if lot is None:
        return {"ok": False, "error": "T+1：买入当日不能记卖出"}
    execute(
        "UPDATE paper_lot SET status='closed', note=? WHERE id=?",
        (f"已于 {asof} 模拟卖出", lot["id"]),
    )
    execute(
        "INSERT INTO paper_lot(code,name,side,qty,price,asof,created_at,status,note,linked_id)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (code, name or lot.get("name") or "", "sell", qty, price, asof, _now(), "filled",
         "模拟卖出，非券商成交", lot["id"]),
    )
    rid = (query(
        "SELECT id FROM paper_lot WHERE code=? AND side='sell' ORDER BY id DESC LIMIT 1",
        (code,),
    ) or [{}])[0].get("id")
    ret = round((price - float(lot["price"])) / float(lot["price"]) * 100.0, 2) if lot.get("price") else None
    return {
        "ok": True, "id": rid, "side": "sell", "code": code, "price": price, "asof": asof,
        "linked_id": lot["id"], "ret_pct": ret,
        "note": "已记模拟卖出。不连接券商。",
    }
