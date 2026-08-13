"""个股财务报表分析（FR3-03）：新浪真实披露数据 + 成长性评述 + 财务健康评级。"""
from datetime import datetime

from ..cache import cached
from ..database import executemany, query
from ..datasources import sina


def _load_db(code: str) -> list[dict]:
    return query(
        "SELECT * FROM finance_report WHERE code=? ORDER BY report_date DESC LIMIT 6", (code,))


def _persist(code: str, rows: list[dict]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    executemany(
        "INSERT OR REPLACE INTO finance_report(code,report_date,report_name,revenue,revenue_yoy,"
        "net_profit,net_profit_yoy,parent_profit,parent_profit_yoy,fetched_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        [(code, r["report_date"], r.get("report_name"),
          r.get("revenue"), r.get("revenue_yoy"),
          r.get("net_profit"), r.get("net_profit_yoy"),
          r.get("parent_profit"), r.get("parent_profit_yoy"), now) for r in rows])


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

    reports = []
    for r in data["rows"][:5]:
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

    latest = reports[0]
    rev_up_streak = 0
    for r in reports:
        if r["revenue_yoy"] is not None and r["revenue_yoy"] > 0:
            rev_up_streak += 1
        else:
            break

    # 成长性评述
    parts = []
    if rev_up_streak >= 2:
        parts.append(f"营收连续 {rev_up_streak} 期正增长")
    elif latest["revenue_yoy"] is not None:
        parts.append(f"最新营收同比 {latest['revenue_yoy']:+.1f}%")
    if latest["profit_yoy"] is not None:
        parts.append(f"最新一期归母净利润同比 {latest['profit_yoy']:+.1f}%")
    if latest["margin"] is not None:
        parts.append(f"净利率 {latest['margin']}%")

    # 健康评级
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

    summary = "，".join(parts) + f"。财务健康评级 {grade}：{grade_desc}。" if parts else f"财务健康评级 {grade}：{grade_desc}。"
    return {
        "code": code, "reports": reports, "summary": summary,
        "grade": grade, "grade_desc": grade_desc,
        "offline": data["offline"],
        "source": "本地缓存" if data["offline"] else "新浪财经（披露数据）",
    }
