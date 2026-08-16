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

-- 13.0.13：个股主力资金日K（亿元），仅落真实拉取结果，不用涨跌幅冒充
-- 13.0.15：small_net_yi 为东财小单净流入（亿元），缺则 NULL，不用涨跌幅冒充
CREATE TABLE IF NOT EXISTS stock_fund_daily (
    code         TEXT NOT NULL,
    trade_date   TEXT NOT NULL,
    main_net_yi  REAL,
    super_net_yi REAL,
    large_net_yi REAL,
    small_net_yi REAL,
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_stock_fund_date ON stock_fund_daily(trade_date);

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

-- 13.0.1：个股右键 AI 简明诊断 / 五行判定（失败不覆盖）
CREATE TABLE IF NOT EXISTS stock_ai_brief (
    code       TEXT NOT NULL,
    kind       TEXT NOT NULL,            -- diagnose / wuxing
    payload    TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (code, kind)
);

-- 13.0.2：宏观情报全部二级菜单（含热词/常识）右键 AI 解读与词库；热门信息块展示
-- 13.0.3：AI 利好/利空与情报拉取板块合并回显；点击板块看个股 TOP20/30/50
-- 13.0.4：板块个股表显示现价；利好/利空同名互斥
-- 13.0.5：股票常识 / 选股票小技巧 AI 更新解释（失败不覆盖）；热门信息去掉来源 tab；公告须公司语境
-- 13.0.6：原智能选股改名为策略选股；新建智能选股菜单（股价未来涨跌方向，本轮只建入口）
-- 13.0.7：热门信息默认按热度倒序，支持关注度/更新时间/事件时间排序
-- 13.0.8：最佳买点/卖点浮窗改用主界面卡片主题
-- 13.0.9：公告读本地快讯库；知识更新接口兼容空 body；买点卖点浮窗用主界面表格
-- 13.0.10：板块事件按日/周/月合并到时间桶并汇总板块；条目多时折叠排版
-- 13.0.11：买点窗紧凑；个股页复制代码/自选同步；机构评级与财务分析改持股 tab
-- 13.0.12：机构评级高亮/目标价上涨空间/多空直方图；财报评级卡；公司公告 tab；买点窗非表格+操作建议
-- 13.0.13：顶栏搜索；财报评级紧凑+综合评分突出；K线下方主力资金图
-- 13.0.14：财报评级卡一行展示现价/一致目标价/相对现价/涨跌停价（现价不重复）
-- 13.0.15：资金栏移到资金K线下并补散户净流入；行情看板二级菜单股票周期（12个月板块强弱）
-- 13.0.16：行情看板市场统计/情绪周期/明日预测三卡紧凑布局
-- 13.0.17：持股构成下十大股东与十大流通股东分 tab 显示
-- 13.0.18：今日黄历·九宫方位从自选移到智能选股页
-- 13.0.19：黄历九宫可按日期查询（干支按甲子日推算，不编造官方黄历）
-- 13.0.20：切换日期时同步更新干支/黄道等，并写入本地 almanac_day
-- 13.0.21：个股主力资金K多源补历史并定时同步到本地 stock_fund_daily
-- 13.0.22：顶栏黄历补齐阳历农历与干支年月日时
-- 13.0.23：方案 I/J、周线门、factor_daily 技术因子回放、本地模拟账本
-- 13.0.24：黄历九宫补时辰吉凶、奇门遁甲、紫微流日示意、节气与节假日倒计时
-- 13.0.25：全球指数下各国汇率；官方日线只落欧洲央行已公布点，不插值、不拿在岸冒充离岸
-- 13.0.26：手动拉取近一年同时写新浪人民币日K补缺，已有官方价不覆盖；拉取后回写本地天数
-- 13.0.27：各国汇率页 AI 利好/利空大A板块回填，点击板块看个股 TOP20/30/50
-- 13.0.28：黄历九宫补易经/大六壬；卜卦六爻只匹配本地代码；奇门预测按旺相+高分策略最多50只
-- 13.0.29：最佳买点/卖点策略拆开重做：买点看潜力结构，卖点止盈避险，取消观察池凑数
-- 13.0.30：买点/卖点卡片补财报评级与五行（未评级显示 —，不并入购买指数）
-- 13.0.31：买/卖点须命中≥2个方案，并结合近半年真实日K与当前价位；缺K线不编造
-- 13.0.32：多方案并行；日K不足即时补真实K线；新股按综合评分+财报评级，未评级不伪造
-- 13.0.33：易经卜卦按卦象五行匹配热门板块，只显示财报A/B且综合评分较高的本地股
-- 13.0.34：买/卖点对齐综合评分与增持减持，叠加板块热度、财报、距半年高点上涨空间
-- 13.0.35：点击个股进入分析时默认展示日K，不再默认分时
-- 13.0.36：买点并行取并集+质量门禁，不再被周线门和互斥交叉命中滤空
-- 13.0.37：卜卦/奇门预测个股落本地，智能选股「预测推荐」回显；不写入上涨/下跌名单
-- 13.0.38：个股分析机构持仓展示机构名称与持股比例，缺披露不编造
-- 13.0.39：预测推荐同股去重；点击在单独 tab 看预测个股，不重复展示
-- 13.0.40：最佳买点恢复第一版选股方案A（购买指数≥80且主力净流入）
-- 13.0.41：买/卖点方案支持删除，恢复默认加回；勾空不复活已删方案
-- 13.0.42：买点BZ更名方案B-企稳趋势；对子/连号/吉利价作近期低点特征
-- 13.0.43：智能选股下新增主页 tab；预测推荐数据层独立成 tab，不混在主页
-- 13.0.44：吉利低点贴水放宽到 8% 并扫全候选池，避免实盘筛不出
-- intel_item_ai / hot_term_ai 表结构不变，失败仍不覆盖已保存结果

CREATE TABLE IF NOT EXISTS knowledge_ai (
    term        TEXT PRIMARY KEY,
    grp         TEXT,
    desc        TEXT NOT NULL,
    payload     TEXT,
    updated_at  TEXT NOT NULL
);

-- 12.0.9：宏观情报条目的 AI 利好/利空、解读、关键词（失败不覆盖）
CREATE TABLE IF NOT EXISTS intel_item_ai (
    item_key      TEXT PRIMARY KEY,      -- source:ident
    source        TEXT NOT NULL,         -- news/policy/official/calendar/...
    source_label  TEXT,
    ident         TEXT,
    title         TEXT,
    text_excerpt  TEXT,
    attention     REAL,                  -- 关注度，缺则 NULL
    heat          REAL,                  -- 热度，缺则 NULL
    event_time    TEXT,
    payload       TEXT NOT NULL,         -- JSON：bull/bear/reading/keywords/...
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intel_item_ai_src ON intel_item_ai(source, updated_at);

-- 13.0：策略引擎快照与信号任务（策略只读本地库，禁止写行情表）
CREATE TABLE IF NOT EXISTS engine_run (
    run_id      TEXT PRIMARY KEY,
    asof        TEXT NOT NULL,
    kind        TEXT NOT NULL,           -- eod / intraday / manual
    started_at  TEXT,
    finished_at TEXT,
    status      TEXT,                    -- ok / partial / fail
    domains_json TEXT,
    brief_json  TEXT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_engine_run_asof ON engine_run(asof, kind);

CREATE TABLE IF NOT EXISTS engine_stock (
    run_id     TEXT NOT NULL,
    code       TEXT NOT NULL,
    name       TEXT,
    price      REAL,
    pct        REAL,
    industry   TEXT,
    d_buy REAL, d_sent REAL, d_dark REAL, d_stab REAL, d_flow REAL,
    d_vol REAL, d_fin REAL, d_sector REAL, d_macro REAL, d_ext REAL, d_env REAL,
    fin_missing INTEGER DEFAULT 0,
    score      REAL,
    hits_json  TEXT,
    catalysts_json TEXT,
    boards_json TEXT,
    reason_bits TEXT,
    PRIMARY KEY (run_id, code)
);
CREATE INDEX IF NOT EXISTS idx_engine_stock_score ON engine_stock(run_id, score);

CREATE TABLE IF NOT EXISTS engine_sector (
    run_id    TEXT NOT NULL,
    dim       TEXT NOT NULL,
    name      TEXT NOT NULL,
    hot_score REAL,
    pct       REAL,
    net_in    REAL,
    direction TEXT,
    tags      TEXT,
    PRIMARY KEY (run_id, dim, name)
);

CREATE TABLE IF NOT EXISTS engine_catalyst (
    run_id        TEXT NOT NULL,
    cat_id        TEXT NOT NULL,
    kind          TEXT,
    title         TEXT,
    direction     TEXT,
    impact_level  INTEGER,
    sectors_json  TEXT,
    event_time    TEXT,
    extra_json    TEXT,
    PRIMARY KEY (run_id, cat_id)
);

CREATE TABLE IF NOT EXISTS signal_task (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    asof      TEXT,
    code      TEXT,
    name      TEXT,
    side      TEXT,
    plan_id   TEXT,
    entry_px  REAL,
    stop_px   REAL,
    take_px   REAL,
    expire_n  INTEGER,
    status    TEXT,
    exit_date TEXT,
    exit_px   REAL,
    ret_1     REAL,
    ret_5     REAL,
    ret_20    REAL,
    note      TEXT,
    run_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_signal_task_open ON signal_task(status, asof);
CREATE INDEX IF NOT EXISTS idx_signal_task_code ON signal_task(code, asof);

-- 13.0.23：方案 I/J 特征（只从已缓存 stock_holders 物化，不猜户数）
CREATE TABLE IF NOT EXISTS holder_feature (
    code TEXT PRIMARY KEY,
    asof TEXT,
    holders_qoq REAL,
    institution_ratio REAL,
    institution_count INTEGER,
    unlock_date TEXT,
    unlock_float_ratio REAL,
    unlock_days_to INTEGER,
    last_unlock_date TEXT,
    last_unlock_days_ago INTEGER,
    has_institution INTEGER DEFAULT 0
);

-- 13.0.23：按日因子，第 t 日只用当日及以前的日 K
CREATE TABLE IF NOT EXISTS factor_daily (
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    ma5 REAL, ma10 REAL, ma20 REAL, macd_bar REAL, rsi14 REAL,
    bias20 REAL, pos60 REAL, vol_ratio REAL,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_factor_daily_date ON factor_daily(date);

-- 13.0.23：本地模拟账本（默认关，T+1，不接券商）
CREATE TABLE IF NOT EXISTS paper_lot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    side TEXT NOT NULL,
    qty REAL,
    price REAL,
    asof TEXT NOT NULL,
    created_at TEXT,
    status TEXT,
    note TEXT,
    linked_id INTEGER
);

-- 13.0.20：黄历按日缓存（始终重算后再写入，库内不是黄道/干支的权威源）
CREATE TABLE IF NOT EXISTS almanac_day (
    day        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 13.0.25：各国汇率日线（1 外币兑人民币）。周末/假日无官方点则无行
CREATE TABLE IF NOT EXISTS fx_daily (
    pair        TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    rate        REAL NOT NULL,
    source      TEXT NOT NULL,
    PRIMARY KEY (pair, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_fx_daily_date ON fx_daily(trade_date);

-- 13.0.37：易经卜卦 / 奇门遁甲预测个股本地回显，不写入上涨/下跌名单
CREATE TABLE IF NOT EXISTS forecast_batch (
    batch_no     TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    kind_label   TEXT NOT NULL,
    predicted_at TEXT NOT NULL,
    almanac_date TEXT,
    hour         INTEGER,
    wuxing       TEXT,
    summary      TEXT,
    extra        TEXT,
    stock_count  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_forecast_batch_at ON forecast_batch(predicted_at DESC);
CREATE TABLE IF NOT EXISTS forecast_stock (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no      TEXT NOT NULL,
    code          TEXT NOT NULL,
    name          TEXT NOT NULL,
    industry      TEXT,
    wuxing        TEXT,
    finance_grade TEXT,
    score         REAL,
    advice        TEXT,
    buy_index     REAL,
    price         REAL,
    pct           REAL,
    wx_state      TEXT,
    UNIQUE(batch_no, code)
);
CREATE INDEX IF NOT EXISTS idx_forecast_stock_batch ON forecast_stock(batch_no);
"""

# 已有表的增量列迁移（幂等）
MIGRATIONS = [
    "ALTER TABLE stock_list ADD COLUMN industry TEXT DEFAULT ''",
    "ALTER TABLE stock_snapshot ADD COLUMN amplitude REAL",
    "ALTER TABLE custom_event ADD COLUMN sectors TEXT DEFAULT ''",
    "ALTER TABLE sector_flow_daily ADD COLUMN source TEXT DEFAULT ''",
    "ALTER TABLE sector_flow_daily ADD COLUMN board_code TEXT DEFAULT ''",
    "ALTER TABLE alert_log ADD COLUMN plan_id TEXT DEFAULT ''",
    "ALTER TABLE stock_fund_daily ADD COLUMN small_net_yi REAL",
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
