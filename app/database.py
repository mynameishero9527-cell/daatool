"""SQLite 本地数据库：建表、连接管理、通用读写。"""
import json
import sqlite3
import threading
from contextlib import contextmanager

from .config import DB_PATH, DATA_DIR

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_list (
    code        TEXT PRIMARY KEY,      -- 带市场前缀，如 sz300432
    name        TEXT NOT NULL,
    market      TEXT NOT NULL,         -- SH/SZ/BJ
    board       TEXT NOT NULL,         -- 主板/创业板/科创板/北交所
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_snapshot (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    price       REAL, pct REAL, turnover_rate REAL, volume_ratio REAL,
    pe_ttm      REAL, pb REAL, float_mv REAL, total_mv REAL,
    main_net_in REAL,                 -- 主力净流入（万元）
    main_in     REAL, main_out REAL,
    main_net_in_d5 REAL,
    pct_d5      REAL, pct_d10 REAL, pct_d20 REAL, pct_d60 REAL,
    amount      REAL,                 -- 成交额（万元）
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshot_pct ON stock_snapshot(pct);
CREATE INDEX IF NOT EXISTS idx_snapshot_main ON stock_snapshot(main_net_in);

CREATE TABLE IF NOT EXISTS daily_kline (
    code   TEXT NOT NULL,
    date   TEXT NOT NULL,
    open REAL, close REAL, high REAL, low REAL, volume REAL,
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS macro_event (
    event_id    TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    summary     TEXT,
    category    TEXT,                 -- 地缘政治/货币政策/财政政策/行业监管/自然灾害/公司事件/经济数据
    region      TEXT,
    impact_level INTEGER,             -- 1-5
    impact_direction TEXT,            -- 利好/利空/中性
    affected_sectors TEXT,            -- JSON 数组
    event_time  TEXT,
    publish_time TEXT,
    source      TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_time ON macro_event(event_time);

CREATE TABLE IF NOT EXISTS watchlist (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    pinned INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commodity_watch (
    symbol TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv_meta (
    k TEXT PRIMARY KEY,
    v TEXT
);

-- 2.0 指标表：全市场K线衍生指标 + 企稳/购买指数/情绪/暗盘力量
CREATE TABLE IF NOT EXISTS stock_metrics (
    code            TEXT PRIMARY KEY,
    ma5 REAL, ma10 REAL, ma20 REAL, ma60 REAL,
    rsi14           REAL,
    macd_bar        REAL,
    macd_gold       INTEGER,          -- 近3日MACD金叉
    ma_bull         INTEGER,          -- 均线多头排列
    above_ma20      INTEGER,
    break20_high    INTEGER,          -- 突破20日新高
    pullback_shrink INTEGER,          -- 缩量回调
    pos60           REAL,             -- 60日区间位置 0-1
    drawdown60      REAL,             -- 60日最大回撤 %
    bias20          REAL,             -- 20日乖离率 %
    stab_g1 INTEGER, stab_g2 INTEGER, stab_g3 INTEGER, stab_g4 INTEGER,
    stabilize_score REAL,             -- 企稳强度 0-100（未过闸门为 NULL）
    buy_index       REAL,             -- 购买指数 0-100
    sentiment       REAL,             -- 情绪温度 0-100
    dark_power      REAL,             -- 暗盘力量 0-100
    divergence      TEXT,             -- 暗中吸筹/暗中派发/无
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS screener_plan (
    plan_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    conditions  TEXT NOT NULL,        -- JSON
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stabilize_record (
    code        TEXT NOT NULL,
    select_date TEXT NOT NULL,
    score       REAL,
    price       REAL,
    pct_after_5d REAL, pct_after_10d REAL, pct_after_20d REAL,
    PRIMARY KEY (code, select_date)
);

CREATE TABLE IF NOT EXISTS custom_event (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    title       TEXT NOT NULL,
    category    TEXT DEFAULT '自定义',
    region      TEXT DEFAULT '中国',
    impact_level INTEGER DEFAULT 3,
    note        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sentiment_history (
    date        TEXT PRIMARY KEY,
    market_temp REAL
);

-- 3.0：概念题材映射与板块快照、财务报告缓存
CREATE TABLE IF NOT EXISTS concept_map (
    concept TEXT NOT NULL,
    code    TEXT NOT NULL,
    PRIMARY KEY (concept, code)
);
CREATE INDEX IF NOT EXISTS idx_concept_code ON concept_map(code);

CREATE TABLE IF NOT EXISTS concept_board (
    concept     TEXT PRIMARY KEY,
    pct         REAL,
    turnover    REAL,
    updated_at  TEXT NOT NULL
);

-- 4.0：智能提醒历史
CREATE TABLE IF NOT EXISTS alert_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type  TEXT NOT NULL,       -- buy_point/sell_point/index_move/fund_switch/rotation
    title       TEXT NOT NULL,
    detail      TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    plan_id     TEXT DEFAULT ''      -- 命中的选股方案，如 A 或 A,B
);
CREATE INDEX IF NOT EXISTS idx_alert_time ON alert_log(created_at);

CREATE TABLE IF NOT EXISTS finance_report (
    code        TEXT NOT NULL,
    report_date TEXT NOT NULL,
    report_name TEXT,
    revenue REAL, revenue_yoy REAL,
    net_profit REAL, net_profit_yoy REAL,
    parent_profit REAL, parent_profit_yoy REAL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (code, report_date)
);

-- 10.0：全市场财报健康评级（独立于购买指数）
CREATE TABLE IF NOT EXISTS stock_finance_grade (
    code        TEXT PRIMARY KEY,
    grade       TEXT NOT NULL,          -- A/B/C/D
    score       INTEGER,
    summary     TEXT,
    updated_at  TEXT NOT NULL
);

-- 10.0：板块资金日频（行业/概念），供多日区间累加
CREATE TABLE IF NOT EXISTS sector_flow_daily (
    dim         TEXT NOT NULL,          -- industry / concept / remote_hy / remote_gn
    name        TEXT NOT NULL,
    trade_date  TEXT NOT NULL,          -- 快照 asof 日 YYYY-MM-DD，不是日历今天
    net_in      REAL,                   -- 主力净流入（万元）
    amount      REAL,                   -- 成交额（万元）
    stocks      INTEGER,
    PRIMARY KEY (dim, name, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_sector_flow_date ON sector_flow_daily(dim, trade_date);

-- 11.0.9：宏观/板块情报本地缓存，供离线回看与后续分析
CREATE TABLE IF NOT EXISTS intel_cache (
    item_id           TEXT PRIMARY KEY,
    kind              TEXT NOT NULL,     -- news/policy/calendar/sector_event
    title             TEXT NOT NULL,
    summary           TEXT,
    event_time        TEXT,
    region            TEXT,
    category          TEXT,
    impact_level      INTEGER,
    impact_direction  TEXT,
    affected_sectors  TEXT,              -- JSON 数组
    source            TEXT,
    raw_json          TEXT,
    fetched_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intel_time ON intel_cache(event_time);
CREATE INDEX IF NOT EXISTS idx_intel_kind ON intel_cache(kind);
CREATE TABLE IF NOT EXISTS intel_sector (
    sector     TEXT NOT NULL,
    item_id    TEXT NOT NULL,
    event_time TEXT,
    PRIMARY KEY (sector, item_id)
);
CREATE INDEX IF NOT EXISTS idx_intel_sector ON intel_sector(sector, event_time);

-- 11.0.12：大A每日量能（成交额/成交量），供板块资金走势副图
CREATE TABLE IF NOT EXISTS market_volume_daily (
    trade_date    TEXT PRIMARY KEY,
    amount_yi     REAL,                 -- 全A成交额（亿），优先中证全指
    sh_amount_yi  REAL,
    sz_amount_yi  REAL,
    bj_amount_yi  REAL,
    volume        REAL,                 -- 中证全指成交量（手）
    sh_volume     REAL,                 -- 上证成交量（手）
    close         REAL,                 -- 中证全指收盘
    pct           REAL,
    source        TEXT
);

-- 11.0.14：个股持股/股东结构本地缓存（季度披露，缺数不编）
CREATE TABLE IF NOT EXISTS stock_holders (
    code        TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,          -- 规范化 JSON
    fetched_at  TEXT NOT NULL
);

-- 11.0.21：官方政策近半年归档（时间/国内外/国家/文件类型），供后续抽关键信息
CREATE TABLE IF NOT EXISTS official_policy (
    policy_id          TEXT PRIMARY KEY,
    title              TEXT NOT NULL,
    summary            TEXT,
    event_time         TEXT,
    scope              TEXT,                 -- 国内 / 国外
    country            TEXT,
    doc_type           TEXT,                 -- 政策文件 / 大会会议 / 通知意见 / 监管动态
    source             TEXT,
    url                TEXT,
    affected_sectors   TEXT,                 -- JSON 数组
    impact_level       INTEGER,
    impact_direction   TEXT,
    raw_json           TEXT,
    fetched_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_official_policy_time ON official_policy(event_time);
CREATE INDEX IF NOT EXISTS idx_official_policy_scope ON official_policy(scope, country);
CREATE INDEX IF NOT EXISTS idx_official_policy_type ON official_policy(doc_type);

-- 11.0.21：近两周热度词汇快照（热度值 / 上升 / 下降）
CREATE TABLE IF NOT EXISTS hot_term (
    term          TEXT NOT NULL,
    window_end    TEXT NOT NULL,            -- 快照日 YYYY-MM-DD
    heat          REAL,
    heat_prev     REAL,
    rise          REAL,
    fall          REAL,
    count_now     INTEGER,
    count_prev    INTEGER,
    sectors       TEXT,                     -- JSON 相关板块
    sample_titles TEXT,                     -- JSON 样例标题
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (term, window_end)
);
CREATE INDEX IF NOT EXISTS idx_hot_term_heat ON hot_term(window_end, heat);

-- 12.0.2：热词利好/利空的 AI 回填（与词库快照分离，重建热词不覆盖）
CREATE TABLE IF NOT EXISTS hot_term_ai (
    term       TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,            -- JSON：bull/bear/reason/text/source
    updated_at TEXT NOT NULL
);

-- 12.0.3：个股持股 AI 分析结果（与 F10 快照分离，手动更新才覆盖）
CREATE TABLE IF NOT EXISTS stock_holder_ai (
    code       TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,            -- JSON：text/source/asof/analyzed_at
    updated_at TEXT NOT NULL
);
"""

# 已有表的增量列迁移（幂等）
MIGRATIONS = [
    "ALTER TABLE stock_list ADD COLUMN industry TEXT DEFAULT ''",
    "ALTER TABLE stock_snapshot ADD COLUMN amplitude REAL",
    "ALTER TABLE custom_event ADD COLUMN sectors TEXT DEFAULT ''",
    "ALTER TABLE sector_flow_daily ADD COLUMN source TEXT DEFAULT ''",
    "ALTER TABLE sector_flow_daily ADD COLUMN board_code TEXT DEFAULT ''",
    "ALTER TABLE alert_log ADD COLUMN plan_id TEXT DEFAULT ''",
]


def _conn() -> sqlite3.Connection:
    if getattr(_local, "conn", None) is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return _local.conn


def init_db() -> None:
    conn = _conn()
    conn.executescript(SCHEMA)
    for sql in MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # 列已存在
    conn.commit()


@contextmanager
def db():
    conn = _conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def query(sql: str, params: tuple = ()) -> list[dict]:
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def execute(sql: str, params: tuple = ()) -> None:
    with db() as conn:
        conn.execute(sql, params)


def executemany(sql: str, seq: list[tuple]) -> None:
    with db() as conn:
        conn.executemany(sql, seq)


def get_meta(key: str, default: str | None = None) -> str | None:
    rows = query("SELECT v FROM kv_meta WHERE k=?", (key,))
    return rows[0]["v"] if rows else default


def set_meta(key: str, value: str) -> None:
    execute(
        "INSERT INTO kv_meta(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (key, value),
    )


def get_meta_json(key: str, default=None):
    raw = get_meta(key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def set_meta_json(key: str, value) -> None:
    set_meta(key, json.dumps(value, ensure_ascii=False))
