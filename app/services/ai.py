"""AI 分析（FR7-07）：可配置 OpenAI 兼容大模型 + 本地规则分析兜底。"""
import json
import logging
import re
import socket
from urllib.parse import urlparse

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
    {"id": "openrouter", "name": "OpenRouter",
     "api_base": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini"},
]


def _new_llm_http() -> httpx.Client:
    """每次调用新建客户端：跟随系统代理，避免旧连接池/证书状态卡住。"""
    return httpx.Client(
        timeout=httpx.Timeout(90.0, connect=20.0),
        headers={"User-Agent": "daatool-ai/11.0.1", "Accept": "application/json"},
        follow_redirects=True,
        trust_env=True,
    )


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
        "api_key": (api_key or "").strip(),
        "model": (model or "").strip(),
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
            err = format_llm_error(exc, base)
            return {"text": fallback + DISCLAIMER,
                    "source": "本地规则分析（大模型未调用）",
                    "ai": False, "error": err, "hint": _hint_for_error(err)}
    return {"text": _local_analysis(mode, code, question) + DISCLAIMER,
            "source": "本地规则分析（未配置AI大模型，可在AI分析页配置）",
            "ai": False, "error": "", "hint": ""}


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


def _auth_headers(base: str, key: str) -> dict:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if "openrouter.ai" in (base or "").lower():
        headers["HTTP-Referer"] = "https://github.com/mynameishero9527-cell/daatool"
        headers["X-Title"] = "daatool"
    return headers


def _vendor_name(base: str) -> str:
    host = ""
    try:
        raw = base or ""
        host = urlparse(raw if "://" in raw else f"https://{raw}").hostname or ""
    except Exception:  # noqa: BLE001
        host = (base or "").lower()
    low = host.lower()
    if "deepseek" in low:
        return "DeepSeek"
    if "dashscope" in low or "aliyun" in low:
        return "通义千问"
    if "openai.com" in low:
        return "OpenAI"
    if "moonshot" in low:
        return "月之暗面"
    if "bigmodel" in low:
        return "智谱"
    if "openrouter" in low:
        return "OpenRouter"
    return "大模型服务商"


def format_llm_error(exc: Exception | str, base: str = "") -> str:
    """把 httpx/服务商原始错误收成短中文，避免 URL 被截成 chat/co）。"""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return _friendly_http_error(exc.response.status_code, _parse_llm_error(exc.response), base)
    msg = str(exc or "").strip()
    m = re.search(r"\b([45]\d\d)\b", msg)
    if m and ("client error" in msg.lower() or "payment required" in msg.lower()
              or msg.startswith("HTTP ") or "for url" in msg.lower()):
        return _friendly_http_error(int(m.group(1)), msg, base)
    if msg.startswith("HTTP ") and "：" in msg:
        try:
            status = int(msg.split("：", 1)[0].split()[1])
            body = msg.split("：", 1)[1]
            return _friendly_http_error(status, body, base)
        except Exception:  # noqa: BLE001
            pass
    return msg[:220]


def _friendly_http_error(status: int, body: str, base: str = "") -> str:
    vendor = _vendor_name(base)
    blob = f"{body or ''} {base or ''}".lower()
    if status == 402 or "payment required" in blob or "insufficient" in blob:
        if "deepseek" in blob or vendor == "DeepSeek":
            return ("DeepSeek 返回 402：账户余额不足。"
                    "请到 https://platform.deepseek.com 充值后再调用；"
                    "接口地址 https://api.deepseek.com/v1 是对的，不是 URL 写错。")
        return f"{vendor}返回 402：账户未付费或余额不足，请到对应平台充值。这不是接口地址写错。"
    labels = {
        400: "请求参数不被该模型接受",
        401: "密钥无效或未开通",
        403: "没有该模型权限",
        404: "地址或模型名不存在",
        429: "限流或额度用尽",
        500: "服务商内部错误",
        503: "服务商暂时不可用",
    }
    label = labels.get(status, f"HTTP {status}")
    extra = (body or "").strip()
    if extra and extra.lower() not in ("payment required", f"http {status}") and "for url" not in extra.lower():
        return f"{vendor}返回 {status}（{label}）：{extra[:140]}"
    return f"{vendor}返回 {status}：{label}"


def _hint_for_error(err: str) -> str:
    low = (err or "").lower()
    if "未配置" in (err or "") or "请先填写" in (err or ""):
        return "先在左侧填完整地址、密钥、模型名称并保存。"
    if "402" in low or "payment required" in low or "余额不足" in (err or "") or "未付费" in (err or ""):
        if "deepseek" in low:
            return "到 DeepSeek 开放平台充值即可，不必改 API 地址或模型名。"
        return "服务商要求付费或余额不足，充值后再试。这不是接口地址写错。"
    if "401" in low or "unauthorized" in low or "invalid api key" in low or "incorrect api key" in low:
        return "密钥被拒绝。核对是否粘贴完整、是否选对了服务商，部分平台要先充值开通。"
    if "403" in low or "permission" in low:
        return "当前密钥没有该模型权限，换一个已开通的模型名再试。"
    if "404" in low or "not found" in low:
        return "地址或模型名不对。地址应止于 /v1（智谱止于 /api/paas/v4），不要再拼 /chat/completions。"
    if "429" in low or "rate" in low or "quota" in low or "额度" in (err or ""):
        return "触发限流或额度用尽，稍后再试或检查账户余额。"
    if "timeout" in low or "timed out" in low:
        return "等待超时。检查代理/科学上网，或换延迟更低的接口。"
    if "ssl" in low or "certificate" in low:
        return "TLS 证书校验失败。检查系统时间、公司代理或 HTTPS 中间人。"
    if "connect" in low or "name or service not known" in low or "nodename" in low or "dns" in low:
        return "连不上该域名。检查网络出口、DNS，或该服务商在当前环境是否可达。"
    if "max_tokens" in low or "max_completion_tokens" in low:
        return "该模型参数不兼容。已自动改用 max_completion_tokens 重试；若仍失败请换模型。"
    return "把「测试连通」的逐步结果对照检查：密钥、地址、模型名、网络。"


def _post_chat(url: str, payload: dict, headers: dict) -> httpx.Response:
    client = _new_llm_http()
    try:
        return client.post(url, json=payload, headers=headers)
    finally:
        client.close()


def _call_llm(cfg: dict, context: str, task: str) -> str:
    base = normalize_api_base(cfg.get("api_base", "") or "")
    key = (cfg.get("api_key") or "").strip()
    if not key:
        raise RuntimeError("未配置 API 密钥")
    if not base:
        raise RuntimeError("未配置 API 地址")
    url = base + "/chat/completions"
    messages = [
        {"role": "system",
         "content": "你是专业的A股量化分析助手。基于用户提供的实时数据回答，客观严谨，"
                    "分点作答，不做收益承诺。所有结论附依据。"},
        {"role": "user", "content": f"【实时数据】{context}\n\n【任务】{task}"},
    ]
    model = (cfg.get("model") or "").strip() or "gpt-4o-mini"
    variants = [
        {"max_tokens": 900, "temperature": 0.4},
        {"max_completion_tokens": 900, "temperature": 0.4},
        {"max_completion_tokens": 900},
    ]
    headers = _auth_headers(base, key)
    last_exc: Exception | None = None
    idx, net_try = 0, 0
    while idx < len(variants):
        payload = {"model": model, "messages": messages, **variants[idx]}
        try:
            resp = _post_chat(url, payload, headers)
            if resp.status_code == 400 and idx < len(variants) - 1:
                last_exc = RuntimeError(f"HTTP 400：{_parse_llm_error(resp)}")
                log.warning("LLM 400，切换参数重试: %s", last_exc)
                idx += 1
                net_try = 0
                continue
            if resp.status_code >= 400:
                raise RuntimeError(_friendly_http_error(resp.status_code, _parse_llm_error(resp), base))
            data = resp.json()
            if data.get("error"):
                raise RuntimeError(_friendly_http_error(resp.status_code, _parse_llm_error(resp), base))
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
        except httpx.HTTPStatusError as exc:
            last_exc = RuntimeError(format_llm_error(exc, base))
            break
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            last_exc = RuntimeError(f"网络失败：{str(exc)[:180]}")
            log.warning("LLM 网络失败 variant=%s try=%s: %s", idx, net_try + 1, exc)
            net_try += 1
            if net_try >= 2:
                break
            continue
        except Exception as exc:  # noqa: BLE001
            last_exc = RuntimeError(format_llm_error(exc, base))
            break
    msg = str(last_exc or "大模型调用失败")[:300]
    _set_last_error(msg)
    raise last_exc or RuntimeError(msg)


def diagnose() -> dict:
    """逐步探测：配置 → DNS → TCP → Chat Completions，并给出处理建议。"""
    cfg = get_meta_json("ai_config", {}) or {}
    base = normalize_api_base(cfg.get("api_base", "") or "")
    key = (cfg.get("api_key") or "").strip()
    model = (cfg.get("model") or "").strip() or "gpt-4o-mini"
    steps: list[dict] = []

    def add(sid: str, label: str, ok: bool, detail: str) -> None:
        steps.append({"id": sid, "label": label, "ok": ok, "detail": detail})

    add("key", "API 密钥", bool(key), "已填写" if key else "未填写")
    add("base", "API 地址", bool(base), base or "未填写")
    add("model", "模型名称", True, model)
    if not key:
        err = "请先填写并保存 API 密钥"
        _set_last_error(err)
        return {"ok": False, "error": err, "hint": _hint_for_error(err),
                "api_base": base, "model": model, "steps": steps}
    if not base:
        err = "请先填写并保存 API 地址"
        _set_last_error(err)
        return {"ok": False, "error": err, "hint": _hint_for_error(err),
                "api_base": base, "model": model, "steps": steps}

    parsed = urlparse(base if "://" in base else f"https://{base}")
    host = parsed.hostname or ""
    port = parsed.port or (443 if (parsed.scheme or "https") == "https" else 80)
    if not host:
        err = f"无法解析 API 地址：{base}"
        add("dns", "域名解析", False, err)
        return {"ok": False, "error": err, "hint": _hint_for_error(err),
                "api_base": base, "model": model, "steps": steps}
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ip = infos[0][4][0] if infos else "?"
        add("dns", "域名解析", True, f"{host} → {ip}")
    except Exception as exc:  # noqa: BLE001
        err = f"DNS 失败：{exc}"
        add("dns", "域名解析", False, str(exc)[:200])
        _set_last_error(err)
        return {"ok": False, "error": err, "hint": _hint_for_error(err),
                "api_base": base, "model": model, "steps": steps}
    try:
        with socket.create_connection((host, port), 8):
            add("tcp", "TCP 连接", True, f"{host}:{port} 可连接")
    except Exception as exc:  # noqa: BLE001
        err = f"TCP 失败：{exc}"
        add("tcp", "TCP 连接", False, str(exc)[:200])
        _set_last_error(err)
        return {"ok": False, "error": err, "hint": _hint_for_error(err),
                "api_base": base, "model": model, "steps": steps}

    try:
        text = _call_llm(cfg, "连通性测试", "请只回复两个字：成功")
        add("chat", "Chat Completions", True, (text or "")[:80] or "ok")
        return {"ok": True, "reply": (text or "")[:120], "error": "", "hint": "",
                "api_base": base, "model": model, "steps": steps}
    except Exception as exc:  # noqa: BLE001
        err = format_llm_error(exc, base)
        add("chat", "Chat Completions", False, err)
        _set_last_error(err)
        return {"ok": False, "error": err, "hint": _hint_for_error(err),
                "api_base": base, "model": model, "steps": steps}


def test_connection() -> dict:
    """连通性探测：返回逐步检查结果与处理建议。"""
    return diagnose()


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
            llm_error = format_llm_error(exc, base)
            source = "语义规则解析（AI点评未调用）"
    elif not (cfg.get("api_key") and base):
        llm_error = ""
    return {**result, "commentary": (commentary + DISCLAIMER) if commentary else "",
            "source": source, "error": llm_error, "hint": _hint_for_error(llm_error) if llm_error else "",
            "ai": bool(commentary)}


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
            err = format_llm_error(exc, cfg.get("api_base") or "")
            text += f"\n（大模型未调用，已回退本地规则：{err}）"
            return {"code": norm, "tags": tags, "text": text + DISCLAIMER,
                    "source": source, "applied": applied, "error": err, "hint": _hint_for_error(err)}
    return {"code": norm, "tags": tags, "text": text + DISCLAIMER,
            "source": source, "applied": applied, "error": "", "hint": ""}


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
