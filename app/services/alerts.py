"""盘面智能提醒中心（FR4-01）：每 10 分钟生成五类提醒并落库。

类型：buy_point 最佳买点 / sell_point 最佳卖点 / index_move 大盘异动
     / fund_switch 资金高低切换 / rotation 板块轮动。
"""
import logging
from datetime import datetime

from ..database import execute, get_meta_json, query, set_meta_json
from . import kline as kline_svc

log = logging.getLogger("alerts")

TYPE_NAMES = {
    "buy_point": "买点关注", "sell_point": "卖点警示", "index_move": "大盘异动",
    "fund_switch": "高低切换", "rotation": "板块轮动",
}


def _add(alert_type: str, title: str, detail: str = "") -> None:
    execute("INSERT INTO alert_log(alert_type,title,detail,created_at) VALUES(?,?,?,?)",
            (alert_type, title, detail, datetime.now().isoformat(timespec="seconds")))


def _scan_buy_points() -> None:
    rows = query(
        """SELECT s.code, s.name, s.main_net_in, m.buy_index, m.pos60
           FROM stock_metrics m JOIN stock_snapshot s ON s.code = m.code
           WHERE m.buy_index >= 80 AND s.main_net_in > 0
             AND s.name NOT LIKE '%ST%' ORDER BY m.buy_index DESC LIMIT 5""")
    for r in rows:
        _add("buy_point",
             f"{r['name']}（{r['code']}）购买指数 {r['buy_index']:.0f}，极佳买点·可分批建仓",
             f"主力净流入 {r['main_net_in'] / 10000:.2f} 亿，60日区间 {r['pos60'] * 100:.0f}% 分位")


def _scan_sell_points() -> None:
    rows = query(
        """SELECT s.code, s.name, s.main_net_in, m.buy_index, m.sentiment
           FROM stock_metrics m JOIN stock_snapshot s ON s.code = m.code
           WHERE (m.buy_index <= 30 OR (s.main_net_in < -8000 AND m.sentiment >= 80))
             AND s.name NOT LIKE '%ST%' ORDER BY m.buy_index ASC LIMIT 5""")
    for r in rows:
        extra = "，情绪过热注意兑现" if (r["sentiment"] or 0) >= 80 else ""
        _add("sell_point",
             f"{r['name']}（{r['code']}）购买指数 {r['buy_index']:.0f}，高风险位置·建议回避{extra}",
             f"主力净流{'入' if (r['main_net_in'] or 0) > 0 else '出'} {abs(r['main_net_in'] or 0) / 10000:.2f} 亿")


def _scan_index_move() -> None:
    try:
        minute = kline_svc.get_minute("sh000001")
    except Exception:  # noqa: BLE001
        return
    points = minute.get("points") or []
    if len(points) < 11 or minute.get("offline"):
        return
    last, prev10 = points[-1][1], points[-11][1]
    move = (last - prev10) / prev10 * 100 if prev10 else 0
    if abs(move) >= 0.3:
        direction = "急拉" if move > 0 else "跳水"
        _add("index_move", f"大盘{direction}：上证指数 10 分钟内 {move:+.2f}%",
             f"最新 {last:.2f}，请留意仓位与节奏")


def _industry_flow() -> list[dict]:
    return query(
        """SELECT l.industry AS name,
                  SUM(s.main_net_in) / 10000.0 AS net_in_yi,
                  AVG(s.pct_d20) AS pct_d20
           FROM stock_snapshot s JOIN stock_list l ON l.code = s.code
           WHERE l.industry != '' AND s.main_net_in IS NOT NULL
           GROUP BY l.industry""")


def _scan_fund_switch() -> None:
    rows = _industry_flow()
    if len(rows) < 16:
        return
    by_pos = sorted(rows, key=lambda r: r["pct_d20"] or 0, reverse=True)
    high, low = by_pos[:8], by_pos[-8:]
    high_flow = sum(r["net_in_yi"] or 0 for r in high)
    low_flow = sum(r["net_in_yi"] or 0 for r in low)
    if high_flow < -3 and low_flow > 3:
        out_names = "、".join(r["name"] for r in sorted(high, key=lambda r: r["net_in_yi"])[:3])
        in_names = "、".join(r["name"] for r in sorted(low, key=lambda r: -(r["net_in_yi"] or 0))[:3])
        _add("fund_switch",
             f"高低切换信号：资金流出高位板块（{out_names}），流入低位板块（{in_names}）",
             f"高位组净流出 {abs(high_flow):.1f} 亿，低位组净流入 {low_flow:.1f} 亿")


def _scan_rotation() -> None:
    rows = _industry_flow()
    if not rows:
        return
    top3 = [r["name"] for r in sorted(rows, key=lambda r: -(r["net_in_yi"] or 0))[:3]]
    bottom = [r["name"] for r in sorted(rows, key=lambda r: r["net_in_yi"] or 0)[:2]]
    prev_top = get_meta_json("rotation_prev_top", [])
    if prev_top and set(top3[:2]) != set(prev_top[:2]):
        _add("rotation",
             f"板块轮动：今日资金主攻 {'、'.join(top3)}（此前：{'、'.join(prev_top[:3]) or '无记录'}）",
             f"资金撤离：{'、'.join(bottom)}")
    set_meta_json("rotation_prev_top", top3)


def scan_all() -> int:
    """一轮全量扫描（调度器每 10 分钟触发）。"""
    before = query("SELECT COUNT(*) AS n FROM alert_log")[0]["n"]
    for fn in (_scan_buy_points, _scan_sell_points, _scan_index_move,
               _scan_fund_switch, _scan_rotation):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            log.warning("提醒扫描 %s 失败: %s", fn.__name__, exc)
    # 只保留最近 500 条
    execute("DELETE FROM alert_log WHERE id NOT IN (SELECT id FROM alert_log ORDER BY id DESC LIMIT 500)")
    return query("SELECT COUNT(*) AS n FROM alert_log")[0]["n"] - before


def get_alerts(limit: int = 50) -> dict:
    rows = query("SELECT alert_type, title, detail, created_at FROM alert_log "
                 "ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["type_name"] = TYPE_NAMES.get(r["alert_type"], r["alert_type"])
    return {"items": rows}
