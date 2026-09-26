"""备份数据库到 data/backup/quant_<时间>.db：python scripts/backup_db.py

用 SQLite 在线备份接口，WAL 中未合并的数据也会带上。只保留最近 KEEP 份。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime

from _common import BACKUP_DIR, DB_PATH, fmt_size

KEEP = 10


def main() -> int:
    if not DB_PATH.exists():
        print("数据库尚未创建，跳过备份")
        return 0
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"quant_{datetime.now():%Y%m%d_%H%M%S}.db"
    src = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=10)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    except sqlite3.Error as exc:
        dst.close()
        target.unlink(missing_ok=True)
        print(f"备份失败: {exc}")
        return 1
    finally:
        src.close()
    dst.close()
    for old in sorted(BACKUP_DIR.glob("quant_2*.db"))[:-KEEP]:
        old.unlink(missing_ok=True)
    print(f"数据库已备份: {target} ({fmt_size(target.stat().st_size)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
