"""供 bat 写启动器日志：python scripts/launcher_log.py INFO|WARNING|ERROR 消息...

写入 data/logs/launcher.log，格式与 app.log 一致，check_logs.py 可直接统计。
"""
from __future__ import annotations

import sys
from datetime import datetime

from _common import LOG_DIR


def main() -> int:
    level = (sys.argv[1] if len(sys.argv) > 1 else "INFO").upper()
    msg = " ".join(sys.argv[2:])
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {level} launcher {msg}\n"
    with (LOG_DIR / "launcher.log").open("a", encoding="utf-8") as fh:
        fh.write(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
