"""个股财务报表分析（FR3-03）与全市场财报评级（FR10-01）。

评级规则独立于购买指数，不并入综合评分权重。
"""
import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from ..cache import cached
from ..database import execute, executemany, get_meta, query, set_meta
from ..datasources import sina

log = logging.getLogger("finance")
_lock = threading.Lock()
_STATE = {
    "running": False, "progress": 0, "total": 0, "stage": "未开始",
    "graded": 0, "fetched": 0,
}
_TZ = ZoneInfo("Asia/Shanghai")


def _now() -> str:
    return datetime.now(_TZ).isoformat(timespec="seconds")


def _load_db(code: str) -> list[dict]:
    return query(
        "SELECT * FROM finance_report WHERE code=? ORDER BY report_date DESC LIMIT 6", (code,))


def _persist(code: str, rows: list[dict]) -> None:
    now = _now()
    executemany(
        "INSERT OR REPLACE INTO finance_report(code,report_date,report_name,revenue,revenue_yoy,"
        "net_profit,net_profit_yoy,parent_profit,parent_profit_yoy,fetched_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        [(code, r["report_date"], r.get("report_name"),
          r.get("revenue"), r.get("revenue_yoy"),
          r.get("net_profit"), r.get("net_profit_yoy"),
          r.get("parent_profit"), r.get("parent_profit_yoy"), now) for r in rows])


def _build_reports(raw_rows: list[dict]) -> list[dict]:
    reports = []
    for r in raw_rows[:5]:
        rev, profit = r.get("revenue"), r.get("parent_profit") or r.get("net_profit")
        margin = round(profit / rev * 100, 1) if rev and profit is not None else None
        reports.append({
            "date": r["report_date"], "name": r.get("report_name") or r["report_date"],
            "revenue_yi": round(rev / 1e8, 2) if rev is not None else None,
            "revenue_yoy": r.get("revenue_yoy"),
            "profit_yi": round(profit / 1e8, 2) if profit is not None else None,
            "profit_yoy": r.get("parent_profit_yoy") or r.get("net_profit_yoy"),
            "margin": margin,
        })
    return reports


def grade_from_reports(reports: list[dict]) -> dict:
    """由已整理的报告期计算 A/B/C/D。无报告返回 grade=None。"""
    if not reports:
        return {"grade": None, "grade_desc": "暂无财务数据", "score": None,
                "summary": "暂无财务数据"}
    latest = reports[0]
    rev_up_streak = 0
    for r in reports:
        if r["revenue_yoy"] is not None and r["revenue_yoy"] > 0:
            rev_up_streak += 1
        else:
            break

    parts = []
    if rev_up_streak >= 2:
        parts.append(f"营收连续 {rev_up_streak} 期正增长")
    elif latest["revenue_yoy"] is not None:
        parts.append(f"最新营收同比 {latest['revenue_yoy']:+.1f}%")
    if latest["profit_yoy"] is not None:
        parts.append(f"最新一期归母净利润同比 {latest['profit_yoy']:+.1f}%")
    if latest["margin"] is not None:
        parts.append(f"净利率 {latest['margin']}%")

    score = 0
    if latest["revenue_yoy"] is not None:
        score += 2 if latest["revenue_yoy"] > 20 else 1 if latest["revenue_yoy"] > 0 else -1
    if latest["profit_yoy"] is not None:
        score += 2 if latest["profit_yoy"] > 30 else 1 if latest["profit_yoy"] > 0 else -2
    if latest["margin"] is not None:
        score += 1 if latest["margin"] > 10 else 0 if latest["margin"] > 3 else -1
    score += 1 if rev_up_streak >= 3 else 0
    if score >= 5:
        grade, grade_desc = "A", "成长性良好，盈利质量较高"
    elif score >= 3:
        grade, grade_desc = "B", "基本面稳健，增长中规中矩"
    elif score >= 0:
        grade, grade_desc = "C", "增长乏力或盈利承压，关注拐点"
    else:
        grade, grade_desc = "D", "营收利润双承压，谨慎对待"

    summary = ("，".join(parts) + f"。财务健康评级 {grade}：{grade_desc}。"
               if parts else f"财务健康评级 {grade}：{grade_desc}。")
    return {"grade": grade, "grade_desc": grade_desc, "score": score, "summary": summary}


def _save_grade(code: str, graded: dict) -> None:
    if not graded.get("grade"):
        return
    execute(
        "INSERT INTO stock_finance_grade(code,grade,score,summary,updated_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(code) DO UPDATE SET grade=excluded.grade, score=excluded.score, "
        "summary=excluded.summary, updated_at=excluded.updated_at",
        (code, graded["grade"], graded.get("score"), graded.get("summary") or "", _now()),
    )


def _analyze(code: str, raw_rows: list[dict], offline: bool) -> dict:
    reports = _build_reports(raw_rows)
    graded = grade_from_reports(reports)
    if graded.get("grade"):
        _save_grade(code, graded)
    return {
        "code": code, "reports": reports,
        "summary": graded["summary"], "grade": graded["grade"],
        "grade_desc": graded["grade_desc"], "score": graded.get("score"),
        "offline": offline,
        "source": "本地缓存" if offline else "新浪财经（披露数据）",
    }


def get_finance(code: str) -> dict:
    """财务分析：优先网络（缓存24h），失败回退本地库。code 形如 sz300432。"""
    digits = code[-6:]

    def loader():
        try:
            rows = sina.fetch_finance_reports(digits, num=6)
            if rows:
                _persist(code, rows)
                return {"rows": rows, "offline": False}
        except Exception:  # noqa: BLE001
            pass
        db_rows = _load_db(code)
        return {"rows": db_rows, "offline": True} if db_rows else None

    data = cached(f"finance:{code}", 86400, loader)
    if not data or not data["rows"]:
        return {"code": code, "reports": [], "summary": "暂无财务数据", "grade": None}
    return _analyze(code, data["rows"], data["offline"])


def attach_grades(rows: list[dict], code_key: str = "code") -> list[dict]:
    """批量为列表项附加 finance_grade / finance_summary（就地修改）。"""
    codes = [r.get(code_key) for r in rows if r.get(code_key)]
    if not codes:
        return rows
    found = {}
    chunk = 400
    for i in range(0, len(codes), chunk):
        part = codes[i:i + chunk]
        ph = ",".join("?" * len(part))
        for g in query(
            f"SELECT code, grade, summary FROM stock_finance_grade WHERE code IN ({ph})",
            tuple(part),
        ):
            found[g["code"]] = g
    for r in rows:
        g = found.get(r.get(code_key)) or {}
        r["finance_grade"] = g.get("grade")
        r["finance_summary"] = g.get("summary") or ""
    return rows


def stats() -> dict:
    graded = query(
        "SELECT COUNT(*) AS n FROM stock_finance_grade WHERE grade IN ('A','B','C','D')")[0]["n"]
    universe = query("SELECT COUNT(*) AS n FROM stock_snapshot")[0]["n"]
    dist = {r["grade"]: r["n"] for r in query(
        "SELECT grade, COUNT(*) AS n FROM stock_finance_grade GROUP BY grade")}
    return {
        **_STATE,
        "graded": graded, "universe": universe,
        "distribution": dist,
        "last_rebuild": get_meta("finance_grade_last_rebuild", "从未"),
    }


def state() -> dict:
    return stats()


def rebuild_all(max_fetch: int = 80) -> dict:
    """后台重建：先对已有财报评级，再按成交额拉取未评级股票（限速，不阻塞启动）。"""
    if not _lock.acquire(blocking=False):
        return state()
    try:
        _STATE.update(running=True, progress=0, total=0, stage="已有财报评级", graded=0, fetched=0)
        codes = [r["code"] for r in query("SELECT DISTINCT code FROM finance_report")]
        _STATE["total"] = len(codes)
        for i, code in enumerate(codes, 1):
            raw = _load_db(code)
            if raw:
                _analyze(code, raw, offline=True)
                _STATE["graded"] += 1
            _STATE["progress"] = i
        have = {r["code"] for r in query("SELECT code FROM stock_finance_grade")}
        todo = query(
            "SELECT code FROM stock_snapshot WHERE price IS NOT NULL "
            "AND name NOT LIKE '%ST%' AND name NOT LIKE '%退%' "
            "ORDER BY amount DESC")
        todo = [r["code"] for r in todo if r["code"] not in have][:max(0, int(max_fetch))]
        _STATE.update(stage="拉取披露数据", total=len(codes) + len(todo),
                      progress=len(codes))
        for code in todo:
            try:
                rows = sina.fetch_finance_reports(code[-6:], num=6)
                if rows:
                    _persist(code, rows)
                    _analyze(code, rows, offline=False)
                    _STATE["fetched"] += 1
                    _STATE["graded"] += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("财报拉取失败 %s: %s", code, exc)
            _STATE["progress"] += 1
            time.sleep(0.15)
        set_meta("finance_grade_last_rebuild", _now())
        _STATE.update(running=False, stage=f"完成，已评级 {_STATE['graded']} 只")
        return state()
    except Exception as exc:  # noqa: BLE001
        _STATE.update(running=False, stage=f"失败: {exc}")
        return state()
    finally:
        _lock.release()
