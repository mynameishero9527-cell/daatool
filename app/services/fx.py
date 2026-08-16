"""各国汇率：即时快照 + 欧洲央行日线落库。不编造周末点，不把在岸价冒充离岸。"""
from __future__ import annotations

import logging
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from ..cache import cache, cached
from ..config import TTL_FX
from ..database import execute, executemany, get_meta, query, set_meta
from ..datasources import fx as fx_src

log = logging.getLogger("fx")
_TZ = ZoneInfo("Asia/Shanghai")

# pair, 名称, 国家/地区, ISO, 新浪符号, 东财 secid, 是否 ECB 官方, 计价说明
# 东财 121.*CNYI 为在岸混合价；JPYCNYI 是 100 日元，换算见 EM_SCALE
FX_CATALOG: list[tuple[str, str, str, str, str, str, bool, str]] = [
    ("USDCNY", "美元/人民币", "美国", "USD", "fx_susdcny", "121.USDCNYI", True, "1美元兑人民币"),
    ("EURCNY", "欧元/人民币", "欧元区", "EUR", "fx_seurcny", "121.EURCNYI", True, "1欧元兑人民币"),
    ("JPYCNY", "日元/人民币", "日本", "JPY", "fx_sjpycny", "121.JPYCNYI", True, "1日元兑人民币"),
    ("GBPCNY", "英镑/人民币", "英国", "GBP", "fx_sgbpcny", "121.GBPCNYI", True, "1英镑兑人民币"),
    ("HKDCNY", "港元/人民币", "中国香港", "HKD", "fx_shkdcny", "121.HKDCNYI", True, "1港元兑人民币"),
    ("AUDCNY", "澳元/人民币", "澳大利亚", "AUD", "fx_saudcny", "121.AUDCNYI", True, "1澳元兑人民币"),
    ("CADCNY", "加元/人民币", "加拿大", "CAD", "fx_scadcny", "121.CADCNYI", True, "1加元兑人民币"),
    ("CHFCNY", "瑞士法郎/人民币", "瑞士", "CHF", "fx_schfcny", "121.CHFCNYI", True, "1法郎兑人民币"),
    ("SGDCNY", "新加坡元/人民币", "新加坡", "SGD", "fx_ssgdcny", "121.SGDCNYI", True, "1新元兑人民币"),
    ("NZDCNY", "新西兰元/人民币", "新西兰", "NZD", "fx_snzdcny", "121.NZDCNYI", True, "1纽元兑人民币"),
    ("KRWCNY", "韩元/人民币", "韩国", "KRW", "fx_skrwcny", "", True, "1韩元兑人民币"),
    ("THBCNY", "泰铢/人民币", "泰国", "THB", "fx_sthbcny", "", True, "1泰铢兑人民币"),
    ("INRCNY", "印度卢比/人民币", "印度", "INR", "fx_sinrcny", "", True, "1卢比兑人民币"),
    ("MYRCNY", "林吉特/人民币", "马来西亚", "MYR", "fx_smyrcny", "", True, "1林吉特兑人民币"),
    ("IDRCNY", "印尼盾/人民币", "印度尼西亚", "IDR", "fx_sidrcny", "", True, "1印尼盾兑人民币"),
    ("PHPCNY", "菲律宾比索/人民币", "菲律宾", "PHP", "fx_sphpcny", "", True, "1比索兑人民币"),
    ("MXNCNY", "墨西哥比索/人民币", "墨西哥", "MXN", "fx_smxncny", "", True, "1比索兑人民币"),
    ("BRLCNY", "巴西雷亚尔/人民币", "巴西", "BRL", "fx_sbrlcny", "", True, "1雷亚尔兑人民币"),
    ("ZARCNY", "南非兰特/人民币", "南非", "ZAR", "fx_szarcny", "", True, "1兰特兑人民币"),
    ("TRYCNY", "土耳其里拉/人民币", "土耳其", "TRY", "fx_strycny", "", True, "1里拉兑人民币"),
    ("DKKCNY", "丹麦克朗/人民币", "丹麦", "DKK", "fx_sdkkcny", "", True, "1克朗兑人民币"),
    ("SEKCNY", "瑞典克朗/人民币", "瑞典", "SEK", "fx_ssekcny", "", True, "1克朗兑人民币"),
    ("NOKCNY", "挪威克朗/人民币", "挪威", "NOK", "fx_snokcny", "", True, "1克朗兑人民币"),
    ("PLNCNY", "波兰兹罗提/人民币", "波兰", "PLN", "fx_splncny", "", True, "1兹罗提兑人民币"),
    ("CZKCNY", "捷克克朗/人民币", "捷克", "CZK", "fx_sczkcny", "", True, "1克朗兑人民币"),
    ("HUFCNY", "匈牙利福林/人民币", "匈牙利", "HUF", "fx_shufcny", "", True, "1福林兑人民币"),
    ("ILSCNY", "以色列新谢克尔/人民币", "以色列", "ILS", "fx_silscny", "", True, "1谢克尔兑人民币"),
    ("USDCNH", "美元/离岸人民币", "中国香港", "USD", "fx_susdcnh", "133.USDCNH", False, "离岸价，无欧洲央行官方日线"),
]

# 东财柜台习惯：100 日元；统一存 1 外币兑人民币，避免和欧洲央行日线混单位
EM_SCALE = {"JPYCNY": 0.01}

KEEP_DAYS = 400
MAX_PULL_DAYS = 400
DEFAULT_HISTORY_DAYS = 365
_PAIR_MAP = {row[0]: row for row in FX_CATALOG}

SCHEDULE = {
    "live": "每 5 分钟拉一次新浪/东财即时价（外汇约北京时间周日 22:00 至周五 22:00；周末无新价不编造）",
    "official": "每个工作日 23:30（北京时间）写入欧洲央行参考价，对应约欧洲中部时间 16:00 公布的当日定盘",
    "year": "每周日 03:50 回补近一年缺口；首次启动若本地为空会自动拉近 365 天",
    "manual": "支持按日期区间手动拉取，单次最多 400 天；只写入官方已公布的交易日",
    "retain": "本地保留约 400 个自然日，更早的官方日线会清理",
}

DISCLAIMER = (
    "官方日线来自欧洲央行参考价（Frankfurter 转发），不是银行买卖价或中间价。"
    "周末与欧央行假日无点，不插值。离岸人民币仅各大行情平台即时价，不用在岸价冒充。"
    "汇率仅供对照，不构成投资建议。"
)


def _now() -> datetime:
    return datetime.now(_TZ)


def _today() -> date:
    return _now().date()


def parse_day(text: str) -> date | None:
    raw = (text or "").strip()[:10]
    if len(raw) != 10 or raw[4:5] != "-" or raw[7:8] != "-":
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def resolve_range(start: str, end: str, default_days: int = DEFAULT_HISTORY_DAYS,
                  max_days: int = MAX_PULL_DAYS) -> tuple[tuple[date, date] | None, str]:
    """解析手动区间。未填则近一年；填了但非法则报错，不猜日期。"""
    start_s = (start or "").strip()
    end_s = (end or "").strip()
    today = _today()
    if start_s and parse_day(start_s) is None:
        return None, "开始日期格式无效，请用 YYYY-MM-DD"
    if end_s and parse_day(end_s) is None:
        return None, "结束日期格式无效，请用 YYYY-MM-DD"
    s = parse_day(start_s) if start_s else None
    e = parse_day(end_s) if end_s else None
    if s is None and e is None:
        e = today
        s = e - timedelta(days=default_days)
    elif s is None:
        s = e - timedelta(days=default_days)
    elif e is None:
        e = today
    if s > e:
        return None, "开始日期不能晚于结束日期"
    if (e - s).days > max_days:
        return None, f"单次最多拉取 {max_days} 天"
    if e > today:
        e = today
    if s.year < 1999:
        s = date(1999, 1, 4)
    if s > e:
        return None, "区间不在可拉取范围内"
    return (s, e), ""


def rate_digits(rate) -> int:
    try:
        a = abs(float(rate))
    except (TypeError, ValueError):
        return 4
    if a >= 1:
        return 4
    if a >= 0.1:
        return 5
    if a >= 0.01:
        return 6
    return 7


def official_currencies() -> list[str]:
    return [row[3] for row in FX_CATALOG if row[6]]


def catalog_public() -> list[dict]:
    return [{
        "pair": p, "name": name, "country": country, "currency": cur,
        "official": official, "unit": unit,
    } for p, name, country, cur, _sina, _em, official, unit in FX_CATALOG]


def _local_latest() -> dict[str, dict]:
    rows = query(
        """SELECT pair, trade_date, rate, source FROM fx_daily
           WHERE (pair, trade_date) IN (
             SELECT pair, MAX(trade_date) FROM fx_daily GROUP BY pair
           )"""
    )
    return {r["pair"]: r for r in rows}


def _upsert_official(rows: list[dict]) -> int:
    """只写入真实官方点。已有欧洲央行价不被即时源覆盖。"""
    if not rows:
        return 0
    existing = {
        (r["pair"], r["trade_date"]): r["source"]
        for r in query("SELECT pair, trade_date, source FROM fx_daily")
    }
    batch = []
    for r in rows:
        pair = r.get("pair")
        day = r.get("trade_date")
        rate = r.get("rate")
        source = r.get("source") or fx_src.ECB_SOURCE
        if not pair or not day or rate is None:
            continue
        if pair == "USDCNH":
            continue
        prev_src = existing.get((pair, day))
        if prev_src and prev_src == fx_src.ECB_SOURCE and source != fx_src.ECB_SOURCE:
            continue
        batch.append((pair, day, float(rate), source))
        existing[(pair, day)] = source
    if not batch:
        return 0
    executemany(
        """INSERT INTO fx_daily(pair, trade_date, rate, source)
           VALUES(?,?,?,?)
           ON CONFLICT(pair, trade_date) DO UPDATE SET
             rate=excluded.rate, source=excluded.source
           WHERE fx_daily.source != '欧洲央行' OR excluded.source = '欧洲央行'""",
        batch,
    )
    return len(batch)


def persist_cnh_snapshot(quotes: dict[str, dict], day: date | None = None) -> int:
    """离岸人民币只在拿到真实即时价时记当日点，不用 USDCNY 冒充。"""
    q = quotes.get("USDCNH") or {}
    rate = q.get("rate")
    source = q.get("source")
    if rate is None or not source:
        return 0
    trade_date = (day or _today()).isoformat()
    execute(
        """INSERT INTO fx_daily(pair, trade_date, rate, source)
           VALUES(?,?,?,?)
           ON CONFLICT(pair, trade_date) DO UPDATE SET
             rate=excluded.rate, source=excluded.source""",
        ("USDCNH", trade_date, float(rate), source),
    )
    return 1


def prune_old(keep_days: int = KEEP_DAYS) -> int:
    cutoff = (_today() - timedelta(days=keep_days)).isoformat()
    before = query("SELECT COUNT(*) AS n FROM fx_daily WHERE trade_date < ?", (cutoff,))
    n = before[0]["n"] if before else 0
    if n:
        execute("DELETE FROM fx_daily WHERE trade_date < ?", (cutoff,))
    return n


def persist_official_range(start: date, end: date) -> dict:
    currencies = official_currencies()
    try:
        rows = fx_src.fetch_frankfurter_range(start, end, currencies)
        written = _upsert_official(rows)
        reason = "" if written or rows else "该区间欧洲央行未公布参考价（周末/假日常见）"
    except Exception as exc:  # noqa: BLE001
        log.warning("欧洲央行汇率拉取失败: %s", exc)
        return {"ok": False, "written": 0, "days": 0, "error": str(exc)[:200],
                "start": start.isoformat(), "end": end.isoformat()}
    days = len({r["trade_date"] for r in rows})
    prune_old()
    cache.delete("fx:live")
    set_meta("fx_last_official_pull", _now().replace(tzinfo=None).isoformat(timespec="seconds"))
    return {
        "ok": True, "written": written, "days": days, "rows": len(rows),
        "start": start.isoformat(), "end": end.isoformat(),
        "source": fx_src.ECB_SOURCE, "reason": reason,
    }


def pull_range(start: str = "", end: str = "") -> dict:
    rng, err = resolve_range(start, end)
    if rng is None:
        return {"ok": False, "error": err, "written": 0}
    s, e = rng
    out = persist_official_range(s, e)
    live = _fetch_live()
    cnh = persist_cnh_snapshot(live, e) if e >= _today() else 0
    out["cnh_written"] = cnh
    out["disclaimer"] = DISCLAIMER
    return out


def ensure_year_history() -> dict:
    """本地官方点过少、币种过少或最新日偏旧时回补近一年，不因单条测试残留而跳过。"""
    rows = query(
        """SELECT COUNT(*) AS n, COUNT(DISTINCT pair) AS pairs, MAX(trade_date) AS last
           FROM fx_daily WHERE source=?""",
        (fx_src.ECB_SOURCE,),
    )
    n = rows[0]["n"] if rows else 0
    pairs = rows[0]["pairs"] if rows else 0
    last = rows[0]["last"] if rows else None
    stale = True
    if last:
        try:
            stale = (_today() - date.fromisoformat(last)).days > 7
        except ValueError:
            stale = True
    if n >= 200 and pairs >= 10 and not stale:
        return {"ok": True, "skipped": True, "local_rows": n, "pairs": pairs}
    end = _today()
    start = end - timedelta(days=DEFAULT_HISTORY_DAYS)
    log.info("回补近一年官方汇率 %s..%s（本地 %s 条/%s 币种）", start, end, n, pairs)
    return persist_official_range(start, end)


def persist_recent_official(days: int = 14) -> dict:
    end = _today()
    start = end - timedelta(days=max(2, min(int(days or 14), 40)))
    return persist_official_range(start, end)


def _fetch_live() -> dict[str, dict]:
    sina_syms = [row[4] for row in FX_CATALOG if row[4]]
    em_ids = [row[5] for row in FX_CATALOG if row[5]]
    by_pair: dict[str, dict] = {}
    try:
        sina_q = fx_src.fetch_sina_fx(sina_syms)
    except Exception as exc:  # noqa: BLE001
        log.warning("新浪汇率失败: %s", exc)
        sina_q = {}
    sina_by_sym = {row[4]: row[0] for row in FX_CATALOG}
    for sym, q in sina_q.items():
        pair = sina_by_sym.get(sym)
        if pair:
            by_pair[pair] = {**q, "pair": pair}
    missing = [row[5] for row in FX_CATALOG if row[0] not in by_pair and row[5]]
    if missing:
        try:
            em_q = fx_src.fetch_eastmoney_fx(em_ids if len(missing) > 8 else missing)
        except Exception as exc:  # noqa: BLE001
            log.warning("东财汇率失败: %s", exc)
            em_q = {}
        em_by_code = {row[5].split(".", 1)[-1]: row[0] for row in FX_CATALOG if row[5]}
        for code, q in em_q.items():
            pair = em_by_code.get(code)
            if pair and pair not in by_pair:
                by_pair[pair] = _scale_em_quote(pair, q)
    return by_pair


def _scale_em_quote(pair: str, quote: dict) -> dict:
    """把东财 100 日元等柜台价折成 1 外币兑人民币。"""
    scale = EM_SCALE.get(pair, 1.0)
    out = {**quote, "pair": pair}
    if scale == 1.0:
        return out
    if out.get("rate") is not None:
        out["rate"] = out["rate"] * scale
    if out.get("prev") is not None:
        out["prev"] = out["prev"] * scale
    if out.get("change") is not None:
        out["change"] = out["change"] * scale
    return out


def refresh_live() -> dict[str, dict]:
    data = _fetch_live()
    if data:
        persist_cnh_snapshot(data)
    cache.delete("fx:live")
    return data


def get_snapshot() -> dict:
    def loader():
        live = _fetch_live()
        local = _local_latest()
        items = []
        live_n = 0
        for pair, name, country, cur, _s, _e, official, unit in FX_CATALOG:
            q = live.get(pair) or {}
            loc = local.get(pair) or {}
            rate = q.get("rate")
            source = q.get("source") or ""
            quote_time = q.get("time") or ""
            prev = q.get("prev")
            change = q.get("change")
            pct = q.get("pct")
            if rate is not None:
                live_n += 1
            if rate is None and loc.get("rate") is not None:
                rate = loc["rate"]
                source = loc.get("source") or "本地缓存"
                quote_time = loc.get("trade_date") or ""
            if pct is None and rate is not None and loc.get("rate") not in (None, 0) and q.get("rate") is not None:
                if prev is None:
                    prev = loc["rate"]
                    change = round(rate - prev, 6)
                    pct = round((rate - prev) / prev * 100, 4)
            items.append({
                "pair": pair, "name": name, "country": country, "currency": cur,
                "official": official, "unit": unit,
                "rate": rate, "prev": prev, "change": change, "pct": pct,
                "source": source, "time": quote_time,
                "local_date": loc.get("trade_date"),
                "local_rate": loc.get("rate"),
                "local_source": loc.get("source"),
                "digits": rate_digits(rate if rate is not None else loc.get("rate")),
            })
        offline = live_n == 0
        reason = ""
        if offline:
            reason = "即时行情暂不可用，已回退本地最近官方日线；没有本地点的品种显示为空，不编造"
        return {
            "items": items,
            "offline": offline,
            "live_count": live_n,
            "asof": _now().replace(tzinfo=None).isoformat(timespec="seconds"),
            "reason": reason,
        }

    payload = cached("fx:live", TTL_FX, loader)
    local_n = query("SELECT COUNT(*) AS n FROM fx_daily")
    latest = query("SELECT MAX(trade_date) AS d FROM fx_daily WHERE source=?", (fx_src.ECB_SOURCE,))
    return {
        **payload,
        "catalog": catalog_public(),
        "schedule": SCHEDULE,
        "disclaimer": DISCLAIMER,
        "local_rows": local_n[0]["n"] if local_n else 0,
        "latest_official": (latest[0]["d"] if latest else None),
        "keep_days": KEEP_DAYS,
        "max_pull_days": MAX_PULL_DAYS,
    }


def get_history(pair: str, start: str = "", end: str = "") -> dict:
    meta = _PAIR_MAP.get((pair or "").strip().upper())
    if not meta:
        return {"ok": False, "error": "未知货币对", "pair": pair, "items": []}
    rng, err = resolve_range(start, end)
    if rng is None:
        return {"ok": False, "error": err, "pair": meta[0], "items": []}
    s, e = rng
    rows = query(
        """SELECT trade_date, rate, source FROM fx_daily
           WHERE pair=? AND trade_date>=? AND trade_date<=?
           ORDER BY trade_date""",
        (meta[0], s.isoformat(), e.isoformat()),
    )
    reason = ""
    if not rows:
        reason = "本地该区间无点。周末/假日欧洲央行不公布；可点「拉取区间」向官方源补数，不会插值编造"
    return {
        "ok": True,
        "pair": meta[0],
        "name": meta[1],
        "country": meta[2],
        "official": meta[6],
        "unit": meta[7],
        "start": s.isoformat(),
        "end": e.isoformat(),
        "items": rows,
        "count": len(rows),
        "empty_reason": reason,
        "disclaimer": DISCLAIMER,
    }


def job_snapshot() -> dict:
    live = refresh_live()
    return {"ok": True, "live": len(live)}


def job_daily() -> dict:
    out = persist_recent_official(14)
    live = _fetch_live()
    out["cnh_written"] = persist_cnh_snapshot(live)
    return out


def job_year() -> dict:
    end = _today()
    start = end - timedelta(days=DEFAULT_HISTORY_DAYS)
    return persist_official_range(start, end)


def fx_session_open(now: datetime | None = None) -> bool:
    """外汇大致交易窗（北京时间）：周日 22:00 至周五 22:00。仅作说明，不据此编造报价。"""
    if now is None:
        cur = _now()
    elif now.tzinfo:
        cur = now.astimezone(_TZ)
    else:
        cur = now
    wd, t = cur.weekday(), cur.time().replace(tzinfo=None)
    if wd == 5:
        return False
    if wd == 6:
        return t >= dtime(22, 0)
    if wd == 4:
        return t <= dtime(22, 0)
    return True
