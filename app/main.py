"""应用入口：启动自检（产品文档 §8）→ 初始化 → 挂载路由与前端。"""
import logging
import threading

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import scheduler
from .api import router
from .config import STATIC_DIR
from .database import init_db
from .services import market, stocklist

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
    # 3) 股票列表为空则后台执行首次全量同步（不阻塞启动）
    if stocklist.snapshot_count() == 0:
        log.info("本地无股票数据，后台启动首次全量同步…")
        threading.Thread(target=stocklist.full_sync, daemon=True).start()
    # 4) 启动定时任务
    scheduler.start()
    log.info("启动自检完成")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
