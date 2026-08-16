"""定时任务调度（产品文档 §4）：盘前准备 / 盘中高中频 / 快讯轮询 / 盘后同步 / 每日维护。"""
import logging
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from .cache import cache
from .config import INTERVAL_MEDIUM, INTERVAL_NEWS, INTERVAL_REALTIME, INTERVAL_SNAPSHOT
from .database import set_meta
from .services import alerts, commodity, finance, fx, global_index, macro, market, stocklist
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


def _job_official_policy():
    from .services import policy_archive
    policy_archive.sync_official_policy("incremental")
    try:
        from .services import hot_terms
        hot_terms.rebuild_hot_terms()
    except Exception as exc:  # noqa: BLE001
        log.warning("热词重算失败: %s", exc)


def _job_official_policy_backfill():
    from .services import policy_archive
    policy_archive.sync_official_policy("backfill")


def _job_hot_terms():
    from .services import hot_terms
    hot_terms.rebuild_hot_terms()


def _job_metrics_rebuild():
    """盘后：全市场K线同步 + 行业映射 + 指标重算（企稳/购买指数/情绪/暗盘力量）。"""
    stocklist.full_sync()
    metrics_svc.rebuild_all(include_kline=True)
    try:
        from .services import engine as engine_svc
        engine_svc.track_open_tasks()
    except Exception as exc:  # noqa: BLE001
        log.warning("信号跟踪失败: %s", exc)


def _job_engine_eod():
    from .services import engine as engine_svc
    engine_svc.maybe_auto_run("eod")


def _job_engine_intraday():
    from .services import engine as engine_svc
    engine_svc.maybe_auto_run("intraday")


def _job_fund_kline():
    """盘中：自选股主力资金日K增量写入本地。"""
    from .services import kline as kline_svc
    kline_svc.sync_watchlist_fund(lookback=40)


def _job_fund_kline_backfill():
    """盘后：自选股主力资金历史回补到本地。"""
    from .services import kline as kline_svc
    kline_svc.sync_watchlist_fund(lookback=240)


def _job_fx_snapshot():
    """外汇即时价：每 5 分钟，不限于 A 股交易时段。"""
    fx.job_snapshot()


def _job_fx_daily():
    """工作日 23:30：写入近两周欧洲央行参考价。"""
    fx.job_daily()


def _job_fx_year():
    """周日回补近一年官方日线缺口。"""
    fx.job_year()


def _job_metrics_recompute():
    """盘中：仅基于最新快照重算指标（不重拉K线，轻量）。"""
    metrics_svc.compute_all_metrics()


from .database import get_meta_json, set_meta_json

# 间隔型任务（可调频率，分钟）
INTERVAL_JOBS = {"medium": 1, "news": 1, "snapshot": 5, "metrics_recompute": 10, "alerts": 10,
                 "sector_flow": 5, "hot_terms": 60, "official_policy": 240, "engine_intraday": 10,
                 "fund_kline": 5, "fx_snapshot": 5}
ALLOWED_MINUTES = [1, 5, 10, 15, 30, 60, 120, 180, 240]


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
        return {"ok": False, "error": "频率仅支持 1/5/10/15/30/60/120/180/240 分钟"}
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
    sched.add_job(_run("官方政策增量同步", _job_official_policy),
                  "interval", minutes=240, id="official_policy")
    sched.add_job(_run("官方政策半年回补", _job_official_policy_backfill),
                  "cron", hour=3, minute=40, id="official_policy_backfill")
    sched.add_job(_run("热度词汇重算", _job_hot_terms),
                  "interval", minutes=60, id="hot_terms")
    sched.add_job(_run("策略引擎日终快照", _job_engine_eod), "cron",
                  day_of_week="mon-fri", hour=15, minute=50, id="engine_eod")
    sched.add_job(_run("策略引擎盘中增量", _job_engine_intraday, only_trading=True),
                  "interval", minutes=10, id="engine_intraday")
    sched.add_job(_run("个股主力资金增量", _job_fund_kline, only_trading=True),
                  "interval", minutes=5, id="fund_kline")
    sched.add_job(_run("个股主力资金历史回补", _job_fund_kline_backfill), "cron",
                  day_of_week="mon-fri", hour=15, minute=32, id="fund_kline_backfill")
    sched.add_job(_run("各国汇率即时", _job_fx_snapshot),
                  "interval", minutes=5, id="fx_snapshot")
    sched.add_job(_run("各国汇率官方日线", _job_fx_daily), "cron",
                  day_of_week="mon-fri", hour=23, minute=30, id="fx_daily")
    sched.add_job(_run("各国汇率近一年回补", _job_fx_year), "cron",
                  day_of_week="sun", hour=3, minute=50, id="fx_year")
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
                    "finance_rebuild": "财报评级重建",
                    "official_policy": "官方政策增量同步",
                    "official_policy_backfill": "官方政策半年回补",
                    "hot_terms": "热度词汇重算",
                    "engine_eod": "策略引擎日终快照",
                    "engine_intraday": "策略引擎盘中增量",
                    "fund_kline": "个股主力资金增量",
                    "fund_kline_backfill": "个股主力资金历史回补",
                    "fx_snapshot": "各国汇率即时",
                    "fx_daily": "各国汇率官方日线",
                    "fx_year": "各国汇率近一年回补"}.get(job.id, job.id)
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
