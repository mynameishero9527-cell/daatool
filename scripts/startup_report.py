"""启动前生成环境与文件报告：python scripts/startup_report.py [--port 8888]

报告写入 data/reports/startup_<时间>.txt，同时覆盖 startup_latest.txt。
"""
from __future__ import annotations

import argparse
import os
import platform
import socket
import sqlite3
import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path

from _common import (BACKUP_DIR, DATA_DIR, DB_PATH, LOG_DIR, REPORT_DIR, ROOT,
                     env_port, fmt_size, inside_project, write_report)

PACKAGES = ("fastapi", "uvicorn", "httpx", "apscheduler", "starlette", "pydantic")
ENV_KEYS = ("TEMP", "TMP", "PIP_CACHE_DIR", "PYTHONPYCACHEPREFIX", "VIRTUAL_ENV", "QUANT_PORT")
KEY_TABLES = ("stock_list", "stock_snapshot", "stock_metrics", "daily_kline", "kv_meta")


def _where(path: str | None) -> str:
    if not path:
        return "未设置"
    return f"{path}  [{'项目内' if inside_project(path) else '项目外'}]"


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _dir_summary(base: Path) -> list[str]:
    if not base.exists():
        return [f"  {base.relative_to(ROOT)}  (尚未创建)"]
    rows = []
    for child in sorted(base.iterdir()):
        if child.is_dir():
            files = [p for p in child.rglob("*") if p.is_file()]
            total = sum(p.stat().st_size for p in files)
            rows.append(f"  {child.relative_to(ROOT)}{os.sep}  {len(files)} 个文件, {fmt_size(total)}")
        else:
            st = child.stat()
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            rows.append(f"  {child.relative_to(ROOT)}  {fmt_size(st.st_size)}  修改于 {mtime}")
    return rows or ["  (空)"]


def _db_lines() -> list[str]:
    if not DB_PATH.exists():
        return ["  数据库尚未创建，首次启动会自动建表"]
    lines = [f"  路径: {_where(str(DB_PATH))}", f"  大小: {fmt_size(DB_PATH.stat().st_size)}"]
    for suffix in ("-wal", "-shm"):
        side = Path(str(DB_PATH) + suffix)
        if side.exists():
            lines.append(f"  {side.name}: {fmt_size(side.stat().st_size)}")
    try:
        conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=3)
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            lines.append(f"  表数量: {len(names)}")
            for table in KEY_TABLES:
                if table in names:
                    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    lines.append(f"  {table}: {n} 行")
                else:
                    lines.append(f"  {table}: 表不存在")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        lines.append(f"  读取失败: {exc}")
    return lines


def build(port: int) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = [
        "A股量化工具 · 启动报告",
        f"生成时间: {now}",
        f"项目目录: {ROOT}",
        "",
        "[运行环境]",
        f"  系统: {platform.platform()}",
        f"  Python: {sys.version.split()[0]}",
        f"  解释器: {_where(sys.executable)}",
        f"  虚拟环境: {'是' if sys.prefix != sys.base_prefix else '否'} ({sys.prefix})",
        "",
        "[依赖版本]",
    ]
    for pkg in PACKAGES:
        try:
            out.append(f"  {pkg}: {metadata.version(pkg)}")
        except metadata.PackageNotFoundError:
            out.append(f"  {pkg}: 未安装")
    out += ["", "[文件落盘位置]"]
    for key in ENV_KEYS:
        val = os.environ.get(key)
        out.append(f"  {key}: {_where(val) if key != 'QUANT_PORT' else (val or '未设置(默认 8888)')}")
    out += [
        f"  数据目录: {_where(str(DATA_DIR))}",
        f"  日志目录: {_where(str(LOG_DIR))}",
        f"  报告目录: {_where(str(REPORT_DIR))}",
        f"  备份目录: {_where(str(BACKUP_DIR))}",
        "  配置: 数据源/AI/策略设置均保存在数据库 kv_meta 表",
        "",
        "[数据库]",
        *_db_lines(),
        "",
        "[data 目录内容]",
        *_dir_summary(DATA_DIR),
        "",
        "[端口]",
        f"  127.0.0.1:{port} {'已被占用(服务可能已在运行)' if _port_in_use(port) else '空闲'}",
    ]
    outside = [k for k in ("TEMP", "TMP", "PIP_CACHE_DIR") if os.environ.get(k) and not inside_project(os.environ[k])]
    out += ["", "[结论]"]
    if outside:
        out.append(f"  注意: {', '.join(outside)} 指向项目外，请用 start.bat 启动")
    else:
        out.append("  运行产生的数据库、日志、缓存、临时文件均在项目目录内")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=env_port())
    args = parser.parse_args()
    text = build(args.port)
    path = write_report("startup", text)
    print(text)
    print(f"启动报告已保存: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
