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


def _add(alert_type: str, title: str, detail: str = "", plan_id: str = "") -> None:
    execute("INSERT INTO alert_log(alert_type,title,detail,created_at,plan_id) VALUES(?,?,?,?,?)",
            (alert_type, title, detail, datetime.now().isoformat(timespec="seconds"), plan_id or ""))


def _flow_yi(v) -> str:
    n = abs(v or 0) / 10000
    return f"{n:.2f} 亿"


def _scan_buy_points() -> None:
    from . import strategy as strategy_svc
    enabled = strategy_svc.get_enabled()
    cap = min(18, max(5, 3 * max(1, len(enabled))))
    rows = strategy_svc.collect_hits("buy", enabled, limit=cap)
    for r in rows:
        picked = r.get("picked_text") or "由方案A选出"
        bi = r.get("buy_index")
        bi_txt = f"{bi:.0f}" if bi is not None else "-"
        pos = r.get("pos60")
        pos_txt = f"{pos * 100:.0f}% 分位" if pos is not None else "—"
        _add("buy_point",
             f"{r['name']}（{r['code']}）{picked}，{r.get('hit_action') or '策略买点'}，购买指数 {bi_txt}",
             f"{picked}；主力净流入 {_flow_yi(r.get('main_net_in'))}，60日区间 {pos_txt}",
             r.get("plan_id") or "")


def _scan_sell_points() -> None:
    from . import strategy as strategy_svc
    enabled = strategy_svc.get_enabled()
    cap = min(18, max(5, 3 * max(1, len(enabled))))
    rows, _src, _note = strategy_svc.collect_sell_points(limit=cap)
    for r in rows:
        picked = r.get("picked_text") or "由方案A选出"
        bi = r.get("buy_index")
        bi_txt = f"{bi:.0f}" if bi is not None else "-"
        extra = "，情绪过热注意兑现" if (r.get("sentiment") or 0) >= 80 else ""
        net = r.get("main_net_in") or 0
        _add("sell_point",
             f"{r['name']}（{r['code']}）{picked}，{r.get('hit_action') or '策略卖点'}{extra}，购买指数 {bi_txt}",
             f"{picked}；主力净流{'入' if net > 0 else '出'} {_flow_yi(net)}",
             r.get("plan_id") or "")


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
    """一轮全量扫描（调度器每 10 分钟触发）。买卖点每轮替换上一批（轮动推送）。"""
    before = query("SELECT COUNT(*) AS n FROM alert_log")[0]["n"]
    execute("DELETE FROM alert_log WHERE alert_type IN ('buy_point','sell_point')")
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
    from . import strategy as strategy_svc
    rows = query("SELECT alert_type, title, detail, created_at, COALESCE(plan_id,'') AS plan_id "
                 "FROM alert_log ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["type_name"] = TYPE_NAMES.get(r["alert_type"], r["alert_type"])
        pid = (r.get("plan_id") or "").strip()
        plans = [p for p in pid.split(",") if p] if pid else []
        r["plans"] = plans
        r["plan_labels"] = [strategy_svc.plan_caption(p) for p in plans]
        r["picked_text"] = f"由{'、'.join(r['plan_labels'])}选出" if r["plan_labels"] else ""
        title = r.get("title") or ""
        m = None
        if "（" in title and "）" in title:
            inner = title.split("（", 1)[1].split("）", 1)[0]
            name = title.split("（", 1)[0].strip()
            if inner and name:
                m = (name, inner)
        if m:
            r["name"], r["code"] = m[0], m[1]
        else:
            r["name"], r["code"] = "", ""
    watched = {row["code"] for row in query("SELECT code FROM watchlist")}
    for r in rows:
        r["in_watchlist"] = bool(r.get("code") and r["code"] in watched)
    return {"items": rows}


def _decorate_buy_rows(rows: list[dict], kind: str = "buy") -> None:
    from . import finance as finance_svc
    from . import metrics as metrics_svc
    from . import rating as rating_svc
    from . import wuxing as wuxing_svc
    try:
        finance_svc.attach_grades(rows)
    except Exception:  # noqa: BLE001
        pass
    _attach_board_heat(rows)
    try:
        wuxing_svc.tags_for_list(rows)
    except Exception:  # noqa: BLE001
        for r in rows:
            r["wuxing"] = r.get("wuxing") or []
    heat_map = _sector_heat_map()
    for r in rows:
        try:
            buy_lv, buy_act = metrics_svc.buy_index_level(r.get("buy_index") or 0)
            sent_lv, sent_ds = metrics_svc.sentiment_level(r.get("sentiment") or 50)
            _score, op = rating_svc.quick_score(r)
            r["buy_level"] = buy_lv
            r["sent_level"] = sent_lv
            r["sent_desc"] = sent_ds
            r["score"] = _score
            r["op_advice"] = op
            heat = r.get("sentiment")
            r["heat"] = None if heat is None else round(float(heat), 1)
            r["heat_level"] = sent_lv
            industry = (r.get("industry") or "").strip()
            r["sector_hot"] = heat_map.get(industry)
            r["sector_hot_level"] = _sector_hot_level(r["sector_hot"])
            picked = (r.get("picked_text") or "").strip()
            hit = (r.get("hit_action") or "").strip()
            if kind == "sell":
                bits = [x for x in (picked, hit, f"购买指数 {buy_lv}") if x]
            else:
                bits = [x for x in (picked, hit, f"{buy_lv}，{buy_act}", f"操作参考：{op}") if x]
            r["advice"] = "；".join(bits)
            r["advice_summary"] = _advice_summary(r, kind, buy_lv, buy_act, op)
            r["finance_grade"] = r.get("finance_grade") or ""
        except Exception:  # noqa: BLE001
            r["buy_level"] = r.get("buy_level") or ""
            r["advice"] = r.get("advice") or r.get("picked_text") or ""
            r["advice_summary"] = r.get("advice_summary") or r.get("advice") or ""
            r["finance_grade"] = r.get("finance_grade") or ""
            r["wuxing"] = r.get("wuxing") or []
    watched = {row["code"] for row in query("SELECT code FROM watchlist")}
    for r in rows:
        r["in_watchlist"] = bool(r.get("code") and r["code"] in watched)


def _sector_hot_level(score) -> str:
    if score is None:
        return "—"
    if score >= 15:
        return "高热"
    if score >= 5:
        return "偏热"
    if score >= -5:
        return "平淡"
    return "低迷"


def _sector_heat_map() -> dict:
    rows = query(
        """SELECT l.industry AS name,
                  ROUND(AVG(s.pct), 2) AS pct,
                  ROUND(AVG(s.pct_d5), 2) AS d5,
                  ROUND(SUM(s.main_net_in) / 10000.0, 1) AS net_in_yi
           FROM stock_snapshot s JOIN stock_list l ON l.code = s.code
           WHERE l.industry != '' AND s.pct IS NOT NULL
           GROUP BY l.industry"""
    )
    out = {}
    for r in rows:
        out[r["name"]] = round(
            (r["pct"] or 0) * 3 + (r["d5"] or 0) * 1.5
            + min(max((r["net_in_yi"] or 0), -20), 20), 1)
    return out


def _attach_board_heat(rows: list[dict]) -> None:
    codes = [r["code"] for r in rows if r.get("code")]
    cmap: dict[str, list[str]] = {}
    if codes:
        marks = ",".join("?" * len(codes))
        for row in query(
            f"SELECT code, concept FROM concept_map WHERE code IN ({marks})",
            tuple(codes),
        ):
            cmap.setdefault(row["code"], []).append(row["concept"])
    for r in rows:
        cons = (cmap.get(r.get("code") or "") or [])[:3]
        r["concepts"] = cons
        industry = (r.get("industry") or "").strip() or "未分类"
        r["board_text"] = f"{industry}（{'、'.join(cons)}）" if cons else industry


def _advice_summary(r: dict, kind: str, buy_lv: str, buy_act: str, op: str) -> str:
    picked = (r.get("picked_text") or "").strip()
    hit = (r.get("hit_action") or "").strip()
    board = (r.get("board_text") or r.get("industry") or "未分类").strip()
    wx = "、".join(r.get("wuxing") or []) or "未标注"
    heat = r.get("heat")
    heat_lv = r.get("heat_level") or ""
    heat_txt = f"{heat:.0f}（{heat_lv}）" if heat is not None else "—"
    sec_hot = r.get("sector_hot")
    sec_lv = r.get("sector_hot_level") or ""
    sec_txt = f"{sec_hot:.1f}（{sec_lv}）" if sec_hot is not None else "—"
    pos = r.get("pos60")
    pos_txt = f"60日位置 {pos * 100:.0f}% 分位" if pos is not None else ""
    net = r.get("main_net_in")
    if net is None:
        flow_txt = ""
    elif net > 0:
        flow_txt = f"主力净流入 {_flow_yi(net)}"
    else:
        flow_txt = f"主力净流出 {_flow_yi(net)}"
    from . import rating as rating_svc
    vol = rating_svc.volume_desc(r.get("volume_ratio"))
    if kind == "sell":
        op_line = f"操作建议：以减仓/兑现为主。{hit or '高风险位置，建议回避'}。"
    else:
        extra = hit or f"{buy_lv}，{buy_act}"
        op_line = f"操作建议：以「{op}」为主。{extra}。"
    bits = [x for x in (picked + "。" if picked else "", op_line,
                        f"所属板块「{board}」，五行属{wx}。",
                        f"个股热度 {heat_txt}，板块热度 {sec_txt}。",
                        "，".join(x for x in (pos_txt, flow_txt, vol) if x) + "。",
                        "仅供量化参考，不构成投资建议。") if x]
    return "".join(bits)


def get_buy_points(limit: int = 8) -> dict:
    """实时最佳买点（供全局弹窗）。空结果必须带回原因，避免窗口空白。

    按设置中启用的选股方案并行取并集。方案 A 即原「购买指数≥80 且主力净流入」。
    全部启用方案均无命中且 A 仍启用时，才回退到≥65 或观察池——不把观察池伪装成极佳买点。
    """
    from . import strategy as strategy_svc

    limit = max(3, min(int(limit or 12), 40))
    metric_n = query("SELECT COUNT(*) AS n FROM stock_metrics")[0]["n"]
    snap_n = query("SELECT COUNT(*) AS n FROM stock_snapshot")[0]["n"]
    asof = (query("SELECT MAX(updated_at) AS t FROM stock_snapshot")[0]["t"] or "")[:19]
    enabled = strategy_svc.get_enabled()
    exe = strategy_svc.executing_text(enabled)
    base = {
        "metrics_count": metric_n, "snapshot_count": snap_n, "asof": asof,
        "enabled": enabled, "executing": exe.get("title") or "",
    }

    if metric_n == 0:
        return {**base, "items": [], "count": 0, "source": "empty",
                "empty_reason": "no_metrics",
                "note": "暂无购买指数：请先全量同步行情并重建指标"}
    if snap_n == 0:
        return {**base, "items": [], "count": 0, "source": "empty",
                "empty_reason": "no_snapshot",
                "note": "暂无行情快照：请先全量同步后再看买点"}

    rows, source, note = strategy_svc.collect_buy_points(limit)
    if not rows:
        return {**base, "items": [], "count": 0, "source": source or "empty",
                "empty_reason": "no_candidates",
                "note": note or "指标已计算但暂无可用个股，请确认快照已同步"}

    _decorate_buy_rows(rows)
    if source == "top_buy_index":
        for r in rows:
            r["buy_level"] = "观察池"
            r["advice"] = "观察池，非策略命中，不构成买入建议"
            wx = "、".join(r.get("wuxing") or []) or "未标注"
            heat = r.get("heat")
            heat_txt = f"{heat:.0f}（{r.get('heat_level') or ''}）" if heat is not None else "—"
            sec = r.get("sector_hot")
            sec_txt = f"{sec:.1f}（{r.get('sector_hot_level') or ''}）" if sec is not None else "—"
            r["advice_summary"] = (
                f"观察池展示，非策略命中，不构成买入建议。"
                f"所属板块「{r.get('board_text') or r.get('industry') or '未分类'}」，五行属{wx}。"
                f"个股热度 {heat_txt}，板块热度 {sec_txt}。"
            )
    elif source == "relaxed_65":
        for r in rows:
            if (r.get("buy_index") or 0) < 80:
                r["buy_level"] = "较好买点"
    return {**base, "items": rows, "count": len(rows), "source": source, "note": note}


def get_sell_points(limit: int = 12) -> dict:
    """实时最佳卖点。空结果必须带回原因。每条标明选出方案。"""
    from . import strategy as strategy_svc

    limit = max(3, min(int(limit or 12), 40))
    metric_n = query("SELECT COUNT(*) AS n FROM stock_metrics")[0]["n"]
    snap_n = query("SELECT COUNT(*) AS n FROM stock_snapshot")[0]["n"]
    asof = (query("SELECT MAX(updated_at) AS t FROM stock_snapshot")[0]["t"] or "")[:19]
    enabled = strategy_svc.get_enabled()
    exe = strategy_svc.executing_text(enabled)
    base = {
        "metrics_count": metric_n, "snapshot_count": snap_n, "asof": asof,
        "enabled": enabled, "executing": exe.get("title") or "",
    }
    if metric_n == 0:
        return {**base, "items": [], "count": 0, "source": "empty",
                "empty_reason": "no_metrics",
                "note": "暂无指标：请先全量同步行情并重建指标"}
    if snap_n == 0:
        return {**base, "items": [], "count": 0, "source": "empty",
                "empty_reason": "no_snapshot",
                "note": "暂无行情快照：请先全量同步后再看卖点"}
    rows, source, note = strategy_svc.collect_sell_points(limit)
    if not rows:
        return {**base, "items": [], "count": 0, "source": source or "empty",
                "empty_reason": "no_candidates",
                "note": note or "当前启用方案暂无卖点命中"}
    _decorate_buy_rows(rows, "sell")
    return {**base, "items": rows, "count": len(rows), "source": source, "note": note}
