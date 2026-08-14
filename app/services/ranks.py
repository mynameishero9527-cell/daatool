"""异动榜单集合（FR8-04）：龙虎榜官方源不可达，以本地异动榜代替（标注口径）。"""
from ..cache import cached
from ..database import query
from . import rating as rating_svc
from . import wuxing

RANK_TYPES = {
    "limit_up": "今日涨停",
    "limit_down": "今日跌停",
    "turnover": "换手率榜",
    "amplitude": "振幅榜",
    "volume_ratio": "量比榜",
    "main_buy": "主力买入榜",
    "main_sell": "主力卖出榜",
    "up_streak": "连续上涨榜(5日)",
    "down_streak": "连续下跌榜(5日)",
}

_SELECT = ("SELECT s.code, s.name, s.price, s.pct, s.pct_d5, s.pct_d20, s.pct_d60, "
           "s.turnover_rate, s.volume_ratio, s.amplitude, s.main_net_in, s.float_mv, "
           "l.industry, m.buy_index, m.sentiment "
           "FROM stock_snapshot s "
           "LEFT JOIN stock_list l ON l.code = s.code "
           "LEFT JOIN stock_metrics m ON m.code = s.code "
           "WHERE s.price IS NOT NULL AND s.name NOT LIKE '%ST%' AND s.name NOT LIKE '%退%' ")

_ORDERS = {
    "limit_up": ("AND s.pct >= 9.8", "s.turnover_rate DESC", "换手%", "turnover_rate"),
    "limit_down": ("AND s.pct <= -9.8", "s.turnover_rate DESC", "换手%", "turnover_rate"),
    "turnover": ("AND s.turnover_rate IS NOT NULL", "s.turnover_rate DESC", "换手%", "turnover_rate"),
    "amplitude": ("AND s.amplitude IS NOT NULL", "s.amplitude DESC", "振幅%", "amplitude"),
    "volume_ratio": ("AND s.volume_ratio IS NOT NULL", "s.volume_ratio DESC", "量比", "volume_ratio"),
    "main_buy": ("AND s.main_net_in IS NOT NULL", "s.main_net_in DESC", "主力净流入(万)", "main_net_in"),
    "main_sell": ("AND s.main_net_in IS NOT NULL", "s.main_net_in ASC", "主力净流入(万)", "main_net_in"),
    "up_streak": ("AND s.pct_d5 IS NOT NULL AND s.pct > 0", "s.pct_d5 DESC", "5日涨幅%", "pct_d5"),
    "down_streak": ("AND s.pct_d5 IS NOT NULL AND s.pct < 0", "s.pct_d5 ASC", "5日跌幅%", "pct_d5"),
}


def get_rank(rank_type: str = "limit_up", limit: int = 50) -> dict:
    rank_type = rank_type if rank_type in _ORDERS else "limit_up"

    def loader():
        where, order, metric_name, metric_key = _ORDERS[rank_type]
        rows = query(_SELECT + where + f" ORDER BY {order} LIMIT ?", (limit,))
        for r in rows:
            r["metric_name"] = metric_name
            r["metric_value"] = r[metric_key]
            r["score"], r["advice"] = rating_svc.quick_score(r)
            r["volume_desc"] = rating_svc.volume_desc(r["volume_ratio"])
        wuxing.tags_for_list(rows)
        return {
            "type": rank_type, "title": RANK_TYPES[rank_type],
            "types": RANK_TYPES, "items": rows,
            "note": "榜单由本地全市场快照实时计算（官方龙虎榜数据源当前网络不可达，以异动榜口径代替）",
        }
    return cached(f"ranks:{rank_type}:{limit}", 60, loader)
