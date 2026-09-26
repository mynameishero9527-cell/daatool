"""启动脚本：python run.py，浏览器访问 http://127.0.0.1:8888

日志写入 data/logs：app.log（全部）、error.log（WARNING 及以上），按 10MB 轮转。
端口可用环境变量 QUANT_PORT 覆盖；QUANT_OPEN_BROWSER=1 时启动后自动打开浏览器。
"""
import copy
import logging
import logging.config
import os
import sys
import threading
import time

# Windows 的 C 运行库不认 "Asia/Shanghai" 这种 TZ 写法，会按 UTC 处理，所以只在有 tzset 的系统上设置
if hasattr(time, "tzset"):
    os.environ.setdefault("TZ", "Asia/Shanghai")
    time.tzset()

import uvicorn
from uvicorn.config import LOGGING_CONFIG

from app.config import LOG_DIR

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def build_log_config() -> dict:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cfg = copy.deepcopy(LOGGING_CONFIG)
    cfg["formatters"]["plain"] = {"format": LOG_FORMAT}
    rotating = {
        "class": "logging.handlers.RotatingFileHandler",
        "formatter": "plain",
        "maxBytes": 10 * 1024 * 1024,
        "backupCount": 5,
        "encoding": "utf-8",
    }
    cfg["handlers"]["console"] = {
        "class": "logging.StreamHandler", "formatter": "plain", "stream": "ext://sys.stderr",
    }
    cfg["handlers"]["app_file"] = {**rotating, "filename": str(LOG_DIR / "app.log")}
    cfg["handlers"]["error_file"] = {
        **rotating, "filename": str(LOG_DIR / "error.log"), "level": "WARNING",
    }
    for name in ("uvicorn", "uvicorn.access"):
        cfg["loggers"][name]["handlers"] = cfg["loggers"][name]["handlers"] + ["app_file", "error_file"]
    # 每次行情请求都会打一条 INFO，放开会很快把有用日志轮转掉
    for noisy in ("httpx", "httpcore"):
        cfg["loggers"][noisy] = {"level": "WARNING"}
    cfg["root"] = {"level": "INFO", "handlers": ["console", "app_file", "error_file"]}
    return cfg


def _install_excepthooks() -> None:
    crash = logging.getLogger("crash")

    def _main_hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        crash.critical("未捕获异常", exc_info=(exc_type, exc, tb))

    def _thread_hook(args):
        crash.critical("线程 %s 未捕获异常", getattr(args.thread, "name", "?"),
                       exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = _main_hook
    threading.excepthook = _thread_hook


if __name__ == "__main__":
    log_config = build_log_config()
    logging.config.dictConfig(log_config)
    _install_excepthooks()
    port = int(os.environ.get("QUANT_PORT") or 8888)
    if os.environ.get("QUANT_OPEN_BROWSER") == "1":
        import webbrowser
        threading.Timer(3.0, webbrowser.open, [f"http://127.0.0.1:{port}"]).start()
    try:
        uvicorn.run("app.main:app", host="0.0.0.0", port=port, log_config=log_config, log_level="info")
    except Exception:
        logging.getLogger("crash").critical("服务启动失败", exc_info=True)
        raise
