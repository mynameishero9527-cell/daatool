/* A股量化工具 前端逻辑：全部数据异步拉取，选项卡平铺切换，自动刷新推送 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

async function api(path, opts = {}) {
  const resp = await fetch(path, opts);
  if (!resp.ok) throw new Error(`${path} -> ${resp.status}`);
  return resp.json();
}
const post = (path) => api(path, { method: "POST" });

const fmt = (v, digits = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "-" : Number(v).toFixed(digits);
const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
const sign = (v) => (v > 0 ? "+" : "");
const pct = (v) => (v === null || v === undefined) ? "-" : `${sign(v)}${fmt(v)}%`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------------- 主选项卡 ---------------- */
let activeTab = "dashboard";
const loaders = {};   // tab -> 加载函数
const timers = {};    // tab -> [interval ids]

$("#mainTabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tab");
  if (!btn) return;
  activeTab = btn.dataset.tab;
  $$("#mainTabs .tab").forEach((t) => t.classList.toggle("active", t === btn));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${activeTab}`));
  loaders[activeTab]?.();
});

function schedule(tab, fn, ms) {
  (timers[tab] ||= []).push(setInterval(() => { if (activeTab === tab) fn(); }, ms));
}

/* ---------------- 行情看板 ---------------- */
async function loadDashboard() {
  try {
    const d = await api("/api/dashboard");
    renderIndices(d.indices);
    renderStats(d.stats);
    renderMovers(d.movers);
    renderWatchlist(d.watchlist);
    $("#lastRefresh").textContent = "更新 " + new Date().toLocaleTimeString("zh-CN");
  } catch (err) { console.warn(err); }
  loadMarketSentiment();
}

async function loadMarketSentiment() {
  try {
    const s = await api("/api/sentiment/market");
    if (s.temp === null) {
      $("#marketSentiment").innerHTML = `<div class="empty">${esc(s.desc)}</div>`;
      return;
    }
    $("#marketSentiment").innerHTML = `
      <div class="thermo">
        <span class="temp ${s.temp >= 55 ? "up" : s.temp < 45 ? "down" : "flat"}">${s.temp}</span>
        <span class="badge ${s.temp >= 70 ? "level-4" : s.temp >= 45 ? "level-2" : "level-1"}">${esc(s.level)}</span>
        <div class="thermo-bar"><div class="pin" style="left:${s.temp}%"></div></div>
        <div class="scale"><span>恐慌</span><span>平静</span><span>亢奋</span></div>
        <div class="muted" style="margin-top:6px">${esc(s.desc)} · 今日涨停 ${s.limit_up} 家</div>
      </div>`;
  } catch (err) { console.warn(err); }
}

function renderIndices(list) {
  $("#indexStrip").innerHTML = list.map((q) => `
    <div class="index-card">
      <div class="name">${esc(q.name)}</div>
      <div class="price ${cls(q.pct)}">${fmt(q.price)}</div>
      <div class="chg ${cls(q.pct)}">${sign(q.change)}${fmt(q.change)}&nbsp;&nbsp;${pct(q.pct)}</div>
    </div>`).join("") || '<div class="empty">暂无指数数据</div>';
}

function renderStats(s) {
  $("#statsTime").textContent = s.updated_at ? `更新于 ${s.updated_at.slice(11, 19) || s.updated_at}` : "待首次全量同步";
  const total = (s.up + s.down) || 1;
  $("#marketStats").innerHTML = `
    <div class="stat"><div class="v up">${s.up}</div><div class="k">上涨</div></div>
    <div class="stat"><div class="v down">${s.down}</div><div class="k">下跌</div></div>
    <div class="stat"><div class="v flat">${s.flat}</div><div class="k">平盘</div></div>
    <div class="stat"><div class="v up">${s.limit_up}</div><div class="k">涨停</div></div>
    <div class="stat"><div class="v down">${s.limit_down}</div><div class="k">跌停</div></div>
    <div class="stat"><div class="v">${fmt(s.amount_yi, 0)}</div><div class="k">成交额(亿)</div></div>
    <div class="updown-bar"><div class="u" style="width:${(s.up / total) * 100}%"></div><div class="d" style="width:${(s.down / total) * 100}%"></div></div>`;
}

function renderMovers(m) {
  const half = (rows) => rows.map((r) => `
    <div class="mv-item" onclick="openStock('${r.code}','${esc(r.name)}')">
      <span>${esc(r.name)} <span class="muted">${r.code}</span></span>
      <span class="${cls(r.pct)} num">${pct(r.pct)}</span>
    </div>`).join("");
  $("#movers").innerHTML = `<div>${half(m.gainers)}</div><div>${half(m.losers)}</div>`;
}

function renderWatchlist(list) {
  if (!list.length) { $("#watchTable").innerHTML = '<div class="empty">暂无自选股</div>'; return; }
  $("#watchTable").innerHTML = `<table><thead><tr>
    <th>代码</th><th>名称</th><th>最新价</th><th>涨跌幅</th><th>涨跌额</th>
    <th>成交量(手)</th><th>成交额(万)</th><th>量比</th><th>换手%</th><th>振幅%</th><th>操作</th>
  </tr></thead><tbody>${list.map((r) => `
    <tr onclick="openStock('${r.code}','${esc(r.name)}')">
      <td>${r.pinned ? "📌 " : ""}${r.code}</td><td>${esc(r.name)}</td>
      <td class="num ${cls(r.pct)}">${fmt(r.price)}</td>
      <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
      <td class="num ${cls(r.pct)}">${sign(r.change)}${fmt(r.change)}</td>
      <td class="num">${fmt(r.volume, 0)}</td><td class="num">${fmt(r.amount, 0)}</td>
      <td class="num">${fmt(r.volume_ratio)}</td><td class="num">${fmt(r.turnover_rate)}</td>
      <td class="num">${fmt(r.amplitude)}</td>
      <td onclick="event.stopPropagation()">
        <button class="btn small ghost" onclick="pinWatch('${r.code}')">置顶</button>
        <button class="btn small danger" onclick="removeWatch('${r.code}')">删除</button>
      </td>
    </tr>`).join("")}</tbody></table>`;
}

window.addWatch = async () => {
  const code = $("#watchInput").value.trim();
  if (!code) return;
  const res = await post(`/api/watchlist/add?code=${encodeURIComponent(code)}`);
  if (!res.ok) { alert(res.error); return; }
  $("#watchInput").value = "";
  loadDashboard();
};
window.removeWatch = async (code) => { await post(`/api/watchlist/remove?code=${code}`); loadDashboard(); };
window.pinWatch = async (code) => { await post(`/api/watchlist/pin?code=${code}`); loadDashboard(); };

/* ---------------- 个股分析 ---------------- */
let currentStock = null;
let currentPeriod = "minute";
let klineChart = null;

$("#stockSearch").addEventListener("input", debounce(async (e) => {
  const q = e.target.value.trim();
  const box = $("#searchResults");
  if (!q) { box.classList.remove("show"); return; }
  const rows = await api(`/api/search?q=${encodeURIComponent(q)}`);
  box.innerHTML = rows.length ? rows.map((r) => `
    <div class="sr-item" onclick="openStock('${r.code}','${esc(r.name)}')">
      <span>${esc(r.name)} <span class="muted">${r.code} · ${esc(r.board || "")}</span></span>
      <span class="${cls(r.pct)} num">${fmt(r.price)} ${pct(r.pct)}</span>
    </div>`).join("") : '<div class="sr-item muted">未找到，可先在设置页执行全量同步</div>';
  box.classList.add("show");
}, 250));
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-wrap")) $("#searchResults").classList.remove("show");
});

$("#periodBtns").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  currentPeriod = btn.dataset.period;
  $$("#periodBtns .opt").forEach((b) => b.classList.toggle("active", b === btn));
  if (currentStock) loadKline();
});

window.openStock = (code, name) => {
  currentStock = { code, name };
  $("#searchResults").classList.remove("show");
  $$("#mainTabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === "stock"));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-stock"));
  activeTab = "stock";
  $("#klineTitle").textContent = `${name} (${code})`;
  loadKline();
  loadAnalysis();
};

async function loadKline() {
  if (!currentStock) return;
  klineChart ||= echarts.init($("#klineChart"), "dark", { renderer: "canvas" });
  klineChart.showLoading({ maskColor: "rgba(13,17,23,.6)", textColor: "#dbe4f0" });
  try {
    const d = await api(`/api/kline?code=${currentStock.code}&period=${currentPeriod}`);
    klineChart.hideLoading();
    if (currentPeriod === "minute") renderMinute(d); else renderCandle(d);
  } catch (err) { klineChart.hideLoading(); console.warn(err); }
}

function baseGrid() {
  return { backgroundColor: "transparent", animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "cross" }, backgroundColor: "#1a2230", borderColor: "#2a3548", textStyle: { color: "#dbe4f0", fontSize: 12 } } };
}

function renderMinute(d) {
  const prices = d.points.map((p) => p[1]);
  const times = d.points.map((p) => `${p[0].slice(0, 2)}:${p[0].slice(2)}`);
  const base = d.prev_close || prices[0];
  klineChart.setOption({
    ...baseGrid(),
    grid: [{ left: 55, right: 20, top: 20, bottom: 40 }],
    xAxis: { type: "category", data: times, axisLine: { lineStyle: { color: "#2a3548" } } },
    yAxis: { scale: true, splitLine: { lineStyle: { color: "#202a3b" } },
      axisLabel: { formatter: (v) => v.toFixed(2) } },
    dataZoom: [{ type: "inside" }],
    series: [
      { type: "line", data: prices, showSymbol: false, lineStyle: { color: "#4a9eff", width: 1.4 },
        areaStyle: { color: "rgba(74,158,255,.12)" },
        markLine: { symbol: "none", data: [{ yAxis: base }], lineStyle: { color: "#7d8aa0", type: "dashed" }, label: { show: false } } },
    ],
  }, true);
}

function renderCandle(d) {
  const maSeries = Object.entries(d.ma).map(([n, values], i) => ({
    name: `MA${n}`, type: "line", data: values, showSymbol: false, smooth: true,
    lineStyle: { width: 1, color: ["#e8c46b", "#4a9eff", "#c678dd", "#56b6c2"][i] },
  }));
  const volColors = d.kline.map((k) => (k[1] >= k[0] ? "#ff5252" : "#26c281"));
  klineChart.setOption({
    ...baseGrid(),
    legend: { data: maSeries.map((s) => s.name), textStyle: { color: "#7d8aa0" }, top: 0 },
    grid: [
      { left: 55, right: 20, top: 28, height: "52%" },
      { left: 55, right: 20, top: "66%", height: "12%" },
      { left: 55, right: 20, top: "82%", height: "12%" },
    ],
    xAxis: [
      { type: "category", data: d.dates, gridIndex: 0, axisLine: { lineStyle: { color: "#2a3548" } } },
      { type: "category", data: d.dates, gridIndex: 1, show: false },
      { type: "category", data: d.dates, gridIndex: 2, show: false },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: "#202a3b" } } },
      { gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
      { gridIndex: 2, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1, 2], start: 60, end: 100 },
      { type: "slider", xAxisIndex: [0, 1, 2], top: "96%", height: 14, borderColor: "#2a3548" },
    ],
    series: [
      { name: "K线", type: "candlestick", data: d.kline,
        itemStyle: { color: "#ff5252", color0: "#26c281", borderColor: "#ff5252", borderColor0: "#26c281" } },
      ...maSeries,
      { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: d.volumes,
        itemStyle: { color: (p) => volColors[p.dataIndex] } },
      { name: "MACD", type: "bar", xAxisIndex: 2, yAxisIndex: 2, data: d.macd.bar,
        itemStyle: { color: (p) => (p.value >= 0 ? "#ff5252" : "#26c281") } },
      { name: "DIF", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: d.macd.dif, showSymbol: false, lineStyle: { width: 1, color: "#e8c46b" } },
      { name: "DEA", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: d.macd.dea, showSymbol: false, lineStyle: { width: 1, color: "#4a9eff" } },
    ],
  }, true);
}

function gaugeHtml(label, value, levelText, extra = "") {
  if (value === null || value === undefined) return "";
  return `<div class="gauge">
    <div class="g-head"><span class="muted">${label}</span>
      <span><span class="g-num ${value >= 65 ? "up" : value < 40 ? "down" : "flat"}">${value}</span>
      <span class="badge ${value >= 65 ? "level-4" : value >= 45 ? "level-2" : "level-1"}">${esc(levelText)}</span></span></div>
    <div class="gauge-track"><div class="gauge-fill" style="width:${value}%"></div></div>
    ${extra ? `<div class="muted" style="margin-top:3px;font-size:12px">${extra}</div>` : ""}
  </div>`;
}

function metrics2Html(m, dark) {
  let html = "";
  if (m) {
    html += gaugeHtml("购买指数", m.buy_index, `${m.buy_level} · ${m.buy_action}`,
      `60日位置 ${m.pos60 !== null ? Math.round(m.pos60 * 100) + "%" : "-"} · 乖离 ${fmt(m.bias20, 1)}% · RSI ${fmt(m.rsi14, 0)}`);
    html += gaugeHtml("情绪温度", m.sentiment, m.sent_level, esc(m.sent_desc));
  }
  if (dark && dark.power !== null && dark.power !== undefined) {
    const of = dark.orderflow;
    let flowBar = "";
    if (of && of.outer !== null && of.inner !== null) {
      const total = of.outer + of.inner || 1;
      const op = Math.round(of.outer / total * 100);
      flowBar = `<div class="flow-bar" title="外盘(主动买) vs 内盘(主动卖)">
        <div class="fo" style="width:${op}%">外 ${fmt(of.outer, 0)}</div>
        <div class="fi" style="width:${100 - op}%">内 ${fmt(of.inner, 0)}</div></div>
        <div class="muted" style="font-size:12px">内外盘比 ${fmt(of.in_out_ratio)} · 委比 ${fmt(of.order_ratio)}%</div>`;
    }
    html += gaugeHtml("暗盘力量", dark.power, dark.level,
      `${esc(dark.desc)}${dark.divergence && dark.divergence !== "无" ? " · <b>" + esc(dark.divergence) + "</b>" : ""}`) + flowBar +
      `<div class="muted" style="font-size:11px;margin-top:2px">口径：${esc(dark.scope)}</div>`;
  }
  if (m && m.stabilize_score !== null && m.stabilize_score !== undefined) {
    const gates = (m.gates || []).map((g, i) =>
      `<span class="gate ${g ? "on" : ""}" title="闸门G${i + 1}">G${i + 1}</span>`).join("");
    html += `<div class="kv"><span class="k">企稳状态</span>
      <span><span class="badge level-4">企稳待涨 ${m.stabilize_score}</span> ${gates}</span></div>`;
  } else if (m && m.gates) {
    const gates = m.gates.map((g, i) =>
      `<span class="gate ${g ? "on" : ""}" title="闸门G${i + 1}">G${i + 1}</span>`).join("");
    html += `<div class="kv"><span class="k">企稳闸门</span><span>${gates} <span class="muted">未全部通过</span></span></div>`;
  }
  return html ? html + '<hr style="border-color:var(--border);margin:10px 0">' : "";
}

async function loadAnalysis() {
  const card = $("#scoreCard");
  card.innerHTML = '<div class="empty">评分计算中…</div>';
  try {
    const d = await api(`/api/analysis?code=${currentStock.code}`);
    const r = d.rating;
    if (r.score === null || r.score === undefined) {
      card.innerHTML = `<div class="empty">${esc(r.message || "暂无评分数据")}</div>` + metrics2Html(d.metrics, d.dark);
      return;
    }
    const s = r.snapshot;
    const br = r.broker_ratings;
    card.innerHTML = `
      <div class="score-head">
        <div class="score-num ${r.score >= 55 ? "up" : r.score < 45 ? "down" : "flat"}">${r.score}</div>
        <span class="badge ${r.grade_css}">${r.grade} · ${r.volume_desc}</span><br>
        <span class="badge ${r.advice_css}" style="margin-top:8px">${r.advice}</span>
        <div class="muted" style="margin-top:8px">${esc(r.advice_reason)}</div>
      </div>
      ${Object.entries(r.components).map(([k, v]) => `
        <div class="comp-bar"><span class="label">${k}</span>
          <div class="track"><div class="fill" style="width:${v}%"></div></div>
          <span class="num">${v}</span></div>`).join("")}
      <hr style="border-color:var(--border);margin:10px 0">
      ${metrics2Html(d.metrics, d.dark)}
      <div class="kv"><span class="k">主力净流入</span><span class="num ${cls(s.main_net_in)}">${fmt((s.main_net_in || 0) / 10000)} 亿</span></div>
      <div class="kv"><span class="k">5日主力净流入</span><span class="num ${cls(s.main_net_in_d5)}">${fmt((s.main_net_in_d5 || 0) / 10000)} 亿</span></div>
      <div class="kv"><span class="k">5日/20日/60日涨幅</span><span class="num">${pct(s.pct_d5)} / ${pct(s.pct_d20)} / ${pct(s.pct_d60)}</span></div>
      <div class="kv"><span class="k">PE(TTM) / PB</span><span class="num">${fmt(s.pe_ttm, 1)} / ${fmt(s.pb)}</span></div>
      <div class="kv"><span class="k">总市值 / 流通市值</span><span class="num">${fmt(s.total_mv, 0)} / ${fmt(s.float_mv, 0)} 亿</span></div>
      <hr style="border-color:var(--border);margin:10px 0">
      <div class="card-title" style="font-size:13px">机构评级 <span class="muted">${br.simulated ? "规则模拟·仅供参考" : ""}</span></div>
      <div class="kv"><span class="k">评级分布</span><span>${Object.entries(br.distribution).filter(([, n]) => n > 0).map(([k, n]) => `${k}${n}家`).join(" · ")}</span></div>
      <div class="kv"><span class="k">一致目标价</span><span class="num">${fmt(br.consensus_target)}</span></div>
      <div style="max-height:180px;overflow-y:auto;margin-top:6px">
        ${br.items.map((it) => `<div class="kv"><span class="k">${esc(it.broker)} <span class="muted">${esc(it.type)}</span></span>
          <span>${it.rating} <span class="num muted">${fmt(it.target_price)}</span> <span class="muted">${it.date.slice(5)}</span></span></div>`).join("")}
      </div>`;
  } catch (err) { card.innerHTML = '<div class="empty">评分加载失败</div>'; console.warn(err); }
}

/* ---------------- 宏观情报 ---------------- */
let macroSub = "news";
$("#macroTabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  macroSub = btn.dataset.sub;
  $$("#macroTabs .opt").forEach((b) => b.classList.toggle("active", b === btn));
  loadMacro();
});

let outlookHorizon = "week";
async function loadMacro() {
  const box = $("#macroContent");
  try {
    if (macroSub === "outlook") {
      const d = await api(`/api/macro/outlook?horizon=${outlookHorizon}`);
      box.innerHTML = `
        <div class="btn-group" style="margin-bottom:12px" id="horizonBtns">
          ${[["week", "未来一周"], ["month", "未来一月"], ["quarter", "未来三月"], ["half", "未来半年"]].map(([k, v]) =>
            `<button class="opt ${k === outlookHorizon ? "active" : ""}" data-h="${k}">${v}</button>`).join("")}
        </div>
        <div class="outlook-summary">📋 <b>${esc(d.label)}展望综述</b><br>${esc(d.summary)}</div>
        ${d.groups.map((g) => `
          <div class="outlook-group">${esc(g.name)}</div>
          ${g.events.map((ev) => `
            <div class="cal-item">
              <span class="cal-date">${ev.date.slice(5)}</span>
              <span class="badge level-${ev.impact_level}">${ev.impact_desc}</span>
              <span class="flag">${esc(ev.region)}</span>
              <span style="flex:1">${esc(ev.title)}${ev.custom ? ` <button class="btn small danger" onclick="delCustomEvent(${ev.id.replace("custom_", "")})">删</button>` : ""}</span>
              <span class="muted">${esc(ev.category)}</span>
            </div>`).join("")}`).join("")}
        <div class="inline-form" style="margin-top:14px">
          <input id="ceDate" placeholder="YYYY-MM-DD" style="width:120px">
          <input id="ceTitle" placeholder="自定义事件标题" style="width:240px">
          <select id="ceLevel" style="background:var(--bg2);color:var(--text);border:1px solid var(--border);border-radius:7px;padding:6px">
            <option value="3">中等</option><option value="4">重大</option><option value="5">极重大</option>
          </select>
          <button class="btn" onclick="addCustomEvent()">添加事件</button>
        </div>`;
      $("#horizonBtns").addEventListener("click", (e) => {
        const btn = e.target.closest(".opt");
        if (!btn) return;
        outlookHorizon = btn.dataset.h;
        loadMacro();
      });
      return;
    }
    if (macroSub === "calendar") {
      const rows = await api("/api/macro/calendar?months=3");
      box.innerHTML = rows.length ? rows.map((ev) => `
        <div class="cal-item">
          <span class="cal-date">${ev.date.slice(5)}</span>
          <span class="badge level-${ev.impact_level}">${ev.impact_desc}</span>
          <span class="flag">${esc(ev.region)}</span>
          <span style="flex:1">${esc(ev.title)}</span>
          <span class="muted">${esc(ev.category)}</span>
        </div>`).join("") : '<div class="empty">近期无日程</div>';
      return;
    }
    const path = macroSub === "policy" ? "/api/macro/policies" : macroSub === "major" ? "/api/macro/major" : "/api/macro/news";
    const rows = await api(path);
    const offline = rows.length && rows[0].offline;
    box.innerHTML = (offline ? '<div class="offline-banner">当前展示本地缓存数据（离线）</div>' : "") +
      (rows.length ? rows.map((n) => `
      <div class="news-item">
        <span class="time">${esc((n.time || "").slice(5, 16))}</span>
        <div class="body">${esc(n.text)}
          <div class="meta">
            <span class="badge level-${n.impact_level}">${n.impact_desc || ""}</span>
            <span class="badge dir-${n.impact_direction}">${n.impact_direction}</span>
            ${n.is_policy ? '<span class="badge sector-tag">政策</span>' : ""}
            ${(n.affected_sectors || []).map((s) => `<span class="badge sector-tag">${esc(s)}</span>`).join("")}
          </div>
        </div>
      </div>`).join("") : '<div class="empty">暂无数据</div>');
  } catch (err) { box.innerHTML = '<div class="empty">加载失败，稍后自动重试</div>'; console.warn(err); }
}

window.addCustomEvent = async () => {
  const res = await api("/api/macro/custom-event", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ date: $("#ceDate").value.trim(), title: $("#ceTitle").value.trim(),
                           impact_level: Number($("#ceLevel").value) }),
  });
  if (!res.ok) { alert(res.error); return; }
  loadMacro();
};
window.delCustomEvent = async (id) => { await post(`/api/macro/custom-event/delete?event_id=${id}`); loadMacro(); };

/* ---------------- 个股筛选器（FR2-01） ---------------- */
let screenerMeta = null;
const screenerState = { boards: [], industries: [], mv: [], pct_today: [], pct_d5: [], pct_d20: [],
  turnover: [], volume_ratio: [], main_flow: [], main_flow_d5: [], pe: [], pb: [], tech: [],
  price_min: "", price_max: "", exclude_st: true, order_by: "buy_index" };
const GROUP_LABELS = { mv: "流通市值", pct_today: "今日涨跌", pct_d5: "5日涨跌", pct_d20: "20日涨跌",
  turnover: "换手率", volume_ratio: "量能", main_flow: "主力资金", main_flow_d5: "5日资金",
  pe: "PE(TTM)", pb: "PB", tech: "技术形态" };

async function initScreener() {
  if (screenerMeta) { renderScreenerConditions(); return; }
  try {
    screenerMeta = await api("/api/screener/meta");
    $("#presetBtns").innerHTML = screenerMeta.presets.map((p, i) =>
      `<button class="opt" onclick="applyPreset(${i})">${esc(p.name)}</button>`).join("");
    renderScreenerConditions();
    runScreener();
    loadPlans();
  } catch (err) { console.warn(err); }
}

function renderScreenerConditions() {
  const m = screenerMeta;
  let html = '<div class="chips" id="condChips"></div>';
  html += `<div class="cond-group"><div class="g-label">所属板块</div><div class="btn-group multi" data-group="boards">
    ${m.boards.map((b) => `<button class="opt ${screenerState.boards.includes(b) ? "active" : ""}" data-val="${b}">${b}</button>`).join("")}</div></div>`;
  if (m.industries.length) {
    html += `<div class="cond-group"><div class="g-label">所属行业</div><div class="btn-group multi" data-group="industries">
      ${m.industries.map((b) => `<button class="opt ${screenerState.industries.includes(b) ? "active" : ""}" data-val="${b}">${b}</button>`).join("")}</div></div>`;
  }
  for (const [group, buckets] of Object.entries(m.buckets)) {
    html += `<div class="cond-group"><div class="g-label">${GROUP_LABELS[group] || group}</div><div class="btn-group multi" data-group="${group}">
      ${Object.entries(buckets).map(([k, label]) =>
        `<button class="opt ${(screenerState[group] || []).includes(k) ? "active" : ""}" data-val="${k}">${esc(label)}</button>`).join("")}</div></div>`;
  }
  html += `<div class="cond-inline" style="margin-top:8px">
    <span><span class="g-label muted">股价区间</span>
      <input id="priceMin" placeholder="最低" value="${screenerState.price_min}">
      ~ <input id="priceMax" placeholder="最高" value="${screenerState.price_max}"></span>
    <label style="color:var(--muted);font-size:13px">
      <input type="checkbox" id="excludeSt" ${screenerState.exclude_st ? "checked" : ""}> 剔除 ST/退市
    </label></div>`;
  $("#screenerConditions").innerHTML = html;
  renderChips();

  $("#screenerConditions").querySelectorAll(".btn-group.multi").forEach((grp) => {
    grp.addEventListener("click", (e) => {
      const btn = e.target.closest(".opt");
      if (!btn) return;
      const group = grp.dataset.group, val = btn.dataset.val;
      const arr = screenerState[group];
      const idx = arr.indexOf(val);
      if (idx >= 0) arr.splice(idx, 1); else arr.push(val);
      btn.classList.toggle("active");
      renderChips();
      runScreenerDebounced();
    });
  });
  $("#priceMin").addEventListener("input", (e) => { screenerState.price_min = e.target.value; runScreenerDebounced(); });
  $("#priceMax").addEventListener("input", (e) => { screenerState.price_max = e.target.value; runScreenerDebounced(); });
  $("#excludeSt").addEventListener("change", (e) => { screenerState.exclude_st = e.target.checked; runScreenerDebounced(); });
}

function renderChips() {
  const chips = [];
  for (const [group, arr] of Object.entries(screenerState)) {
    if (!Array.isArray(arr) || !arr.length) continue;
    for (const v of arr) {
      const label = group === "boards" || group === "industries" ? v
        : (screenerMeta.buckets[group] || {})[v] || v;
      chips.push(`<span class="chip" onclick="removeCond('${group}','${v}')">${GROUP_LABELS[group] || ""}${GROUP_LABELS[group] ? ":" : ""}${esc(label)} ✕</span>`);
    }
  }
  $("#condChips").innerHTML = chips.join("") || '<span class="muted" style="font-size:12px">未设置条件（默认展示全市场按购买指数排序）</span>';
}

window.removeCond = (group, val) => {
  const arr = screenerState[group];
  const idx = arr.indexOf(val);
  if (idx >= 0) arr.splice(idx, 1);
  renderScreenerConditions();
  runScreenerDebounced();
};

window.applyPreset = (i) => {
  const preset = screenerMeta.presets[i];
  clearScreener(false);
  Object.assign(screenerState, JSON.parse(JSON.stringify(preset.conditions)));
  for (const g of Object.keys(GROUP_LABELS)) screenerState[g] ||= [];
  screenerState.boards ||= []; screenerState.industries ||= [];
  renderScreenerConditions();
  runScreener();
};

window.clearScreener = (rerun = true) => {
  for (const k of Object.keys(screenerState)) {
    if (Array.isArray(screenerState[k])) screenerState[k] = [];
  }
  screenerState.price_min = ""; screenerState.price_max = ""; screenerState.exclude_st = true;
  renderScreenerConditions();
  if (rerun) runScreener();
};

const runScreenerDebounced = debounce(() => runScreener(), 350);

async function runScreener() {
  $("#screenerCount").textContent = "筛选中…";
  try {
    const d = await api("/api/screener/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(screenerState),
    });
    $("#screenerCount").textContent = `命中 ${d.total} 只（最多显示100）`;
    $("#screenerResult").innerHTML = d.items.length ? `<table><thead><tr>
      <th>#</th><th>名称</th><th>行业</th><th>最新价</th><th>涨跌幅</th><th>购买指数</th>
      <th>情绪</th><th>暗盘力量</th><th>主力净流入(万)</th><th>PE</th><th>量比</th><th>企稳</th>
    </tr></thead><tbody>${d.items.map((r, i) => `
      <tr onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${i + 1}</td><td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td>${esc(r.industry || "-")}</td>
        <td class="num ${cls(r.pct)}">${fmt(r.price)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num">${r.buy_index !== null ? `<b>${fmt(r.buy_index, 0)}</b> <span class="muted">${esc(r.buy_level || "")}</span>` : "-"}</td>
        <td>${r.sent_level ? esc(r.sent_level) : "-"}</td>
        <td class="num">${fmt(r.dark_power, 0)}${r.divergence && r.divergence !== "无" ? ` <span class="badge sector-tag" style="font-size:10px">${esc(r.divergence)}</span>` : ""}</td>
        <td class="num ${cls(r.main_net_in)}">${fmt(r.main_net_in, 0)}</td>
        <td class="num">${fmt(r.pe_ttm, 1)}</td>
        <td class="num">${fmt(r.volume_ratio)}</td>
        <td>${r.stabilize_score !== null ? `<span class="badge level-4" style="font-size:11px">${fmt(r.stabilize_score, 0)}</span>` : "-"}</td>
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:8px;font-size:12px">指标为量化参考，不构成投资建议。指标每日盘后重算，可在设置页手动重建。</div>`
      : '<div class="empty">无符合条件的个股，可放宽条件</div>';
  } catch (err) {
    $("#screenerCount").textContent = "筛选失败";
    console.warn(err);
  }
}

$("#screenerOrder").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  screenerState.order_by = btn.dataset.order;
  $$("#screenerOrder .opt").forEach((b) => b.classList.toggle("active", b === btn));
  runScreener();
});

window.savePlan = async () => {
  const name = prompt("方案名称：");
  if (!name) return;
  await api("/api/screener/plans", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, conditions: screenerState }),
  });
  loadPlans();
};

async function loadPlans() {
  const plans = await api("/api/screener/plans");
  $("#planList").style.display = plans.length ? "" : "none";
  $("#planItems").innerHTML = plans.map((p) => `
    <div class="src-row"><span>${esc(p.name)} <span class="muted">${p.created_at.slice(0, 10)}</span></span>
      <span><button class="btn small" onclick='applyPlan(${JSON.stringify(JSON.stringify(p.conditions))})'>套用</button>
      <button class="btn small danger" onclick="deletePlan(${p.plan_id})">删除</button></span></div>`).join("");
}
window.applyPlan = (condJson) => {
  clearScreener(false);
  Object.assign(screenerState, JSON.parse(condJson));
  renderScreenerConditions();
  runScreener();
};
window.deletePlan = async (id) => { await post(`/api/screener/plans/delete?plan_id=${id}`); loadPlans(); };

/* ---------------- 大宗商品 ---------------- */
let commodityCats = [];
async function initCommodityCats() {
  const d = await api("/api/commodities");
  commodityCats = d.catalog;
  $("#commodityCats").innerHTML = d.catalog.map((c) =>
    `<button class="opt active" data-cat="${c}">${c}</button>`).join("");
  renderCommodities(d.items);
}
$("#commodityCats").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  btn.classList.toggle("active");
  loadCommodities();
});

async function loadCommodities() {
  const cats = $$("#commodityCats .opt.active").map((b) => b.dataset.cat);
  const d = await api(`/api/commodities?categories=${encodeURIComponent(cats.join(","))}`);
  renderCommodities(d.items);
}

function renderCommodities(items) {
  const offline = items.length && items[0].offline;
  $("#commodityGrid").innerHTML = (offline ? '<div class="offline-banner" style="grid-column:1/-1">商品行情暂不可用，请稍后</div>' : "") +
    (items.map((c) => `
    <div class="q-card">
      <div class="head">
        <div><div class="name">${esc(c.name)}</div><div class="sub">${esc(c.category)} · ${esc(c.unit)}</div></div>
        <span class="star ${c.watched ? "on" : ""}" onclick="toggleCommodity('${c.symbol}')">${c.watched ? "★" : "☆"}</span>
      </div>
      <div class="price ${cls(c.pct)}">${fmt(c.price, 3)}</div>
      <div class="chg ${cls(c.pct)}">${pct(c.pct)}</div>
      <div class="row2"><span>高 ${fmt(c.high, 2)} 低 ${fmt(c.low, 2)}</span><span>关联: ${esc(c.related_sector)}</span></div>
    </div>`).join("") || '<div class="empty" style="grid-column:1/-1">请选择至少一个分类</div>');
}
window.toggleCommodity = async (sym) => { await post(`/api/commodities/watch?symbol=${sym}`); loadCommodities(); };

/* ---------------- 全球指数 ---------------- */
let globalSub = "indices";
$("#globalTabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  globalSub = btn.dataset.sub;
  $$("#globalTabs .opt").forEach((b) => b.classList.toggle("active", b === btn));
  loadGlobal();
});

async function loadGlobal() {
  const box = $("#globalContent");
  try {
    if (globalSub === "etf") {
      const rows = await api("/api/etfs");
      box.innerHTML = `<div class="card"><table><thead><tr>
        <th>代码</th><th>名称</th><th>分类</th><th>跟踪标的</th><th>最新价</th><th>涨跌幅</th><th>成交额(万)</th>
      </tr></thead><tbody>${rows.map((r) => `
        <tr onclick="openStock('${r.code}','${esc(r.name)}')">
          <td>${r.code}</td><td>${esc(r.name)}</td><td>${esc(r.category)}</td><td>${esc(r.track)}</td>
          <td class="num ${cls(r.pct)}">${fmt(r.price, 3)}</td>
          <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
          <td class="num">${fmt(r.amount, 0)}</td>
        </tr>`).join("")}</tbody></table></div>`;
      return;
    }
    const d = await api("/api/global-indices");
    box.innerHTML = (d.offline ? '<div class="offline-banner">海外指数暂不可用，仅展示国内数据</div>' : "") +
      d.regions.map((rg) => `
      <div class="region-title">${esc(rg.region)}</div>
      <div class="cards-grid">${rg.items.map((q) => `
        <div class="q-card">
          <div class="head"><div class="name">${esc(q.name)}</div><span class="flag">${esc(q.country)}</span></div>
          <div class="price ${cls(q.pct)}">${fmt(q.price)}</div>
          <div class="chg ${cls(q.pct)}">${sign(q.change)}${fmt(q.change)}&nbsp;&nbsp;${pct(q.pct)}</div>
          <div class="row2"><span>${esc(q.desc)}</span></div>
        </div>`).join("")}</div>`).join("");
  } catch (err) { box.innerHTML = '<div class="empty">加载失败</div>'; console.warn(err); }
}

/* ---------------- 个股推荐 ---------------- */
let recommendBoard = "composite";
async function loadRecommend() {
  const box = $("#recommendTable");
  try {
    const d = await api(`/api/recommend?board=${recommendBoard}`);
    if (!$("#recommendBoards").children.length) {
      $("#recommendBoards").innerHTML = Object.entries(d.boards).map(([k, v]) =>
        `<button class="opt ${k === recommendBoard ? "active" : ""}" data-board="${k}">${v}</button>`).join("");
    }
    const statsHtml = d.stats && d.stats.n ? `
      <div class="offline-banner" style="border-color:rgba(74,158,255,.4);color:var(--accent);background:rgba(74,158,255,.08)">
        历史入选 ${d.stats.n} 次 · 5日胜率 ${fmt(d.stats.win5, 0)}%（均值 ${fmt(d.stats.avg5, 1)}%） ·
        10日胜率 ${fmt(d.stats.win10, 0)}%（均值 ${fmt(d.stats.avg10, 1)}%） ·
        20日胜率 ${fmt(d.stats.win20, 0)}%（均值 ${fmt(d.stats.avg20, 1)}%）
      </div>` : "";
    const isStab = recommendBoard === "stabilize";
    box.innerHTML = d.items.length ? statsHtml + `<table><thead><tr>
      <th>#</th><th>名称</th><th>最新价</th><th>涨跌幅</th><th>${esc(d.items[0].metric_name)}</th>
      ${isStab ? "<th>闸门</th><th>星级</th>" : ""}<th>购买指数</th><th>情绪</th>
      <th>量能</th><th>评分</th><th>提示</th><th>入选理由</th>
    </tr></thead><tbody>${d.items.map((r, i) => `
      <tr onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${i + 1}</td><td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td class="num ${cls(r.pct)}">${fmt(r.price)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num">${fmt(r.metric_value)}</td>
        ${isStab ? `<td>${(r.gates || []).map((g, gi) => `<span class="gate ${g ? "on" : ""}">G${gi + 1}</span>`).join("")}</td>
        <td class="up">${r.stars || ""}</td>` : ""}
        <td class="num">${r.buy_index !== null && r.buy_index !== undefined ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
        <td>${r.sent_level ? esc(r.sent_level) : "-"}</td>
        <td>${esc(r.volume_desc)}</td>
        <td class="num">${fmt(r.score, 1)}</td>
        <td><span class="badge ${r.advice === "增持" ? "advice-buy" : r.advice === "减持" ? "advice-sell" : "advice-hold"}" style="font-size:12px;padding:2px 8px">${r.advice}</span></td>
        <td class="muted" style="font-size:12px;white-space:normal;min-width:180px">${esc(r.reason || "")}</td>
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:8px;font-size:12px">榜单为量化参考，不构成投资建议。</div>`
      : '<div class="empty">榜单暂无数据：企稳/指标类榜单需先在设置页执行「重建指标」</div>';
  } catch (err) { box.innerHTML = '<div class="empty">加载失败</div>'; console.warn(err); }
}
$("#recommendBoards").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  recommendBoard = btn.dataset.board;
  $$("#recommendBoards .opt").forEach((b) => b.classList.toggle("active", b === btn));
  loadRecommend();
});

/* ---------------- 设置 ---------------- */
async function loadSettings() {
  try {
    const d = await api("/api/system/status");
    $("#sourceHealth").innerHTML = d.sources.map((s) => `
      <div class="src-row">
        <span>${s.circuit_open ? "🔴" : s.enabled ? "🟢" : "⚪"} ${esc(s.name)}
          <span class="muted">成功率 ${s.success_rate === null ? "-" : (s.success_rate * 100).toFixed(1) + "%"} · 延迟 ${s.avg_latency_ms ?? "-"}ms</span>
        </span>
        <button class="btn small ghost" onclick="toggleSource('${esc(s.name)}')">${s.enabled ? "禁用" : "启用"}</button>
      </div>`).join("") || '<div class="empty">尚未产生请求</div>';
    $("#cacheStats").innerHTML = `
      <div class="kv"><span class="k">缓存条目</span><span class="num">${d.cache.entries}</span></div>
      <div class="kv"><span class="k">命中 / 未命中</span><span class="num">${d.cache.hits} / ${d.cache.misses}</span></div>
      <div class="kv"><span class="k">命中率</span><span class="num">${(d.cache.hit_rate * 100).toFixed(1)}%</span></div>`;
    const sync = d.sync;
    $("#syncState").innerHTML = `
      <div class="kv"><span class="k">本地股票数</span><span class="num">${sync.count}</span></div>
      <div class="kv"><span class="k">上次同步</span><span>${esc(sync.last_sync)}</span></div>
      <div class="kv"><span class="k">状态</span><span>${esc(sync.message)}</span></div>
      ${sync.running ? '<div class="progress"><div class="p" style="width:60%"></div></div>' : ""}`;
    loadMetricsState();
    $("#jobTable").innerHTML = `<table><thead><tr>
      <th>任务</th><th>上次执行</th><th>状态</th><th>下次执行</th>
    </tr></thead><tbody>${d.jobs.map((j) => `
      <tr><td>${esc(j.name)}</td><td>${esc(j.last_run)}</td>
        <td>${j.ok === true ? '<span class="down">正常</span>' : j.ok === false ? `<span class="up">失败</span> <span class="muted">${esc(j.error)}</span>` : '<span class="muted">未执行</span>'}</td>
        <td>${esc(j.next_run)}</td></tr>`).join("")}</tbody></table>`;
    const anyCircuit = d.sources.some((s) => s.circuit_open);
    const anyFail = d.jobs.some((j) => j.ok === false);
    $("#healthDot").className = "dot " + (anyCircuit ? "bad" : anyFail ? "warn" : "ok");
  } catch (err) { console.warn(err); }
}
async function loadMetricsState() {
  try {
    const m = await api("/api/system/metrics-state");
    $("#metricsState").innerHTML = `
      <div class="kv"><span class="k">已计算指标股票数</span><span class="num">${m.metrics_count}</span></div>
      <div class="kv"><span class="k">已入库K线股票数</span><span class="num">${m.kline_codes}</span></div>
      <div class="kv"><span class="k">K线上次同步</span><span>${esc(m.kline_last_sync)}</span></div>
      <div class="kv"><span class="k">指标上次计算</span><span>${esc(m.metrics_last_compute)}</span></div>
      <div class="kv"><span class="k">行业映射</span><span>${esc(m.industry_last_sync)}</span></div>
      <div class="kv"><span class="k">状态</span><span>${m.running ? `${esc(m.stage)} ${m.progress}/${m.total}` : esc(m.stage)}</span></div>
      ${m.running ? `<div class="progress"><div class="p" style="width:${m.total ? m.progress / m.total * 100 : 30}%"></div></div>` : ""}`;
  } catch (err) { console.warn(err); }
}

window.rebuildMetrics = async () => {
  await post("/api/system/rebuild-metrics");
  const poll = setInterval(async () => {
    const m = await api("/api/system/metrics-state");
    loadMetricsState();
    if (!m.running) clearInterval(poll);
  }, 3000);
};

window.toggleSource = async (name) => { await post(`/api/system/source-toggle?name=${encodeURIComponent(name)}`); loadSettings(); };
window.clearCache = async () => { await post("/api/system/clear-cache"); loadSettings(); };
window.fullSync = async () => {
  await post("/api/system/sync");
  loadSettings();
  const poll = setInterval(async () => {
    const s = await api("/api/system/sync-state");
    loadSettings();
    if (!s.running) clearInterval(poll);
  }, 2000);
};

/* ---------------- 工具与启动 ---------------- */
function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

loaders.dashboard = loadDashboard;
loaders.stock = () => { if (currentStock) { loadKline(); } };
loaders.screener = initScreener;
loaders.macro = loadMacro;
loaders.commodity = loadCommodities;
loaders.global = loadGlobal;
loaders.recommend = loadRecommend;
loaders.settings = loadSettings;

/* 自动刷新（仅刷新当前页，避免无谓请求） */
schedule("dashboard", loadDashboard, 10000);
schedule("stock", () => { if (currentStock && currentPeriod === "minute") loadKline(); }, 30000);
schedule("macro", () => { if (macroSub !== "calendar") loadMacro(); }, 60000);
schedule("commodity", loadCommodities, 60000);
schedule("global", loadGlobal, 60000);
schedule("settings", loadSettings, 10000);
setInterval(loadSettingsHealthOnly, 30000);
async function loadSettingsHealthOnly() {
  try {
    const d = await api("/api/system/status");
    const anyCircuit = d.sources.some((s) => s.circuit_open);
    $("#healthDot").className = "dot " + (anyCircuit ? "bad" : "ok");
  } catch { $("#healthDot").className = "dot bad"; }
}

window.addEventListener("resize", () => klineChart?.resize());

/* 首屏 */
loadDashboard();
initCommodityCats();
loadSettingsHealthOnly();
