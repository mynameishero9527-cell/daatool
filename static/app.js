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
const wxBadges = (tags) => (tags && tags.length)
  ? tags.map((t) => `<span class="wx-badge wx-${esc(t)}">${esc(t)}</span>`).join("") : "";
let watchCodes = new Set();

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
  loadMarketCycle();
  loadMiniMinute();
  loadForecast();
  loadAlerts();
  loadDashAlmanac();
  loadTopAlmanac();
}

$("#dashTabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  $$("#dashTabs .opt").forEach((b) => b.classList.toggle("active", b === btn));
  $("#dashMarket").style.display = btn.dataset.view === "market" ? "" : "none";
  $("#dashWatch").style.display = btn.dataset.view === "watch" ? "" : "none";
});

async function loadTopAlmanac() {
  try {
    const a = await api("/api/macro/almanac");
    const h = a.huangdao || {};
    const el = $("#topAlmanac");
    el.textContent = `📅 ${a.day_ganzhi} · ${a.zodiac}年 · ${h.text || ""}`;
    el.title = `${a.date} ${a.weekday}\n${a.year_ganzhi}【${a.zodiac}年】 ${a.month_ganzhi} ${a.day_ganzhi}\n`
      + `${h.text || ""}\n${a.wuxing}\n财神：${a.caishen}\n（民俗参考，不构成投资建议）`;
    el.classList.toggle("heidao", !h.is_huangdao);
  } catch (err) { console.warn(err); }
}

let dashAlmanacLoaded = false;
async function loadDashAlmanac() {
  if (dashAlmanacLoaded) return;
  try {
    const a = await api("/api/macro/almanac");
    const t = a.tomorrow || {};
    $("#dashAlmanac").innerHTML = `
      <div style="font-size:15px">${esc(a.date)}（${esc(a.weekday)}）</div>
      <div class="desc-hl">${esc(a.year_ganzhi)}【${esc(a.zodiac)}年】${esc(a.month_ganzhi)} ${esc(a.day_ganzhi)}
        ${a.solar_term ? `<span class="badge level-3">${esc(a.solar_term)}</span>` : ""}
        ${a.huangdao ? `<span class="badge ${a.huangdao.is_huangdao ? "level-3" : "level-2"}">${esc(a.huangdao.text)}</span>` : ""}</div>
      <div class="muted" style="font-size:12px;margin:4px 0">${esc(a.wuxing)} ｜ 财神：${esc(a.caishen)}</div>
      <div class="kv"><span class="k">旺相休囚</span><span style="color:#e8c46b">${esc(a.wangxiang_text)}</span></div>
      <div class="jiugong">${a.jiugong.flat().map((c) =>
        `<div class="${c === "中宫" ? "center" : ""}">${esc(c)}</div>`).join("")}</div>
      <div class="kv"><span class="k">明日预览</span>
        <span>${esc(t.date || "")}（${esc(t.weekday || "")}）${esc(t.day_ganzhi || "")}
        ${t.solar_term ? `<span class="badge level-3">${esc(t.solar_term)}</span>` : ""}
        ${t.festival ? `<span class="badge level-4">${esc(t.festival)}</span>` : ""}
        <span class="muted">财神${esc(t.caishen || "")}</span></span></div>
      <div class="muted" style="font-size:11px;margin-top:4px">${esc(a.note)}</div>`;
    dashAlmanacLoaded = true;
  } catch (err) { console.warn(err); }
}

let miniChart = null;
let miniIndexCode = "sh000001";
$("#miniIndexBtns").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  miniIndexCode = btn.dataset.code;
  $("#miniMinuteName").textContent = btn.dataset.name;
  $$("#miniIndexBtns .opt").forEach((b) => b.classList.toggle("active", b === btn));
  loadMiniMinute();
});

async function loadMiniMinute() {
  try {
    const d = await api(`/api/market/minute?code=${miniIndexCode}`);
    if (!d.points || !d.points.length) return;
    miniChart ||= echarts.init($("#miniMinute"), "dark");
    const prices = d.points.map((p) => p[1]);
    const base = d.prev_close || prices[0];
    const last = prices[prices.length - 1];
    $("#miniMinuteTime").textContent = `${fmt(last)}（${pct((last - base) / base * 100)}）${d.offline ? " · 离线数据" : ""}`;
    miniChart.setOption({
      backgroundColor: "transparent", animation: false,
      grid: { left: 50, right: 10, top: 8, bottom: 20 },
      tooltip: { trigger: "axis", backgroundColor: "#1a2230", borderColor: "#2a3548", textStyle: { color: "#dbe4f0", fontSize: 12 } },
      xAxis: { type: "category", data: d.points.map((p) => `${p[0].slice(0, 2)}:${p[0].slice(2)}`),
        axisLine: { lineStyle: { color: "#2a3548" } }, axisLabel: { fontSize: 10 } },
      yAxis: { scale: true, splitLine: { lineStyle: { color: "#202a3b" } }, axisLabel: { fontSize: 10 } },
      series: [{ type: "line", data: prices, showSymbol: false,
        lineStyle: { color: last >= base ? "#ff5252" : "#26c281", width: 1.5 },
        areaStyle: { color: last >= base ? "rgba(255,82,82,.12)" : "rgba(38,194,129,.12)" },
        markLine: { symbol: "none", data: [{ yAxis: base }], lineStyle: { color: "#7d8aa0", type: "dashed" }, label: { show: false } } }],
    }, true);
  } catch (err) { console.warn(err); }
}

async function loadForecast() {
  try {
    const f = await api("/api/market/forecast");
    $("#forecastBox").innerHTML = `
      <div style="text-align:center">
        <span class="prob-num ${f.prob_up >= 58 ? "up" : f.prob_up <= 42 ? "down" : "flat"}">${f.prob_up}%</span>
        <span class="badge ${f.prob_up >= 58 ? "level-4" : f.prob_up <= 42 ? "level-1" : "level-2"}">明日${esc(f.view)}</span>
      </div>
      <div class="prob-bar"><div class="p" style="width:${f.prob_up}%"></div></div>
      ${f.factors.map((x) => `<div class="kv"><span class="k">${esc(x.name)}</span>
        <span>${esc(x.value)} <span class="num ${cls(x.impact)}">${sign(x.impact)}${fmt(x.impact, 1)}</span></span></div>`).join("")}
      <div class="muted" style="font-size:11px;margin-top:6px">${esc(f.disclaimer)}</div>`;
  } catch (err) { console.warn(err); }
}

async function loadAlerts() {
  try {
    const d = await api("/api/alerts");
    const item = (a) => `
      <div class="alert-item">
        <span class="time">${esc((a.created_at || "").slice(5, 16).replace("T", " "))}</span>
        <div class="body">
          <span class="badge at-${a.alert_type}">${esc(a.type_name)}</span> ${esc(a.title)}
          ${a.detail ? `<div class="detail">${esc(a.detail)}</div>` : ""}
        </div>
      </div>`;
    const buys = d.items.filter((a) => a.alert_type === "buy_point");
    const sells = d.items.filter((a) => a.alert_type === "sell_point");
    const others = d.items.filter((a) => a.alert_type !== "buy_point" && a.alert_type !== "sell_point");
    if (!d.items.length) {
      $("#alertFeed").innerHTML = '<div class="empty">暂无提醒，交易时段每10分钟自动扫描</div>';
      return;
    }
    $("#alertFeed").innerHTML = `
      <div class="alert-cols">
        <div><div class="alert-col-title">最佳买点</div>${buys.map(item).join("") || '<div class="empty">暂无买点</div>'}</div>
        <div><div class="alert-col-title">最佳卖点</div>${sells.map(item).join("") || '<div class="empty">暂无卖点</div>'}</div>
      </div>
      ${others.length ? `<div class="alert-other">${others.map(item).join("")}</div>` : ""}`;
  } catch (err) { console.warn(err); }
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
    <div class="index-card" style="cursor:pointer" title="点击查看K线"
         onclick="openStock('${q.code}','${esc(q.name)}')">
      <div class="name">${esc(q.name)} <span class="muted" style="font-size:10px">K线 ›</span></div>
      <div class="price ${cls(q.pct)}">${fmt(q.price)}</div>
      <div class="chg ${cls(q.pct)}">${sign(q.change)}${fmt(q.change)}&nbsp;&nbsp;${pct(q.pct)}</div>
    </div>`).join("") || '<div class="empty">暂无指数数据</div>';
}

const isIndexCode = (code) => /^(sh000|sz399|bj899|sh880)/.test(code);

async function loadMarketCycle() {
  try {
    const c = await api("/api/market/cycle");
    if (!c.stage || c.stage === "未知") { $("#marketCycle").innerHTML = ""; return; }
    $("#marketCycle").innerHTML = `
      <hr style="border-color:var(--border);margin:8px 0">
      <div class="kv"><span class="k">攻守姿态</span>
        <span><span class="badge ${c.stance_css}" style="font-size:13px;padding:3px 12px">${esc(c.stance)}</span>
        <span class="muted">恐慌指数 ${fmt(c.panic_index, 0)}</span></span></div>
      <div class="muted" style="font-size:12px;margin-bottom:4px">${esc(c.stance_desc)}</div>
      <div class="kv"><span class="k">大周期阶段</span>
        <span><span class="badge ${c.stage_css}">${esc(c.stage)}</span></span></div>
      <div class="kv"><span class="k">大盘量能</span><span>${esc(c.vol_desc)}（5日/20日均量比 ${fmt(c.vol_ratio)}）</span></div>
      <div class="muted" style="font-size:12px">${esc(c.stage_desc)} · 市场宽度 ${fmt(c.breadth, 0)}% · 年化波动 ${fmt(c.volatility20, 0)}%</div>`;
  } catch (err) { console.warn(err); }
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
  watchCodes = new Set(list.map((r) => r.code));
  if (!list.length) { $("#watchTable").innerHTML = '<div class="empty">暂无自选股</div>'; return; }
  $("#watchTable").innerHTML = `<table><thead><tr>
    <th>代码</th><th>名称</th><th>五行</th><th>最新价</th><th>涨跌幅</th><th>涨跌额</th>
    <th>成交量(手)</th><th>成交额(万)</th><th>量比</th><th>换手%</th><th>振幅%</th><th>操作</th>
  </tr></thead><tbody>${list.map((r) => `
    <tr data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
      <td>${r.pinned ? "📌 " : ""}${r.code}</td><td>${esc(r.name)}</td>
      <td>${wxBadges(r.wuxing)}</td>
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
  if (isIndexCode(code)) {
    $("#stockProfile").innerHTML = "";
    loadIndexPanel();
  } else {
    loadProfile();
    loadAnalysis();
  }
};

async function loadProfile() {
  const box = $("#stockProfile");
  box.innerHTML = "";
  try {
    const [p, wx] = await Promise.all([
      api(`/api/profile?code=${currentStock.code}`),
      api(`/api/wuxing?code=${currentStock.code}`).catch(() => ({ tags: [] })),
    ]);
    if (!p.industry && !p.concepts.length && !(wx.tags || []).length) { box.innerHTML = ""; return; }
    box.innerHTML = `
      <div style="margin-bottom:10px">
        ${wxBadges(wx.tags)}
        ${p.industry ? `<span class="badge level-3" style="cursor:pointer" onclick="drillFromProfile('industry','${esc(p.industry)}')">${esc(p.industry)}</span>` : ""}
        ${p.concepts.map((c) => `<span class="badge sector-tag" style="cursor:pointer" onclick="drillFromProfile('concept','${esc(c)}')">${esc(c)}</span>`).join("")}
        <div class="muted" style="margin-top:6px;font-size:12px">${esc(p.desc)}${wx.note ? " · " + esc(wx.note) : ""}</div>
      </div>`;
  } catch (err) { console.warn(err); }
}

async function loadIndexPanel() {
  const card = $("#scoreCard");
  card.innerHTML = '<div class="empty">周期研判计算中…</div>';
  try {
    const c = await api("/api/market/cycle");
    card.innerHTML = `
      <div class="score-head">
        <span class="badge ${c.stage_css}" style="font-size:16px;padding:6px 18px">${esc(c.stage)}</span>
        <span class="badge ${c.stance_css}" style="font-size:16px;padding:6px 18px">${esc(c.stance)}姿态</span>
        <div class="muted" style="margin-top:8px">${esc(c.stage_desc)} · ${esc(c.stance_desc)}</div>
      </div>
      <div class="kv"><span class="k">恐慌指数</span><span class="num"><b>${fmt(c.panic_index, 0)}</b> / 100（年化波动 ${fmt(c.volatility20, 0)}%）</span></div>
      <div class="kv"><span class="k">上证指数</span><span class="num">${fmt(c.index_price)}</span></div>
      <div class="kv"><span class="k">大盘量能</span><span>${esc(c.vol_desc)}（${fmt(c.vol_ratio)}）</span></div>
      <div class="kv"><span class="k">市场宽度</span><span class="num">${fmt(c.breadth, 0)}% 上涨</span></div>
      <hr style="border-color:var(--border);margin:10px 0">
      <div class="card-title" style="font-size:13px">研判信号</div>
      ${c.signals.map((s) => `<div class="kv">
        <span class="k">${s.ok ? "🟢" : "🔴"} ${esc(s.name)}</span></div>
        <div class="muted" style="font-size:12px;margin:-4px 0 6px">${esc(s.text)}</div>`).join("")}
      <div class="muted" style="font-size:11px;margin-top:8px">周期研判为量化参考，不构成投资建议。</div>`;
  } catch (err) { card.innerHTML = '<div class="empty">周期研判加载失败</div>'; }
}

window.drillFromProfile = (dim, name) => {
  $$("#mainTabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === "sector"));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-sector"));
  activeTab = "sector";
  sectorDim = dim;
  $$("#sectorDim .opt").forEach((b) => b.classList.toggle("active", b.dataset.dim === dim));
  loadSector().then(() => drillSector(name));
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

function attributionHtml(att) {
  if (!att || (!att.reasons.length && att.risk_index === null)) return "";
  const risk = att.risk_index ?? 0;
  return `
    <div class="card-title" style="font-size:14px;margin-top:2px">今日${(att.industry_pct ?? 0) >= 0 ? "上涨" : "下跌"}归因
      ${att.industry ? `<span class="muted">所属：${esc(att.industry)}（板块 ${pct(att.industry_pct)}）</span>` : ""}</div>
    ${att.reasons.map((r) => `<div class="reason-item"><span class="rt">[${esc(r.type)}]</span><span class="desc-hl" style="font-size:14px">${esc(r.text)}</span></div>`).join("")}
    ${att.sector_events.length ? `
      <div class="card-title" style="font-size:14px;margin-top:10px">板块重大事件</div>
      ${att.sector_events.map((e) => `<div class="reason-item">
        <span class="badge dir-${e.direction}">${esc(e.direction)}</span>
        <span class="badge level-${e.level}">${esc(e.desc)}</span>
        <span style="font-size:13px">${esc(e.text)}…</span></div>`).join("")}` : ""}
    <div class="card-title" style="font-size:14px;margin-top:10px">利空风险指数
      <span class="num ${risk >= 60 ? "up" : risk >= 40 ? "flat" : "down"}" style="font-size:20px;font-weight:800">${fmt(risk, 0)}</span></div>
    <div class="risk-bar"><div class="p" style="width:${risk}%"></div></div>
    ${risk >= 60 ? `<div class="risk-warn">${esc(att.risk_warning)}</div>`
      : `<div class="desc-hl" style="font-size:13px">${esc(att.risk_warning)}</div>`}
    ${att.risk_factors && att.risk_factors.length ? `<div class="muted" style="font-size:12px;margin-top:3px">风险因素：${att.risk_factors.map(esc).join("；")}</div>` : ""}
    <hr style="border-color:var(--border);margin:10px 0">`;
}

function pullSmashHtml(ps) {
  if (!ps || !ps.available) return "";
  const patternBadge = ps.pattern_score
    ? `<span class="badge ${ps.pattern.includes("地天") ? "level-4" : "level-0"}" style="font-size:12px">${esc(ps.pattern)} · 形态分${ps.pattern_score}</span>`
    : `<span class="muted" style="font-size:12px">${esc(ps.pattern)}</span>`;
  return `
    <div class="kv" style="margin-top:4px"><span class="k">盘口行为</span><span>${patternBadge}</span></div>
    <div class="comp-bar"><span class="label">拉升</span>
      <div class="track"><div class="fill" style="width:${ps.pull_score}%;background:var(--up)"></div></div>
      <span class="num">${fmt(ps.pull_score, 0)}</span></div>
    <div class="comp-bar"><span class="label">砸盘</span>
      <div class="track"><div class="fill" style="width:${ps.smash_score}%;background:var(--down)"></div></div>
      <span class="num">${fmt(ps.smash_score, 0)}</span></div>
    <div class="muted" style="font-size:12px">${esc(ps.desc)}</div>
    <hr style="border-color:var(--border);margin:10px 0">`;
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
    const q = d.quote || {};
    const priceHead = q.price !== undefined && q.price !== null ? `
      <div style="text-align:center;padding-bottom:6px;border-bottom:1px solid var(--border);margin-bottom:8px">
        <span style="font-size:30px;font-weight:800" class="${cls(q.pct)}">${fmt(q.price)}</span>
        <span class="${cls(q.pct)}" style="font-size:15px;font-weight:600;margin-left:8px">${sign(q.change)}${fmt(q.change)} (${pct(q.pct)})</span>
        <div class="muted" style="font-size:11px">现价 · ${esc(String(q.time || "").replace(/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/, "$2-$3 $4:$5:$6"))} · ${esc(q.source || "")}</div>
      </div>` : "";
    card.innerHTML = `${priceHead}
      <div class="score-head">
        <div class="score-num ${r.score >= 55 ? "up" : r.score < 45 ? "down" : "flat"}">${r.score}</div>
        <span class="badge ${r.grade_css}">${r.grade} · ${r.volume_desc}</span><br>
        <span class="badge ${r.advice_css}" style="margin-top:8px">${r.advice}</span>
        <div class="desc-hl" style="margin-top:8px">${esc(r.advice_reason)}</div>
      </div>
      ${attributionHtml(d.attribution)}
      ${Object.entries(r.components).map(([k, v]) => `
        <div class="comp-bar"><span class="label">${k}</span>
          <div class="track"><div class="fill" style="width:${v}%"></div></div>
          <span class="num">${v}</span></div>`).join("")}
      <hr style="border-color:var(--border);margin:10px 0">
      ${pullSmashHtml(d.pull_smash)}
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
      </div>
      <div id="financeSec"><div class="muted" style="margin-top:10px">财务数据加载中…</div></div>`;
    loadFinance();
  } catch (err) { card.innerHTML = '<div class="empty">评分加载失败</div>'; console.warn(err); }
}

async function loadFinance() {
  const box = $("#financeSec");
  if (!box) return;
  try {
    const f = await api(`/api/finance?code=${currentStock.code}`);
    if (!f.reports.length) { box.innerHTML = ""; return; }
    box.innerHTML = `
      <hr style="border-color:var(--border);margin:10px 0">
      <div class="card-title" style="font-size:13px">财务分析
        <span>${f.grade ? `<span class="badge ${f.grade === "A" ? "level-4" : f.grade === "B" ? "level-3" : f.grade === "C" ? "level-2" : "level-1"}">评级 ${f.grade}</span>` : ""}</span>
      </div>
      <div class="muted" style="font-size:12px;margin-bottom:6px">${esc(f.summary)}</div>
      <table style="font-size:12px"><thead><tr>
        <th>报告期</th><th>营收(亿)</th><th>同比</th><th>归母净利(亿)</th><th>同比</th><th>净利率</th>
      </tr></thead><tbody>${f.reports.map((r) => `
        <tr style="cursor:default">
          <td>${esc(r.name)}</td>
          <td class="num">${fmt(r.revenue_yi, 1)}</td>
          <td class="num ${cls(r.revenue_yoy)}">${r.revenue_yoy !== null ? sign(r.revenue_yoy) + fmt(r.revenue_yoy, 1) + "%" : "-"}</td>
          <td class="num">${fmt(r.profit_yi, 2)}</td>
          <td class="num ${cls(r.profit_yoy)}">${r.profit_yoy !== null ? sign(r.profit_yoy) + fmt(r.profit_yoy, 1) + "%" : "-"}</td>
          <td class="num">${r.margin !== null ? fmt(r.margin, 1) + "%" : "-"}</td>
        </tr>`).join("")}</tbody></table>
      <div class="muted" style="font-size:11px;margin-top:4px">数据来源：${esc(f.source || "")}</div>`;
  } catch (err) { box.innerHTML = ""; console.warn(err); }
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

const stars = (lv) => `<span class="${lv >= 4 ? "up" : "flat"}" style="letter-spacing:1px">${"★".repeat(lv)}${"☆".repeat(5 - lv)}</span>`;
let outlookHorizon = "week";
let calRange = 0;      // 0=全部, 1=今天, 3, 7, 31 天
let calMinLevel = 0;   // 0=全部, 4, 5
const newsFilter = { level: 0, direction: "", region: "", days: 0 };  // FR5-01-1 + FR8-02-1
let sectorEvGroup = "day";

function newsFilterBar() {
  return `
    <div class="btn-group" style="margin-bottom:6px" id="nfDays">
      ${[[1, "当日"], [3, "3日内"], [5, "5日内"], [10, "10日内"], [0, "全部"]].map(([v, t]) =>
        `<button class="opt ${v === newsFilter.days ? "active" : ""}" data-v="${v}">${t}</button>`).join("")}
    </div>
    <div class="btn-group" style="margin-bottom:6px" id="nfLevel">
      ${[[0, "全部等级"], [2, "较小+"], [3, "中等+"], [4, "重大+"], [5, "极重大"]].map(([v, t]) =>
        `<button class="opt ${v === newsFilter.level ? "active" : ""}" data-v="${v}">${t}</button>`).join("")}
    </div>
    <div class="btn-group" style="margin-bottom:6px" id="nfDir">
      ${[["", "全部方向"], ["利好", "利好"], ["利空", "利空"], ["中性", "中性"]].map(([v, t]) =>
        `<button class="opt ${v === newsFilter.direction ? "active" : ""}" data-v="${v}">${t}</button>`).join("")}
    </div>
    <div class="btn-group" style="margin-bottom:12px" id="nfRegion">
      ${[["", "全部区域"], ["国内", "国内"], ["国外", "国外"]].map(([v, t]) =>
        `<button class="opt ${v === newsFilter.region ? "active" : ""}" data-v="${v}">${t}</button>`).join("")}
    </div>`;
}

function bindNewsFilters() {
  for (const [id, key, isNum] of [["nfDays", "days", true], ["nfLevel", "level", true], ["nfDir", "direction", false], ["nfRegion", "region", false]]) {
    const el = $(`#${id}`);
    if (!el) continue;
    el.addEventListener("click", (e) => {
      const btn = e.target.closest(".opt");
      if (!btn) return;
      newsFilter[key] = isNum ? Number(btn.dataset.v) : btn.dataset.v;
      loadMacro();
    });
  }
}

function applyNewsFilter(rows) {
  return rows.filter((n) => {
    if (newsFilter.level && (n.impact_level || 1) < newsFilter.level) return false;
    if (newsFilter.direction && n.impact_direction !== newsFilter.direction) return false;
    if (newsFilter.region === "国内" && n.region !== "中国") return false;
    if (newsFilter.region === "国外" && n.region === "中国") return false;
    return true;
  });
}

async function almanacCard() {
  try {
    const a = await api("/api/macro/almanac");
    return `
      <div class="outlook-summary" style="border-color:rgba(232,196,107,.4);background:rgba(232,196,107,.06)">
        <b>📅 今日黄历</b> <span class="muted">${esc(a.note)}</span><br>
        ${esc(a.date)}（${esc(a.weekday)}）｜ ${esc(a.year_ganzhi)}【${esc(a.zodiac)}年】 ${esc(a.month_ganzhi)} ${esc(a.day_ganzhi)}
        ${a.solar_term ? `｜ <span class="badge level-3">今日${esc(a.solar_term)}</span>` : ""}
        ${a.huangdao ? `｜ <span class="badge ${a.huangdao.is_huangdao ? "level-3" : "level-2"}">${esc(a.huangdao.text)}</span>` : ""}<br>
        <span class="muted">五行：${esc(a.wuxing)} ｜ 财神方位：${esc(a.caishen)}（民俗参考）</span><br>
        <span class="muted" style="font-size:12px">时辰方位：${a.shichen.map((s) => `${esc(s.name.split(" ")[0])}${esc(s.direction)}`).join(" · ")}</span>
      </div>`;
  } catch { return ""; }
}

async function loadMacro() {
  const box = $("#macroContent");
  try {
    if (macroSub === "sectorEvents") {
      const rows = await api("/api/macro/sector-events");
      const groups = {};
      for (const ev of rows) {
        let key = ev.date;
        if (sectorEvGroup === "week") {
          const d = new Date(ev.date + "T00:00:00");
          const day = d.getDay() || 7;
          d.setDate(d.getDate() - day + 1);
          key = d.toISOString().slice(0, 10) + " 当周";
        } else if (sectorEvGroup === "month") {
          key = ev.date.slice(0, 7);
        }
        (groups[key] ||= []).push(ev);
      }
      const evRow = (ev) => `
        <div class="cal-item" style="cursor:pointer" onclick="showEventDetail('${esc(ev.title)}','${esc(ev.sectors)}')">
          <span class="cal-date" style="width:108px">${esc(ev.date)}</span>
          ${stars(ev.impact_level)}
          <span class="flag">${esc(ev.city)}</span>
          <span style="flex:1">${esc(ev.title)} <span class="badge sector-tag" style="font-size:11px">${esc(ev.sectors)}</span></span>
          <span class="muted">${esc(ev.cycle_desc)}</span>
        </div>`;
      box.innerHTML = (await almanacCard()) + '<div id="eventDetailBox"></div>' +
        `<div class="btn-group" style="margin-bottom:10px" id="seGroupBtns">
          ${[["day", "按日"], ["week", "按周"], ["month", "按月"]].map(([v, t]) =>
            `<button class="opt ${v === sectorEvGroup ? "active" : ""}" data-v="${v}">${t}</button>`).join("")}
        </div>
        <div class="muted" style="margin-bottom:8px">板块周期大事件：点击查看影响板块与相关个股 · 日期为完整年月日</div>` +
        (rows.length ? Object.entries(groups).map(([k, list]) =>
          `<div class="outlook-group">${esc(k)}（${list.length}）</div>` + list.map(evRow).join("")).join("")
          : '<div class="empty">暂无板块事件</div>');
      $("#seGroupBtns")?.addEventListener("click", (e) => {
        const btn = e.target.closest(".opt");
        if (!btn) return;
        sectorEvGroup = btn.dataset.v;
        loadMacro();
      });
      return;
    }
    if (macroSub === "outlook") {
      const d = await api(`/api/macro/outlook?horizon=${outlookHorizon}`);
      box.innerHTML = `
        <div class="btn-group" style="margin-bottom:12px" id="horizonBtns">
          ${[["week", "未来一周"], ["month", "未来一月"], ["quarter", "未来三月"], ["half", "未来半年"]].map(([k, v]) =>
            `<button class="opt ${k === outlookHorizon ? "active" : ""}" data-h="${k}">${v}</button>`).join("")}
        </div>
        ${await almanacCard()}
        <div class="outlook-summary">📋 <b>${esc(d.label)}展望综述</b><br>${esc(d.summary)}</div>
        <div id="eventDetailBox"></div>
        ${d.groups.map((g) => {
          const evHtml = (ev) => `
            <div class="cal-item" style="cursor:pointer" onclick="showEventDetail('${esc(ev.title)}','${esc(ev.sectors || "")}')" title="点击查看影响板块与相关个股">
              <span class="cal-date">${ev.date.slice(5)}</span>
              ${stars(ev.impact_level)}
              <span class="badge level-${ev.impact_level}">${ev.impact_desc}</span>
              <span class="flag">${esc(ev.region)}</span>
              <span style="flex:1">${esc(ev.title)}${ev.custom ? ` <button class="btn small danger" onclick="event.stopPropagation();delCustomEvent(${ev.id.replace("custom_", "")})">删</button>` : ""}</span>
              <span class="muted">${esc(ev.category)}</span>
            </div>`;
          if (outlookHorizon === "week") {
            const cn = g.events.filter((ev) => ev.region === "中国");
            const intl = g.events.filter((ev) => ev.region !== "中国");
            return `<div class="outlook-group">${esc(g.name)}</div>
              <div class="grid-2">
                <div><div class="muted" style="margin-bottom:4px">🇨🇳 国内</div>${cn.map(evHtml).join("") || '<div class="muted" style="padding:6px 0">无</div>'}</div>
                <div><div class="muted" style="margin-bottom:4px">🌍 国外</div>${intl.map(evHtml).join("") || '<div class="muted" style="padding:6px 0">无</div>'}</div>
              </div>`;
          }
          return `<div class="outlook-group">${esc(g.name)}</div>` + g.events.map(evHtml).join("");
        }).join("")}
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
      const months = calRange >= 180 ? 6 : 3;
      const rows = await api(`/api/macro/calendar?months=${months}`);
      const today = new Date().toISOString().slice(0, 10);
      const rangeEnd = calRange ? new Date(Date.now() + calRange * 86400000).toISOString().slice(0, 10) : "9999";
      const filtered = rows.filter((ev) =>
        ev.date >= today && ev.date <= rangeEnd && ev.impact_level >= calMinLevel);
      box.innerHTML = `
        ${await almanacCard()}
        <div class="btn-group" style="margin-bottom:6px" id="calRangeBtns">
          ${[[0, "全部"], [1, "今天"], [3, "3天内"], [7, "一周内"], [31, "一月内"], [183, "半年内"]].map(([v, t]) =>
            `<button class="opt ${v === calRange ? "active" : ""}" data-range="${v}">${t}</button>`).join("")}
        </div>
        <div class="btn-group" style="margin-bottom:12px" id="calLevelBtns">
          ${[[0, "全部星级"], [4, "★★★★+"], [5, "★★★★★"]].map(([v, t]) =>
            `<button class="opt ${v === calMinLevel ? "active" : ""}" data-level="${v}">${t}</button>`).join("")}
        </div>
        <div id="eventDetailBox"></div>
        ${filtered.length ? filtered.map((ev) => `
        <div class="cal-item" style="cursor:pointer" onclick="showEventDetail('${esc(ev.title)}','${esc(ev.sectors || "")}')" title="点击查看影响板块与相关个股">
          <span class="cal-date" style="width:108px">${esc(ev.date)}</span>
          ${stars(ev.impact_level)}
          <span class="badge level-${ev.impact_level}">${ev.impact_desc}</span>
          <span class="flag">${esc(ev.region)}</span>
          <span style="flex:1">${esc(ev.title)}</span>
          <span class="muted">${esc(ev.category)}</span>
        </div>`).join("") : '<div class="empty">该筛选条件下无事件</div>'}`;
      $("#calRangeBtns").addEventListener("click", (e) => {
        const btn = e.target.closest(".opt");
        if (!btn) return;
        calRange = Number(btn.dataset.range);
        loadMacro();
      });
      $("#calLevelBtns").addEventListener("click", (e) => {
        const btn = e.target.closest(".opt");
        if (!btn) return;
        calMinLevel = Number(btn.dataset.level);
        loadMacro();
      });
      return;
    }
    if (macroSub === "knowledge") {
      box.innerHTML = `
        <div class="inline-form" style="margin-bottom:10px;width:100%">
          <input id="kbSearch" placeholder="搜索术语，如 量比 / 换手率 / 量比" style="flex:1">
        </div>
        <div id="kbContent"><div class="empty">加载中…</div></div>`;
      $("#kbSearch").addEventListener("input", debounce(loadKnowledge, 300));
      loadKnowledge();
      return;
    }
    if (macroSub === "announce") {
      box.innerHTML = `
        <div class="inline-form" style="margin-bottom:10px;width:100%">
          <input id="annSearch" placeholder="输入代码/名称过滤，如 300432" style="flex:1">
          <button class="btn" onclick="loadAnnouncements()">查询</button>
          <button class="btn ghost" onclick="clearAnnSearch()">全部公告</button>
        </div>
        <div class="muted" id="annNote" style="margin-bottom:8px"></div>
        <div id="annRatings"></div>
        <div id="annList"><div class="empty">加载中…</div></div>`;
      loadAnnouncements();
      return;
    }
    const path = macroSub === "policy" ? "/api/macro/policies" : macroSub === "major" ? "/api/macro/major" : "/api/macro/news";
    const qs = (macroSub === "news" || macroSub === "policy") && newsFilter.days
      ? `?days=${newsFilter.days}` : "";
    const rows = applyNewsFilter(await api(path + qs));
    const offline = rows.length && rows[0].offline;
    box.innerHTML = newsFilterBar() + '<div id="eventDetailBox"></div>' +
      (offline ? '<div class="offline-banner">当前展示本地缓存数据（离线）</div>' : "") +
      (rows.length ? rows.map((n) => `
      <div class="news-item" data-news="${esc(n.text)}" data-impact="${esc(n.impact_desc || "")}">
        <span class="time">${esc((n.time || "").slice(5, 16))}</span>
        <div class="body">${esc(n.text)}
          <div class="meta">
            ${n.score !== undefined ? `<span class="badge ${n.score >= 70 ? "level-4" : n.score >= 50 ? "level-3" : "level-2"}">关注度 ${fmt(n.score, 0)}</span>` : ""}
            <span class="badge level-${n.impact_level}">${n.impact_desc || ""}</span>
            <span class="badge dir-${n.impact_direction}">${n.impact_direction}</span>
            ${n.is_policy ? '<span class="badge sector-tag">政策</span>' : ""}
            ${(n.affected_sectors || []).map((s) => `<span class="badge sector-tag">${esc(s)}</span>`).join("")}
            ${(n.affected_sectors || []).length ? `<button class="btn small ghost" onclick='showNewsStocks(${JSON.stringify((n.affected_sectors || []).join(","))}, ${JSON.stringify((n.text || "").slice(0, 24))})'>相关个股 ›</button>` : ""}
          </div>
          ${n.brief ? `<div class="muted" style="font-size:12px;margin-top:3px">💡 ${esc(n.brief)}</div>` : ""}
          ${n.commentary ? `<div class="desc-hl" style="font-size:13px;margin-top:3px">💬 ${esc(n.commentary)}</div>` : ""}
        </div>
      </div>`).join("") : '<div class="empty">该筛选条件下暂无数据</div>');
    bindNewsFilters();
  } catch (err) { box.innerHTML = '<div class="empty">加载失败，稍后自动重试</div>'; console.warn(err); }
}

window.showNewsStocks = (sectors, title) => showEventDetail(title, sectors);

window.showEventDetail = async (title, sectors = "") => {
  const box = $("#eventDetailBox");
  if (!box) return;
  box.innerHTML = '<div class="empty">分析中…</div>';
  try {
    const d = await api(`/api/macro/event-detail?title=${encodeURIComponent(title)}&bull=${encodeURIComponent(sectors)}`);
    box.innerHTML = `
      <div class="outlook-summary" style="border-color:rgba(255,169,64,.4)">
        <b>📌 ${esc(d.title)} — 影响分析</b>
        <button class="btn small ghost" style="float:right" onclick="this.closest('.outlook-summary').remove()">收起</button>
        <div class="kv"><span class="k">利好板块</span><span>${d.bull_sectors.map((s) => `<span class="badge dir-利好">${esc(s)}</span>`).join("")}</span></div>
        <div class="kv"><span class="k">利空板块</span><span>${d.bear_sectors.map((s) => `<span class="badge dir-利空">${esc(s)}</span>`).join("")}</span></div>
        <div class="kv"><span class="k">利好概率</span><span><b class="${d.bull_prob >= 55 ? "up" : d.bull_prob <= 45 ? "down" : "flat"}">${d.bull_prob}%</b> <span class="muted">${esc(d.prob_note)}</span></span></div>
        ${d.stocks.length ? `
        <div class="muted" style="margin:6px 0 4px">相关题材个股（${d.stocks.length} 只，点击进入个股分析）：</div>
        <div>${d.stocks.map((s) => `
          <span class="chip" style="cursor:pointer" onclick="openStock('${s.code}','${esc(s.name)}')">
            ${esc(s.name)} ${s.code} <span class="${cls(s.pct)}">${pct(s.pct)}</span>${s.buy_index !== null ? ` · 购${fmt(s.buy_index, 0)}` : ""}
          </span>`).join("")}</div>` : '<div class="muted">未匹配到相关个股</div>'}
        <div class="muted" style="font-size:11px;margin-top:6px">${esc(d.disclaimer)}</div>
      </div>`;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { box.innerHTML = '<div class="empty">分析加载失败</div>'; }
};

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

let industriesCollapsed = true;
function condGroup(group, label, buttonsHtml, collapsible = false) {
  const count = (screenerState[group] || []).length;
  return `<div class="cond-group">
    <div class="g-label">${label}${count ? ` <span class="badge sector-tag" style="font-size:10px;padding:0 6px">${count}</span>` : ""}
      ${count ? `<span class="g-clear" onclick="clearGroup('${group}')">清除</span>` : ""}
      ${collapsible ? `<span class="collapse-toggle" onclick="toggleIndustries()">${industriesCollapsed ? "展开 ▾" : "收起 ▴"}</span>` : ""}
    </div>
    <div class="btn-group multi" data-group="${group}" ${collapsible && industriesCollapsed ? 'style="display:none"' : ""}>
      ${buttonsHtml}</div></div>`;
}

function renderScreenerConditions() {
  const m = screenerMeta;
  const totalConds = Object.entries(screenerState).filter(([, v]) => Array.isArray(v) && v.length).reduce((a, [, v]) => a + v.length, 0);
  let html = `<div class="chips" id="condChips"></div><div class="cond-grid">`;
  html += condGroup("boards", "所属板块",
    m.boards.map((b) => `<button class="opt ${screenerState.boards.includes(b) ? "active" : ""}" data-val="${b}">${b}</button>`).join(""));
  if (m.industries.length) {
    html += condGroup("industries", `所属行业（${m.industries.length}个）`,
      m.industries.map((b) => `<button class="opt ${screenerState.industries.includes(b) ? "active" : ""}" data-val="${b}">${b}</button>`).join(""), true);
  }
  for (const [group, buckets] of Object.entries(m.buckets)) {
    html += condGroup(group, GROUP_LABELS[group] || group,
      Object.entries(buckets).map(([k, label]) =>
        `<button class="opt ${(screenerState[group] || []).includes(k) ? "active" : ""}" data-val="${k}">${esc(label)}</button>`).join(""));
  }
  html += `</div><div class="cond-inline" style="margin-top:8px">
    <span><span class="g-label muted">股价区间</span>
      <input id="priceMin" placeholder="最低" value="${screenerState.price_min}">
      ~ <input id="priceMax" placeholder="最高" value="${screenerState.price_max}"></span>
    <label style="color:var(--muted);font-size:13px">
      <input type="checkbox" id="excludeSt" ${screenerState.exclude_st ? "checked" : ""}> 剔除 ST/退市
    </label>
    <span class="muted">已选条件 ${totalConds} 项</span></div>`;
  $("#screenerConditions").innerHTML = html;
  renderChips();
  updateScreenerSentence();

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
  updateScreenerSentence();
}

function updateScreenerSentence() {
  const el = $("#screenerSentence");
  if (!el || !screenerMeta) return;
  const parts = [];
  if (screenerState.boards.length) parts.push(screenerState.boards.join("/"));
  if (screenerState.industries.length) parts.push(screenerState.industries.join("/"));
  for (const [group, arr] of Object.entries(screenerState)) {
    if (!Array.isArray(arr) || !arr.length) continue;
    if (group === "boards" || group === "industries") continue;
    const labels = arr.map((v) => (screenerMeta.buckets[group] || {})[v] || v);
    parts.push(`${GROUP_LABELS[group] || group}=${labels.join("/")}`);
  }
  if (screenerState.price_min || screenerState.price_max) {
    parts.push(`股价${screenerState.price_min || "*"}~${screenerState.price_max || "*"}`);
  }
  if (screenerState.exclude_st) parts.push("剔除ST");
  el.textContent = parts.length ? parts.join(" 且 ") : "未设置条件（全市场按购买指数排序）";
}

window.parseScreenerSemantic = async () => {
  const text = ($("#screenerSemantic")?.value || "").trim();
  if (!text) return;
  try {
    const d = await api("/api/screener/parse", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    clearScreener(false);
    Object.assign(screenerState, d.conditions);
    for (const g of Object.keys(GROUP_LABELS)) screenerState[g] ||= [];
    screenerState.boards ||= []; screenerState.industries ||= [];
    renderScreenerConditions();
    $("#screenerSentence").textContent = d.summary || $("#screenerSentence").textContent;
    runScreener();
  } catch (err) { console.warn(err); }
};

window.clearGroup = (group) => {
  if (Array.isArray(screenerState[group])) screenerState[group] = [];
  renderScreenerConditions();
  runScreenerDebounced();
};
window.toggleIndustries = () => {
  industriesCollapsed = !industriesCollapsed;
  renderScreenerConditions();
};

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
      <th>#</th><th>名称</th><th>五行</th><th>行业</th><th>最新价</th><th>涨跌幅</th><th>购买指数</th>
      <th>情绪</th><th>暗盘力量</th><th>主力净流入(万)</th><th>PE</th><th>量比</th><th>企稳</th>
    </tr></thead><tbody>${d.items.map((r, i) => `
      <tr data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${i + 1}</td><td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td>${wxBadges(r.wuxing)}</td>
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

/* ---------------- 板块资金（FR3-01） ---------------- */
let sectorDim = "industry";
let sectorView = "map";
let sectorChart = null;
let sectorPage = "flow";

$("#sectorPageTabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  sectorPage = btn.dataset.page;
  $$("#sectorPageTabs .opt").forEach((b) => b.classList.toggle("active", b === btn));
  $("#sectorFlowView").style.display = sectorPage === "flow" ? "" : "none";
  $("#sectorRecView").style.display = sectorPage === "recommend" ? "" : "none";
  if (sectorPage === "flow") { loadSector(); sectorChart?.resize(); }
  else loadSectorRecommend();
});

$("#sectorDim").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  sectorDim = btn.dataset.dim;
  $$("#sectorDim .opt").forEach((b) => b.classList.toggle("active", b === btn));
  loadSector();
});
$("#sectorView").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  sectorView = btn.dataset.view;
  $$("#sectorView .opt").forEach((b) => b.classList.toggle("active", b === btn));
  $("#sectorMap").style.display = sectorView === "map" ? "" : "none";
  $("#sectorList").style.display = sectorView === "list" ? "" : "none";
  if (sectorView === "map") sectorChart?.resize();
});

async function loadSector() {
  if (sectorPage === "recommend") { loadSectorRecommend(); return; }
  try {
    const d = await api(`/api/sector/flow?dim=${sectorDim}`);
    $("#sectorTime").textContent = `更新于 ${d.updated_at}`;
    renderSectorMap(d.items);
    renderSectorList(d.items);
  } catch (err) { console.warn(err); }
}

async function loadSectorRecommend() {
  const box = $("#sectorRecBox");
  box.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const rows = await api("/api/sector/recommend");
    box.innerHTML = rows.length ? rows.map((sec, i) => `
      <div class="sec-rec">
        <div class="card-title" style="margin-bottom:6px">
          <span>${i + 1}. ${esc(sec.name)}
            <span class="badge ${cls(sec.pct) === "up" ? "level-4" : cls(sec.pct) === "down" ? "level-1" : "level-2"}">${pct(sec.pct)}</span>
            <span class="muted">热度 ${fmt(sec.hot_score, 1)} · 5日 ${pct(sec.d5)} · 净流入 ${fmt(sec.net_in_yi)} 亿</span>
          </span>
        </div>
        <table><thead><tr>
          <th>名称</th><th>五行</th><th>现价</th><th>涨跌幅</th><th>购买指数</th><th>评分</th><th>提示</th>
        </tr></thead><tbody>${(sec.stocks || []).map((r) => `
          <tr data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
            <td>${esc(r.name)} <span class="muted">${r.code}</span></td>
            <td>${wxBadges(r.wuxing)}</td>
            <td class="num ${cls(r.pct)}">${fmt(r.price)}</td>
            <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
            <td class="num">${r.buy_index !== null ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
            <td class="num"><b>${fmt(r.score, 1)}</b></td>
            <td><span class="badge ${r.advice === "增持" ? "advice-buy" : r.advice === "减持" ? "advice-sell" : "advice-hold"}" style="font-size:11px;padding:2px 7px">${esc(r.advice || "-")}</span></td>
          </tr>`).join("")}</tbody></table>
      </div>`).join("") : '<div class="empty">暂无板块推荐（需全量快照）</div>';
  } catch (err) { box.innerHTML = '<div class="empty">加载失败</div>'; }
}

function flowColor(ratio) {
  // 净流入率 % → 红入绿出
  const r = Math.max(-6, Math.min(6, ratio || 0)) / 6;
  if (r >= 0) {
    const t = r;
    return `rgb(${Math.round(90 + 165 * t)},${Math.round(60 - 30 * t)},${Math.round(70 - 30 * t)})`;
  }
  const t = -r;
  return `rgb(${Math.round(50 - 20 * t)},${Math.round(110 + 84 * t)},${Math.round(95 + 34 * t)})`;
}

function renderSectorMap(items) {
  sectorChart ||= echarts.init($("#sectorMap"), "dark");
  const data = items.map((it) => ({
    name: it.name,
    value: it.amount_yi || 0,
    itemStyle: { color: flowColor(it.net_in_ratio) },
    _meta: it,
  }));
  sectorChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      backgroundColor: "#1a2230", borderColor: "#2a3548", textStyle: { color: "#dbe4f0", fontSize: 12 },
      formatter: (p) => {
        const m = p.data._meta || {};
        return `<b>${p.name}</b><br>成交额 ${m.amount_yi} 亿<br>主力净流入 <b>${m.net_in_yi} 亿</b>（${m.net_in_ratio}%）<br>` +
          `涨跌幅 ${m.pct}% · 上涨${m.up}/下跌${m.down}家` +
          (m.leader ? `<br>领涨：${m.leader.name} +${m.leader.pct}%` : "");
      },
    },
    series: [{
      type: "treemap", roam: false, nodeClick: false, breadcrumb: { show: false },
      width: "100%", height: "100%",
      label: {
        show: true, fontSize: 12,
        formatter: (p) => {
          const m = p.data._meta || {};
          return `${p.name}\n${m.pct > 0 ? "+" : ""}${m.pct}%  ${m.net_in_yi > 0 ? "流入" : "流出"}${Math.abs(m.net_in_yi)}亿`;
        },
      },
      itemStyle: { borderColor: "#0d1117", borderWidth: 2, gapWidth: 2 },
      data,
    }],
  }, true);
  sectorChart.off("click");
  sectorChart.on("click", (p) => { if (p.name) drillSector(p.name); });
}

function renderSectorList(items) {
  $("#sectorList").innerHTML = `<table><thead><tr>
    <th>板块</th><th>涨跌幅</th><th>主力净流入(亿)</th><th>净流入率</th><th>成交额(亿)</th><th>上涨/下跌</th><th>领涨股</th>
  </tr></thead><tbody>${items.map((it) => `
    <tr onclick="drillSector('${esc(it.name)}')">
      <td>${esc(it.name)}</td>
      <td class="num ${cls(it.pct)}">${pct(it.pct)}</td>
      <td class="num ${cls(it.net_in_yi)}">${fmt(it.net_in_yi)}</td>
      <td class="num ${cls(it.net_in_ratio)}">${fmt(it.net_in_ratio)}%</td>
      <td class="num">${fmt(it.amount_yi, 0)}</td>
      <td><span class="up">${it.up}</span>/<span class="down">${it.down}</span></td>
      <td>${it.leader ? `${esc(it.leader.name)} <span class="up">+${fmt(it.leader.pct)}%</span>` : "-"}</td>
    </tr>`).join("")}</tbody></table>`;
}

window.drillSector = async (name) => {
  const box = $("#sectorDrill");
  box.style.display = "";
  $("#sectorDrillTitle").textContent = `「${name}」成分股（按主力净流入排序）`;
  $("#sectorDrillTable").innerHTML = '<div class="empty">加载中…</div>';
  try {
    const rows = await api(`/api/sector/stocks?dim=${sectorDim}&name=${encodeURIComponent(name)}`);
    $("#sectorDrillTable").innerHTML = rows.length ? `<table><thead><tr>
      <th>名称</th><th>最新价</th><th>涨跌幅</th><th>主力净流入(万)</th><th>量比</th><th>购买指数</th><th>情绪</th><th>暗盘力量</th>
    </tr></thead><tbody>${rows.map((r) => `
      <tr onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td class="num ${cls(r.pct)}">${fmt(r.price)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num ${cls(r.main_net_in)}">${fmt(r.main_net_in, 0)}</td>
        <td class="num">${fmt(r.volume_ratio)}</td>
        <td class="num">${r.buy_index !== null ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
        <td>${r.sent_level ? esc(r.sent_level) : "-"}</td>
        <td class="num">${fmt(r.dark_power, 0)}</td>
      </tr>`).join("")}</tbody></table>` : '<div class="empty">暂无成分股数据</div>';
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { $("#sectorDrillTable").innerHTML = '<div class="empty">加载失败</div>'; }
};

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
    <div class="q-card" style="cursor:pointer" onclick="openCommodityKline('${c.symbol}','${esc(c.name)}')" title="点击查看K线">
      <div class="head">
        <div><div class="name">${esc(c.name)} <span class="muted" style="font-size:10px">K线 ›</span></div><div class="sub">${esc(c.category)} · ${esc(c.unit)}</div></div>
        <span class="star ${c.watched ? "on" : ""}" onclick="event.stopPropagation();toggleCommodity('${c.symbol}')">${c.watched ? "★" : "☆"}</span>
      </div>
      <div class="price ${cls(c.pct)}">${fmt(c.price, 3)}</div>
      <div class="chg ${cls(c.pct)}">${pct(c.pct)}</div>
      <div class="row2"><span>高 ${fmt(c.high, 2)} 低 ${fmt(c.low, 2)}</span><span>关联: ${esc(c.related_sector)}</span></div>
    </div>`).join("") || '<div class="empty" style="grid-column:1/-1">请选择至少一个分类</div>');
}
window.toggleCommodity = async (sym) => { await post(`/api/commodities/watch?symbol=${sym}`); loadCommodities(); };

/* 商品K线（FR4-03-2） */
let ckChart = null;
let ckState = { symbol: null, name: "", period: "day" };

window.openCommodityKline = (symbol, name) => {
  ckState = { ...ckState, symbol, name };
  ckRelPage = 1;
  $("#commodityKlineCard").style.display = "";
  loadCommodityKline();
  loadCommodityRelated();
  $("#commodityKlineCard").scrollIntoView({ behavior: "smooth", block: "nearest" });
};

let ckRelPage = 1;
async function loadCommodityRelated() {
  if (!ckState.symbol) return;
  const box = $("#ckRelatedTable");
  box.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const d = await api(`/api/commodities/related?symbol=${ckState.symbol}&page=${ckRelPage}&page_size=20`);
    $("#ckRelatedSector").textContent = d.sector ? `关联板块：${d.sector} · 共 ${d.total} 只` : "";
    box.innerHTML = d.items.length ? `<table><thead><tr>
      <th>名称</th><th>所属板块</th><th>现价</th><th>涨跌幅</th><th>主力净流入(万)</th><th>量能</th><th>购买指数</th><th>评分</th><th>情绪</th><th>暗盘力量</th><th>提示</th>
    </tr></thead><tbody>${d.items.map((r) => `
      <tr class="${scoreRowClass(r.score)}" onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td>${esc(r.industry || "-")}</td>
        <td class="num ${cls(r.pct)}">${fmt(r.price)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num ${cls(r.main_net_in)}">${fmt(r.main_net_in, 0)}</td>
        <td>${esc(r.volume_desc || "-")}</td>
        <td class="num">${r.buy_index !== null ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
        <td class="num"><b>${fmt(r.score, 1)}</b></td>
        <td>${r.sent_level ? esc(r.sent_level) : "-"}</td>
        <td class="num">${fmt(r.dark_power, 0)}</td>
        <td><span class="badge ${r.advice === "增持" ? "advice-buy" : r.advice === "减持" ? "advice-sell" : "advice-hold"}" style="font-size:11px;padding:2px 7px">${esc(r.advice || "-")}</span></td>
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:6px;font-size:12px">行背景按评分五档着色。</div>` : '<div class="empty">暂无关联个股</div>';
    $("#ckRelatedPager").innerHTML = d.pages > 1 ? `
      <button class="btn small ghost" ${d.page <= 1 ? "disabled" : ""} onclick="ckRelGo(${d.page - 1})">‹ 上一页</button>
      <span class="info">第 ${d.page} / ${d.pages} 页</span>
      <button class="btn small ghost" ${d.page >= d.pages ? "disabled" : ""} onclick="ckRelGo(${d.page + 1})">下一页 ›</button>` : "";
  } catch (err) { box.innerHTML = '<div class="empty">加载失败</div>'; }
}
window.ckRelGo = (p) => { ckRelPage = p; loadCommodityRelated(); };
window.closeCommodityKline = () => { $("#commodityKlineCard").style.display = "none"; };

$("#ckPeriod").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  ckState.period = btn.dataset.p;
  $$("#ckPeriod .opt").forEach((b) => b.classList.toggle("active", b === btn));
  loadCommodityKline();
});

async function loadCommodityKline() {
  if (!ckState.symbol) return;
  ckChart ||= echarts.init($("#ckChart"), "dark");
  ckChart.showLoading({ maskColor: "rgba(13,17,23,.6)", textColor: "#dbe4f0" });
  try {
    const d = await api(`/api/commodities/kline?symbol=${ckState.symbol}&period=${ckState.period}`);
    ckChart.hideLoading();
    if (d.error || !d.dates || !d.dates.length) {
      $("#ckTitle").textContent = `${ckState.name} — ${d.error || "暂无K线数据"}`;
      ckChart.clear();
      return;
    }
    $("#ckTitle").textContent = `${d.name}（${d.unit}）${{ day: "日K", week: "周K", month: "月K" }[d.period]}`;
    const maSeries = Object.entries(d.ma).map(([n, values], i) => ({
      name: `MA${n}`, type: "line", data: values, showSymbol: false, smooth: true,
      lineStyle: { width: 1, color: ["#e8c46b", "#4a9eff", "#c678dd"][i] },
    }));
    ckChart.setOption({
      backgroundColor: "transparent", animation: false,
      tooltip: { trigger: "axis", axisPointer: { type: "cross" }, backgroundColor: "#1a2230", borderColor: "#2a3548", textStyle: { color: "#dbe4f0", fontSize: 12 } },
      legend: { data: maSeries.map((s) => s.name), textStyle: { color: "#7d8aa0" }, top: 0 },
      grid: [{ left: 60, right: 20, top: 28, height: "62%" }, { left: 60, right: 20, top: "78%", height: "16%" }],
      xAxis: [
        { type: "category", data: d.dates, gridIndex: 0, axisLine: { lineStyle: { color: "#2a3548" } } },
        { type: "category", data: d.dates, gridIndex: 1, show: false },
      ],
      yAxis: [
        { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: "#202a3b" } } },
        { gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
      ],
      dataZoom: [{ type: "inside", xAxisIndex: [0, 1], start: 40, end: 100 },
                 { type: "slider", xAxisIndex: [0, 1], top: "96%", height: 12, borderColor: "#2a3548" }],
      series: [
        { name: "K线", type: "candlestick", data: d.kline,
          itemStyle: { color: "#ff5252", color0: "#26c281", borderColor: "#ff5252", borderColor0: "#26c281" } },
        ...maSeries,
        { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: d.volumes,
          itemStyle: { color: (p) => (d.kline[p.dataIndex][1] >= d.kline[p.dataIndex][0] ? "#ff5252" : "#26c281") } },
      ],
    }, true);
  } catch (err) { ckChart.hideLoading(); console.warn(err); }
}

/* ---------------- 全球指数 ---------------- */
let globalSub = "indices";
let etfFilter = "all";
let etfPage = 1;
window.etfGo = (p) => { etfPage = p; loadGlobal(); };
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
      const d = await api(`/api/etfs?filter=${etfFilter}&page=${etfPage}&page_size=20`);
      box.innerHTML = `<div class="card">
        <div class="btn-group" style="margin-bottom:8px" id="etfFilterBtns">
          ${[["all", "全部"], ["top_up", "上涨TOP20"], ["top_down", "下跌TOP20"]].map(([v, t]) =>
            `<button class="opt ${v === etfFilter ? "active" : ""}" data-v="${v}">${t}</button>`).join("")}
          <span class="muted" style="margin-left:10px">共 ${d.total} 只 · 点击行查看持仓详情与K线</span>
        </div>
        <table><thead><tr>
        <th>代码</th><th>名称</th><th>分类</th><th>跟踪标的</th><th>最新价</th><th>涨跌幅</th><th>成交额(万)</th>
      </tr></thead><tbody>${d.items.map((r) => `
        <tr onclick="openEtfDetail('${r.code}','${esc(r.name)}')">
          <td>${r.code}</td><td>${esc(r.name)}</td><td>${esc(r.category)}</td><td>${esc(r.track)}</td>
          <td class="num ${cls(r.pct)}">${fmt(r.price, 3)}</td>
          <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
          <td class="num">${fmt(r.amount, 0)}</td>
        </tr>`).join("")}</tbody></table>
        ${d.pages > 1 ? `<div class="pager">
          <button class="btn small ghost" ${d.page <= 1 ? "disabled" : ""} onclick="etfGo(${d.page - 1})">‹ 上一页</button>
          <span class="info">第 ${d.page} / ${d.pages} 页</span>
          <button class="btn small ghost" ${d.page >= d.pages ? "disabled" : ""} onclick="etfGo(${d.page + 1})">下一页 ›</button>
        </div>` : ""}</div>`;
      $("#etfFilterBtns").addEventListener("click", (e) => {
        const btn = e.target.closest(".opt");
        if (!btn) return;
        etfFilter = btn.dataset.v;
        etfPage = 1;
        loadGlobal();
      });
      return;
    }
    const d = await api("/api/global-indices");
    box.innerHTML = (d.offline ? '<div class="offline-banner">海外指数暂不可用，仅展示国内数据</div>' : "") +
      d.regions.map((rg) => `
      <div class="region-title">${esc(rg.region)}</div>
      <div class="cards-grid">${rg.items.map((q) => {
        const clickable = rg.region === "中国大陆";
        return `
        <div class="q-card" ${clickable ? `style="cursor:pointer" onclick="openStock('${q.symbol}','${esc(q.name)}')" title="点击查看K线"` : ""}>
          <div class="head"><div class="name">${esc(q.name)}${clickable ? ' <span class="muted" style="font-size:10px">K线 ›</span>' : ""}</div><span class="flag">${esc(q.country)}</span></div>
          <div class="price ${cls(q.pct)}">${fmt(q.price)}</div>
          <div class="chg ${cls(q.pct)}">${sign(q.change)}${fmt(q.change)}&nbsp;&nbsp;${pct(q.pct)}</div>
          <div class="row2"><span>${esc(q.desc)}</span>${clickable ? "" : '<span class="muted">暂不支持K线</span>'}</div>
        </div>`;
      }).join("")}</div>`).join("");
  } catch (err) { box.innerHTML = '<div class="empty">加载失败</div>'; console.warn(err); }
}

window.openEtfDetail = async (code, name) => {
  const box = $("#etfDetail");
  box.style.display = "";
  $("#etfDetailTitle").textContent = `${name}（${code}）近似持仓`;
  $("#etfKlineBtn").onclick = () => openStock(code, name);
  $("#etfHoldings").innerHTML = '<div class="empty">加载中…</div>';
  try {
    const d = await api(`/api/etf/holdings?code=${code}`);
    $("#etfDetailNote").textContent = d.note || "";
    $("#etfHoldings").innerHTML = d.holdings.length ? `<table><thead><tr>
      <th>#</th><th>持仓个股</th><th>所属板块</th><th>近似权重</th><th>现价</th><th>涨跌幅</th>
      <th>购买指数</th><th>评分</th><th>量能</th><th>情绪</th><th>暗盘力量</th><th>提示</th>
    </tr></thead><tbody>${d.holdings.map((r, i) => `
      <tr class="${scoreRowClass(r.score)}" onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${i + 1}</td>
        <td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td>${esc(r.industry || "-")}</td>
        <td class="num"><b>${fmt(r.weight)}%</b></td>
        <td class="num ${cls(r.pct)}">${fmt(r.price)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num">${r.buy_index !== null ? `<b>${fmt(r.buy_index, 0)}</b> <span class="muted">${esc(r.buy_level || "")}</span>` : "-"}</td>
        <td class="num"><b>${fmt(r.score, 1)}</b></td>
        <td>${esc(r.volume_desc || "-")}</td>
        <td>${r.sent_level ? esc(r.sent_level) : "-"}</td>
        <td class="num">${fmt(r.dark_power, 0)}</td>
        <td><span class="badge ${r.advice === "增持" ? "advice-buy" : r.advice === "减持" ? "advice-sell" : "advice-hold"}" style="font-size:11px;padding:2px 7px">${esc(r.advice)}</span></td>
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:6px;font-size:12px">行背景按评分五档着色（≥55 起）。</div>` : `<div class="empty">${esc(d.note || "暂无持仓数据")}</div>`;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { $("#etfHoldings").innerHTML = '<div class="empty">加载失败</div>'; }
};

/* ---------------- 个股推荐 ---------------- */
let recommendBoard = "composite";
const recState = { page: 1, page_size: 20, advice: "", min_score: 0, vol_filter: "", order_by: "",
  mv_filter: "", turn_filter: "" };

const scoreRowClass = (s) => s >= 95 ? "score-95" : s >= 85 ? "score-85" : s >= 75 ? "score-75" : s >= 65 ? "score-65" : s >= 55 ? "score-55" : "";

for (const [id, key] of [["recAdvice", "advice"], ["recScore", "min_score"], ["recVol", "vol_filter"],
  ["recOrder", "order_by"], ["recMv", "mv_filter"], ["recTurn", "turn_filter"]]) {
  $(`#${id}`).addEventListener("click", (e) => {
    const btn = e.target.closest(".opt");
    if (!btn) return;
    recState[key] = key === "min_score" ? Number(btn.dataset.v) : btn.dataset.v;
    recState.page = 1;
    $$(`#${id} .opt`).forEach((b) => b.classList.toggle("active", b === btn));
    loadRecommend();
  });
}

function renderPager(d) {
  $("#recPager").innerHTML = `
    <button class="btn small ghost" ${d.page <= 1 ? "disabled" : ""} onclick="recPage(${d.page - 1})">‹ 上一页</button>
    <span class="info">第 ${d.page} / ${d.pages} 页 · 共 ${d.total} 条</span>
    <button class="btn small ghost" ${d.page >= d.pages ? "disabled" : ""} onclick="recPage(${d.page + 1})">下一页 ›</button>
    <span class="info" style="margin-left:12px">每页</span>
    <span class="btn-group">${[20, 50, 100].map((n) =>
      `<button class="opt ${recState.page_size === n ? "active" : ""}" onclick="recPageSize(${n})">${n}</button>`).join("")}</span>`;
}
window.recPage = (p) => { recState.page = p; loadRecommend(); };
window.recPageSize = (n) => { recState.page_size = n; recState.page = 1; loadRecommend(); };

async function loadRecommend() {
  const box = $("#recommendTable");
  try {
    const qs = new URLSearchParams({ board: recommendBoard, page: recState.page,
      page_size: recState.page_size, advice: recState.advice,
      min_score: recState.min_score, vol_filter: recState.vol_filter, order_by: recState.order_by,
      mv_filter: recState.mv_filter, turn_filter: recState.turn_filter });
    const d = await api(`/api/recommend?${qs}`);
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
    const demonBanner = recommendBoard === "demon"
      ? '<div class="offline-banner" style="border-color:rgba(255,82,82,.5);color:var(--up);background:rgba(255,82,82,.08)">⚠️ 妖股波动剧烈，随时可能天地板，本榜仅作市场现象研究，严禁跟风追高</div>'
      : "";
    box.innerHTML = d.items.length ? demonBanner + statsHtml + `<table><thead><tr>
      <th>#</th><th>名称</th><th>五行</th><th>所属板块</th><th>现价</th><th>涨跌幅</th><th>${esc(d.items[0].metric_name)}</th>
      ${isStab ? "<th>闸门</th><th>星级</th>" : ""}<th>购买指数</th><th>情绪</th>
      <th>量能</th><th>评分</th><th>提示</th><th>入选理由</th>
    </tr></thead><tbody>${d.items.map((r, i) => `
      <tr class="${scoreRowClass(r.score)}" data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${(d.page - 1) * d.page_size + i + 1}</td><td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td>${wxBadges(r.wuxing)}</td>
        <td>${esc(r.industry || "-")}</td>
        <td class="num ${cls(r.pct)}">${fmt(r.price)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num">${fmt(r.metric_value)}</td>
        ${isStab ? `<td>${(r.gates || []).map((g, gi) => `<span class="gate ${g ? "on" : ""}">G${gi + 1}</span>`).join("")}</td>
        <td class="up">${r.stars || ""}</td>` : ""}
        <td class="num">${r.buy_index !== null && r.buy_index !== undefined ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
        <td>${r.sent_level ? esc(r.sent_level) : "-"}</td>
        <td>${esc(r.volume_desc)}</td>
        <td class="num"><b>${fmt(r.score, 1)}</b></td>
        <td><span class="badge ${r.advice === "增持" ? "advice-buy" : r.advice === "减持" ? "advice-sell" : "advice-hold"}" style="font-size:12px;padding:2px 8px">${r.advice}</span></td>
        <td class="desc-hl" style="white-space:normal;min-width:220px;max-width:340px">${esc(r.reason || "")}</td>
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:8px;font-size:12px">评分行背景：≥55 淡橙 → ≥95 深红 递进。榜单为量化参考，不构成投资建议。</div>`
      : '<div class="empty">该筛选条件下无个股（企稳/指标类榜单需先在设置页执行「重建指标」）</div>';
    renderPager(d);
  } catch (err) { box.innerHTML = '<div class="empty">加载失败</div>'; console.warn(err); }
}
$("#recommendBoards").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  recommendBoard = btn.dataset.board;
  recState.page = 1;
  $$("#recommendBoards .opt").forEach((b) => b.classList.toggle("active", b === btn));
  loadRecommend();
});

/* ---------------- 公司公告（FR7-04，8.0 收编至宏观二级） ---------------- */
async function loadAnnouncements() {
  const inp = $("#annSearch");
  const code = inp ? inp.value.trim() : "";
  const list = $("#annList");
  if (!list) return;
  list.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const d = await api(`/api/announcements?code=${encodeURIComponent(code)}`);
    const note = $("#annNote");
    if (note) note.textContent = d.note + (d.target_name ? ` · 当前筛选：${d.target_name}` : "");
    const ratings = $("#annRatings");
    if (ratings) ratings.innerHTML = d.ratings ? `
      <div class="outlook-summary" style="margin-bottom:10px">
        <b>📊 ${esc(d.target_name)} 机构评级</b> <span class="muted">${d.ratings.simulated ? "规则模拟·仅供参考" : ""}</span><br>
        评级分布：${Object.entries(d.ratings.distribution).filter(([, n]) => n > 0).map(([k, n]) => `${k} ${n} 家`).join(" · ")}
        ｜ 一致目标价 <b>${fmt(d.ratings.consensus_target)}</b><br>
        <span class="muted" style="font-size:12px">${d.ratings.items.slice(0, 5).map((it) =>
          `${esc(it.broker)}:${it.rating}(${fmt(it.target_price)})`).join("　")}</span>
      </div>` : "";
    list.innerHTML = d.items.length ? d.items.map((a) => `
      <div class="news-item" data-news="${esc(a.text)}" data-impact="${esc(a.impact_desc || "")}">
        <span class="time">${esc((a.time || "").slice(5, 16))}</span>
        <div class="body">${esc(a.text)}
          <div class="meta">
            <span class="badge sector-tag">${esc(a.tag)}</span>
            <span class="badge dir-${a.direction}">${esc(a.direction)}</span>
            <span class="badge level-${a.impact_level}">${esc(a.impact_desc)}</span>
            ${(a.affected_sectors || []).map((s) => `<span class="badge sector-tag">${esc(s)}</span>`).join("")}
          </div>
          ${a.brief ? `<div class="desc-hl" style="font-size:13px;margin-top:3px">💡 ${esc(a.brief)}</div>` : ""}
        </div>
      </div>`).join("") : '<div class="empty">暂无匹配公告（公告源为7x24快讯识别）</div>';
  } catch (err) { list.innerHTML = '<div class="empty">加载失败</div>'; }
}
window.loadAnnouncements = loadAnnouncements;
window.clearAnnSearch = () => { const el = $("#annSearch"); if (el) el.value = ""; loadAnnouncements(); };

/* ---------------- 股票常识（FR7-03，8.0 收编至宏观二级） ---------------- */
async function loadKnowledge() {
  const inp = $("#kbSearch");
  const kw = inp ? inp.value.trim() : "";
  const box = $("#kbContent");
  if (!box) return;
  try {
    const groups = await api(`/api/knowledge?q=${encodeURIComponent(kw)}`);
    box.innerHTML = groups.length ? groups.map((g) => `
      <div class="region-title">${esc(g.group)}（${g.items.length}）</div>
      ${g.items.map((it) => `<div class="kb-item">
        <div class="term">${esc(it.term)}</div>
        <div class="desc">${esc(it.desc)}</div></div>`).join("")}`).join("")
      : '<div class="empty">未找到相关词条</div>';
  } catch (err) { console.warn(err); }
}

/* ---------------- 榜单（FR8-04） ---------------- */
let rankType = "limit_up";
async function loadRanks() {
  const box = $("#rankTable");
  try {
    const d = await api(`/api/ranks?type=${rankType}`);
    const tabs = $("#rankTabs");
    if (tabs && !tabs.children.length) {
      tabs.innerHTML = Object.entries(d.types).map(([k, v]) =>
        `<button class="opt ${k === rankType ? "active" : ""}" data-type="${k}">${v}</button>`).join("");
      tabs.addEventListener("click", (e) => {
        const btn = e.target.closest(".opt");
        if (!btn) return;
        rankType = btn.dataset.type;
        $$("#rankTabs .opt").forEach((b) => b.classList.toggle("active", b === btn));
        loadRanks();
      });
    }
    $("#rankNote").textContent = d.note || "";
    box.innerHTML = d.items.length ? `<table><thead><tr>
      <th>#</th><th>名称</th><th>五行</th><th>所属板块</th><th>现价</th><th>涨跌幅</th>
      <th>${esc(d.items[0].metric_name)}</th><th>购买指数</th><th>评分</th><th>提示</th>
    </tr></thead><tbody>${d.items.map((r, i) => `
      <tr class="${scoreRowClass(r.score)}" data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${i + 1}</td><td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td>${wxBadges(r.wuxing)}</td>
        <td>${esc(r.industry || "-")}</td>
        <td class="num ${cls(r.pct)}">${fmt(r.price)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num">${fmt(r.metric_value)}</td>
        <td class="num">${r.buy_index !== null ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
        <td class="num"><b>${fmt(r.score, 1)}</b></td>
        <td><span class="badge ${r.advice === "增持" ? "advice-buy" : r.advice === "减持" ? "advice-sell" : "advice-hold"}" style="font-size:11px;padding:2px 7px">${esc(r.advice || "-")}</span></td>
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:8px;font-size:12px">行背景按评分五档着色。榜单为本地异动口径，不构成投资建议。</div>`
      : '<div class="empty">该榜暂无数据（需全量快照）</div>';
  } catch (err) { box.innerHTML = '<div class="empty">加载失败</div>'; console.warn(err); }
}

/* ---------------- AI 选股（FR8-05） ---------------- */
window.runAiPick = async () => {
  const desc = ($("#aiPickInput")?.value || "").trim();
  if (!desc) return;
  $("#aiPickTable").innerHTML = '<div class="empty">解析筛选中…</div>';
  $("#aiPickRule").style.display = "none";
  $("#aiPickComment").innerHTML = "";
  try {
    const d = await api("/api/ai/pick", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description: desc }),
    });
    $("#aiPickRule").style.display = "";
    $("#aiPickRule").innerHTML = `<b>解析规则</b>：${esc(d.summary || "未解析出规则")}
      <span class="muted"> · 来源 ${esc(d.source || "")} · 命中 ${d.total || 0} 只</span>`;
    if (d.commentary) {
      $("#aiPickComment").innerHTML = `<div class="outlook-summary" style="white-space:pre-wrap">${esc(d.commentary)}</div>`;
    }
    $("#aiPickTable").innerHTML = d.items && d.items.length ? `<table><thead><tr>
      <th>#</th><th>名称</th><th>五行</th><th>行业</th><th>现价</th><th>涨跌幅</th>
      <th>购买指数</th><th>PE</th><th>量比</th><th>主力净流入(万)</th>
    </tr></thead><tbody>${d.items.map((r, i) => `
      <tr data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${i + 1}</td><td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td>${wxBadges(r.wuxing)}</td>
        <td>${esc(r.industry || "-")}</td>
        <td class="num ${cls(r.pct)}">${fmt(r.price)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num">${r.buy_index !== null ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
        <td class="num">${fmt(r.pe_ttm, 1)}</td>
        <td class="num">${fmt(r.volume_ratio)}</td>
        <td class="num ${cls(r.main_net_in)}">${fmt(r.main_net_in, 0)}</td>
      </tr>`).join("")}</tbody></table>` : '<div class="empty">未命中个股，可换个描述或放宽条件</div>';
  } catch (err) { $("#aiPickTable").innerHTML = '<div class="empty">选股失败</div>'; console.warn(err); }
};
async function loadAiPick() { /* 进入页不自动请求，等待用户输入 */ }

/* ---------------- AI 分析（FR7-07） ---------------- */
let aiMode = "market";
$("#aiModeBtns").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  aiMode = btn.dataset.mode;
  $$("#aiModeBtns .opt").forEach((b) => b.classList.toggle("active", b === btn));
  $("#aiInput").placeholder = aiMode === "stock" ? "输入股票代码，如 300432"
    : aiMode === "custom" ? "输入你的问题，如：新能源板块还能持有吗"
    : "大盘综述无需输入，直接点击开始分析";
});

async function loadAiConfig() {
  try {
    const c = await api("/api/ai/config");
    $("#aiBase").value = c.api_base || "";
    $("#aiKey").value = c.api_key || "";
    $("#aiModel").value = c.model || "";
    $("#aiStatus").textContent = c.configured ? "✅ 已配置" : "未配置（使用本地规则分析）";
  } catch (err) { console.warn(err); }
}

window.saveAiConfig = async () => {
  const res = await api("/api/ai/config", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_base: $("#aiBase").value, api_key: $("#aiKey").value, model: $("#aiModel").value }),
  });
  $("#aiStatus").textContent = res.configured ? "✅ 已配置" : "未配置（使用本地规则分析）";
};

window.runAiAnalyze = async () => {
  const input = $("#aiInput").value.trim();
  $("#aiOutput").innerHTML = '<div class="empty">分析中，请稍候…</div>';
  try {
    const d = await api("/api/ai/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: aiMode, code: aiMode === "stock" ? input : "", question: aiMode === "custom" ? input : "" }),
    });
    $("#aiOutput").textContent = d.text;
    $("#aiOutput").innerHTML = `<div class="muted" style="margin-bottom:8px">来源：${esc(d.source)}</div>` + esc(d.text).replace(/\n/g, "<br>");
  } catch (err) { $("#aiOutput").innerHTML = '<div class="empty">分析失败，请检查配置</div>'; }
};

/* AI 小窗 + 右键菜单（FR7-07-4 / FR8-07） */
let ctxStock = null;
let ctxNews = null;
function hideCtxMenu() { $("#ctxMenu").style.display = "none"; }
function showStockCtxItems(show) {
  ["ctxAiItem", "ctxOpenItem", "ctxWatchItem", "ctxWxAi"].forEach((id) => {
    const el = $(`#${id}`); if (el) el.style.display = show ? "" : "none";
  });
  const wx = $("#ctxWx"); if (wx) wx.style.display = show ? "" : "none";
  const sub = document.querySelector(".ctx-sub"); if (sub) sub.style.display = show ? "" : "none";
  $("#ctxNewsAi").style.display = show ? "none" : "";
}

document.addEventListener("contextmenu", async (e) => {
  const newsEl = e.target.closest(".news-item[data-news]");
  const el = e.target.closest("[data-code], [onclick]");
  const m = el && ((el.dataset && el.dataset.code && { code: el.dataset.code, name: el.dataset.name })
    || (() => { const g = /openStock\('([^']+)','([^']*)'\)/.exec(el.getAttribute("onclick") || "");
      return g ? { code: g[1], name: g[2] } : null; })());
  if (!m && !newsEl) { hideCtxMenu(); return; }
  e.preventDefault();
  const menu = $("#ctxMenu");
  menu.style.display = "";
  menu.style.left = Math.min(e.clientX, window.innerWidth - 220) + "px";
  menu.style.top = Math.min(e.clientY, window.innerHeight - 280) + "px";
  if (newsEl && !m) {
    ctxStock = null;
    ctxNews = { text: newsEl.dataset.news, impact: newsEl.dataset.impact || "" };
    showStockCtxItems(false);
    return;
  }
  ctxNews = newsEl ? { text: newsEl.dataset.news, impact: newsEl.dataset.impact || "" } : null;
  ctxStock = m;
  showStockCtxItems(true);
  $("#ctxNewsAi").style.display = ctxNews ? "" : "none";
  $("#ctxWatchItem").textContent = watchCodes.has(m.code) ? "☆ 移出自选" : "⭐ 加入自选";
  try {
    const wx = await api(`/api/wuxing?code=${m.code}`);
    $$("#ctxWx input").forEach((inp) => { inp.checked = (wx.tags || []).includes(inp.value); });
  } catch { $$("#ctxWx input").forEach((inp) => { inp.checked = false; }); }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#ctxMenu")) hideCtxMenu();
});
$("#ctxAiItem").addEventListener("click", () => {
  if (ctxStock) openAiModal(ctxStock.code, ctxStock.name);
  hideCtxMenu();
});
$("#ctxOpenItem").addEventListener("click", () => {
  if (ctxStock) openStock(ctxStock.code, ctxStock.name);
  hideCtxMenu();
});
$("#ctxWatchItem").addEventListener("click", async () => {
  if (!ctxStock) return;
  if (watchCodes.has(ctxStock.code)) {
    await post(`/api/watchlist/remove?code=${ctxStock.code}`);
    watchCodes.delete(ctxStock.code);
  } else {
    await post(`/api/watchlist/add?code=${encodeURIComponent(ctxStock.code)}`);
    watchCodes.add(ctxStock.code);
  }
  hideCtxMenu();
  if (activeTab === "dashboard") loadDashboard();
});
$("#ctxWx").addEventListener("click", (e) => e.stopPropagation());
$("#ctxWx").addEventListener("change", async () => {
  if (!ctxStock) return;
  const tags = $$("#ctxWx input:checked").map((i) => i.value);
  await api("/api/wuxing/set", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code: ctxStock.code, tags }),
  });
  if (currentStock && currentStock.code === ctxStock.code) loadProfile();
});
$("#ctxWxAi").addEventListener("click", async () => {
  if (!ctxStock) return;
  const { code, name } = ctxStock;
  hideCtxMenu();
  const modal = $("#aiModal");
  modal.style.display = "";
  $("#aiModalTitle").textContent = `✨ 五行分类：${name}（${code}）`;
  $("#aiModalBody").innerHTML = '<div class="empty">判定中，请稍候…</div>';
  try {
    const d = await api("/api/ai/wuxing", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    $("#aiModalBody").innerHTML = `<div class="muted" style="margin-bottom:6px">来源：${esc(d.source)}
      ${d.applied ? " · 已回填标签 " + wxBadges(d.tags) : " · 建议 " + wxBadges(d.tags)}</div>`
      + esc(d.text).replace(/\n/g, "<br>")
      + (d.applied ? "" : `<div style="margin-top:10px"><button class="btn small" id="applyWxBtn">应用建议标签</button></div>`);
    const btn = $("#applyWxBtn");
    if (btn) btn.addEventListener("click", async () => {
      await api("/api/wuxing/set", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, tags: d.tags }),
      });
      btn.textContent = "已应用";
      if (currentStock && currentStock.code === code) loadProfile();
    });
    if (d.applied && currentStock && currentStock.code === code) loadProfile();
  } catch (err) { $("#aiModalBody").innerHTML = '<div class="empty">分类失败</div>'; }
});
$("#ctxNewsAi").addEventListener("click", async () => {
  if (!ctxNews) return;
  hideCtxMenu();
  const modal = $("#aiModal");
  modal.style.display = "";
  $("#aiModalTitle").textContent = "🤖 AI 解读消息";
  $("#aiModalBody").innerHTML = '<div class="empty">分析中，请稍候…</div>';
  try {
    const d = await api("/api/ai/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: "custom",
        question: `请解读以下财经消息的市场影响（利好/利空、可能受益或受损板块、风险提示，分点，200字内）。\n影响评估：${ctxNews.impact}\n消息：${ctxNews.text}`,
      }),
    });
    $("#aiModalBody").innerHTML = `<div class="muted" style="margin-bottom:6px">来源：${esc(d.source)}</div>`
      + esc(d.text).replace(/\n/g, "<br>");
  } catch (err) { $("#aiModalBody").innerHTML = '<div class="empty">分析失败</div>'; }
});

window.openAiModal = async (code, name) => {
  const modal = $("#aiModal");
  modal.style.display = "";
  $("#aiModalTitle").textContent = `🤖 AI 分析：${name}（${code}）`;
  $("#aiModalBody").innerHTML = '<div class="empty">分析中，请稍候…</div>';
  try {
    const d = await api("/api/ai/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "stock", code }),
    });
    $("#aiModalBody").innerHTML = `<div class="muted" style="margin-bottom:6px">来源：${esc(d.source)}</div>` + esc(d.text).replace(/\n/g, "<br>");
  } catch (err) { $("#aiModalBody").innerHTML = '<div class="empty">分析失败</div>'; }
};
window.closeAiModal = () => { $("#aiModal").style.display = "none"; };

// 小窗拖动
(() => {
  const modal = $("#aiModal"), head = $("#aiModalHead");
  let drag = null;
  head.addEventListener("mousedown", (e) => {
    if (e.target.closest("button")) return;
    const rect = modal.getBoundingClientRect();
    drag = { dx: e.clientX - rect.left, dy: e.clientY - rect.top };
    e.preventDefault();
  });
  document.addEventListener("mousemove", (e) => {
    if (!drag) return;
    modal.style.left = Math.max(0, e.clientX - drag.dx) + "px";
    modal.style.top = Math.max(0, e.clientY - drag.dy) + "px";
    modal.style.right = "auto";
    modal.style.bottom = "auto";
  });
  document.addEventListener("mouseup", () => { drag = null; });
})();

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
    const freqOpts = [1, 5, 10, 15, 30, 60, 120, 180];
    const freqLabel = (m) => m >= 60 ? `${m / 60}小时` : `${m}分钟`;
    $("#jobTable").innerHTML = `<table><thead><tr>
      <th>任务</th><th>上次执行</th><th>状态</th><th>下次执行</th><th>频率</th><th>开关</th>
    </tr></thead><tbody>${d.jobs.map((j) => `
      <tr style="cursor:default">
        <td>${esc(j.name)}</td><td>${esc(j.last_run)}</td>
        <td>${j.ok === true ? '<span class="down">正常</span>' : j.ok === false ? `<span class="up">失败</span> <span class="muted">${esc(j.error)}</span>` : '<span class="muted">未执行</span>'}</td>
        <td>${esc(j.next_run)}</td>
        <td>${j.adjustable ? `<span class="btn-group job-freq">${freqOpts.map((m) =>
          `<button class="opt ${j.interval_minutes === m ? "active" : ""}" onclick="setJobInterval('${j.id}',${m})">${freqLabel(m)}</button>`).join("")}</span>`
          : '<span class="muted">定点</span>'}</td>
        <td><button class="btn small ${j.paused ? "" : "ghost"}" onclick="toggleJob('${j.id}')">${j.paused ? "开启" : "暂停"}</button></td>
      </tr>`).join("")}</tbody></table>`;
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

window.verifyKline = async () => {
  $("#verifyResult").innerHTML = '<div class="muted">校验中…</div>';
  try {
    const d = await api("/api/system/verify-kline");
    $("#verifyResult").innerHTML = `
      <div class="kv"><span class="k">总体结果</span>
        <span class="${d.passed ? "down" : "up"}">${d.passed ? "✅ 通过（日K末根与实时价一致）" : "⚠️ 存在偏差"}</span></div>
      ${d.report.map((r) => `<div class="kv">
        <span class="k">${esc(r.name || r.code)}</span>
        <span>${r.ok !== undefined && r.kline_close !== undefined
          ? `K线收 ${fmt(r.kline_close)} vs 实时 ${fmt(r.realtime)} · 偏差 ${fmt(r.diff_pct, 3)}% ${r.ok ? "✅" : "❌"}`
          : esc(r.msg || "")}</span></div>`).join("")}`;
  } catch (err) { $("#verifyResult").innerHTML = '<div class="empty">校验失败</div>'; }
};

window.rebuildMetrics = async () => {
  await post("/api/system/rebuild-metrics");
  const poll = setInterval(async () => {
    const m = await api("/api/system/metrics-state");
    loadMetricsState();
    if (!m.running) clearInterval(poll);
  }, 3000);
};

window.toggleSource = async (name) => { await post(`/api/system/source-toggle?name=${encodeURIComponent(name)}`); loadSettings(); };
window.toggleJob = async (id) => { await post(`/api/system/job-toggle?job_id=${encodeURIComponent(id)}`); loadSettings(); };
window.setJobInterval = async (id, minutes) => {
  await api("/api/system/job-interval", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: id, minutes }),
  });
  loadSettings();
};
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
loaders.sector = loadSector;
loaders.macro = loadMacro;
loaders.commodity = loadCommodities;
loaders.global = loadGlobal;
loaders.recommend = loadRecommend;
loaders.ranks = loadRanks;
loaders.aipick = loadAiPick;
loaders.ai = loadAiConfig;
loaders.settings = loadSettings;

/* 自动刷新（仅刷新当前页，避免无谓请求） */
schedule("dashboard", loadDashboard, 10000);
schedule("stock", () => { if (currentStock && currentPeriod === "minute") loadKline(); }, 30000);
schedule("macro", () => { if (macroSub !== "calendar" && macroSub !== "outlook") loadMacro(); }, 60000);
schedule("sector", loadSector, 60000);
schedule("commodity", loadCommodities, 60000);
schedule("global", loadGlobal, 60000);
schedule("recommend", loadRecommend, 60000);
schedule("ranks", loadRanks, 60000);
schedule("settings", loadSettings, 10000);
setInterval(loadSettingsHealthOnly, 30000);
async function loadSettingsHealthOnly() {
  try {
    const d = await api("/api/system/status");
    const anyCircuit = d.sources.some((s) => s.circuit_open);
    $("#healthDot").className = "dot " + (anyCircuit ? "bad" : "ok");
  } catch { $("#healthDot").className = "dot bad"; }
}

window.addEventListener("resize", () => { klineChart?.resize(); sectorChart?.resize(); ckChart?.resize(); miniChart?.resize(); });

/* 首屏 */
loadDashboard();
initCommodityCats();
loadSettingsHealthOnly();
loadTopAlmanac();
