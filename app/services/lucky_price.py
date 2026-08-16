"""股价口彩特征：对子 / 连号 / 吉利数字。

只用真实现价和日K最低价，不编造价格，也不把普通价硬标成吉利。
"""
from __future__ import annotations

from ..database import query

NEAR_PCT = 0.08
LOOKBACK_BARS = 40
MIN_BARS = 10
LUCKY_EXACT = {
    "1.68", "5.20", "6.18", "6.66", "6.68", "6.88",
    "8.16", "8.18", "8.68", "8.86", "8.88", "9.99",
    "13.14", "16.80", "16.88", "18.80", "18.88", "19.88",
    "26.88", "28.88", "36.88", "38.88", "58.88",
    "66.66", "66.88", "68.88", "78.88", "88.88", "98.88",
}
LUCKY_INTS = {6, 8, 9, 16, 18, 26, 28, 36, 38, 58, 66, 68, 78, 88, 98}
LUCKY_CENTS = {"16", "18", "28", "66", "68", "78", "86", "88", "98", "99"}
LUCKY_SUBS = ("168", "188", "666", "888", "999", "520", "1314", "668", "886")
DOC = (
    "吉利价特征（真实现价 + 日K最低价，缺K线不编低点）："
    "对子如 6.66/12.12；连号如 1.23/12.34；"
    "吉利如 8.88/16.88/5.20/13.14 及含 168/888 的口彩。"
    "现价仍贴着该近期低点（不超过 8%）才算「吉利低点」。"
)


def format_px(price) -> str | None:
    try:
        x = float(price)
    except (TypeError, ValueError):
        return None
    if x <= 0 or x > 100000:
        return None
    return f"{x:.2f}"


def _digits(text: str) -> str:
    return "".join(c for c in text if c.isdigit())


def _is_consecutive(ds: str) -> bool:
    if len(ds) < 3:
        return False
    nums = [int(c) for c in ds]
    for i in range(len(nums) - 2):
        a, b, c = nums[i], nums[i + 1], nums[i + 2]
        if b - a == 1 and c - b == 1:
            return True
        if a - b == 1 and b - c == 1:
            return True
    diffs = [nums[i + 1] - nums[i] for i in range(len(nums) - 1)]
    return bool(diffs) and (all(d == 1 for d in diffs) or all(d == -1 for d in diffs))


def classify(price) -> dict:
    """识别对子 / 全同号 / 连号 / 吉利。普通价返回空标签，不硬标。"""
    text = format_px(price)
    if not text:
        return {"lucky_tags": [], "lucky_label": "", "price_text": ""}
    ds = _digits(text)
    int_part, dec = text.split(".")
    tags: list[str] = []
    if ds and len(set(ds)) == 1:
        tags.append("全同号")
    if int_part == dec or (len(ds) == 4 and ds[0] == ds[2] and ds[1] == ds[3]):
        tags.append("对子")
    if len(ds) == 4 and ds[0] == ds[1] and ds[2] == ds[3]:
        tags.append("对子")
    if len(int_part) == 1 and dec[0] == dec[1] == int_part:
        tags.append("对子")
    if _is_consecutive(ds):
        tags.append("连号")
    if text in LUCKY_EXACT or any(p in ds for p in LUCKY_SUBS):
        tags.append("吉利")
    else:
        try:
            iv = int(int_part)
        except ValueError:
            iv = None
        if dec in LUCKY_CENTS and iv in LUCKY_INTS:
            tags.append("吉利")
    seen: list[str] = []
    for t in tags:
        if t not in seen:
            seen.append(t)
    label = ("·".join(seen) + f" {text}") if seen else ""
    return {"lucky_tags": seen, "lucky_label": label, "price_text": text}


def _near_low(price, low) -> bool:
    try:
        p = float(price)
        lo = float(low)
    except (TypeError, ValueError):
        return False
    if p <= 0 or lo <= 0:
        return False
    return lo * (1.0 - 0.005) <= p <= lo * (1.0 + NEAR_PCT)


def annotate(price, low=None) -> dict:
    """现价口彩 + 是否贴着吉利/对子/连号的近期低点。缺低点不编造。"""
    px = classify(price)
    low_feat = classify(low) if low is not None else {"lucky_tags": [], "lucky_label": "", "price_text": ""}
    near = _near_low(price, low) if low is not None else False
    lucky_low = bool(near and (low_feat["lucky_tags"] or px["lucky_tags"]))
    tags = list(px["lucky_tags"])
    if lucky_low:
        for t in low_feat["lucky_tags"]:
            if t not in tags:
                tags.append(t)
    show = px["lucky_label"] or (low_feat["lucky_label"] if lucky_low else "")
    if lucky_low and low_feat.get("price_text") and px.get("price_text") != low_feat.get("price_text"):
        show = (show + f" · 低点{low_feat['price_text']}").strip(" ·")
    return {
        "lucky_tags": tags,
        "lucky_label": show,
        "price_text": px.get("price_text") or "",
        "lucky_low": lucky_low,
        "lucky_low_price": float(low) if lucky_low and low is not None else None,
    }


def recent_low_map(codes: list[str]) -> dict[str, dict]:
    """近 40 根真实日K最低价。K线不足不编造。"""
    codes = [c for c in codes if c]
    if not codes:
        return {}
    out: dict[str, dict] = {}
    chunk = 300
    for i in range(0, len(codes), chunk):
        part = codes[i:i + chunk]
        marks = ",".join("?" * len(part))
        rows = query(
            f"SELECT code, date, low, close FROM daily_kline "
            f"WHERE code IN ({marks}) ORDER BY code, date DESC",
            tuple(part),
        )
        buckets: dict[str, list] = {}
        for row in rows:
            buckets.setdefault(row["code"], []).append(row)
        for code, bars in buckets.items():
            vals: list[float] = []
            for b in bars[:LOOKBACK_BARS]:
                raw = b.get("low")
                if raw is None:
                    raw = b.get("close")
                try:
                    v = float(raw)
                except (TypeError, ValueError):
                    continue
                if v > 0:
                    vals.append(v)
            if len(vals) < MIN_BARS:
                continue
            out[code] = {"low": min(vals), "bars": len(vals)}
    return out


def attach_rows(items: list[dict]) -> list[dict]:
    codes = [r.get("code") for r in items if r.get("code")]
    lows = recent_low_map(codes)
    for r in items:
        info = lows.get(r.get("code") or "") or {}
        r.update(annotate(r.get("price"), info.get("low")))
    return items
