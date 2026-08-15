"""应用入口：启动自检（产品文档 §8）→ 初始化 → 挂载路由与前端。"""
import logging
import threading

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import scheduler
from .api import router
from .config import STATIC_DIR
from .database import init_db, query
from .services import market, stocklist
from .services import metrics as metrics_svc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("main")

_NO_STORE = {"Cache-Control": "no-store, max-age=0"}

_HTML_404 = """<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>页面不存在</title>
<style>
body{font-family:-apple-system,sans-serif;background:#0d1117;color:#dbe4f0;padding:40px 24px;max-width:640px;margin:0 auto;line-height:1.6}
a{color:#4a9eff;text-decoration:none} a:hover{text-decoration:underline}
.card{background:#161b22;border:1px solid #30363d;padding:18px 20px;border-radius:10px;margin:14px 0}
h1{font-size:22px;margin:0 0 8px} p{margin:8px 0} .muted{color:#8b9bb4;font-size:13px}
</style></head><body>
<h1>404 · 页面不存在</h1>
<p class="muted">请从下面入口进入，不要直接打开本地 HTML 文件。</p>
<div class="card">
  <p><a href="/">前端界面</a></p>
  <p><a href="/docs">后端 Swagger 文档</a></p>
  <p><a href="/api">API 入口</a></p>
</div>
</body></html>
"""

app = FastAPI(title="A股量化工具", version="11.0.17", docs_url="/docs", redoc_url="/redoc")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


def _index_page():
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html", headers=_NO_STORE)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404 and "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(_HTML_404, status_code=404)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/", include_in_schema=False)
def index_root():
    """前端入口。"""
    return _index_page()


@app.get("/index.html", include_in_schema=False)
def index_html():
    """兼容直接访问 /index.html（此前会落到 FastAPI JSON 404）。"""
    return _index_page()


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect fill="#0d1117" width="32" height="32" rx="6"/>'
        '<text x="16" y="22" text-anchor="middle" font-size="16" fill="#4a9eff">A</text></svg>'
    )
    return Response(svg, media_type="image/svg+xml")



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
        if query("SELECT COUNT(*) AS n FROM alert_log")[0]["n"] == 0:
            from .services import alerts
            try:
                alerts.scan_all()
            except Exception as exc:  # noqa: BLE001
                log.warning("首次提醒扫描失败: %s", exc)
        try:
            from .services import finance as finance_svc
            from .services import sector as sector_svc
            finance_svc.rebuild_all(max_fetch=0)
            if stocklist.snapshot_count():
                sector_svc.record_daily_flow()
                try:
                    sector_svc.pull_remote_flow()
                except Exception as exc2:  # noqa: BLE001
                    log.warning("板块资金独立源拉取失败: %s", exc2)
            try:
                from .services import macro as macro_svc
                macro_svc.sync_intel()
            except Exception as exc3:  # noqa: BLE001
                log.warning("宏观情报缓存失败: %s", exc3)
        except Exception as exc:  # noqa: BLE001
            log.warning("财报评级/板块资金落库跳过: %s", exc)
    threading.Thread(target=bootstrap, daemon=True).start()
    # 4) 启动定时任务
    scheduler.start()
    log.info("启动自检完成")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
