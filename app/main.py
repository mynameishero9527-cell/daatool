"""应用入口：启动自检（产品文档 §8）→ 初始化 → 挂载路由与前端。"""
import logging
import threading

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import scheduler
from .api import router
from .config import STATIC_DIR
from .database import init_db, query
from .services import market, stocklist
from .services import metrics as metrics_svc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("main")

app = FastAPI(title="A股量化工具", version="1.0.0")
app.include_router(router)


@app.on_event("startup")
def startup() -> None:
    # 1) 数据库自检与建表
    init_db()
    log.info("数据库初始化完成")
    # 2) 默认自选股
    market.ensure_default_watchlist()
    # 3) 首次启动链路：快照同步 → K线/行业/指标重建（均在后台，不阻塞启动）
    def bootstrap():
        if stocklist.snapshot_count() == 0:
            log.info("本地无股票数据，执行首次全量同步…")
            stocklist.full_sync()
        if query("SELECT COUNT(*) AS n FROM stock_metrics")[0]["n"] == 0:
            log.info("指标表为空，后台执行K线同步与指标重建…")
            metrics_svc.rebuild_all(include_kline=True)
        elif query("SELECT COUNT(*) AS n FROM concept_map")[0]["n"] == 0:
            log.info("概念映射为空，后台同步题材概念…")
            from .services import sector
            try:
                sector.sync_concepts()
            except Exception as exc:  # noqa: BLE001
                log.warning("概念映射同步失败: %s", exc)
    threading.Thread(target=bootstrap, daemon=True).start()
    # 4) 启动定时任务
    scheduler.start()
    log.info("启动自检完成")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
