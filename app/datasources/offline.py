"""离线兜底数据源（P3）：网络全断时基于代码哈希生成确定性演示数据，界面标注「离线数据」。"""
import hashlib
import math
from datetime import date, timedelta

SOURCE = "离线兜底"


def _seed(key: str) -> float:
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def fetch_quotes(codes: list[str]) -> dict[str, dict]:
    result = {}
    for code in codes:
        s = _seed(code)
        base = 5 + s * 95
        pct = round((s - 0.5) * 6, 2)
        price = round(base * (1 + pct / 100), 2)
        result[code] = {
            "code": code, "name": code.upper(),
            "price": price, "prev_close": round(base, 2),
            "open": round(base * (1 + pct / 200), 2),
            "high": round(price * 1.01, 2), "low": round(base * 0.99, 2),
            "volume": int(s * 500000), "amount": round(s * 80000, 1),
            "change": round(price - base, 2), "pct": pct,
            "time": "offline",
            "turnover_rate": round(s * 5, 2), "pe": round(10 + s * 40, 1),
            "pb": round(1 + s * 5, 2), "amplitude": round(s * 4, 2),
            "float_mv": round(s * 500, 1), "total_mv": round(s * 600, 1),
            "volume_ratio": round(0.5 + s * 2, 2),
            "source": SOURCE,
        }
    return result


def fetch_kline(code: str, period: str = "day", count: int = 320) -> list[list]:
    s = _seed(code)
    base = 5 + s * 95
    step = {"day": 1, "week": 7, "month": 30}.get(period, 1)
    out = []
    price = base
    for i in range(count):
        d = date.today() - timedelta(days=(count - i) * step)
        drift = math.sin(i / 9 + s * 10) * 0.02 + (_seed(f"{code}{i}") - 0.5) * 0.03
        open_ = price
        close = max(0.5, price * (1 + drift))
        high = max(open_, close) * 1.012
        low = min(open_, close) * 0.988
        vol = int(100000 * (0.6 + _seed(f"v{code}{i}")))
        out.append([d.isoformat(), round(open_, 2), round(close, 2), round(high, 2), round(low, 2), vol])
        price = close
    return out
