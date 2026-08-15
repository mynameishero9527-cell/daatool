"""策略引擎 12.0 蓝图：只读设计对象，不跑采集、不写快照、不编评分。"""

VERSION = "12.0.0"
STATUS = "design_only"

DOMAINS = [
    {"id": "market", "name": "大盘环境", "from": "指数/涨跌家数/成交额", "on": True},
    {"id": "quote", "name": "个股行情", "from": "stock_snapshot", "on": True},
    {"id": "flow", "name": "主力资金", "from": "快照资金字段", "on": True},
    {"id": "metrics", "name": "量化四指标", "from": "购买指数/情绪/暗盘/企稳", "on": True},
    {"id": "finance", "name": "财报评级", "from": "A/B/C/D 独立维度", "on": True},
    {"id": "industry", "name": "行业资金", "from": "板块聚合/日账本", "on": True},
    {"id": "concept", "name": "概念题材", "from": "concept_map", "on": True},
    {"id": "news", "name": "快讯情报", "from": "intel_cache", "on": True},
    {"id": "policy", "name": "官方政策", "from": "近半年归档", "on": True},
    {"id": "hot_terms", "name": "热度词汇", "from": "近两周热词", "on": True},
    {"id": "calendar", "name": "事件日历", "from": "展望/高星事件", "on": True},
    {"id": "announce", "name": "公司公告", "from": "公告接口", "on": True},
    {"id": "commodity", "name": "大宗商品", "from": "商品+关联个股", "on": True},
    {"id": "global", "name": "全球指数", "from": "外盘指数缓存", "on": True},
    {"id": "strategy_hit", "name": "买/卖方案命中", "from": "选股策略 A–H", "on": True},
    {"id": "holders", "name": "股东结构", "from": "有则用，缺则缺", "on": False},
]

WEIGHTS = [
    {"id": "d_buy", "name": "购买指数", "w": 18, "note": "不并入财报"},
    {"id": "d_flow", "name": "主力资金", "w": 14, "note": "净流入+买比"},
    {"id": "d_fin", "name": "财报评级", "w": 12, "note": "无评级=50 不奖不罚"},
    {"id": "d_sector", "name": "板块资金", "w": 10, "note": "有账本才谈多日"},
    {"id": "d_macro", "name": "宏观催化", "w": 10, "note": "政策/热词/日历"},
    {"id": "d_vol", "name": "量能", "w": 8, "note": "量比映射"},
    {"id": "d_stab", "name": "企稳", "w": 8, "note": "未过闸门为缺失"},
    {"id": "d_dark", "name": "暗盘", "w": 7, "note": "吸筹/派发"},
    {"id": "d_env", "name": "市场体制", "w": 7, "note": "偏多/震荡/偏空"},
    {"id": "d_ext", "name": "商品外盘", "w": 6, "note": "无映射记 0"},
]

SCHEDULE = [
    {"id": "intraday", "name": "盘中刷新", "when": "交易时段每 10 分钟", "out": "intraday 快照，允许部分域缺失"},
    {"id": "eod", "name": "日终定拍", "when": "交易日 15:50（指标重建之后）", "out": "当日正式快照"},
    {"id": "llm", "name": "LLM 摘要", "when": "默认关闭", "out": "只写 Brief 句子，失败回退规则"},
]

OUTPUTS = [
    {"id": "MarketRegime", "name": "市场体制", "use": "环境分、引擎快照页"},
    {"id": "SectorRotation", "name": "板块轮动", "use": "流入/流出 Top、热词/政策标签"},
    {"id": "MacroCatalyst", "name": "宏观催化", "use": "政策/热词/日历 → 板块 → 个股"},
    {"id": "StockVector", "name": "个股向量", "use": "智能选股综合分"},
    {"id": "SignalBundle", "name": "信号包", "use": "买点池/卖点池"},
    {"id": "EngineBrief", "name": "关键信息清单", "use": "快照页与 AI 语境"},
]

SMARTPICK_TABS = [
    {
        "id": "composite",
        "name": "综合选股",
        "live": True,
        "blurb": "本轮仍用现场计算（模板/硬条件/权重）。引擎启用后改为消费 StockVector，页头会写明快照 run。",
    },
    {
        "id": "signals",
        "name": "策略命中",
        "live": False,
        "blurb": "将列出引擎信号包中的买点/卖点，方案名与设置→选股策略启用集一致。本轮不跑引擎，故无名单。",
        "empty": "策略引擎未产出信号包。可先在「综合选股」计算，或到设置→选股策略查看启用方案。",
        "jumps": [{"tab": "settings", "view": "strategy", "label": "打开选股策略"}],
    },
    {
        "id": "catalyst",
        "name": "宏观催化",
        "live": False,
        "blurb": "将展示引擎抽取的关键政策、热词、高星日历，点击路径：催化 → 热门板块 → 个股 TOP20。",
        "empty": "尚无引擎 Brief。原始数据仍在宏观情报里，可先查看官方政策与热度词汇。",
        "jumps": [
            {"tab": "macro", "sub": "official", "label": "官方政策信息"},
            {"tab": "macro", "sub": "hotwords", "label": "热度词汇"},
        ],
    },
    {
        "id": "snapshot",
        "name": "引擎快照",
        "live": False,
        "blurb": "将展示体制、量能、关键事实清单、各域新鲜度与缺失原因。",
        "empty": "尚无引擎快照。完整设计见设置→策略引擎配置。",
        "jumps": [{"tab": "settings", "view": "engine", "label": "打开策略引擎配置"}],
    },
]

RULES = [
    "财报评级不并入购买指数",
    "禁止用涨跌幅伪造多日资金",
    "无评级显示为 —，不得记为 A",
    "策略 SQL 只在服务端硬编码",
    "缺数给原因，不编造政策/热度/股东/资金",
    "LLM 失败回退本地规则，不假装成功",
    "全部结论为量化参考，不构成投资建议",
]

FLOW = [
    {"step": "1 采集", "text": "只读本地库与缓存，记录每个域是否可用、条数、asof"},
    {"step": "2 提取", "text": "压成关键指标与短文本事实，空值保留并写 missing_reason"},
    {"step": "3 分析", "text": "规则计算体制、轮动、催化、个股向量、买/卖信号包"},
    {"step": "4 快照", "text": "按交易日落盘，供智能选股与其它入口消费"},
    {"step": "5 消费", "text": "综合分=向量加权；命中页=信号包；催化页=Brief；缺快照则回退现场计算"},
]


def blueprint() -> dict:
    return {
        "ok": True,
        "version": VERSION,
        "status": STATUS,
        "enabled": False,
        "title": "策略引擎配置",
        "subtitle": "本轮只落设计逻辑，不跑采集、不写快照、不编评分。",
        "doc": "需求优化文档12.0.md",
        "domains": DOMAINS,
        "weights": WEIGHTS,
        "schedule": SCHEDULE,
        "outputs": OUTPUTS,
        "smartpick_tabs": SMARTPICK_TABS,
        "rules": RULES,
        "flow": FLOW,
        "note": "启用开关将在实现阶段写入 kv_meta.engine_config，默认仍为关闭。",
    }
