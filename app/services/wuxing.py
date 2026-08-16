"""个股五行标签体系（FR8-03）：行业/题材自动分类 + 用户右键勾选覆盖。"""
import json

from ..database import execute, query

WUXING = ["金", "木", "水", "火", "土"]
WUXING_COLOR = {"金": "#e8c46b", "木": "#26c281", "水": "#4a9eff", "火": "#ff5252", "土": "#b08050"}

# 行业 → 五行（传统行业五行归类，民俗参考）
_INDUSTRY_WUXING = {
    "有色金属": ["金"], "钢铁": ["金"], "银行": ["金"], "非银金融": ["金"],
    "机械设备": ["金"], "国防军工": ["金", "火"], "汽车": ["金"],
    "农林牧渔": ["木"], "医药生物": ["木"], "轻工制造": ["木"], "纺织服饰": ["木"],
    "家用电器": ["木", "火"], "美容护理": ["木"],
    "交通运输": ["水"], "商贸零售": ["水"], "食品饮料": ["水", "土"],
    "环保": ["水"], "社会服务": ["水"], "传媒": ["水"],
    "石油石化": ["火"], "煤炭": ["火"], "电力设备": ["火"], "电子": ["火"],
    "计算机": ["火"], "通信": ["火"], "公用事业": ["火", "水"],
    "房地产": ["土"], "建筑装饰": ["土"], "建筑材料": ["土"], "基础化工": ["土", "火"],
    "综合": ["土"],
}
_CONCEPT_HINTS = [
    (("黄金", "白银", "贵金属", "证券", "芯片封测"), "金"),
    (("中药", "种业", "林业", "造纸"), "木"),
    (("水务", "航运", "港口", "白酒", "啤酒", "饮料"), "水"),
    (("锂电", "光伏", "储能", "半导体", "算力", "人工智能", "军工电子"), "火"),
    (("水泥", "基建", "土地", "房地产"), "土"),
]


def auto_tags(industry: str, concepts: list[str] | None = None) -> list[str]:
    tags = list(_INDUSTRY_WUXING.get(industry or "", []))
    for kws, wx in _CONCEPT_HINTS:
        if any(any(k in c for k in kws) for c in (concepts or [])):
            if wx not in tags:
                tags.append(wx)
    return tags or ["土"]


_user_cache: dict | None = None


def _user_tags() -> dict:
    global _user_cache
    if _user_cache is None:
        _user_cache = {}
        try:
            for r in query("SELECT k, v FROM kv_meta WHERE k LIKE 'wuxing_%'"):
                _user_cache[r["k"][7:]] = json.loads(r["v"])
        except Exception:  # noqa: BLE001
            pass
    return _user_cache


def get_tags(code: str) -> dict:
    """个股五行：用户标记优先，否则自动分类。"""
    user = _user_tags().get(code)
    ind_rows = query("SELECT industry FROM stock_list WHERE code=?", (code,))
    industry = ind_rows[0]["industry"] if ind_rows else ""
    concepts = [r["concept"] for r in query(
        "SELECT concept FROM concept_map WHERE code=? LIMIT 10", (code,))]
    auto = auto_tags(industry, concepts)
    return {
        "code": code, "tags": user if user is not None else auto,
        "auto_tags": auto, "user_defined": user is not None,
        "industry": industry,
        "colors": WUXING_COLOR,
        "note": "五行分类按行业/题材民俗归类，仅供文化参考；右键个股可自定义勾选",
    }


def set_tags(code: str, tags: list[str]) -> dict:
    tags = [t for t in tags if t in WUXING]
    execute("INSERT INTO kv_meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (f"wuxing_{code}", json.dumps(tags, ensure_ascii=False)))
    _user_tags()[code] = tags
    return {"ok": True, "tags": tags}


def industries_for(elements) -> list[str]:
    """五行 → 本地行业名。只返回词表里已有的行业，不编造板块。"""
    want = {str(x) for x in (elements or []) if x in WUXING}
    if not want:
        return []
    return [ind for ind, tags in _INDUSTRY_WUXING.items() if want.intersection(tags)]


def tags_for_list(items: list[dict], code_key: str = "code",
                  industry_key: str = "industry") -> None:
    """批量为列表项附加五行标签（就地修改，供榜单/推荐使用）。"""
    user = _user_tags()
    for it in items:
        code = it.get(code_key, "")
        if code in user:
            it["wuxing"] = user[code]
        else:
            it["wuxing"] = auto_tags(it.get(industry_key) or "", it.get("concepts"))
