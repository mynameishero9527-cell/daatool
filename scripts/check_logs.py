"""扫描 data/logs 生成异常日志报告：python scripts/check_logs.py [--days 7] [--port 8000]

报告写入 data/reports/log_report_<时间>.txt，同时覆盖 log_report_latest.txt。
有 ERROR/CRITICAL 时退出码为 1，便于脚本判断。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from _common import LOG_DIR, env_port, fmt_size, write_report

LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d+)? (?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) "
    r"(?P<name>\S+) (?P<msg>.*)$"
)
PROBLEM_LEVELS = ("WARNING", "ERROR", "CRITICAL")
RECENT_LIMIT = 30
TOP_LIMIT = 20


def _norm(msg: str) -> str:
    return re.sub(r"\d+(\.\d+)?", "#", msg)[:160]


def _log_files() -> list[Path]:
    if not LOG_DIR.exists():
        return []
    files = [p for p in LOG_DIR.iterdir() if p.is_file() and ".log" in p.name]
    # error.log 是 app.log 中 WARNING 以上的副本，扫描 app.log 即可避免重复计数；缺 app.log 时再用它
    names = {p.name for p in files}
    if any(n.startswith("app.log") for n in names):
        files = [p for p in files if not p.name.startswith("error.log")]
    return sorted(files)


def parse(files: list[Path], since: datetime) -> list[dict]:
    records: list[dict] = []
    for path in files:
        current = None
        # 无时间戳的行（pip 输出、裸 Traceback）按文件修改时间或上一条时间归属
        last_ts = datetime.fromtimestamp(path.stat().st_mtime)
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.rstrip("\r\n")
                m = LINE_RE.match(line)
                if m:
                    last_ts = datetime.strptime(m["ts"], "%Y-%m-%d %H:%M:%S")
                    current = None
                    if last_ts >= since and m["level"] in PROBLEM_LEVELS:
                        current = {"ts": last_ts, "level": m["level"], "name": m["name"],
                                   "msg": m["msg"], "file": path.name, "trace": []}
                        records.append(current)
                elif current is not None and line.strip():
                    current["trace"].append(line)
                elif last_ts >= since and line.startswith(("Traceback", "ERROR:", "fatal:")):
                    current = {"ts": last_ts, "level": "ERROR", "name": "stderr", "msg": line,
                               "file": path.name, "trace": []}
                    records.append(current)
    records.sort(key=lambda r: r["ts"])
    return records


def _service_status(port: int) -> str:
    url = f"http://127.0.0.1:{port}/api/system/status"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            body = resp.read(4000).decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
            brief = json.dumps(data, ensure_ascii=False)[:600]
        except ValueError:
            brief = body[:600]
        return f"运行中 ({url})\n  {brief}"
    except Exception as exc:  # noqa: BLE001
        return f"未响应 ({url}): {exc}"


def build(days: int, port: int) -> tuple[str, int]:
    since = datetime.now() - timedelta(days=days)
    files = _log_files()
    records = parse(files, since)
    levels = Counter(r["level"] for r in records)
    by_logger = Counter(r["name"] for r in records if r["level"] != "WARNING")
    groups = Counter((r["level"], r["name"], _norm(r["msg"])) for r in records)
    errors = [r for r in records if r["level"] in ("ERROR", "CRITICAL")]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    out = [
        "A股量化工具 · 异常日志报告",
        f"生成时间: {now}",
        f"统计范围: 最近 {days} 天 (自 {since:%Y-%m-%d %H:%M})",
        f"日志目录: {LOG_DIR}",
        "",
        "[服务状态]",
        f"  {_service_status(port)}",
        "",
        "[日志文件]",
    ]
    if not files:
        out.append("  暂无日志文件，请先用 start.bat 启动一次")
    for p in files:
        st = p.stat()
        out.append(f"  {p.name}  {fmt_size(st.st_size)}  修改于 {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M:%S}")
    out += [
        "",
        "[汇总]",
        f"  CRITICAL: {levels.get('CRITICAL', 0)}",
        f"  ERROR:    {levels.get('ERROR', 0)}",
        f"  WARNING:  {levels.get('WARNING', 0)}",
    ]
    if by_logger:
        out += ["", "[错误来源模块]"]
        out += [f"  {name}: {n}" for name, n in by_logger.most_common()]
    if groups:
        out += ["", f"[高频问题 TOP{TOP_LIMIT}]  (数字已归一为 #)"]
        for (level, name, msg), n in groups.most_common(TOP_LIMIT):
            out.append(f"  {n:>5} 次  {level:<8} {name}  {msg}")
    if errors:
        out += ["", f"[最近 {min(RECENT_LIMIT, len(errors))} 条错误详情]"]
        for r in errors[-RECENT_LIMIT:]:
            out.append(f"- {r['ts']:%Y-%m-%d %H:%M:%S} {r['level']} {r['name']} ({r['file']})")
            out.append(f"  {r['msg']}")
            out += [f"    {t}" for t in r["trace"][-15:]]
    out += ["", "[结论]"]
    if errors:
        out.append(f"  发现 {len(errors)} 条错误，请查看上方详情；可把本报告发给开发排查")
    elif levels.get("WARNING"):
        out.append("  无错误，仅有警告（多为数据源超时/熔断切换，通常可自动恢复）")
    else:
        out.append("  未发现异常")
    return "\n".join(out) + "\n", (1 if errors else 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--port", type=int, default=env_port())
    args = parser.parse_args()
    text, code = build(max(1, args.days), args.port)
    path = write_report("log_report", text)
    print(text)
    print(f"异常日志报告已保存: {path}")
    return code


if __name__ == "__main__":
    sys.exit(main())
