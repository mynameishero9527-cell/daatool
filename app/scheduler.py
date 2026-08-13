"""定时任务调度（产品文档 §4）：盘前准备 / 盘中高中频 / 快讯轮询 / 盘后同步 / 每日维护。"""
import logging
from datetime import datetime, time as dtime

from apscheduler.schedulers.background import BackgroundScheduler

from .cache import cache
from .config import INTERVAL_MEDIUM, INTERVAL_NEWS, INTERVAL_REALTIME, INTERVAL_SNAPSHOT
from .database import set_meta
from .services import commodity, global_index, macro, market, stocklist
from .services import metrics as metrics_svc

log = logging.getLogger("scheduler")
_scheduler: BackgroundScheduler | None = None
JOB_STATUS: dict[str, dict] = {}


def _trading_time() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(9, 15) <= t <= dtime(11, 30) or dtime(13, 0) <= t <= dtime(15, 5)


def _run(name: str, fn, only_trading: bool = False):
    def wrapper():
        if only_trading and not _trading_time():
            return
        started = datetime.now().isoformat(timespec="seconds")
        try:
            fn()
            JOB_STATUS[name] = {"last_run": started, "ok": True, "error": ""}
        except Exception as exc:  # noqa: BLE001
            JOB_STATUS[name] = {"last_run": started, "ok": False, "error": str(exc)[:200]}
            log.warning("任务 %s 失败: %s", name, exc)
    return wrapper


def _job_refresh_realtime():
    """盘中高频：刷新自选股与指数缓存（写缓存供 UI 读取）。"""
    cache.delete("cn_indices")
    codes = [w["code"] for w in market.get_watchlist() if w.get("code")]
    for key in list(cache._store):  # noqa: SLF001 - 定向失效行情键
        if key.startswith("quotes:"):
            cache.delete(key)
    market.get_quotes(codes)
    market.get_indices_overview()
    set_meta("last_realtime_refresh", datetime.now().isoformat(timespec="seconds"))


def _job_refresh_medium():
    cache.delete("global:indices")
    for cats in ("commodity:能源,贵金属,化工农产品,有色金属",):
        cache.delete(cats)
    commodity.get_quotes()
    global_index.get_global()


def _job_refresh_news():
    cache.delete("macro:news")
    macro.get_news()


def _job_snapshot_sync():
    """盘中每 5 分钟增量刷新全市场快照（支撑看板统计与推荐榜单）。"""
    stocklist.full_sync()
    for key in list(cache._store):  # noqa: SLF001
        if key.startswith("recommend:"):
            cache.delete(key)


def _job_daily_maintain():
    cache.clear()
    stocklist.full_sync()
    set_meta("last_daily_maintain", datetime.now().isoformat(timespec="seconds"))


def _job_metrics_rebuild():
    """盘后：全市场K线同步 + 行业映射 + 指标重算（企稳/购买指数/情绪/暗盘力量）。"""
    stocklist.full_sync()
    metrics_svc.rebuild_all(include_kline=True)


def _job_metrics_recompute():
    """盘中：仅基于最新快照重算指标（不重拉K线，轻量）。"""
    metrics_svc.compute_all_metrics()


def start() -> None:
    global _scheduler
    if _scheduler:
        return
    sched = BackgroundScheduler(timezone="Asia/Shanghai")
    sched.add_job(_run("盘中高频行情", _job_refresh_realtime, only_trading=True),
                  "interval", seconds=INTERVAL_REALTIME, id="realtime")
    sched.add_job(_run("盘中中频(商品/全球)", _job_refresh_medium),
                  "interval", seconds=INTERVAL_MEDIUM, id="medium")
    sched.add_job(_run("快讯轮询", _job_refresh_news),
                  "interval", seconds=INTERVAL_NEWS, id="news")
    sched.add_job(_run("全市场快照刷新", _job_snapshot_sync, only_trading=True),
                  "interval", seconds=INTERVAL_SNAPSHOT, id="snapshot")
    sched.add_job(_run("盘前准备", stocklist.full_sync), "cron",
                  day_of_week="mon-fri", hour=9, minute=0, id="premarket")
    sched.add_job(_run("盘后同步", _job_snapshot_sync), "cron",
                  day_of_week="mon-fri", hour=15, minute=30, id="postmarket")
    sched.add_job(_run("盘后指标重建(K线+四大指标)", _job_metrics_rebuild), "cron",
                  day_of_week="mon-fri", hour=15, minute=40, id="metrics_rebuild")
    sched.add_job(_run("盘中指标轻量重算", _job_metrics_recompute, only_trading=True),
                  "interval", minutes=10, id="metrics_recompute")
    sched.add_job(_run("每日维护", _job_daily_maintain), "cron", hour=2, minute=0, id="maintain")
    sched.start()
    _scheduler = sched
    log.info("调度器已启动，共 %d 个任务", len(sched.get_jobs()))


def status() -> list[dict]:
    jobs = []
    if _scheduler:
        for job in _scheduler.get_jobs():
            name = {"realtime": "盘中高频行情", "medium": "盘中中频(商品/全球)", "news": "快讯轮询",
                    "snapshot": "全市场快照刷新", "premarket": "盘前准备",
                    "postmarket": "盘后同步", "maintain": "每日维护",
                    "metrics_rebuild": "盘后指标重建(K线+四大指标)",
                    "metrics_recompute": "盘中指标轻量重算"}.get(job.id, job.id)
            st = JOB_STATUS.get(name, {})
            jobs.append({
                "id": job.id, "name": name,
                "next_run": job.next_run_time.strftime("%m-%d %H:%M:%S") if job.next_run_time else "-",
                "last_run": st.get("last_run", "-"), "ok": st.get("ok"), "error": st.get("error", ""),
            })
    return jobs
