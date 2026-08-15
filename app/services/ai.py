"""AI 分析（FR7-07）：可配置 OpenAI 兼容大模型 + 本地规则分析兜底。"""
import json
import logging
import threading

import httpx

from ..database import get_meta, get_meta_json, query, set_meta, set_meta_json
from . import cycle as cycle_svc
from . import market as market_svc
from . import rating as rating_svc

log = logging.getLogger("ai")

DISCLAIMER = "\n\n——\nAI生成内容仅供参考，不构成投资建议。"

PRESETS = [
    {"id": "openai", "name": "OpenAI",
     "api_base": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    {"id": "deepseek", "name": "DeepSeek",
     "api_base": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    {"id": "qwen", "name": "通义千问",
     "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    {"id": "moonshot", "name": "月之暗面",
     "api_base": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    {"id": "zhipu", "name": "智谱 GLM",
     "api_base": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
]

_llm_client: httpx.Client | None = None
_llm_lock = threading.Lock()


def _llm_http() -> httpx.Client:
    """独立客户端：连接 15s / 总超时 60s，不复用行情 6s 连接池。"""
    global _llm_client
    with _llm_lock:
        if _llm_client is None:
            _llm_client = httpx.Client(
                timeout=httpx.Timeout(60.0, connect=15.0),
                headers={"User-Agent": "daatool-ai/1.0", "Accept": "application/json"},
                follow_redirects=True,
            )
        return _llm_client


def normalize_api_base(raw: str) -> str:
    """纠正常见粘贴错误：缺 /v1、多贴了 /chat/completions、通义未走 compatible-mode。"""
    url = (raw or "").strip()
    if not url:
        return ""
    url = url.rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if url.lower().endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    low = url.lower()
    if "dashscope.aliyuncs.com" in low and "compatible-mode" not in low:
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if "api.openai.com" in low and not low.endswith("/v1"):
        url = url + "/v1"
    elif "api.deepseek.com" in low and "/v1" not in low.split("api.deepseek.com", 1)[-1]:
        url = url.rstrip("/") + "/v1"
    elif "api.moonshot.cn" in low and not low.endswith("/v1"):
        url = url + "/v1"
    return url.rstrip("/")


def _set_last_error(msg: str) -> None:
    set_meta("ai_last_error", (msg or "")[:400])


def _last_error() -> str:
    return get_meta("ai_last_error", "") or ""


def get_config(masked: bool = True) -> dict:
    cfg = get_meta_json("ai_config", {}) or {}
    base = normalize_api_base(cfg.get("api_base", "") or "")
    out = {
        "api_base": base,
        "model": cfg.get("model", ""),
        "configured": bool(cfg.get("api_key") and base),
        "last_error": _last_error(),
        "presets": PRESETS,
    }
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
        "api_base": normalize_api_base(api_base),
        "api_key": api_key.strip(),
        "model": model.strip(),
    })
    _set_last_error("")
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
    base = normalize_api_base(cfg.get("api_base", "") or "")
    if cfg.get("api_key") and base:
        try:
            text = _call_llm(cfg, context, task)
            return {"text": text + DISCLAIMER, "source": f"AI大模型（{cfg.get('model') or '默认'}）",
                    "ai": True, "error": ""}
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM调用失败: %s", exc)
            fallback = _local_analysis(mode, code, question)
            err = str(exc)[:240]
            return {"text": fallback + DISCLAIMER,
                    "source": f"本地规则分析（大模型调用失败：{err}）",
                    "ai": False, "error": err}
    return {"text": _local_analysis(mode, code, question) + DISCLAIMER,
            "source": "本地规则分析（未配置AI大模型，可在AI分析页配置）",
            "ai": False, "error": ""}


def _parse_llm_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        text = (resp.text or "").strip()
        return text[:300] if text else f"HTTP {resp.status_code}"
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("msg") or err.get("code") or err)[:300]
    if isinstance(err, str) and err.strip():
        return err[:300]
    for key in ("message", "msg", "detail"):
        if data.get(key):
            return str(data[key])[:300]
    return (resp.text or f"HTTP {resp.status_code}")[:300]


def _call_llm(cfg: dict, context: str, task: str) -> str:
    base = normalize_api_base(cfg.get("api_base", "") or "")
    key = (cfg.get("api_key") or "").strip()
    if not key:
        raise RuntimeError("未配置 API 密钥")
    if not base:
        raise RuntimeError("未配置 API 地址")
    url = base + "/chat/completions"
    payload = {
        "model": (cfg.get("model") or "").strip() or "gpt-4o-mini",
        "messages": [
            {"role": "system",
             "content": "你是专业的A股量化分析助手。基于用户提供的实时数据回答，客观严谨，"
                        "分点作答，不做收益承诺。所有结论附依据。"},
            {"role": "user", "content": f"【实时数据】{context}\n\n【任务】{task}"},
        ],
        "temperature": 0.4,
        "max_tokens": 900,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            resp = _llm_http().post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}：{_parse_llm_error(resp)}")
            data = resp.json()
            if data.get("error"):
                raise RuntimeError(_parse_llm_error(resp))
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError("模型返回空 choices，请检查模型名称是否正确")
            content = ((choices[0] or {}).get("message") or {}).get("content") or ""
            if isinstance(content, list):
                content = "".join(
                    (p.get("text") or "") if isinstance(p, dict) else str(p) for p in content)
            text = str(content).strip()
            if not text:
                raise RuntimeError("模型返回空内容，请检查模型名称或额度")
            _set_last_error("")
            return text
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            last_exc = RuntimeError(f"网络失败：{exc}"[:240])
            log.warning("LLM 网络失败 attempt=%s: %s", attempt + 1, exc)
            continue
        except Exception as exc:  # noqa: BLE001
            last_exc = exc if isinstance(exc, RuntimeError) else RuntimeError(str(exc)[:240])
            break
    msg = str(last_exc or "大模型调用失败")[:300]
    _set_last_error(msg)
    raise last_exc or RuntimeError(msg)


def test_connection() -> dict:
    """最小连通性探测：配置缺失时给出明确原因，成功则返回模型短回复。"""
    cfg = get_meta_json("ai_config", {}) or {}
    base = normalize_api_base(cfg.get("api_base", "") or "")
    if not cfg.get("api_key"):
        return {"ok": False, "error": "请先填写并保存 API 密钥", "api_base": base, "model": cfg.get("model", "")}
    if not base:
        return {"ok": False, "error": "请先填写并保存 API 地址", "api_base": base, "model": cfg.get("model", "")}
    try:
        text = _call_llm(cfg, "连通性测试", "请只回复两个字：成功")
        return {"ok": True, "reply": text[:120], "api_base": base, "model": cfg.get("model") or "gpt-4o-mini"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300], "api_base": base, "model": cfg.get("model", "")}


def pick_stocks(description: str) -> dict:
    """AI 选股（FR8-05）：语义解析 → 本地筛选；已配置大模型时附 AI 点评。"""
    from . import screener
    result = screener.run_semantic(description, limit=50)
    commentary, source, llm_error = "", "语义规则解析", ""
    cfg = get_meta_json("ai_config", {}) or {}
    base = normalize_api_base(cfg.get("api_base", "") or "")
    if cfg.get("api_key") and base and result["items"]:
        try:
            top = "；".join(
                f"{i['name']}({i['code']}) 涨跌{i['pct']}% 购买指数{i['buy_index']}"
                for i in result["items"][:10])
            commentary = _call_llm(
                cfg,
                f"用户选股描述：「{description}」。解析规则：{result['summary']}。命中个股（前10）：{top}",
                "请从命中个股中精选3只并各用一句话点评（结合规则与数据），最后给一句风险提示，150字内。")
            source = f"语义规则解析 + AI点评（{cfg.get('model')}）"
        except Exception as exc:  # noqa: BLE001
            log.warning("AI选股点评失败: %s", exc)
            llm_error = str(exc)[:240]
            source = f"语义规则解析（AI点评失败：{llm_error[:80]}）"
    elif not (cfg.get("api_key") and base):
        llm_error = ""
    return {**result, "commentary": (commentary + DISCLAIMER) if commentary else "",
            "source": source, "error": llm_error, "ai": bool(commentary)}


def classify_wuxing(code: str) -> dict:
    """AI 五行打标（FR8-03-4）：大模型判定金木水火土，未配置时回退本地行业规则。"""
    import re
    from . import wuxing as wx
    from . import market as mkt
    norm = mkt.normalize_code(code) or code
    info = wx.get_tags(norm)
    tags = list(info.get("auto_tags") or ["土"])
    text = (f"【本地规则】行业「{info.get('industry') or '未知'}」自动归类为"
            f"{'、'.join(tags)}。{info.get('note', '')}")
    source = "本地规则分类"
    applied = False
    cfg = get_meta_json("ai_config", {}) or {}
    if cfg.get("api_key") and cfg.get("api_base"):
        try:
            raw = _call_llm(
                cfg,
                f"股票代码 {norm}，行业 {info.get('industry')}，"
                f"本地自动分类参考 {tags}",
                "请判定该股五行归属（金木水火土，可多选）。严格只输出一行JSON："
                '{"tags":["火"],"reason":"一句话理由"}',
            )
            m = re.search(r"\{[^{}]*\}", raw)
            if m:
                parsed = json.loads(m.group())
                cand = [t for t in parsed.get("tags", []) if t in wx.WUXING]
                if cand:
                    tags = cand
                    wx.set_tags(norm, tags)
                    applied = True
            text = raw
            source = f"AI大模型（{cfg.get('model') or '默认'}）"
        except Exception as exc:  # noqa: BLE001
            log.warning("AI五行分类失败: %s", exc)
            text += f"\n（大模型调用失败，已回退本地规则：{str(exc)[:80]}）"
    return {"code": norm, "tags": tags, "text": text + DISCLAIMER,
            "source": source, "applied": applied}


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
