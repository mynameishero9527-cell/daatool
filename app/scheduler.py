"""定时任务调度（产品文档 §4）：盘前准备 / 盘中高中频 / 快讯轮询 / 盘后同步 / 每日维护。"""
import logging
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from .cache import cache
from .config import INTERVAL_MEDIUM, INTERVAL_NEWS, INTERVAL_REALTIME, INTERVAL_SNAPSHOT
from .database import set_meta
from .services import alerts, commodity, finance, global_index, macro, market, stocklist
from .services import metrics as metrics_svc

log = logging.getLogger("scheduler")
_scheduler: BackgroundScheduler | None = None
JOB_STATUS: dict[str, dict] = {}
_TZ = ZoneInfo("Asia/Shanghai")


def _now() -> datetime:
    return datetime.now(_TZ)


def _trading_time() -> bool:
    """A 股交易时段按北京时间判断（云主机默认 UTC，不能用 naive datetime.now()）。"""
    now = _now()
    if now.weekday() >= 5:
        return False
    t = now.time().replace(tzinfo=None)
    return dtime(9, 15) <= t <= dtime(11, 30) or dtime(13, 0) <= t <= dtime(15, 5)


def _run(name: str, fn, only_trading: bool = False):
    def wrapper():
        if only_trading and not _trading_time():
            return
        started = _now().replace(tzinfo=None).isoformat(timespec="seconds")
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
    set_meta("last_realtime_refresh", _now().replace(tzinfo=None).isoformat(timespec="seconds"))


def _job_refresh_medium():
    cache.delete("global:indices")
    for cats in ("commodity:能源,贵金属,化工农产品,有色金属",):
        cache.delete(cats)
    commodity.get_quotes()
    global_index.get_global()


def _job_refresh_news():
    cache.delete("macro:news")
    macro.get_news()
    try:
        macro.sync_intel()
    except Exception as exc:  # noqa: BLE001
        log.warning("情报缓存失败: %s", exc)


def _job_sector_flow():
    from .services import sector as sector_svc
    sector_svc.record_daily_flow()
    sector_svc.pull_remote_flow()
    try:
        sector_svc.pull_em_flow_history("industry")
        sector_svc.pull_em_flow_history("concept")
    except Exception as exc:  # noqa: BLE001
        log.warning("板块资金日K同步失败: %s", exc)
    try:
        market.record_market_volume()
    except Exception as exc:  # noqa: BLE001
        log.warning("大A量能落库失败: %s", exc)


def _job_snapshot_sync():
    """盘中每 5 分钟增量刷新全市场快照（支撑看板统计与推荐榜单）。"""
    stocklist.full_sync()
    for key in list(cache._store):  # noqa: SLF001
        if key.startswith("recommend:"):
            cache.delete(key)


def _job_daily_maintain():
    cache.clear()
    stocklist.full_sync()
    set_meta("last_daily_maintain", _now().replace(tzinfo=None).isoformat(timespec="seconds"))


def _job_finance_rebuild():
    finance.rebuild_all(max_fetch=80)


def _job_metrics_rebuild():
    """盘后：全市场K线同步 + 行业映射 + 指标重算（企稳/购买指数/情绪/暗盘力量）。"""
    stocklist.full_sync()
    metrics_svc.rebuild_all(include_kline=True)


def _job_metrics_recompute():
    """盘中：仅基于最新快照重算指标（不重拉K线，轻量）。"""
    metrics_svc.compute_all_metrics()


from .database import get_meta_json, set_meta_json

# 间隔型任务（可调频率，分钟）
INTERVAL_JOBS = {"medium": 1, "news": 1, "snapshot": 5, "metrics_recompute": 10, "alerts": 10,
                 "sector_flow": 5}
ALLOWED_MINUTES = [1, 5, 10, 15, 30, 60, 120, 180]


def _apply_overrides(sched) -> None:
    overrides = get_meta_json("job_overrides", {}) or {}
    for job_id, cfg in overrides.items():
        job = sched.get_job(job_id)
        if not job:
            continue
        minutes = cfg.get("minutes")
        if minutes in ALLOWED_MINUTES and job_id in INTERVAL_JOBS:
            job.reschedule("interval", minutes=minutes)
        if cfg.get("paused"):
            job.pause()


def toggle_job(job_id: str) -> dict:
    if not _scheduler:
        return {"ok": False, "error": "调度器未启动"}
    job = _scheduler.get_job(job_id)
    if not job:
        return {"ok": False, "error": "未知任务"}
    overrides = get_meta_json("job_overrides", {}) or {}
    cfg = overrides.get(job_id, {})
    if job.next_run_time is None:
        job.resume()
        cfg["paused"] = False
    else:
        job.pause()
        cfg["paused"] = True
    overrides[job_id] = cfg
    set_meta_json("job_overrides", overrides)
    return {"ok": True, "paused": cfg["paused"]}


def set_job_interval(job_id: str, minutes: int) -> dict:
    if not _scheduler:
        return {"ok": False, "error": "调度器未启动"}
    if job_id not in INTERVAL_JOBS:
        return {"ok": False, "error": "该任务不支持调整频率（定点任务）"}
    if minutes not in ALLOWED_MINUTES:
        return {"ok": False, "error": "频率仅支持 1/5/10/15/30/60/120/180 分钟"}
    job = _scheduler.get_job(job_id)
    if not job:
        return {"ok": False, "error": "未知任务"}
    was_paused = job.next_run_time is None
    job.reschedule("interval", minutes=minutes)
    if was_paused:
        job.pause()
    overrides = get_meta_json("job_overrides", {}) or {}
    overrides.setdefault(job_id, {})["minutes"] = minutes
    set_meta_json("job_overrides", overrides)
    return {"ok": True, "minutes": minutes}


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
    sched.add_job(_run("智能提醒扫描(10分钟)", alerts.scan_all, only_trading=True),
                  "interval", minutes=10, id="alerts")
    sched.add_job(_run("板块资金独立源", _job_sector_flow),
                  "interval", minutes=5, id="sector_flow")
    sched.add_job(_run("每日维护", _job_daily_maintain), "cron", hour=2, minute=0, id="maintain")
    sched.add_job(_run("财报评级重建", _job_finance_rebuild), "cron", hour=3, minute=0, id="finance_rebuild")
    sched.start()
    _scheduler = sched
    _apply_overrides(sched)
    log.info("调度器已启动，共 %d 个任务", len(sched.get_jobs()))


def status() -> list[dict]:
    jobs = []
    if _scheduler:
        for job in _scheduler.get_jobs():
            name = {"realtime": "盘中高频行情", "medium": "盘中中频(商品/全球)", "news": "快讯轮询",
                    "snapshot": "全市场快照刷新", "premarket": "盘前准备",
                    "postmarket": "盘后同步", "maintain": "每日维护",
                    "metrics_rebuild": "盘后指标重建(K线+四大指标)",
                    "metrics_recompute": "盘中指标轻量重算",
                    "alerts": "智能提醒扫描(10分钟)",
                    "sector_flow": "板块资金独立源",
                    "finance_rebuild": "财报评级重建"}.get(job.id, job.id)
            st = JOB_STATUS.get(name, {})
            overrides = get_meta_json("job_overrides", {}) or {}
            minutes = (overrides.get(job.id, {}) or {}).get("minutes") or INTERVAL_JOBS.get(job.id)
            jobs.append({
                "id": job.id, "name": name,
                "next_run": job.next_run_time.strftime("%m-%d %H:%M:%S") if job.next_run_time else "已暂停",
                "paused": job.next_run_time is None,
                "interval_minutes": minutes,
                "adjustable": job.id in INTERVAL_JOBS,
                "last_run": st.get("last_run", "-"), "ok": st.get("ok"), "error": st.get("error", ""),
            })
    return jobs
