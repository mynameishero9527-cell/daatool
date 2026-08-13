"""AI 分析（FR7-07）：可配置 OpenAI 兼容大模型 + 本地规则分析兜底。"""
import json
import logging

from ..database import get_meta_json, query, set_meta_json
from ..datasources.base import http_client
from . import cycle as cycle_svc
from . import market as market_svc
from . import rating as rating_svc

log = logging.getLogger("ai")

DISCLAIMER = "\n\n——\nAI生成内容仅供参考，不构成投资建议。"


def get_config(masked: bool = True) -> dict:
    cfg = get_meta_json("ai_config", {}) or {}
    out = {"api_base": cfg.get("api_base", ""), "model": cfg.get("model", ""),
           "configured": bool(cfg.get("api_key"))}
    if masked:
        key = cfg.get("api_key", "")
        out["api_key"] = (key[:6] + "****" + key[-4:]) if len(key) > 12 else ("****" if key else "")
    else:
        out["api_key"] = cfg.get("api_key", "")
    return out


def save_config(api_base: str, api_key: str, model: str) -> dict:
    old = get_meta_json("ai_config", {}) or {}
    if api_key and "****" in api_key:  # 未修改脱敏值则保留原密钥
        api_key = old.get("api_key", "")
    set_meta_json("ai_config", {
        "api_base": api_base.strip().rstrip("/"),
        "api_key": api_key.strip(),
        "model": model.strip(),
    })
    return {"ok": True, **get_config()}


# ---------------- 上下文构建 ----------------

def _market_context() -> str:
    parts = []
    try:
        c = cycle_svc.get_cycle()
        parts.append(f"大盘：上证指数 {c['index_price']}，周期阶段「{c['stage']}」，"
                     f"攻守姿态「{c['stance']}」（恐慌指数 {c['panic_index']}），"
                     f"市场宽度 {c['breadth']}% 上涨，量能{c['vol_desc']}")
    except Exception:  # noqa: BLE001
        pass
    rows = query("SELECT SUM(CASE WHEN pct>=9.8 THEN 1 ELSE 0 END) lu, "
                 "SUM(CASE WHEN pct<=-9.8 THEN 1 ELSE 0 END) ld FROM stock_snapshot")
    if rows:
        parts.append(f"涨停 {rows[0]['lu'] or 0} 家，跌停 {rows[0]['ld'] or 0} 家")
    flows = query("SELECT l.industry, ROUND(SUM(s.main_net_in)/10000,1) f FROM stock_snapshot s "
                  "JOIN stock_list l ON l.code=s.code WHERE l.industry!='' "
                  "GROUP BY l.industry ORDER BY f DESC LIMIT 3")
    if flows:
        parts.append("资金主攻板块：" + "、".join(f"{r['industry']}(+{r['f']}亿)" for r in flows))
    return "；".join(parts)


def _stock_context(code: str) -> str:
    norm = market_svc.normalize_code(code) or code
    q = (market_svc.get_quotes([norm]) or {}).get(norm) or {}
    s = query("SELECT * FROM stock_snapshot WHERE code=?", (norm,))
    m = query("SELECT * FROM stock_metrics WHERE code=?", (norm,))
    ind = query("SELECT industry FROM stock_list WHERE code=?", (norm,))
    parts = [f"股票：{q.get('name', norm)}（{norm}），现价 {q.get('price')}，涨跌幅 {q.get('pct')}%"]
    if ind and ind[0]["industry"]:
        parts.append(f"所属行业：{ind[0]['industry']}")
    if s:
        r = s[0]
        parts.append(f"5日/20日/60日涨幅 {r['pct_d5']}%/{r['pct_d20']}%/{r['pct_d60']}%，"
                     f"换手 {r['turnover_rate']}%，量比 {r['volume_ratio']}，"
                     f"PE {r['pe_ttm']}，PB {r['pb']}，流通市值 {r['float_mv']} 亿，"
                     f"主力当日净流入 {round((r['main_net_in'] or 0) / 10000, 2)} 亿")
    if m:
        r = m[0]
        parts.append(f"量化指标：购买指数 {r['buy_index']}，情绪温度 {r['sentiment']}，"
                     f"暗盘力量 {r['dark_power']}，RSI {r['rsi14']}，"
                     f"60日位置 {round((r['pos60'] or 0) * 100)}% 分位"
                     + (f"，企稳强度 {r['stabilize_score']}" if r["stabilize_score"] else ""))
    return "；".join(parts)


# ---------------- 分析入口 ----------------

def analyze(mode: str = "market", code: str = "", question: str = "") -> dict:
    if mode == "stock" and code:
        context = _market_context() + "。" + _stock_context(code)
        task = f"请基于以上实时数据，对该股做简明诊断：走势研判、资金与量能解读、主要风险、操作参考（分点，300字内）。"
    elif mode == "custom" and question:
        context = _market_context()
        task = question
    else:
        mode = "market"
        context = _market_context()
        task = "请基于以上实时数据，输出今日大盘综述与明日展望：市场状态、资金主线、风险提示、策略建议（分点，300字内）。"

    cfg = get_meta_json("ai_config", {}) or {}
    if cfg.get("api_key") and cfg.get("api_base"):
        try:
            text = _call_llm(cfg, context, task)
            return {"text": text + DISCLAIMER, "source": f"AI大模型（{cfg.get('model') or '默认'}）", "ai": True}
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM调用失败: %s", exc)
            fallback = _local_analysis(mode, code, question)
            return {"text": fallback + DISCLAIMER,
                    "source": f"本地规则分析（大模型调用失败：{str(exc)[:80]}）", "ai": False}
    return {"text": _local_analysis(mode, code, question) + DISCLAIMER,
            "source": "本地规则分析（未配置AI大模型，可在AI分析页配置）", "ai": False}


def _call_llm(cfg: dict, context: str, task: str) -> str:
    url = cfg["api_base"].rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg.get("model") or "gpt-4o-mini",
        "messages": [
            {"role": "system",
             "content": "你是专业的A股量化分析助手。基于用户提供的实时数据回答，客观严谨，"
                        "分点作答，不做收益承诺。所有结论附依据。"},
            {"role": "user", "content": f"【实时数据】{context}\n\n【任务】{task}"},
        ],
        "temperature": 0.4,
        "max_tokens": 900,
    }
    resp = http_client().post(url, json=payload, timeout=60,
                              headers={"Authorization": f"Bearer {cfg['api_key']}"})
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def _local_analysis(mode: str, code: str, question: str) -> str:
    """本地规则分析兜底：由既有指标体系生成结构化文本。"""
    if mode == "stock" and code:
        norm = market_svc.normalize_code(code) or code
        r = rating_svc.score_stock(norm)
        if r.get("score") is None:
            return f"【本地诊断】{norm}：{r.get('message', '数据不足')}"
        from . import attribution
        att = attribution.get_attribution(norm)
        lines = [
            f"【本地规则诊断】{r['name']}（{norm}）",
            f"1. 综合评分 {r['score']}（{r['grade']}），操作提示：{r['advice']}。",
            f"2. 依据：{r['advice_reason']}。",
            f"3. 今日归因：" + "；".join(x["text"] for x in att["reasons"][:3]),
            f"4. 利空风险指数 {att['risk_index']}：{att['risk_warning']}",
        ]
        return "\n".join(lines)
    ctx = _market_context()
    try:
        c = cycle_svc.get_cycle()
        strategy = c.get("stance_desc", "")
    except Exception:  # noqa: BLE001
        strategy = ""
    lines = [
        "【本地规则综述】",
        f"1. 市场状态：{ctx}。",
        f"2. 策略参考：{strategy}",
    ]
    if question:
        lines.append(f"3. 你的问题「{question[:50]}」需要 AI 大模型能力，请在 AI 分析页配置后重试。")
    return "\n".join(lines)
