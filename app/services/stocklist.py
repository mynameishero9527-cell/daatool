"""全市场股票列表与快照同步：拉取所有 A 股到本地数据库，供搜索与推荐分析。"""
import threading
import time
from datetime import datetime

from ..config import RANK_PAGE_SIZE
from ..database import executemany, get_meta, query, set_meta
from ..datasources import tencent

_sync_lock = threading.Lock()
_sync_state = {"running": False, "progress": 0, "total": 0, "message": "未开始"}


def _board_of(code: str) -> str:
    body = code[2:]
    if code.startswith("sh68"):
        return "科创板"
    if code.startswith("sz30"):
        return "创业板"
    if code.startswith(("bj", "sh8", "sh4")) or body.startswith(("8", "4", "92")):
        return "北交所"
    return "主板"


def _market_of(code: str) -> str:
    if code.startswith("sh"):
        return "SH"
    if code.startswith("sz"):
        return "SZ"
    return "BJ"


def sync_state() -> dict:
    state = dict(_sync_state)
    state["last_sync"] = get_meta("stocklist_last_sync", "从未同步")
    state["count"] = query("SELECT COUNT(*) AS n FROM stock_list")[0]["n"]
    return state


def full_sync() -> dict:
    """分页拉取全市场排行并写入 stock_list + stock_snapshot。幂等，可重复执行。"""
    if not _sync_lock.acquire(blocking=False):
        return sync_state()
    try:
        _sync_state.update(running=True, progress=0, message="同步中")
        now = datetime.now().isoformat(timespec="seconds")
        offset, total = 0, 0
        while True:
            rows = tencent.fetch_rank_page(sort="price", direct="down", offset=offset, count=RANK_PAGE_SIZE)
            if not rows:
                break
            list_rows, snap_rows = [], []
            for r in rows:
                code = r["code"]
                if not code:
                    continue
                list_rows.append((code, r["name"], _market_of(code), _board_of(code), now))
                main_net_d5 = None
                if r["main_in_d5"] is not None and r["main_out_d5"] is not None:
                    main_net_d5 = r["main_in_d5"] - r["main_out_d5"]
                snap_rows.append((
                    code, r["name"], r["price"], r["pct"], r["turnover_rate"], r["volume_ratio"],
                    r["pe_ttm"], r["pb"], r["float_mv"], r["total_mv"],
                    r["main_net_in"], r["main_in"], r["main_out"], main_net_d5,
                    r["pct_d5"], r["pct_d10"], r["pct_d20"], r["pct_d60"], r["amount"], now,
                ))
            executemany(
                "INSERT INTO stock_list(code,name,market,board,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(code) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at",
                list_rows,
            )
            executemany(
                "INSERT OR REPLACE INTO stock_snapshot(code,name,price,pct,turnover_rate,volume_ratio,"
                "pe_ttm,pb,float_mv,total_mv,main_net_in,main_in,main_out,main_net_in_d5,"
                "pct_d5,pct_d10,pct_d20,pct_d60,amount,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                snap_rows,
            )
            total += len(rows)
            offset += RANK_PAGE_SIZE
            _sync_state.update(progress=total, total=total, message=f"已同步 {total} 只")
            if len(rows) < RANK_PAGE_SIZE:
                break
            time.sleep(0.15)  # 限速防封禁
        set_meta("stocklist_last_sync", datetime.now().isoformat(timespec="seconds"))
        _sync_state.update(running=False, message=f"同步完成，共 {total} 只")
        return sync_state()
    except Exception as exc:  # noqa: BLE001
        _sync_state.update(running=False, message=f"同步失败: {exc}")
        return sync_state()
    finally:
        _sync_lock.release()


def search(keyword: str, limit: int = 20) -> list[dict]:
    """本地库搜索：代码前缀 / 名称模糊。"""
    kw = keyword.strip()
    if not kw:
        return []
    like = f"%{kw}%"
    return query(
        """SELECT l.code, l.name, l.market, l.board, s.price, s.pct
           FROM stock_list l LEFT JOIN stock_snapshot s ON s.code = l.code
           WHERE l.code LIKE ? OR l.name LIKE ?
           ORDER BY CASE WHEN l.code LIKE ? THEN 0 ELSE 1 END, l.code
           LIMIT ?""",
        (like, like, f"%{kw}", limit),
    )


def snapshot_count() -> int:
    return query("SELECT COUNT(*) AS n FROM stock_snapshot")[0]["n"]
