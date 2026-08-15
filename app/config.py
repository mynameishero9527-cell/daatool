"""全局配置。"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
DB_PATH = DATA_DIR / "quant.db"

# HTTP
HTTP_TIMEOUT = 6.0          # 单次请求超时（秒）
HTTP_RETRIES = 1
CIRCUIT_FAIL_THRESHOLD = 5  # 连续失败 N 次熔断
CIRCUIT_OPEN_SECONDS = 300  # 熔断时长

# 缓存 TTL（秒）
TTL_REALTIME = 10
TTL_INDEX = 60
TTL_COMMODITY = 60
TTL_GLOBAL_INDEX = 60
TTL_NEWS = 60
TTL_KLINE_INTRADAY = 300
TTL_FUND_KLINE = 60         # 主力资金K：盘中需较新，历史靠本地库累积
TTL_STOCK_LIST = 86400

# 调度间隔（秒）
INTERVAL_REALTIME = 10      # 盘中高频：自选+指数
INTERVAL_MEDIUM = 60        # 盘中中频：商品/全球指数
INTERVAL_NEWS = 60          # 快讯轮询
INTERVAL_SNAPSHOT = 300     # 全市场快照增量刷新（盘中）

# 全市场同步
RANK_PAGE_SIZE = 200

# 默认自选股
DEFAULT_WATCHLIST = [
    ("sh600519", "贵州茅台"),
    ("sz300432", "富临精工"),
    ("sh601318", "中国平安"),
    ("sz000858", "五粮液"),
    ("sz300750", "宁德时代"),
    ("sh688981", "中芯国际"),
]
