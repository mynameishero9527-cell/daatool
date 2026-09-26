"""运维脚本公共工具：项目路径、报告落盘与清理。"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 与 run.py 一致，日志时间戳和报告统计窗口才对得上
if hasattr(time, "tzset"):
    os.environ.setdefault("TZ", "Asia/Shanghai")
    time.tzset()

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import BACKUP_DIR, DATA_DIR, DB_PATH, LOG_DIR, REPORT_DIR  # noqa: E402

KEEP_REPORTS = 30

__all__ = [
    "ROOT", "DATA_DIR", "DB_PATH", "LOG_DIR", "REPORT_DIR", "BACKUP_DIR", "env_port",
    "fmt_size", "inside_project", "write_report",
]


def env_port() -> int:
    """QUANT_PORT 非法时回退 8888，报告脚本不能因为配置写错而跟着崩溃。"""
    try:
        return int(os.environ.get("QUANT_PORT") or 8888)
    except ValueError:
        return 8888


def fmt_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def inside_project(path: str | Path | None) -> bool:
    if not path:
        return False
    try:
        Path(path).resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def write_report(prefix: str, text: str) -> Path:
    """写入 data/reports/<prefix>_时间.txt 与 <prefix>_latest.txt，只保留最近 KEEP_REPORTS 份。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"{prefix}_{stamp}.txt"
    path.write_text(text, encoding="utf-8-sig")
    (REPORT_DIR / f"{prefix}_latest.txt").write_text(text, encoding="utf-8-sig")
    old = sorted(REPORT_DIR.glob(f"{prefix}_2*.txt"))
    for extra in old[:-KEEP_REPORTS]:
        extra.unlink(missing_ok=True)
    return path
