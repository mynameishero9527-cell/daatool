"""股票常识知识库（FR7-03）：术语解读与实用技巧（本地静态 + AI 覆盖，失败不覆盖）。"""
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ..database import execute, query

_TZ = ZoneInfo("Asia/Shanghai")

KNOWLEDGE: list[dict] = [
    {"group": "基础术语", "items": [
        ("K线", "以开盘/收盘/最高/最低四价绘制的蜡烛图。红（阳线）收盘>开盘，绿（阴线）反之；上下影线代表盘中冲高回落/探底回升。"),
        ("分时图", "当日每分钟价格走势，虚线为昨收基准。分时上穿均价线且量能配合，为盘中转强信号。"),
        ("量比", "当日每分钟成交量 / 过去5日同时段均量。>1.5温和放量，>2.5显著放量，<0.5地量。"),
        ("换手率", "成交股数/流通股本。<1%冷清，3-7%活跃，>15%极度活跃（警惕分歧）。"),
        ("委比", "五档委买卖量差/总和，衡量挂单意愿。正值买盘挂单占优，但可撤单，需结合内外盘。"),
        ("内外盘", "外盘=以卖价成交（主动买入），内盘=以买价成交（主动卖出）。外盘>内盘代表买方更急。"),
        ("涨跌停", "主板±10%，创业板/科创板±20%，北交所±30%，ST股主板±5%。"),
        ("振幅", "（最高-最低）/昨收。振幅大代表多空分歧激烈。"),
        ("复权", "除权除息后修正历史价格。前复权以当前价为基准（常用），保证K线连续。"),
        ("市盈率PE", "股价/每股收益。TTM为滚动四季。低PE不一定便宜（周期股顶部PE最低），需结合行业。"),
        ("市净率PB", "股价/每股净资产。<1为破净，常见于银行地产；重资产行业更适用。"),
        ("ROE", "净资产收益率=净利润/净资产，衡量赚钱效率。连续多年>15%为优质企业常见特征。"),
        ("流通市值", "流通股本×股价。小市值弹性大易被资金撬动，大市值稳定但拉升需巨量资金。"),
        ("集合竞价", "9:15-9:25撮合开盘价。9:20前可撤单（虚假挂单多），9:20-9:25不可撤（更真实）。"),
        ("解禁", "限售股到期可流通。大比例解禁前股价常承压，关注股东是否有减持意愿。"),
    ]},
    {"group": "资金术语", "items": [
        ("主力资金", "单笔大额成交的资金统称（机构/游资/大户）。持续净流入且股价温和上行，多为吸筹。"),
        ("游资", "短线大额投机资金，偏好小市值题材股，龙虎榜常客。特点是快进快出。"),
        ("北向资金", "经沪深股通买入A股的境外资金，偏好白马蓝筹，动向被视为「聪明钱」参考。"),
        ("大宗交易", "盘后大额协议成交。折价大且频繁，警惕股东减持；溢价成交多为看好。"),
        ("龙虎榜", "交易所披露的异动股买卖前五席位。机构专用席位净买入具参考意义。"),
        ("暗盘力量", "本工具指标：内外盘+大单结构+资金背离合成，刻画表面涨跌之外的真实买卖力量。"),
        ("吸筹/派发", "主力低位悄悄买入为吸筹（价平量增，大单买小单卖）；高位悄悄卖出为派发（价滞量增）。"),
        ("对倒", "主力自买自卖制造虚假活跃。特征：量大但价不动，盘口成交规律整齐。"),
    ]},
    {"group": "形态战法", "items": [
        ("均线多头排列", "MA5>MA10>MA20 向上发散，趋势向好。回踩MA10/MA20不破为常见加仓点。"),
        ("MACD金叉", "DIF上穿DEA。零轴上方金叉可靠性高于零轴下方；配合放量更佳。"),
        ("突破20日新高", "站上近20日最高价，动量策略经典信号，需放量确认，缩量突破易假。"),
        ("超跌反弹", "深跌后RSI<30，出现止跌企稳形态博反弹。反弹不等于反转，逢压力减仓。"),
        ("底部企稳", "本工具四闸门模型：超跌→横盘收敛→缩量后温和放量→资金回流，全过闸为企稳信号。"),
        ("地天板", "从跌停拉至涨停，极端多空反转，多有重大消息或游资博弈，次日波动巨大。"),
        ("天地板", "从涨停砸至跌停，极端风险形态，多为利好证伪或出货，严禁抄底。"),
        ("打板", "追买涨停板的短线战法，赚钱效应依赖情绪周期，冰点期胜率极低，新手慎用。"),
        ("妖股", "短期连续暴涨、脱离基本面的高波动个股。特征：小市值+高换手+连板。参与风险极高。"),
    ]},
    {"group": "实用技巧", "items": [
        ("仓位管理", "单票不超总仓30%，总仓位随市场攻守姿态调整：进攻期6-8成，防守期3成以下。"),
        ("止损纪律", "买入前先定止损位（如-7%或跌破关键均线），触发即执行，避免小亏拖成深套。"),
        ("财报要点", "先看营收与净利同比、经营现金流是否匹配利润、应收账款异常增长是暴雷前兆。"),
        ("事件驱动", "利好兑现常是高点（buy the rumor, sell the news），公告落地后追高需谨慎。"),
        ("周期思维", "行业有季节性（消费Q4、基建春季）；市场有情绪周期（启动-发酵-高潮-退潮）。"),
        ("分散与聚焦", "行业适度分散防黑天鹅，但过度分散摊薄收益。3-5只组合较适合个人跟踪。"),
        ("不要满仓单吊", "任何评分/指标都是概率工具，不存在确定性。本工具全部指标仅供研究参考。"),
    ]},
]

SECTION_GROUPS = {
    "股票常识": ("基础术语", "资金术语"),
    "选股票小技巧": ("形态战法", "实用技巧"),
}
GROUP_SECTION = {g: s for s, gs in SECTION_GROUPS.items() for g in gs}


def ensure_table() -> None:
    execute(
        "CREATE TABLE IF NOT EXISTS knowledge_ai ("
        "term TEXT PRIMARY KEY, grp TEXT, desc TEXT NOT NULL, payload TEXT, updated_at TEXT NOT NULL)"
    )


def _overlays() -> dict[str, dict]:
    ensure_table()
    out = {}
    try:
        rows = query("SELECT term, grp, desc, payload, updated_at FROM knowledge_ai")
    except Exception:  # noqa: BLE001
        return out
    for r in rows:
        term = (r.get("term") or "").strip()
        if not term:
            continue
        out[term] = r
    return out


def builtin_item(term: str) -> tuple[str, str, str] | None:
    """返回 (group, builtin_desc, section)。"""
    for g in KNOWLEDGE:
        for t, d in g["items"]:
            if t == term:
                return g["group"], d, GROUP_SECTION.get(g["group"], "股票常识")
    return None


def get_knowledge(keyword: str = "") -> list[dict]:
    kw = keyword.strip()
    ov = _overlays()
    out = []
    for g in KNOWLEDGE:
        items = []
        for t, d in g["items"]:
            rec = ov.get(t)
            desc = (rec.get("desc") if rec else "") or d
            if kw and kw not in t and kw not in desc:
                continue
            items.append({
                "term": t, "desc": desc, "builtin_desc": d,
                "ai_updated": bool(rec and rec.get("desc")),
                "updated_at": (rec or {}).get("updated_at") or "",
                "section": GROUP_SECTION.get(g["group"], "股票常识"),
            })
        if items:
            out.append({
                "section": GROUP_SECTION.get(g["group"], "股票常识"),
                "group": g["group"],
                "items": items,
            })
    return out


def revert_knowledge(term: str) -> dict:
    term = (term or "").strip()
    found = builtin_item(term)
    if not found:
        return {"ok": False, "applied": False, "error": "无此词条"}
    ensure_table()
    execute("DELETE FROM knowledge_ai WHERE term=?", (term,))
    group, desc, section = found
    return {
        "ok": True, "applied": False, "reverted": True, "term": term,
        "group": group, "section": section, "desc": desc,
    }


def save_knowledge_ai(term: str, desc: str, group: str, payload: dict) -> None:
    ensure_table()
    now = datetime.now(_TZ).replace(tzinfo=None).isoformat(timespec="seconds")
    execute(
        "INSERT OR REPLACE INTO knowledge_ai(term, grp, desc, payload, updated_at) VALUES(?,?,?,?,?)",
        (term, group, desc, json.dumps(payload or {}, ensure_ascii=False), now),
    )


def _parse_desc(raw: str) -> str:
    parsed = None
    m = re.search(r"\{.*\}", raw or "", re.S)
    if m:
        try:
            parsed = json.loads(m.group())
        except Exception:  # noqa: BLE001
            parsed = None
    desc = str((parsed or {}).get("desc") or "").strip()
    if len(desc) < 24:
        plain = re.sub(r"\{.*\}", "", raw or "", flags=re.S).strip()
        if len(plain) >= 24:
            desc = plain[:400]
    return desc[:400] if len(desc) >= 24 else ""


def _parse_items(raw: str) -> dict[str, str]:
    parsed = None
    m = re.search(r"\{.*\}", raw or "", re.S)
    if m:
        try:
            parsed = json.loads(m.group())
        except Exception:  # noqa: BLE001
            parsed = None
    out = {}
    rows = (parsed or {}).get("items") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        t = str(row.get("term") or "").strip()
        d = str(row.get("desc") or "").strip()
        if t and len(d) >= 24:
            out[t] = d[:400]
    return out


def ai_update(term: str = "", section: str = "") -> dict:
    """右键 AI 更新知识。term 更新一条；section=股票常识|选股票小技巧 整节一次调用。失败不覆盖。"""
    from . import ai as ai_svc
    term = (term or "").strip()
    section = (section or "").strip()
    targets: list[tuple[str, str, str, str]] = []  # term, group, section, builtin_desc
    if term:
        found = builtin_item(term)
        if not found:
            return {"ok": False, "applied": False, "error": "无此词条"}
        group, desc, sec = found
        targets.append((term, group, sec, desc))
    elif section in SECTION_GROUPS:
        for g in KNOWLEDGE:
            if g["group"] not in SECTION_GROUPS[section]:
                continue
            for t, d in g["items"]:
                targets.append((t, g["group"], section, d))
    else:
        return {"ok": False, "applied": False, "error": "请指定词条或知识分类"}

    cfg = ai_svc.get_config(masked=False)
    base_url = ai_svc.normalize_api_base(cfg.get("api_base", "") or "")
    configured = bool(cfg.get("api_key") and base_url)
    ov = _overlays()

    def _keep_one(t: str, group: str, sec: str, builtin: str, error: str = "", hint: str = "") -> dict:
        rec = ov.get(t)
        desc = (rec.get("desc") if rec else "") or builtin
        return {
            "term": t, "group": group, "section": sec, "desc": desc,
            "ok": True, "applied": False, "kept": bool(rec),
            "error": error, "hint": hint, "configured": configured,
        }

    if not configured:
        first = targets[0]
        kept = _keep_one(*first, error="未配置AI大模型",
                         hint="到 AI 分析页填写地址、密钥、模型后再右键更新知识。未改写已保存解释。")
        kept["items"] = [_keep_one(*x) for x in targets]
        kept["text"] = "未配置AI大模型，已保留原解释。" + ai_svc.DISCLAIMER
        kept["applied_n"] = 0
        kept["failed_n"] = len(targets)
        kept["total"] = len(targets)
        return kept

    model_src = f"AI大模型（{cfg.get('model') or '默认'}）"

    def _apply(t: str, group: str, sec: str, builtin: str, desc: str, raw: str) -> dict:
        save_knowledge_ai(t, desc, group, {"source": model_src, "raw": (raw or "")[:800]})
        return {
            "term": t, "group": group, "section": sec, "desc": desc,
            "ok": True, "applied": True, "kept": False, "error": "", "hint": "",
            "configured": True, "source": model_src,
        }

    results = []
    if len(targets) == 1:
        t, group, sec, builtin = targets[0]
        rec = ov.get(t)
        prev_desc = (rec.get("desc") if rec else "") or builtin
        kind = "选股票小技巧" if sec == "选股票小技巧" else "股票常识"
        context = f"分类：{kind} / {group}\n词条：{t}\n现有解释：{prev_desc}"
        task = (
            f"请用A股投资者能看懂的话更新该{kind}词条解释。"
            "严格只输出一行JSON："
            '{"desc":"80到180字"}。'
            "只写常见用法与注意点，禁止编造法条编号或未给出的数据，禁止收益承诺。"
        )
        try:
            raw = ai_svc._call_llm(cfg, context, task)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            err = ai_svc.format_llm_error(exc, base_url)
            results.append(_keep_one(t, group, sec, builtin, error=err,
                                    hint=ai_svc._hint_for_error(err)))  # noqa: SLF001
            raw = ""
        else:
            desc = _parse_desc(raw)
            if desc:
                results.append(_apply(t, group, sec, builtin, desc, raw))
            else:
                results.append(_keep_one(
                    t, group, sec, builtin,
                    error="模型返回过短，未覆盖已有解释", hint="可再试一次。"))
    else:
        kind = section
        listing = "\n".join(
            f"- {t}（{group}）：{((ov.get(t) or {}).get('desc') or builtin)[:160]}"
            for t, group, _sec, builtin in targets
        )
        context = f"分类：{kind}\n词条与现有解释：\n{listing}"
        task = (
            f"请用A股投资者能看懂的话更新这些{kind}词条。"
            "严格只输出JSON："
            '{"items":[{"term":"与输入完全一致","desc":"80到180字"}]}。'
            "必须覆盖列出的全部词条名。只写常见用法与注意点，禁止编造法条编号或未给出的数据，禁止收益承诺。"
        )
        try:
            raw = ai_svc._call_llm(cfg, context, task)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            err = ai_svc.format_llm_error(exc, base_url)
            hint = ai_svc._hint_for_error(err)  # noqa: SLF001
            results = [_keep_one(t, group, sec, builtin, error=err, hint=hint)
                       for t, group, sec, builtin in targets]
            raw = ""
        else:
            parsed = _parse_items(raw)
            for t, group, sec, builtin in targets:
                desc = parsed.get(t) or ""
                if desc:
                    results.append(_apply(t, group, sec, builtin, desc, raw))
                else:
                    results.append(_keep_one(
                        t, group, sec, builtin,
                        error="该词条未返回有效解释，未覆盖", hint="可再试一次或改右键单条更新。"))

    applied_n = sum(1 for x in results if x.get("applied"))
    first = results[0] if results else {"ok": False, "error": "无词条"}
    return {
        **first,
        "ok": True,
        "applied": applied_n > 0,
        "applied_n": applied_n,
        "failed_n": len(results) - applied_n,
        "total": len(results),
        "items": results,
        "text": (results[0].get("desc") if results else "") + ai_svc.DISCLAIMER,
        "source": model_src if applied_n else "本地规则（未覆盖）",
    }
