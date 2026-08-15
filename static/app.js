/* A股量化工具 前端逻辑：全部数据异步拉取，选项卡平铺切换，自动刷新推送 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

function apiUrl(path) {
  /* 同源请求；file:// 直接打开 HTML 时回落到本机后端 */
  if (/^https?:\/\//i.test(path)) return path;
  const abs = path.startsWith("/") ? path : `/${path}`;
  const origin = window.location.origin || "";
  if (!origin || origin === "null" || origin.startsWith("file:")) {
    return "http://127.0.0.1:8000" + abs;
  }
  try {
    const u = new URL(abs, origin);
    return u.pathname + u.search;
  } catch {
    return abs;
  }
}

function showApiBanner(msg) {
  const el = $("#apiBanner");
  if (!el) return;
  el.style.display = msg ? "" : "none";
  el.textContent = msg || "";
}

function polishAiError(d) {
  if (!d || typeof d !== "object") return d;
  const err = String(d.error || "");
  const low = err.toLowerCase();
  if (err && (low.includes("402") || low.includes("insufficient") || low.includes("payment required"))) {
    d.error = "DeepSeek 账户余额不足（402）。请到 https://platform.deepseek.com 充值后再调用，不必改 API 地址或模型名。";
    d.hint = d.hint || "到 DeepSeek 开放平台充值即可。密钥、地址 https://api.deepseek.com/v1、模型 deepseek-chat 都不用改。";
  }
  return d;
}

function aiErrBanner(d, extra) {
  d = polishAiError(d);
  if (!d || !d.error) return "";
  const hint = d.hint ? `<br>建议：${esc(d.hint)}` : "";
  return `<div class="offline-banner" style="margin-bottom:8px">大模型失败：${esc(d.error)}${hint}${extra || ""}</div>`;
}

async function api(path, opts = {}) {
  const resp = await fetch(apiUrl(path), opts);
  const ct = resp.headers.get("content-type") || "";
  if (!ct.includes("application/json")) {
    throw new Error(`${path} → ${resp.status}（返回的不是 JSON，请用 http://主机:8000/ 打开前端，不要直接打开本地 HTML 文件）`);
  }
  const data = await resp.json();
  if (!resp.ok) throw new Error(`${path} → ${resp.status} ${data.detail ? JSON.stringify(data.detail) : ""}`);
  return data;
}
const post = (path) => api(path, { method: "POST" });

const fmt = (v, digits = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "-" : Number(v).toFixed(digits);
const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
const sign = (v) => (v > 0 ? "+" : "");
const pct = (v) => (v === null || v === undefined) ? "-" : `${sign(v)}${fmt(v)}%`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const escAttr = (s) => esc(s).replace(/[\r\n]+/g, " ");

const UI_KEY = "uiPrefs";
const UI_THEMES = [
  { id: "night", name: "黑夜", group: "昼夜" },
  { id: "day", name: "白天", group: "昼夜" },
  { id: "dawn", name: "拂晓", group: "配色" },
  { id: "ocean", name: "深海", group: "配色" },
  { id: "forest", name: "竹林", group: "配色" },
  { id: "violet", name: "紫霞", group: "配色" },
  { id: "gold", name: "墨金", group: "配色" },
];
let uiPrefs = { theme: "night", fontScale: 1 };

function clampFontScale(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return 1;
  return Math.max(0.8, Math.min(1.5, Math.round(n * 20) / 20));
}
function loadUiPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem(UI_KEY) || "{}");
    if (p.theme && UI_THEMES.some((t) => t.id === p.theme)) uiPrefs.theme = p.theme;
    if (p.fontScale != null) uiPrefs.fontScale = clampFontScale(p.fontScale);
  } catch { /* ignore */ }
}
function saveUiPrefs() {
  try { localStorage.setItem(UI_KEY, JSON.stringify(uiPrefs)); } catch { /* ignore */ }
}
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback || "";
}
function fs(n) {
  return Math.max(8, Math.round(Number(n) * (uiPrefs.fontScale || 1)));
}
function cp() {
  return {
    card: cssVar("--card", "#1a2230"),
    border: cssVar("--border", "#2a3548"),
    text: cssVar("--text", "#dbe4f0"),
    muted: cssVar("--muted", "#7d8aa0"),
    split: cssVar("--chart-split", "#202a3b"),
    accent: cssVar("--accent", "#4a9eff"),
    up: cssVar("--up", "#ff5252"),
    down: cssVar("--down", "#26c281"),
    hover: cssVar("--card-hover", "#202a3b"),
    bg: cssVar("--bg", "#0d1117"),
  };
}
function pxHtml(v, pct, digits = 2, extraClass = "") {
  const n = (v === null || v === undefined || Number.isNaN(Number(v))) ? null : Number(v);
  const text = n == null ? "-" : fmt(n, digits);
  const extra = extraClass ? ` ${extraClass}` : "";
  return `<span class="px ${cls(pct)}${extra}">${text}</span>`;
}
function ttStyle() {
  const c = cp();
  return { backgroundColor: c.card, borderColor: c.border, textStyle: { color: c.text, fontSize: fs(12) } };
}
function candleStyle() {
  const c = cp();
  return { color: c.up, color0: c.down, borderColor: c.up, borderColor0: c.down };
}
function makeChart(el) {
  if (!el) return null;
  return echarts.init(el, null, { renderer: "canvas" });
}
function disposeChart(c) {
  try { if (c && !c.isDisposed()) c.dispose(); } catch { /* ignore */ }
  return null;
}
function applyUiPrefs(refresh = true) {
  const root = document.documentElement;
  root.setAttribute("data-theme", uiPrefs.theme || "night");
  root.style.setProperty("--font-scale", String(uiPrefs.fontScale || 1));
  saveUiPrefs();
  const moon = $("#themeQuickBtn");
  if (moon) moon.textContent = uiPrefs.theme === "day" || uiPrefs.theme === "dawn" ? "☀️" : "🌙";
  syncAppearanceUi();
  if (refresh) reloadChartsForTheme();
}
function syncAppearanceUi() {
  const label = $("#fontScaleLabel");
  if (label) label.textContent = Math.round((uiPrefs.fontScale || 1) * 100) + "%";
  const range = $("#fontScaleRange");
  if (range) range.value = String(Math.round((uiPrefs.fontScale || 1) * 100));
  $$("#fontScaleBtns .opt").forEach((b) => {
    const s = Number(b.dataset.scale);
    b.classList.toggle("active", Math.abs(s - (uiPrefs.fontScale || 1)) < 0.001);
  });
  const grid = $("#themeGrid");
  if (grid) {
    grid.innerHTML = UI_THEMES.map((t) => `
      <button type="button" class="theme-card ${uiPrefs.theme === t.id ? "active" : ""}" data-theme="${t.id}">
        <div class="tc-name">${esc(t.name)} <span class="muted">${esc(t.group)}</span></div>
        <div class="tc-swatch" data-preview="${t.id}">
          <i style="background:var(--swatch-1)"></i>
          <i style="background:var(--swatch-2)"></i>
          <i style="background:var(--swatch-3)"></i>
        </div>
      </button>`).join("");
    grid.querySelectorAll(".theme-card").forEach((card) => {
      const id = card.dataset.theme;
      card.style.setProperty("--swatch-1", themeSwatch(id, 1));
      card.style.setProperty("--swatch-2", themeSwatch(id, 2));
      card.style.setProperty("--swatch-3", themeSwatch(id, 3));
    });
  }
}
function themeSwatch(id, i) {
  const map = {
    night: ["#0d1117", "#1a2230", "#4a9eff"],
    day: ["#eef2f7", "#ffffff", "#1d6fd8"],
    dawn: ["#f6efe4", "#fffaf2", "#c45c20"],
    ocean: ["#06141f", "#102636", "#2ec6e8"],
    forest: ["#0b120e", "#17241c", "#d4b06a"],
    violet: ["#120c1c", "#241b36", "#b07cff"],
    gold: ["#100e0a", "#241e14", "#d4b06a"],
  };
  return (map[id] || map.night)[i - 1];
}
function reloadChartsForTheme() {
  miniChart = disposeChart(miniChart);
  klineChart = disposeChart(klineChart);
  fundKlineChart = disposeChart(fundKlineChart);
  sectorChart = disposeChart(sectorChart);
  flowBarChart = disposeChart(flowBarChart);
  flowTrendChart = disposeChart(flowTrendChart);
  ckChart = disposeChart(ckChart);
  try { if (typeof echarts !== "undefined") echarts.getInstanceByDom && null; } catch { /* ignore */ }
  if (activeTab === "stock" && currentStock) loadKline();
  if (activeTab === "dashboard") loaders.dashboard?.();
  if (activeTab === "sector") loaders.sector?.();
  if (activeTab === "commodity" && typeof ckState !== "undefined" && ckState.symbol) loadCommodityKline();
}
function setFontScale(v) {
  uiPrefs.fontScale = clampFontScale(v);
  applyUiPrefs(true);
}
function setTheme(id) {
  if (!UI_THEMES.some((t) => t.id === id)) return;
  uiPrefs.theme = id;
  applyUiPrefs(true);
}
loadUiPrefs();
applyUiPrefs(false);
function planPickedHtml(row, kind) {
  const side = (row && row.side) || kind || "buy";
  const clsName = side === "sell" ? "sell" : "buy";
  const labels = Array.isArray(row && row.plan_labels) ? row.plan_labels.filter(Boolean) : [];
  if (labels.length) {
    return `<div class="plan-picked">${labels.map((l) => `<span class="plan-badge-full ${clsName}">${esc(l)}</span>`).join("")}<span class="plan-picked-verb">选出</span></div>`;
  }
  const text = (row && (row.picked_text || row.picked_by)) || "";
  return text ? `<div class="plan-picked"><span class="plan-picked-verb">${esc(text)}</span></div>` : "";
}
function signalLevelsHtml(r) {
  if (!r || (r.entry_px == null && r.take_px == null && r.stop_px == null)) return "";
  const retTxt = (v) => (v == null || v === "") ? "待收盘验证" : pct(v);
  return `<div class="signal-levels">
    <span>入场参考 <b>${fmt(r.entry_px)}</b></span>
    <span>止盈 <b>${fmt(r.take_px)}</b></span>
    <span>止损 <b>${fmt(r.stop_px)}</b></span>
    <span>+1日 ${retTxt(r.ret_1)}</span>
    <span>+5日 ${retTxt(r.ret_5)}</span>
  </div>`;
}
function signalContextHtml(r) {
  const board = r.board_text || r.industry || "—";
  const wx = wxBadges(r.wuxing) || "—";
  const heat = r.heat == null ? "—" : `${fmt(r.heat, 0)} ${r.heat_level || ""}`.trim();
  const sec = r.sector_hot == null ? "—" : `${fmt(r.sector_hot, 1)} ${r.sector_hot_level || ""}`.trim();
  return `<div class="signal-extra">
    <span>现价 ${pxHtml(r.price, r.pct)}</span>
    <span class="${cls(r.pct)}">${pct(r.pct)}</span>
    <span>板块 ${esc(board)}</span>
    <span>五行 ${wx}</span>
    <span>个股热度 <b>${esc(heat)}</b></span>
    <span>板块热度 <b>${esc(sec)}</b></span>
  </div>${signalLevelsHtml(r)}`;
}
const wxBadges = (tags) => (tags && tags.length)
  ? tags.map((t) => `<span class="wx-badge wx-${esc(t)}">${esc(t)}</span>`).join("") : "";
function beijingYMD(d) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(d || new Date());
}
function addCalendarDays(ymd, n) {
  const [y, m, d] = String(ymd).split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + Number(n || 0)));
  return dt.toISOString().slice(0, 10);
}
function weekStartYMD(ymd) {
  const s = String(ymd || "").slice(0, 10);
  const [y, m, d] = s.split("-").map(Number);
  if (!y || !m || !d) return "";
  const dt = new Date(Date.UTC(y, m - 1, d));
  const day = dt.getUTCDay() || 7;
  dt.setUTCDate(dt.getUTCDate() - day + 1);
  return dt.toISOString().slice(0, 10);
}
function finBadge(r) {
  const g = r && r.finance_grade;
  if (!g) return `<span class="fin-badge fin-none" title="暂无财报">—</span>`;
  const tip = r.finance_summary || `财报评级 ${g}`;
  return `<span class="fin-badge fin-${esc(g)}" title="${esc(tip)}">${esc(g)}</span>`;
}
function wanYi(v) {
  if (v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  const abs = Math.abs(n);
  if (abs >= 10000) return `${(n / 10000).toFixed(2)}亿`;
  if (abs >= 100) return `${n.toFixed(0)}万`;
  return `${n.toFixed(2)}万`;
}
function flowMetricHeaders() {
  return `<th title="腾讯主力资金流入（万元）">主力买入</th>`
    + `<th title="腾讯主力资金流出（万元）">主力卖出</th>`
    + `<th title="成交额减主力买入的估算，非逐笔">散户买入</th>`
    + `<th title="成交额减主力卖出的估算，非逐笔">散户卖出</th>`
    + `<th title="主力买入/(主力买入+主力卖出)">主力买比</th>`;
}
function flowMetricCells(r) {
  const ratio = r && r.main_buy_ratio;
  return `<td class="num ${cls(r.main_buy)}">${wanYi(r.main_buy)}</td>`
    + `<td class="num ${(r.main_sell || 0) > 0 ? "down" : "flat"}">${wanYi(r.main_sell)}</td>`
    + `<td class="num ${cls(r.retail_buy)}">${wanYi(r.retail_buy)}</td>`
    + `<td class="num ${(r.retail_sell || 0) > 0 ? "down" : "flat"}">${wanYi(r.retail_sell)}</td>`
    + `<td class="num">${ratio === null || ratio === undefined ? "-" : fmt(ratio, 1) + "%"}</td>`;
}
const FLOW_NOTE = "散户买入/卖出为成交额与主力差额估算，非逐笔；主力买比=买入/(买入+卖出)。";
let watchCodes = new Set();
let watchCodesLoaded = false;

function isWatched(code, hint) {
  if (!code) return false;
  if (watchCodes.has(code)) return true;
  if (watchCodesLoaded) return false;
  return !!hint;
}

function watchBtnHtml(code, name, hint) {
  if (!code) return "";
  const on = isWatched(code, hint);
  const label = on ? "已加自选" : "+自选";
  return `<button class="btn small js-watch-btn ${on ? "ghost" : ""}" type="button" data-code="${escAttr(code)}" data-name="${escAttr(name || "")}" data-watched="${on ? "1" : "0"}" title="${on ? "已在自选，点击可移出" : "加入自选"}" onclick="event.stopPropagation(); toggleWatchCode('${esc(code)}','${esc(name || "")}')">${label}</button>`;
}

function paintWatchBtn(btn, code) {
  if (!btn || !code) return;
  const on = isWatched(code);
  btn.dataset.watched = on ? "1" : "0";
  btn.textContent = on ? "已加自选" : "+自选";
  btn.title = on ? "已在自选，点击可移出" : "加入自选";
  btn.classList.toggle("ghost", on);
}

function refreshWatchTabCount() {
  const tab = $("#dashWatchTab");
  if (!tab || !watchCodesLoaded) return;
  tab.textContent = watchCodes.size ? `自选（${watchCodes.size}）` : "自选";
}

function syncWatchButtons() {
  $$(".js-watch-btn").forEach((btn) => {
    const row = btn.closest("[data-code]");
    const code = btn.dataset.code || (row && row.dataset.code) || "";
    paintWatchBtn(btn, code);
  });
  const ctx = $("#ctxWatchItem");
  if (ctx && ctxStock) {
    ctx.textContent = watchCodes.has(ctxStock.code) ? "☆ 移出自选" : "⭐ 加入自选";
  }
  refreshWatchTabCount();
}

window.toggleWatchCode = async (code, name) => {
  if (!code) return;
  try {
    if (watchCodes.has(code)) {
      await post(`/api/watchlist/remove?code=${encodeURIComponent(code)}`);
      watchCodes.delete(code);
    } else {
      const res = await post(`/api/watchlist/add?code=${encodeURIComponent(code)}`);
      if (!res.ok) { alert(res.error || "添加失败"); return; }
      watchCodes.add(res.code || code);
    }
    watchCodesLoaded = true;
  } catch (err) {
    alert((err && err.message) ? err.message : "自选操作失败");
    return;
  }
  syncWatchButtons();
  if (activeTab === "dashboard") loadDashboard();
};
window.addWatchCode = (code, name) => window.toggleWatchCode(code, name);

function stockCodeDigits(code) {
  const m = String(code || "").match(/(\d{6})$/);
  return m ? m[1] : String(code || "");
}
async function copyText(text) {
  const t = String(text || "");
  if (!t) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(t);
      return true;
    }
  } catch { /* fallback */ }
  const ta = document.createElement("textarea");
  ta.value = t;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  try { return document.execCommand("copy"); } finally { document.body.removeChild(ta); }
}
async function ensureWatchCodes() {
  if (watchCodesLoaded) return;
  try {
    const list = await api("/api/watchlist");
    const items = Array.isArray(list) ? list : [];
    watchCodes = new Set(items.map((r) => r && r.code).filter(Boolean));
    watchCodesLoaded = true;
  } catch { /* 保持未加载，按钮按 hint 显示 */ }
}
function paintStockHead() {
  const title = $("#klineTitle");
  const copyBtn = $("#copyStockBtn");
  const slot = $("#stockWatchSlot");
  if (!currentStock) {
    if (title) title.textContent = "K线走势";
    if (copyBtn) copyBtn.style.display = "none";
    if (slot) slot.innerHTML = "";
    return;
  }
  const digits = stockCodeDigits(currentStock.code);
  if (title) {
    title.innerHTML = `${esc(currentStock.name)} <span class="stock-code-text">${esc(digits)}</span>`;
  }
  if (copyBtn) {
    copyBtn.style.display = "";
    copyBtn.dataset.code = digits;
  }
  if (slot) {
    if (isIndexCode(currentStock.code)) slot.innerHTML = "";
    else slot.innerHTML = watchBtnHtml(currentStock.code, currentStock.name);
  }
  syncWatchButtons();
}
window.copyStockCode = async () => {
  const btn = $("#copyStockBtn");
  const digits = (btn && btn.dataset.code) || stockCodeDigits(currentStock && currentStock.code);
  if (!digits) return;
  const ok = await copyText(digits);
  if (!btn) return;
  const prev = btn.textContent;
  btn.textContent = ok ? "已复制" : "复制失败";
  setTimeout(() => { if (btn.textContent === "已复制" || btn.textContent === "复制失败") btn.textContent = prev || "复制"; }, 1200);
};

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
    showApiBanner("");
  } catch (err) {
    console.warn(err);
    showApiBanner("前端拉接口失败：" + (err && err.message ? err.message : err));
  }
  loadMarketSentiment();
  loadMarketCycle();
  loadMiniMinute();
  loadForecast();
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
      <div style="font-size:calc(15px * var(--font-scale))">${esc(a.date)}（${esc(a.weekday)}）</div>
      <div class="desc-hl">${esc(a.year_ganzhi)}【${esc(a.zodiac)}年】${esc(a.month_ganzhi)} ${esc(a.day_ganzhi)}
        ${a.solar_term ? `<span class="badge level-3">${esc(a.solar_term)}</span>` : ""}
        ${a.huangdao ? `<span class="badge ${a.huangdao.is_huangdao ? "level-3" : "level-2"}">${esc(a.huangdao.text)}</span>` : ""}</div>
      <div class="muted" style="font-size:calc(12px * var(--font-scale));margin:4px 0">${esc(a.wuxing)} ｜ 财神：${esc(a.caishen)}</div>
      <div class="kv"><span class="k">旺相休囚</span><span style="color:#e8c46b">${esc(a.wangxiang_text)}</span></div>
      <div class="jiugong">${a.jiugong.flat().map((c) =>
        `<div class="${c === "中宫" ? "center" : ""}">${esc(c)}</div>`).join("")}</div>
      <div class="kv"><span class="k">明日预览</span>
        <span>${esc(t.date || "")}（${esc(t.weekday || "")}）${esc(t.day_ganzhi || "")}
        ${t.solar_term ? `<span class="badge level-3">${esc(t.solar_term)}</span>` : ""}
        ${t.festival ? `<span class="badge level-4">${esc(t.festival)}</span>` : ""}
        <span class="muted">财神${esc(t.caishen || "")}</span></span></div>
      <div class="muted" style="font-size:calc(11px * var(--font-scale));margin-top:4px">${esc(a.note)}</div>`;
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
    miniChart ||= makeChart($("#miniMinute"));
    const prices = d.points.map((p) => p[1]);
    const base = d.prev_close || prices[0];
    const last = prices[prices.length - 1];
    const lastPct = (last - base) / base * 100;
    $("#miniMinuteTime").innerHTML = `${pxHtml(last, lastPct)}（${pct(lastPct)}）${d.offline ? " · 离线数据" : ""}`;
    const c = cp();
    miniChart.setOption({
      backgroundColor: "transparent", animation: false,
      grid: { left: 50, right: 10, top: 8, bottom: 20 },
      tooltip: { trigger: "axis", ...ttStyle() },
      xAxis: { type: "category", data: d.points.map((p) => `${p[0].slice(0, 2)}:${p[0].slice(2)}`),
        axisLine: { lineStyle: { color: c.border } }, axisLabel: { fontSize: fs(10), color: c.muted } },
      yAxis: { scale: true, splitLine: { lineStyle: { color: c.split } }, axisLabel: { fontSize: fs(10), color: c.muted } },
      series: [{ type: "line", data: prices, showSymbol: false,
        lineStyle: { color: last >= base ? c.up : c.down, width: 1.5 },
        areaStyle: { color: last >= base ? cssVar("--up-soft") : cssVar("--down-soft") },
        markLine: { symbol: "none", data: [{ yAxis: base }], lineStyle: { color: c.muted, type: "dashed" }, label: { show: false } } }],
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
      <div class="muted" style="font-size:calc(11px * var(--font-scale));margin-top:6px">${esc(f.disclaimer)}</div>`;
  } catch (err) { console.warn(err); }
}

const ALERT_DOCK_KEY = "daatool_alert_dock";
const ALERT_COLLAPSED_H = 64;
const ALERT_EXPAND_H = 500;
const ALERT_MIN_H = 160;
function alertDockMaxH() { return Math.max(ALERT_MIN_H, Math.min(800, window.innerHeight - 100)); }
function readAlertDockState() {
  try { return JSON.parse(localStorage.getItem(ALERT_DOCK_KEY) || "") || {}; }
  catch { return {}; }
}
const alertDockState = (() => {
  const s = readAlertDockState();
  return {
    collapsed: s.collapsed !== false,
    height: Number(s.height) > 0 ? Number(s.height) : ALERT_EXPAND_H,
  };
})();

function applyAlertDock() {
  const dock = $("#alertDock");
  if (!dock) return;
  dock.classList.toggle("collapsed", !!alertDockState.collapsed);
  const h = alertDockState.collapsed
    ? ALERT_COLLAPSED_H
    : Math.min(alertDockMaxH(), Math.max(ALERT_MIN_H, alertDockState.height || ALERT_EXPAND_H));
  if (!alertDockState.collapsed) alertDockState.height = h;
  dock.style.height = h + "px";
  document.documentElement.style.setProperty("--alert-dock-h", h + "px");
  const fold = $("#alertDockFold");
  const expand = $("#alertDockExpand");
  if (fold) fold.style.display = alertDockState.collapsed ? "none" : "";
  if (expand) expand.style.display = alertDockState.collapsed ? "" : "none";
  try { localStorage.setItem(ALERT_DOCK_KEY, JSON.stringify(alertDockState)); } catch { /* ignore */ }
}

function bindAlertDock() {
  $("#alertDockFold")?.addEventListener("click", () => {
    alertDockState.collapsed = true;
    applyAlertDock();
  });
  $("#alertDockExpand")?.addEventListener("click", () => {
    alertDockState.collapsed = false;
    if (!alertDockState.height) alertDockState.height = ALERT_EXPAND_H;
    applyAlertDock();
  });
  const handle = $("#alertDockHandle");
  if (!handle) return;
  handle.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    const startY = e.clientY;
    const startH = alertDockState.collapsed ? ALERT_COLLAPSED_H : (alertDockState.height || ALERT_EXPAND_H);
    const onMove = (ev) => {
      const next = startH + (startY - ev.clientY);
      if (next < 90) {
        alertDockState.collapsed = true;
      } else {
        alertDockState.collapsed = false;
        alertDockState.height = Math.min(alertDockMaxH(), Math.max(ALERT_MIN_H, next));
      }
      applyAlertDock();
    };
    const onUp = () => {
      handle.releasePointerCapture(e.pointerId);
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
  });
}

async function loadAlerts() {
  try {
    const [d, buyLive, sellLive] = await Promise.all([
      api("/api/alerts"),
      api("/api/alerts/buy-points?limit=16").catch(() => ({ items: [] })),
      api("/api/alerts/sell-points?limit=16").catch(() => ({ items: [] })),
    ]);
    const items = d.items || [];
    const liveBuys = buyLive.items || [];
    const liveSells = sellLive.items || [];
    const countEl = $("#alertDockCount");
    if (countEl) {
      const n = liveBuys.length + liveSells.length + items.filter((a) => a.alert_type !== "buy_point" && a.alert_type !== "sell_point").length;
      countEl.textContent = n ? `${n} 条` : "暂无";
    }
    const strip = $("#alertStrip");
    if (strip) {
      const chips = [
        ...liveBuys.slice(0, 8).map((r) => ({ kind: "buy_point", type_name: "买点关注", title: `${r.name} ${r.picked_text || ""}`.trim() })),
        ...liveSells.slice(0, 8).map((r) => ({ kind: "sell_point", type_name: "卖点警示", title: `${r.name} ${r.picked_text || ""}`.trim() })),
      ];
      strip.innerHTML = chips.length
        ? chips.map((a) =>
          `<span class="alert-chip"><span class="badge at-${a.kind}">${esc(a.type_name)}</span>`
          + `<span>${esc(a.title)}</span></span>`).join("")
        : `<span class="muted">暂无提醒，请在设置→选股策略勾选方案</span>`;
    }
    const feed = $("#alertFeed");
    if (!feed) return;
    const signalItem = (r, kind) => {
      const code = r.code || "";
      const name = r.name || "";
      const watchBtn = code ? watchBtnHtml(code, name, r.in_watchlist) : "";
      const attrs = code ? ` data-code="${esc(code)}" data-name="${esc(name)}"` : "";
      const typeName = kind === "buy" ? "买点关注" : "卖点警示";
      const typeCls = kind === "buy" ? "buy_point" : "sell_point";
      return `
      <div class="alert-item"${attrs}>
        <div class="body">
          <span class="badge at-${typeCls}">${typeName}</span>
          <b>${esc(name)}</b> <span class="muted">${esc(code)}</span>
          ${pxHtml(r.price, r.pct)}
          <span class="num ${cls(r.pct)}">${pct(r.pct)}</span>
          ${planPickedHtml(r, kind)}
          ${signalContextHtml(r)}
          <div class="advice-summary">${esc(r.advice_summary || r.advice || r.hit_action || "")}</div>
        </div>
        ${watchBtn}
      </div>`;
    };
    const item = (a) => {
      const code = a.code || "";
      const name = a.name || "";
      const canStock = !!(code && (a.alert_type === "buy_point" || a.alert_type === "sell_point"));
      const watchBtn = canStock ? watchBtnHtml(code, name, a.in_watchlist) : "";
      const attrs = canStock ? ` data-code="${esc(code)}" data-name="${esc(name)}"` : "";
      return `
      <div class="alert-item"${attrs}>
        <span class="time">${esc((a.created_at || "").slice(5, 16).replace("T", " "))}</span>
        <div class="body">
          <span class="badge at-${a.alert_type}">${esc(a.type_name)}</span> ${esc(a.title)}
          ${planPickedHtml(a, a.side || (a.alert_type === "sell_point" ? "sell" : "buy"))}
          ${a.detail ? `<div class="detail">${esc(a.detail)}</div>` : ""}
        </div>
        ${watchBtn}
      </div>`;
    };
    const others = items.filter((a) => a.alert_type !== "buy_point" && a.alert_type !== "sell_point");
    if (!watchCodesLoaded) {
      for (const r of [...liveBuys, ...liveSells, ...items]) {
        if (r.in_watchlist && r.code) watchCodes.add(r.code);
      }
    }
    const buyHtml = liveBuys.length ? liveBuys.map((r) => signalItem(r, "buy")).join("") : '<div class="empty">暂无买点</div>';
    const sellHtml = liveSells.length ? liveSells.map((r) => signalItem(r, "sell")).join("") : '<div class="empty">暂无卖点</div>';
    feed.innerHTML = `
      <div class="alert-cols">
        <div><div class="alert-col-title buy">最佳买点 <span class="muted">${esc(buyLive.executing || "")}</span></div>${buyHtml}</div>
        <div><div class="alert-col-title sell">最佳卖点 <span class="muted">${esc(sellLive.executing || "")}</span></div>${sellHtml}</div>
      </div>
      ${others.length ? `<div class="alert-other">${others.map(item).join("")}</div>` : ""}`;
    syncWatchButtons();
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
      <div class="name">${esc(q.name)} <span class="muted" style="font-size:calc(10px * var(--font-scale))">K线 ›</span></div>
      <div class="price">${pxHtml(q.price, q.pct)}</div>
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
        <span><span class="badge ${c.stance_css}" style="font-size:calc(13px * var(--font-scale));padding:3px 12px">${esc(c.stance)}</span>
        <span class="muted">恐慌指数 ${fmt(c.panic_index, 0)}</span></span></div>
      <div class="muted" style="font-size:calc(12px * var(--font-scale));margin-bottom:4px">${esc(c.stance_desc)}</div>
      <div class="kv"><span class="k">大周期阶段</span>
        <span><span class="badge ${c.stage_css}">${esc(c.stage)}</span></span></div>
      <div class="kv"><span class="k">大盘量能</span><span>${esc(c.vol_desc)}（5日/20日均量比 ${fmt(c.vol_ratio)}）</span></div>
      <div class="muted" style="font-size:calc(12px * var(--font-scale))">${esc(c.stage_desc)} · 市场宽度 ${fmt(c.breadth, 0)}% · 年化波动 ${fmt(c.volatility20, 0)}%${
        c.consec_days ? ` · ${c.consec_days > 0 ? "连涨" + c.consec_days + "日" : "连跌" + Math.abs(c.consec_days) + "日"}` : ""}</div>`;
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
  watchCodes = new Set((list || []).map((r) => r.code).filter(Boolean));
  watchCodesLoaded = true;
  refreshWatchTabCount();
  if (!list.length) {
    $("#watchTable").innerHTML = '<div class="empty">暂无自选股</div>';
    syncWatchButtons();
    return;
  }
  $("#watchTable").innerHTML = `<table><thead><tr>
    <th>代码</th><th>名称</th><th>五行</th><th>财报</th><th>最新价</th><th>涨跌幅</th><th>涨跌额</th>
    <th>成交量(手)</th><th>成交额(万)</th><th>量比</th>${flowMetricHeaders()}<th>换手%</th><th>振幅%</th><th>操作</th>
  </tr></thead><tbody>${list.map((r) => `
    <tr data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
      <td>${r.pinned ? "📌 " : ""}${r.code}</td><td>${esc(r.name)}</td>
      <td>${wxBadges(r.wuxing)}</td>
      <td>${finBadge(r)}</td>
      <td class="num">${pxHtml(r.price, r.pct)}</td>
      <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
      <td class="num ${cls(r.pct)}">${sign(r.change)}${fmt(r.change)}</td>
      <td class="num">${fmt(r.volume, 0)}</td><td class="num">${fmt(r.amount, 0)}</td>
      <td class="num">${fmt(r.volume_ratio)}</td>${flowMetricCells(r)}
      <td class="num">${fmt(r.turnover_rate)}</td>
      <td class="num">${fmt(r.amplitude)}</td>
      <td onclick="event.stopPropagation()">
        <button class="btn small ghost" onclick="pinWatch('${r.code}')">置顶</button>
        <button class="btn small danger" onclick="removeWatch('${r.code}')">删除</button>
      </td>
    </tr>`).join("")}</tbody></table>
    <div class="muted" style="margin-top:6px;font-size:calc(12px * var(--font-scale))">${FLOW_NOTE}</div>`;
  syncWatchButtons();
}

window.addWatch = async () => {
  const code = $("#watchInput").value.trim();
  if (!code) return;
  const res = await post(`/api/watchlist/add?code=${encodeURIComponent(code)}`);
  if (!res.ok) { alert(res.error); return; }
  $("#watchInput").value = "";
  watchCodes.add(res.code || code);
  watchCodesLoaded = true;
  syncWatchButtons();
  loadDashboard();
};
window.removeWatch = async (code) => {
  await post(`/api/watchlist/remove?code=${encodeURIComponent(code)}`);
  watchCodes.delete(code);
  syncWatchButtons();
  loadDashboard();
};
window.pinWatch = async (code) => { await post(`/api/watchlist/pin?code=${code}`); loadDashboard(); };

/* ---------------- 个股分析 ---------------- */
let currentStock = null;
let currentPeriod = "minute";
let klineChart = null;
let fundKlineChart = null;
let lastSearchRows = [];

function paintSearchResults(rows) {
  const box = $("#searchResults");
  if (!box) return;
  lastSearchRows = Array.isArray(rows) ? rows : [];
  box.innerHTML = lastSearchRows.length ? lastSearchRows.map((r) => `
    <div class="sr-item" onclick="openStock('${r.code}','${esc(r.name)}')">
      <span>${esc(r.name)} <span class="muted">${r.code} · ${esc(r.board || "")}</span></span>
      <span class="num">${pxHtml(r.price, r.pct)} ${pct(r.pct)}</span>
    </div>`).join("") : '<div class="sr-item muted">未找到，可先在设置页执行全量同步</div>';
  box.classList.add("show");
}

$("#stockSearch")?.addEventListener("input", debounce(async (e) => {
  const q = e.target.value.trim();
  const box = $("#searchResults");
  if (!q) { box?.classList.remove("show"); lastSearchRows = []; return; }
  const rows = await api(`/api/search?q=${encodeURIComponent(q)}`);
  paintSearchResults(rows);
}, 250));
$("#stockSearch")?.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const first = lastSearchRows[0];
  if (first) {
    e.preventDefault();
    openStock(first.code, first.name);
  }
});
$("#topSearchBtn")?.addEventListener("click", (e) => {
  e.preventDefault();
  const inp = $("#stockSearch");
  inp?.focus();
  inp?.select();
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-wrap")) $("#searchResults")?.classList.remove("show");
});

$("#periodBtns").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  currentPeriod = btn.dataset.period;
  $$("#periodBtns .opt").forEach((b) => b.classList.toggle("active", b === btn));
  if (currentStock) loadKline();
});

$("#copyStockBtn")?.addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  copyStockCode();
});

window.openStock = (code, name) => {
  currentStock = { code, name };
  stockAnalysisCache = null;
  stockFinanceCache = null;
  stockAnnounceCache = null;
  $("#searchResults").classList.remove("show");
  $$("#mainTabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === "stock"));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-stock"));
  activeTab = "stock";
  ensureWatchCodes().then(() => paintStockHead());
  paintStockHead();
  loadKline();
  const holdersCard = $("#holdersCard");
  if (isIndexCode(code)) {
    $("#stockProfile").innerHTML = "";
    const mini = $("#holdersMini");
    if (mini) mini.innerHTML = "";
    if (holdersCard) holdersCard.style.display = "none";
    loadIndexPanel();
  } else {
    if (holdersCard) {
      holdersCard.style.display = "";
      $("#holdersMeta").textContent = "";
      $("#holdersSec").innerHTML = '<div class="empty">持股数据加载中…</div>';
      const mini = $("#holdersMini");
      if (mini) mini.innerHTML = '<div class="hold-mini"><span class="muted">持股摘要加载中…</span></div>';
    }
    loadProfile();
    loadAnalysis();
    loadHolders();
    loadStockAnnouncements();
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
        <div class="muted" style="margin-top:6px;font-size:calc(12px * var(--font-scale))">${esc(p.desc)}${wx.note ? " · " + esc(wx.note) : ""}</div>
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
        <span class="badge ${c.stage_css}" style="font-size:calc(16px * var(--font-scale));padding:6px 18px">${esc(c.stage)}</span>
        <span class="badge ${c.stance_css}" style="font-size:calc(16px * var(--font-scale));padding:6px 18px">${esc(c.stance)}姿态</span>
        <div class="muted" style="margin-top:8px">${esc(c.stage_desc)} · ${esc(c.stance_desc)}</div>
      </div>
      <div class="kv"><span class="k">恐慌指数</span><span class="num"><b>${fmt(c.panic_index, 0)}</b> / 100（年化波动 ${fmt(c.volatility20, 0)}%）</span></div>
      <div class="kv"><span class="k">上证指数</span><span class="num">${fmt(c.index_price)}</span></div>
      <div class="kv"><span class="k">大盘量能</span><span>${esc(c.vol_desc)}（${fmt(c.vol_ratio)}）</span></div>
      <div class="kv"><span class="k">市场宽度</span><span class="num">${fmt(c.breadth, 0)}% 上涨</span></div>
      <hr style="border-color:var(--border);margin:10px 0">
      <div class="card-title" style="font-size:calc(13px * var(--font-scale))">研判信号</div>
      ${c.signals.map((s) => `<div class="kv">
        <span class="k">${s.ok ? "🟢" : "🔴"} ${esc(s.name)}</span></div>
        <div class="muted" style="font-size:calc(12px * var(--font-scale));margin:-4px 0 6px">${esc(s.text)}</div>`).join("")}
      <div class="muted" style="font-size:calc(11px * var(--font-scale));margin-top:8px">周期研判为量化参考，不构成投资建议。</div>`;
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
  klineChart ||= makeChart($("#klineChart"));
  klineChart.showLoading({ maskColor: cssVar("--bg") + "99", textColor: cssVar("--text") });
  try {
    const d = await api(`/api/kline?code=${currentStock.code}&period=${currentPeriod}`);
    klineChart.hideLoading();
    if (currentPeriod === "minute") {
      if (!d.points || !d.points.length) {
        klineChart.clear();
        klineChart.setOption({
          ...baseGrid(),
          title: { text: "暂无分时数据", left: "center", top: "middle", textStyle: { color: cp().muted, fontSize: fs(14) } },
        }, true);
        loadFundKline(0);
        return;
      }
      renderMinute(d);
    } else {
      if (!d.dates || !d.dates.length) {
        klineChart.clear();
        klineChart.setOption({
          ...baseGrid(),
          title: { text: "暂无K线数据", left: "center", top: "middle", textStyle: { color: cp().muted, fontSize: fs(14) } },
        }, true);
        loadFundKline(0);
        return;
      }
      renderCandle(d);
    }
    loadFundKline((d.dates || d.points || []).length);
  } catch (err) { klineChart.hideLoading(); console.warn(err); loadFundKline(0); }
}

function baseGrid() {
  return { backgroundColor: "transparent", animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "cross" }, ...ttStyle() } };
}

function renderMinute(d) {
  const prices = d.points.map((p) => p[1]);
  const times = d.points.map((p) => {
    const t = String(p[0] || "");
    return t.length >= 4 ? `${t.slice(0, 2)}:${t.slice(2, 4)}` : t;
  });
  const base = d.prev_close || prices[0];
  const c = cp();
  klineChart.setOption({
    ...baseGrid(),
    grid: [{ left: 55, right: 20, top: 20, bottom: 40 }],
    xAxis: { type: "category", data: times, axisLine: { lineStyle: { color: c.border } }, axisLabel: { color: c.muted } },
    yAxis: { scale: true, splitLine: { lineStyle: { color: c.split } },
      axisLabel: { color: c.muted, formatter: (v) => (v == null || Number.isNaN(v) ? "" : Number(v).toFixed(2)) } },
    dataZoom: [{ type: "inside", start: 0, end: 100 }],
    series: [
      { type: "line", data: prices, showSymbol: false, lineStyle: { color: c.accent, width: 1.4 },
        areaStyle: { color: cssVar("--accent-soft") },
        markLine: { symbol: "none", data: [{ yAxis: base }], lineStyle: { color: c.muted, type: "dashed" }, label: { show: false } } },
    ],
  }, true);
}

function candleOHLC(k) {
  if (!k || k.length < 4) return k;
  const o = k[0], c = k[1], a = Number(k[2]), b = Number(k[3]);
  return [o, c, Math.min(a, b), Math.max(a, b)];
}
function zoomStart(n) {
  if (!n || n <= 20) return 0;
  if (n <= 60) return 15;
  if (n <= 120) return 40;
  return 60;
}

function renderCandle(d) {
  const candles = (d.kline || []).map(candleOHLC);
  const maSeries = Object.entries(d.ma || {}).map(([n, values], i) => ({
    name: `MA${n}`, type: "line", data: values, showSymbol: false, smooth: true,
    lineStyle: { width: 1, color: ["#e8c46b", "#4a9eff", "#c678dd", "#56b6c2"][i] },
  }));
  const pal = cp();
  const volColors = candles.map((k) => (k[1] >= k[0] ? pal.up : pal.down));
  const n = (d.dates || []).length;
  const start = zoomStart(n);
  const early = n > 0 && n <= 12 && currentPeriod === "day";
  klineChart.setOption({
    ...baseGrid(),
    title: early ? {
      text: `上市初期仅 ${n} 根日K，已展示全部`,
      left: 58, top: 4, textStyle: { color: pal.muted, fontSize: fs(11), fontWeight: 400 },
    } : undefined,
    legend: { data: maSeries.map((s) => s.name), textStyle: { color: pal.muted, fontSize: fs(12) }, top: 0 },
    grid: [
      { left: 55, right: 20, top: 28, height: "52%" },
      { left: 55, right: 20, top: "66%", height: "12%" },
      { left: 55, right: 20, top: "82%", height: "12%" },
    ],
    xAxis: [
      { type: "category", data: d.dates, gridIndex: 0, axisLine: { lineStyle: { color: pal.border } } },
      { type: "category", data: d.dates, gridIndex: 1, show: false },
      { type: "category", data: d.dates, gridIndex: 2, show: false },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: pal.split } } },
      { gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
      { gridIndex: 2, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1, 2], start, end: 100 },
      { type: "slider", xAxisIndex: [0, 1, 2], top: "96%", height: 14, borderColor: pal.border, start, end: 100 },
    ],
    series: [
      { name: "K线", type: "candlestick", data: candles, itemStyle: candleStyle() },
      ...maSeries,
      { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: d.volumes,
        itemStyle: { color: (p) => volColors[p.dataIndex] } },
      { name: "MACD", type: "bar", xAxisIndex: 2, yAxisIndex: 2, data: (d.macd && d.macd.bar) || [],
        itemStyle: { color: (p) => (p.value >= 0 ? "#ff5252" : "#26c281") } },
      { name: "DIF", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: (d.macd && d.macd.dif) || [], showSymbol: false, lineStyle: { width: 1, color: "#e8c46b" } },
      { name: "DEA", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: (d.macd && d.macd.dea) || [], showSymbol: false, lineStyle: { width: 1, color: "#4a9eff" } },
    ],
  }, true);
}

function syncStockCharts(nPrice, nFund) {
  try { if (typeof echarts !== "undefined") echarts.disconnect("stock-k"); } catch { /* ignore */ }
  if (!klineChart || !fundKlineChart || nPrice < 8 || nFund < 8) return;
  if (Math.abs(nPrice - nFund) / Math.max(nPrice, nFund) > 0.35) return;
  klineChart.group = "stock-k";
  fundKlineChart.group = "stock-k";
  echarts.connect("stock-k");
}

function renderFundKline(d) {
  const host = $("#fundKlineChart");
  const meta = $("#fundKlineMeta");
  if (!host) return;
  fundKlineChart ||= makeChart(host);
  const dates = d.dates || [];
  const bars = d.main_net_yi || [];
  const cum = d.cumulative_yi || [];
  const pal = cp();
  if (meta) {
    const src = [d.source, d.note].filter(Boolean).join(" · ");
    meta.textContent = d.empty_reason || src || "";
  }
  if (!dates.length) {
    fundKlineChart.clear();
    fundKlineChart.setOption({
      ...baseGrid(),
      title: {
        text: d.empty_reason || "暂无主力资金",
        left: "center", top: "middle",
        textStyle: { color: pal.muted, fontSize: fs(12), fontWeight: 400, width: 360, overflow: "break" },
      },
    }, true);
    return;
  }
  const start = zoomStart(dates.length);
  fundKlineChart.setOption({
    ...baseGrid(),
    legend: {
      data: ["主力净流入", "累计"],
      textStyle: { color: pal.muted, fontSize: fs(11) }, top: 0,
    },
    grid: [{ left: 55, right: 20, top: 22, bottom: 24 }],
    xAxis: { type: "category", data: dates, axisLine: { lineStyle: { color: pal.border } },
      axisLabel: { color: pal.muted, fontSize: fs(10) } },
    yAxis: { scale: true, splitLine: { lineStyle: { color: pal.split } },
      axisLabel: { color: pal.muted, fontSize: fs(10), formatter: (v) => `${Number(v).toFixed(2)}亿` } },
    dataZoom: [{ type: "inside", start, end: 100 }],
    series: [
      {
        name: "主力净流入", type: "bar", data: bars,
        itemStyle: { color: (p) => (p.value >= 0 ? pal.up : pal.down) },
      },
      {
        name: "累计", type: "line", data: cum, showSymbol: false, yAxisIndex: 0,
        lineStyle: { width: 1.4, color: pal.accent },
      },
    ],
  }, true);
}

async function loadFundKline(nPrice) {
  const host = $("#fundKlineChart");
  const meta = $("#fundKlineMeta");
  if (!host || !currentStock) return;
  fundKlineChart ||= makeChart(host);
  if (isIndexCode(currentStock.code)) {
    renderFundKline({ dates: [], empty_reason: "指数没有个股主力资金流向，不编造。" });
    return;
  }
  const code = currentStock.code;
  const period = currentPeriod;
  fundKlineChart.showLoading({ maskColor: cssVar("--bg") + "99", textColor: cssVar("--text") });
  try {
    const d = await api(`/api/kline/fund?code=${encodeURIComponent(code)}&period=${encodeURIComponent(period)}`);
    if (!currentStock || currentStock.code !== code || currentPeriod !== period) return;
    fundKlineChart.hideLoading();
    renderFundKline(d);
    syncStockCharts(nPrice || 0, (d.dates || []).length);
    requestAnimationFrame(() => fundKlineChart?.resize());
  } catch (err) {
    fundKlineChart.hideLoading();
    if (meta) meta.textContent = "主力资金加载失败";
    renderFundKline({ dates: [], empty_reason: "主力资金加载失败。未用涨跌幅代替资金。" });
    console.warn(err);
  }
}

function gaugeHtml(label, value, levelText, extra = "") {
  if (value === null || value === undefined) return "";
  return `<div class="gauge">
    <div class="g-head"><span class="muted">${label}</span>
      <span><span class="g-num ${value >= 65 ? "up" : value < 40 ? "down" : "flat"}">${value}</span>
      <span class="badge ${value >= 65 ? "level-4" : value >= 45 ? "level-2" : "level-1"}">${esc(levelText)}</span></span></div>
    <div class="gauge-track"><div class="gauge-fill" style="width:${value}%"></div></div>
    ${extra ? `<div class="muted" style="margin-top:3px;font-size:calc(12px * var(--font-scale))">${extra}</div>` : ""}
  </div>`;
}

function attributionHtml(att) {
  if (!att || (!att.reasons.length && att.risk_index === null)) return "";
  const risk = att.risk_index ?? 0;
  return `
    <div class="card-title" style="font-size:calc(14px * var(--font-scale));margin-top:2px">今日${(att.industry_pct ?? 0) >= 0 ? "上涨" : "下跌"}归因
      ${att.industry ? `<span class="muted">所属：${esc(att.industry)}（板块 ${pct(att.industry_pct)}）</span>` : ""}</div>
    ${att.reasons.map((r) => `<div class="reason-item"><span class="rt">[${esc(r.type)}]</span><span class="desc-hl" style="font-size:calc(14px * var(--font-scale))">${esc(r.text)}</span></div>`).join("")}
    ${att.sector_events.length ? `
      <div class="card-title" style="font-size:calc(14px * var(--font-scale));margin-top:10px">板块重大事件</div>
      ${att.sector_events.map((e) => `<div class="reason-item">
        <span class="badge dir-${e.direction}">${esc(e.direction)}</span>
        <span class="badge level-${e.level}">${esc(e.desc)}</span>
        <span style="font-size:calc(13px * var(--font-scale))">${esc(e.text)}…</span></div>`).join("")}` : ""}
    <div class="card-title" style="font-size:calc(14px * var(--font-scale));margin-top:10px">利空风险指数
      <span class="num ${risk >= 60 ? "up" : risk >= 40 ? "flat" : "down"}" style="font-size:calc(20px * var(--font-scale));font-weight:800">${fmt(risk, 0)}</span></div>
    <div class="risk-bar"><div class="p" style="width:${risk}%"></div></div>
    ${risk >= 60 ? `<div class="risk-warn">${esc(att.risk_warning)}</div>`
      : `<div class="desc-hl" style="font-size:calc(13px * var(--font-scale))">${esc(att.risk_warning)}</div>`}
    ${att.risk_factors && att.risk_factors.length ? `<div class="muted" style="font-size:calc(12px * var(--font-scale));margin-top:3px">风险因素：${att.risk_factors.map(esc).join("；")}</div>` : ""}
    <hr style="border-color:var(--border);margin:10px 0">`;
}

function pullSmashHtml(ps) {
  if (!ps || !ps.available) return "";
  const patternBadge = ps.pattern_score
    ? `<span class="badge ${ps.pattern.includes("地天") ? "level-4" : "level-0"}" style="font-size:calc(12px * var(--font-scale))">${esc(ps.pattern)} · 形态分${ps.pattern_score}</span>`
    : `<span class="muted" style="font-size:calc(12px * var(--font-scale))">${esc(ps.pattern)}</span>`;
  return `
    <div class="kv" style="margin-top:4px"><span class="k">盘口行为</span><span>${patternBadge}</span></div>
    <div class="comp-bar"><span class="label">拉升</span>
      <div class="track"><div class="fill" style="width:${ps.pull_score}%;background:var(--up)"></div></div>
      <span class="num">${fmt(ps.pull_score, 0)}</span></div>
    <div class="comp-bar"><span class="label">砸盘</span>
      <div class="track"><div class="fill" style="width:${ps.smash_score}%;background:var(--down)"></div></div>
      <span class="num">${fmt(ps.smash_score, 0)}</span></div>
    <div class="muted" style="font-size:calc(12px * var(--font-scale))">${esc(ps.desc)}</div>
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
        <div class="muted" style="font-size:calc(12px * var(--font-scale))">内外盘比 ${fmt(of.in_out_ratio)} · 委比 ${fmt(of.order_ratio)}%</div>`;
    }
    html += gaugeHtml("暗盘力量", dark.power, dark.level,
      `${esc(dark.desc)}${dark.divergence && dark.divergence !== "无" ? " · <b>" + esc(dark.divergence) + "</b>" : ""}`) + flowBar +
      `<div class="muted" style="font-size:calc(11px * var(--font-scale));margin-top:2px">口径：${esc(dark.scope)}</div>`;
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

function upsideHtml(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) {
    return '<span class="muted">—</span>';
  }
  const n = Number(v);
  const label = n >= 0 ? "上涨空间" : "下跌空间";
  return `<span class="upside-hl ${cls(n)}">${label} ${sign(n)}${fmt(n)}%</span>`;
}

function stanceFromRatings(br) {
  if (br && br.stance && (br.stance.增持 != null || br.stance.中性 != null || br.stance.减持 != null)) {
    return {
      增持: Number(br.stance.增持) || 0,
      中性: Number(br.stance.中性) || 0,
      减持: Number(br.stance.减持) || 0,
    };
  }
  const stance = { 增持: 0, 中性: 0, 减持: 0 };
  const items = (br && Array.isArray(br.items)) ? br.items : [];
  items.forEach((it) => {
    const r = it.rating || it.stance || "";
    if (r === "买入" || r === "增持") stance.增持 += 1;
    else if (r === "中性") stance.中性 += 1;
    else stance.减持 += 1;
  });
  return stance;
}

function stanceBarHtml(stance) {
  const longN = (stance && stance.增持) || 0;
  const midN = (stance && stance.中性) || 0;
  const shortN = (stance && stance.减持) || 0;
  const tot = longN + midN + shortN;
  const w = (n) => (tot ? (n / tot * 100) : 0);
  return `<div class="stance-hist">
    <div class="stance-bar" title="增持(含买入) ${longN} / 中性 ${midN} / 减持(含卖出) ${shortN}">
      <div class="seg long" style="width:${w(longN)}%"></div>
      <div class="seg mid" style="width:${w(midN)}%"></div>
      <div class="seg short" style="width:${w(shortN)}%"></div>
    </div>
    <div class="stance-legend">
      <span class="up">做多 ${longN} 家</span>
      <span class="muted">中性 ${midN} 家</span>
      <span class="down">做空 ${shortN} 家</span>
    </div>
  </div>`;
}

function brokerTargetBlock(br, quotePrice) {
  if (!br) {
    return `<div class="target-box"><div class="muted">暂无机构目标价</div></div>`;
  }
  const price = quotePrice != null && quotePrice !== "" ? Number(quotePrice) : Number(br.current_price);
  const target = br.consensus_target;
  let up = br.upside_pct;
  if ((up == null || Number.isNaN(Number(up))) && Number.isFinite(price) && price && target != null) {
    up = (Number(target) - price) / price * 100;
  }
  return `<div class="target-box">
    <div class="kv"><span class="k">现价</span><span class="num">${Number.isFinite(price) ? fmt(price) : "—"}</span></div>
    <div class="kv"><span class="k">机构一致目标价</span><span class="target-hl">${target != null ? fmt(target) : "—"}</span></div>
    <div class="kv"><span class="k">相对现价</span>${upsideHtml(up)}</div>
  </div>`;
}

function ratingBadge(rating) {
  const r = rating || "";
  const css = (r === "买入" || r === "增持") ? "advice-buy"
    : (r === "减持" || r === "卖出") ? "advice-sell" : "advice-hold";
  return `<span class="badge ${css}" style="font-size:calc(11px * var(--font-scale));padding:1px 8px">${esc(r || "—")}</span>`;
}

function analysisMetricsHtml(d) {
  const r = d && d.rating;
  if (!r) return "";
  if (r.score === null || r.score === undefined) {
    return `<div class="muted" style="margin-top:8px">${esc(r.message || "暂无综合评分数据")}</div>`
      + metrics2Html(d.metrics, d.dark);
  }
  const s = r.snapshot || {};
  return `
      <div class="score-clamp">${esc(r.advice_reason || "")}</div>
      ${attributionHtml(d.attribution)}
      ${Object.entries(r.components || {}).map(([k, v]) => `
        <div class="comp-bar"><span class="label">${k}</span>
          <div class="track"><div class="fill" style="width:${v}%"></div></div>
          <span class="num">${v}</span></div>`).join("")}
      ${pullSmashHtml(d.pull_smash)}
      ${metrics2Html(d.metrics, d.dark)}
      <div class="kv"><span class="k">主力净流入</span><span class="num ${cls(s.main_net_in)}">${fmt((s.main_net_in || 0) / 10000)} 亿</span></div>
      <div class="kv"><span class="k">5日主力净流入</span><span class="num ${cls(s.main_net_in_d5)}">${fmt((s.main_net_in_d5 || 0) / 10000)} 亿</span></div>
      <div class="kv"><span class="k">5日/20日/60日涨幅</span><span class="num">${pct(s.pct_d5)} / ${pct(s.pct_d20)} / ${pct(s.pct_d60)}</span></div>
      <div class="kv"><span class="k">PE(TTM) / PB</span><span class="num">${fmt(s.pe_ttm, 1)} / ${fmt(s.pb)}</span></div>
      <div class="kv"><span class="k">总市值 / 流通市值</span><span class="num">${fmt(s.total_mv, 0)} / ${fmt(s.float_mv, 0)} 亿</span></div>`;
}

function paintScoreCard() {
  const card = $("#scoreCard");
  if (!card || !currentStock || isIndexCode(currentStock.code)) return;
  const d = stockAnalysisCache;
  const f = stockFinanceCache;
  const r = d && d.rating;
  const q = (d && d.quote) || {};
  const br = r && r.broker_ratings;
  const price = q.price != null ? q.price : (br && br.current_price);
  const priceHead = price !== undefined && price !== null ? `
      <div class="price-inline">
        ${pxHtml(price, q.pct, 2)}
        <span class="${cls(q.pct)}" style="font-weight:600;margin-left:6px">${q.change != null ? `${sign(q.change)}${fmt(q.change)} (${pct(q.pct)})` : pct(q.pct)}</span>
      </div>` : "";
  let finHero;
  if (!f) {
    finHero = `<div class="score-hero"><div class="muted">财报加载中…</div></div>`;
  } else if (!f.grade) {
    finHero = `<div class="score-hero">
      <div class="score-num muted">—</div>
      <span class="badge level-2">财报评级</span>
      <div class="score-clamp">${esc(f.summary || f.grade_desc || "暂无财务分析披露")}</div>
    </div>`;
  } else {
    const gcss = f.grade === "A" ? "level-4" : f.grade === "B" ? "level-3" : f.grade === "C" ? "level-2" : "level-1";
    const tone = (f.grade === "A" || f.grade === "B") ? "up" : f.grade === "D" ? "down" : "flat";
    finHero = `<div class="score-hero">
      <div class="score-num ${tone}">${esc(f.grade)}</div>
      <span class="badge ${gcss}">财报评级</span>
      <div class="score-clamp">${esc(f.grade_desc || f.summary || "")}</div>
    </div>`;
  }
  let scoreHero;
  if (!r || r.score === null || r.score === undefined) {
    scoreHero = `<div class="score-hero composite">
      <div class="score-num muted">—</div>
      <span class="badge level-2">综合评分</span>
      <div class="score-clamp">${esc((r && r.message) || (d ? "暂无综合评分" : "评分加载中…"))}</div>
    </div>`;
  } else {
    const tone = r.score >= 55 ? "up" : r.score < 45 ? "down" : "flat";
    scoreHero = `<div class="score-hero composite">
      <div class="score-num ${tone}">${esc(r.score)}</div>
      <span class="badge ${r.grade_css || "level-2"}">综合评分</span>
      <div style="margin-top:4px">
        <span class="badge ${r.grade_css || ""}">${esc(r.grade || "")} · ${esc(r.volume_desc || "")}</span>
        <span class="badge ${r.advice_css || "advice-hold"}" style="font-size:calc(11px * var(--font-scale));padding:2px 8px">${esc(r.advice || "")}</span>
      </div>
    </div>`;
  }
  card.innerHTML = `<div class="card-title">财报评级 · 综合评分</div>
      ${priceHead}
      <div class="score-duo">${finHero}${scoreHero}</div>
      ${brokerTargetBlock(br, price)}
      ${d ? analysisMetricsHtml(d) : ""}`;
}

async function loadAnalysis() {
  const card = $("#scoreCard");
  const code = currentStock && currentStock.code;
  card.innerHTML = '<div class="empty">财报评级计算中…</div>';
  try {
    const d = await api(`/api/analysis?code=${code}`);
    if (!currentStock || currentStock.code !== code) return;
    stockAnalysisCache = d;
    paintScoreCard();
    refreshHoldExtraPanes();
    loadFinance();
  } catch (err) {
    if (!currentStock || currentStock.code !== code) return;
    stockAnalysisCache = null;
    console.warn(err);
    paintScoreCard();
    loadFinance();
  }
}

async function loadFinance() {
  if (!currentStock) return;
  const code = currentStock.code;
  try {
    const f = await api(`/api/finance?code=${code}`);
    if (!currentStock || currentStock.code !== code) return;
    stockFinanceCache = f;
  } catch (err) {
    if (!currentStock || currentStock.code !== code) return;
    stockFinanceCache = { reports: [], summary: "财务数据加载失败", error: String(err && err.message || err) };
    console.warn(err);
  }
  if (!currentStock || currentStock.code !== code) return;
  paintScoreCard();
  refreshHoldExtraPanes();
}

const fmtInt = (v) => (v === null || v === undefined || Number.isNaN(Number(v)))
  ? "-" : Math.round(Number(v)).toLocaleString("zh-CN");
const kindBadge = (kind) => {
  const k = kind || "未标注";
  const css = k === "机构" ? "kind-org" : k === "个人" ? "kind-person" : "kind-na";
  return `<span class="badge ${css}">${esc(k)}</span>`;
};
function holderTable(title, rows, floatHolder) {
  if (!rows || !rows.length) {
    return `<div><div class="hold-sub">${esc(title)}</div><div class="muted" style="font-size:calc(12px * var(--font-scale))">暂无披露</div></div>`;
  }
  const ratioHead = floatHolder ? "占流通比" : "占总股本";
  return `<div>
    <div class="hold-sub">${esc(title)}</div>
    <table><thead><tr>
      <th>名次</th><th>股东</th><th>类型</th><th>${ratioHead}</th><th>持股</th><th>变动</th>
    </tr></thead><tbody>${rows.map((r) => `
      <tr>
        <td>${r.rank || "-"}</td>
        <td><span class="holder-name">${esc(r.name)}</span>${r.shares_type ? ` <span class="muted">${esc(r.shares_type)}</span>` : ""}</td>
        <td>${kindBadge(r.kind)}${r.holder_type ? ` <span class="muted">${esc(r.holder_type)}</span>` : ""}</td>
        <td class="num">${r.ratio !== null && r.ratio !== undefined ? fmt(r.ratio, 2) + "%" : "-"}</td>
        <td class="num">${esc(r.shares_txt || "-")}</td>
        <td class="num">${esc(r.change || "-")}</td>
      </tr>`).join("")}</tbody></table>
  </div>`;
}

function qoqLabel(row) {
  if (!row) return "-";
  if (row.holders_qoq_note) return row.holders_qoq_note;
  if (row.holders_qoq == null) return "-";
  return `${sign(row.holders_qoq)}${fmt(row.holders_qoq, 1)}%`;
}

let holdersData = null;
let holdTab = "structure";
let holdOrgType = "";
let holdStructSub = "top10";
let holdAiBusy = false;
let holdBriefBusy = false;
let stockAnalysisCache = null;
let stockFinanceCache = null;
let stockAnnounceCache = null;

function normalizeHoldTab() {
  if (holdTab === "funds") {
    holdTab = "structure";
    holdStructSub = "funds";
  } else if (holdTab === "org") {
    holdTab = "structure";
    holdStructSub = "org";
  } else if (holdTab === "counts") {
    holdTab = "structure";
    holdStructSub = "counts";
  }
}

function holdTabBtns(h) {
  normalizeHoldTab();
  const unlockN = (h.unlocks || []).length;
  const annN = (stockAnnounceCache && Array.isArray(stockAnnounceCache.items))
    ? stockAnnounceCache.items.length : 0;
  const tabs = [
    ["structure", "持股构成"],
    ["ratings", "机构评级"],
    ["finance", "财务分析"],
    ["unlocks", "限售解禁"],
    ["announce", "公司公告"],
    ["ai", "AI分析结果"],
    ["brief", "AI简明诊断"],
  ];
  return `<div class="btn-group sub-tabs hold-tabs" id="holdTabs">${tabs.map(([id, title]) => {
    const extra = id === "unlocks" && unlockN ? `（${unlockN}）`
      : (id === "announce" && annN ? `（${annN}）` : "");
    const aiOn = id === "ai" && h.ai && h.ai.applied;
    const briefOn = id === "brief" && h.brief && h.brief.applied;
    const mark = aiOn || briefOn ? " · 已存" : "";
    return `<button type="button" class="opt js-hold-tab ${holdTab === id ? "active" : ""}" data-tab="${id}">${title}${extra}${mark}</button>`;
  }).join("")}</div>`;
}

function structurePane(h) {
  const subs = [
    ["top10", "十大股东"],
    ["org", "机构持仓"],
    ["counts", "股东变化"],
    ["funds", "基金持股"],
  ];
  const bar = `<div class="btn-group hold-org-tabs hold-struct-tabs" id="holdStructTabs">${subs.map(([id, title]) =>
    `<button type="button" class="opt js-hold-struct ${holdStructSub === id ? "active" : ""}" data-sub="${id}">${title}</button>`
  ).join("")}</div>`;
  if (holdStructSub === "funds") return bar + fundsPane(h);
  if (holdStructSub === "org") return bar + orgHoldPane(h);
  if (holdStructSub === "counts") return bar + countsPane(h);
  return bar + `<div class="hold-tables">
    ${holderTable("十大股东", h.top10, false)}
    ${holderTable("十大流通股东", h.top10_float, true)}
  </div>`;
}

function ratingsPane() {
  const d = stockAnalysisCache;
  if (!d) return '<div class="empty">机构评级加载中…</div>';
  const br = d.rating && d.rating.broker_ratings;
  if (!br) return '<div class="empty">暂无机构评级</div>';
  const dist = br.distribution || {};
  const items = Array.isArray(br.items) ? br.items : [];
  const stance = stanceFromRatings(br);
  const q = (d.quote && d.quote.price != null) ? d.quote.price : br.current_price;
  return `
    <div class="muted" style="font-size:calc(12px * var(--font-scale));margin-bottom:8px">${esc(br.note || (br.simulated ? "规则模拟·仅供参考" : "机构评级"))}</div>
    ${stanceBarHtml(stance)}
    ${brokerTargetBlock(br, q)}
    <div class="kv"><span class="k">评级分布</span><span>${Object.entries(dist).filter(([, n]) => n > 0).map(([k, n]) => `${k}${n}家`).join(" · ") || "暂无"}</span></div>
    <div class="broker-list">
      ${items.length ? items.map((it) => `<div class="kv broker-row">
        <span class="k"><span class="broker-name">${esc(it.broker)}</span> <span class="muted">${esc(it.type || "")}</span></span>
        <span>${ratingBadge(it.rating)} <span class="num">${fmt(it.target_price)}</span>
          ${it.upside_pct != null ? `<span class="${cls(it.upside_pct)}">${sign(it.upside_pct)}${fmt(it.upside_pct)}%</span>` : ""}
          <span class="muted">${esc((it.date || "").slice(5))}</span></span>
      </div>`).join("") : '<div class="empty">暂无明细</div>'}
    </div>`;
}

function announceItemHtml(a) {
  const ident = a.id || `${a.time || ""}:${(a.text || "").slice(0, 40)}`;
  const key = intelKey("announce", ident);
  const secs = Array.isArray(a.affected_sectors)
    ? a.affected_sectors.filter((s) => typeof s === "string").join(",")
    : String(a.affected_sectors || "");
  const dir = a.direction || "中性";
  const dirCls = dir === "利空" ? "dir-利空" : dir === "利好" ? "dir-利好" : "level-2";
  const t = String(a.time || "");
  const tshow = t.length >= 16 ? t.slice(5, 16) : t;
  return `
      <div class="news-item js-intel-row js-news-item" data-news="${escAttr(a.text)}" data-impact="${escAttr(a.impact_desc || "")}"
        data-title="${escAttr((a.text || "").slice(0, 40))}" data-sectors="${escAttr(secs)}" data-direction="${escAttr(dir)}"
        data-intel-source="announce" data-intel-id="${escAttr(ident)}" data-intel-key="${escAttr(key)}"
        data-intel-title="${escAttr((a.text || "").slice(0, 40))}" data-intel-text="${escAttr((a.text || "").slice(0, 500))}"
        data-intel-time="${escAttr(t)}" data-attention="${escAttr((a.impact_level || 0) * 20)}">
        <span class="time">${esc(tshow)}</span>
        <div class="body">${esc(a.text)}
          <div class="meta">
            <span class="badge sector-tag">${esc(a.tag || "公告")}</span>
            <span class="badge ${dirCls}">${esc(dir)}</span>
            ${a.impact_desc ? `<span class="badge level-${esc(a.impact_level || 1)}">${esc(a.impact_desc)}</span>` : ""}
            ${intelFlagHtml(key)}
            ${intelSectorBadges(key, secs, a.tag, dir)}
          </div>
          ${a.brief ? `<div class="desc-hl" style="font-size:calc(13px * var(--font-scale));margin-top:3px">💡 ${esc(a.brief)}</div>` : ""}
          ${intelReasonLine(key)}
        </div>
      </div>`;
}

function announcePane() {
  const d = stockAnnounceCache;
  if (!d) return '<div class="empty">公司公告加载中…</div>';
  const items = Array.isArray(d.items) ? d.items : [];
  if (!items.length) {
    return `<div class="empty">${esc(d.empty_reason || "暂无匹配的公司公告")}</div>
      <div class="muted" style="font-size:calc(11px * var(--font-scale));margin-top:6px">${esc(d.note || "公告源为 7x24 快讯识别，非交易所全量公告。")}</div>`;
  }
  return `<div class="ann-pane">${items.map(announceItemHtml).join("")}</div>
    <div class="muted" style="font-size:calc(11px * var(--font-scale));margin-top:6px">${esc(d.note || "公告源为 7x24 快讯识别，非交易所全量公告。")}</div>`;
}

async function loadStockAnnouncements() {
  if (!currentStock || isIndexCode(currentStock.code)) return;
  const code = currentStock.code;
  try {
    await refreshIntelAiIndex();
    const d = await api(`/api/announcements?code=${encodeURIComponent(code)}&limit=40`);
    if (!currentStock || currentStock.code !== code) return;
    stockAnnounceCache = d;
  } catch (err) {
    if (!currentStock || currentStock.code !== code) return;
    stockAnnounceCache = { items: [], empty_reason: "公司公告加载失败", note: String(err && err.message || err) };
    console.warn(err);
  }
  refreshHoldExtraPanes();
}

function financePane() {
  const f = stockFinanceCache;
  if (!f) return '<div class="empty">财务数据加载中…</div>';
  if (!f.reports || !f.reports.length) {
    return `<div class="empty">${esc(f.summary || "暂无财务分析披露")}</div>`;
  }
  return `
    <div class="card-title" style="font-size:calc(13px * var(--font-scale));margin-bottom:8px">财务分析
      <span>${f.grade ? `<span class="badge ${f.grade === "A" ? "level-4" : f.grade === "B" ? "level-3" : f.grade === "C" ? "level-2" : "level-1"}">评级 ${f.grade}</span>` : ""}</span>
    </div>
    <div class="muted" style="font-size:calc(12px * var(--font-scale));margin-bottom:6px">${esc(f.summary)}</div>
    <table style="font-size:calc(12px * var(--font-scale))"><thead><tr>
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
    <div class="muted" style="font-size:calc(11px * var(--font-scale));margin-top:4px">数据来源：${esc(f.source || "")}</div>`;
}

function refreshHoldExtraPanes() {
  if (!holdersData) return;
  if (holdTab === "ratings" || holdTab === "finance" || holdTab === "announce") {
    paintHoldersCard(holdersData);
    return;
  }
  const btn = document.querySelector('#holdTabs .js-hold-tab[data-tab="announce"]');
  if (btn) {
    const n = (stockAnnounceCache && stockAnnounceCache.items || []).length;
    btn.textContent = n ? `公司公告（${n}）` : "公司公告";
  }
}

function orgHoldPane(h) {
  const all = h.org_hold || [];
  const types = [["", "全部"], ...all.map((r) => [
    r.org_type,
    `${r.name}${r.count != null ? `（${fmtInt(r.count)}）` : ""}`,
  ])];
  if (holdOrgType && !all.some((r) => r.org_type === holdOrgType)) holdOrgType = "";
  const rows = all.filter((r) => !holdOrgType || r.org_type === holdOrgType);
  const sub = types.length > 1 ? `<div class="btn-group hold-org-tabs" id="holdOrgTabs">${types.map(([id, title]) =>
    `<button type="button" class="opt js-hold-org ${holdOrgType === id ? "active" : ""}" data-org="${esc(id)}">${esc(title)}</button>`
  ).join("")}</div>` : "";
  if (!all.length) {
    return `${sub}<div class="empty">本期未披露机构持仓构成，不编造分类。</div>`;
  }
  return `${sub}
    <table><thead><tr><th>类型</th><th>家数</th><th>持股</th><th>占流通比</th><th>报告期</th></tr></thead>
    <tbody>${rows.map((r) => `
      <tr>
        <td>${esc(r.name)}</td>
        <td class="num">${r.count != null ? fmtInt(r.count) : "-"}</td>
        <td class="num">${esc(r.shares_txt || "-")}</td>
        <td class="num">${r.float_ratio != null ? fmt(r.float_ratio, 2) + "%" : "-"}</td>
        <td>${esc(r.date || "-")}</td>
      </tr>`).join("")}</tbody></table>
    <div class="muted" style="font-size:calc(11px * var(--font-scale));margin-top:6px">子集为已披露机构类型。无明细名单的类型只展示汇总，不编造持有人。</div>`;
}

function countsPane(h) {
  if (!(h.counts || []).length) {
    return '<div class="empty">暂无股东户数变化披露。缺数不编造。</div>';
  }
  return `<table><thead><tr><th>报告期</th><th>股东户数</th><th>环比</th><th>人均流通股</th><th>集中度</th><th>十大股东合计</th></tr></thead>
    <tbody>${h.counts.map((r) => `
      <tr>
        <td>${esc(r.date || "-")}</td>
        <td class="num">${fmtInt(r.holders)}</td>
        <td class="num ${r.holders_qoq_note ? "flat" : cls(r.holders_qoq)}">${esc(qoqLabel(r))}</td>
        <td class="num">${fmtInt(r.avg_free_shares)}</td>
        <td>${esc(r.focus || "-")}</td>
        <td class="num">${r.top10_ratio != null ? fmt(r.top10_ratio, 1) + "%" : "-"}</td>
      </tr>`).join("")}</tbody></table>`;
}

function fundsPane(h) {
  const rows = h.funds || [];
  if (!rows.length) {
    return '<div class="empty">暂无基金持股披露，不编造名单。</div>';
  }
  return `<div class="hold-sub">基金持股数量前 ${rows.length}（最多10）</div>
    <table><thead><tr><th>名次</th><th>基金</th><th>代码</th><th>持股数量</th><th>占总股本</th><th>市值(亿)</th><th>报告期</th></tr></thead>
    <tbody>${rows.map((r) => `
      <tr>
        <td>${r.rank || "-"}</td>
        <td>${esc(r.name)}</td>
        <td>${esc(r.code || "-")}</td>
        <td class="num">${esc(r.shares_txt || "-")}</td>
        <td class="num">${r.ratio != null ? fmt(r.ratio, 3) + "%" : "-"}</td>
        <td class="num">${r.value_yi != null ? fmt(r.value_yi, 2) : "-"}</td>
        <td>${esc(r.date || "-")}</td>
      </tr>`).join("")}</tbody></table>
    <div class="muted" style="font-size:calc(11px * var(--font-scale));margin-top:6px">${esc(h.funds_note || "按持股数量降序取前10。")}</div>`;
}

function unlocksPane(h) {
  if (!(h.unlocks || []).length) {
    return '<div class="empty">暂无限售解禁披露。</div>';
  }
  return `<table><thead><tr><th>解禁日</th><th>数量</th><th>占总股本</th><th>占流通</th><th>类型</th></tr></thead>
    <tbody>${h.unlocks.map((r) => `
      <tr>
        <td>${esc(r.date || "-")}</td>
        <td class="num">${esc(r.shares_txt || "-")}</td>
        <td class="num">${r.total_ratio != null ? fmt(r.total_ratio, 2) + "%" : "-"}</td>
        <td class="num">${r.float_ratio != null ? fmt(r.float_ratio, 2) + "%" : "-"}</td>
        <td>${esc(r.lift_type || "-")}</td>
      </tr>`).join("")}</tbody></table>`;
}

function holderAiPane(h) {
  const ai = h.ai || {};
  const text = (ai.text || "").trim();
  const has = !!(ai.applied && text);
  const when = ai.analyzed_at || ai.updated_at || "";
  const stamp = has
    ? `上次分析 ${esc(when)} · ${esc(ai.source || "")}${ai.asof ? " · 报告期 " + esc(ai.asof) : ""}`
    : "尚未保存大模型分析结果。点「更新AI分析」手动调用；失败不覆盖旧结果、不假装成功。";
  const body = has
    ? `<div class="hold-ai-text">${esc(ai.text).replace(/\n/g, "<br>")}</div>`
    : (text
      ? `<div class="muted" style="margin-bottom:6px">本地说明（未落库）：</div><div class="hold-ai-text">${esc(ai.text).replace(/\n/g, "<br>")}</div>`
      : `<div class="empty">暂无已保存的 AI 分析结果</div>`);
  const lastErr = ai.error
    ? aiErrBanner(ai, ai.kept ? "。已保留上次成功分析，未覆盖。" : "。未假装成功。")
    : "";
  const kws = ai.keywords || [];
  const kwHtml = kws.length
    ? `<div class="ic-kws" style="margin-top:8px">${kws.map((k) => `<span class="kw-chip">${esc(k)}</span>`).join("")}</div>`
    : "";
  return `<div class="hold-ai-bar">
      <div class="muted" style="font-size:calc(12px * var(--font-scale))">${stamp}</div>
      <button type="button" class="btn small" id="holderAiBtn">更新AI分析</button>
    </div>
    <div id="holderAiBanner">${lastErr}</div>
    <div id="holderAiBody">${body}${kwHtml}</div>`;
}

function briefPane(h) {
  const b = h.brief || {};
  const wx = h.wuxing_ai || {};
  const text = (b.text || "").trim();
  const has = !!(b.applied && text);
  const when = b.analyzed_at || b.updated_at || "";
  const stamp = has
    ? `上次诊断 ${esc(when)} · ${esc(b.source || "")}`
    : "尚未保存简明诊断。全站右键「AI 分析该股」会写入这里；也可点「更新AI简明诊断」。失败不覆盖旧结果。";
  const body = has
    ? `<div class="hold-ai-text">${esc(b.text).replace(/\n/g, "<br>")}</div>`
    : (text
      ? `<div class="muted" style="margin-bottom:6px">未落库预览：</div><div class="hold-ai-text">${esc(b.text).replace(/\n/g, "<br>")}</div>`
      : `<div class="empty">暂无已保存的 AI 简明诊断</div>`);
  const lastErr = b.error
    ? aiErrBanner(b, b.kept ? "。已保留上次成功诊断，未覆盖。" : "。未假装成功。")
    : "";
  const wxHas = !!(wx.applied && (wx.text || "").trim());
  const wxBlock = wxHas
    ? `<div class="hold-sub" style="margin-top:16px">五行判定（右键已保存）</div>
       <div class="muted" style="font-size:calc(12px * var(--font-scale));margin-bottom:6px">${esc(wx.analyzed_at || wx.updated_at || "")} · ${esc(wx.source || "")}${wx.tags && wx.tags.length ? " · " + wx.tags.join("、") : ""}</div>
       <div class="hold-ai-text">${esc(wx.text).replace(/\n/g, "<br>")}</div>`
    : `<div class="muted" style="margin-top:14px;font-size:calc(12px * var(--font-scale))">右键「发送AI五行分类」成功后也会保存在本页下方，便于回显。</div>`;
  return `<div class="hold-ai-bar">
      <div class="muted" style="font-size:calc(12px * var(--font-scale))">${stamp}</div>
      <button type="button" class="btn small" id="briefAiBtn">更新AI简明诊断</button>
    </div>
    <div id="briefAiBanner">${lastErr}</div>
    <div id="briefAiBody">${body}</div>
    ${wxBlock}`;
}

function holdPaneHtml(h) {
  normalizeHoldTab();
  if (holdTab === "ratings") return ratingsPane();
  if (holdTab === "finance") return financePane();
  if (holdTab === "unlocks") return unlocksPane(h);
  if (holdTab === "announce") return announcePane();
  if (holdTab === "ai") return holderAiPane(h);
  if (holdTab === "brief") return briefPane(h);
  return structurePane(h);
}

function paintHoldersCard(h) {
  const box = $("#holdersSec");
  if (!box || !h) return;
  if (h.empty) {
    box.innerHTML = `
      ${holdTabBtns(h)}
      <div class="hold-pane" id="holdPane">${holdPaneHtml(h)}</div>
      <div class="muted" style="font-size:calc(11px * var(--font-scale));margin-top:8px">${esc(h.note || "暂无持股数据")} · 仍可查看或更新 AI 简明诊断
        <button class="btn ghost small" type="button" onclick="loadHolders()">重新加载持股</button></div>`;
    return;
  }
  const L = h.latest || {};
  const inst = h.institution_ratio;
  const person = h.person_ratio;
  const ctrl = (h.controller || []).map((c) => c.name + (c.ratio != null ? ` ${fmt(c.ratio, 2)}%` : "")).join("、");
  const qoqText = qoqLabel(L);
  const qoqClass = L.holders_qoq > 0 ? "up" : L.holders_qoq < 0 ? "down" : "";
  const kpis = [
    ["股东户数", L.holders != null ? fmtInt(L.holders) + " 户" : "-"],
    ["户数环比", qoqText],
    ["人均流通股", L.avg_free_shares != null ? fmtInt(L.avg_free_shares) : "-"],
    ["筹码集中度", L.focus || "-"],
    ["十大股东合计", L.top10_ratio != null ? fmt(L.top10_ratio, 1) + "%" : "-"],
    ["实际控制人", ctrl || "-"],
  ];
  const split = (inst != null && person != null) ? `
    <div class="hold-split" title="机构 ${fmt(inst, 2)}% / 个人及其他 ${fmt(person, 2)}%">
      <div class="org" style="width:${Math.max(0, Math.min(100, inst))}%"></div>
      <div class="person" style="width:${Math.max(0, Math.min(100, person))}%"></div>
    </div>
    <div class="hold-legend">
      <span><i class="org"></i>机构 ${fmt(inst, 2)}%${h.institution_count != null ? ` · ${fmtInt(h.institution_count)} 家` : ""}</span>
      <span><i class="person"></i>个人及其他 ${fmt(person, 2)}%</span>
    </div>` : `<div class="muted" style="font-size:calc(12px * var(--font-scale));margin:6px 0 10px">暂无机构/个人持股占比（本期未披露机构持仓构成）</div>`;
  box.innerHTML = `
    <div class="hold-kpis">${kpis.map(([k, v]) => `
      <div class="hold-kpi"><div class="v ${k === "户数环比" ? qoqClass : ""}">${esc(String(v))}</div>
      <div class="k">${esc(k)}</div></div>`).join("")}</div>
    ${split}
    ${holdTabBtns(h)}
    <div class="hold-pane" id="holdPane">${holdPaneHtml(h)}</div>
    <div class="muted" style="font-size:calc(11px * var(--font-scale));margin-top:8px">${esc(h.note || "")} 数据来源：${esc(h.source || "")}${h.fetched_at ? " · " + esc(h.fetched_at) : ""} · 右键可 AI 提取关键信息词库</div>`;
}

async function loadHolders() {
  const card = $("#holdersCard");
  const box = $("#holdersSec");
  const meta = $("#holdersMeta");
  const mini = $("#holdersMini");
  if (!currentStock || (!card && !box && !mini)) return;
  if (card) card.style.display = "";
  if (box) box.innerHTML = '<div class="empty">持股数据加载中…</div>';
  if (meta) meta.textContent = "";
  if (mini) mini.innerHTML = '<div class="hold-mini"><span class="muted">持股摘要加载中…</span></div>';
  try {
    const h = await api(`/api/holders?code=${encodeURIComponent(currentStock.code)}`);
    holdersData = h;
    if (h.empty) {
      if (box) paintHoldersCard(h);
      if (meta) meta.textContent = h.offline ? "拉取失败" : "";
      if (mini) mini.innerHTML = `<div class="hold-mini"><span class="muted">${esc(h.note || "暂无持股摘要")}</span>
        <button class="btn ghost small" type="button" onclick="loadHolders()">重试</button></div>`;
      return;
    }
    const L = h.latest || {};
    const inst = h.institution_ratio;
    const person = h.person_ratio;
    const ctrl = (h.controller || []).map((c) => c.name + (c.ratio != null ? ` ${fmt(c.ratio, 2)}%` : "")).join("、");
    if (meta) {
      meta.textContent = `${h.asof || ""}${h.offline ? " · 本地缓存" : ""} · ${h.source || ""}`.trim();
    }
    const qoqText = qoqLabel(L);
    const qoqClass = L.holders_qoq > 0 ? "up" : L.holders_qoq < 0 ? "down" : "";
    if (mini) {
      mini.innerHTML = `<div class="hold-mini">
        <span>股东 ${L.holders != null ? fmtInt(L.holders) + " 户" : "-"}</span>
        <span class="${L.holders_qoq_note ? "muted" : qoqClass}">${L.holders_qoq_note ? esc(L.holders_qoq_note) : (L.holders_qoq != null ? "环比 " + qoqText : "")}</span>
        <span>${inst != null ? "机构 " + fmt(inst, 1) + "%" : ""}</span>
        <span>${person != null ? "个人及其他 " + fmt(person, 1) + "%" : ""}</span>
        <span>${ctrl ? "实控人 " + esc(ctrl) : ""}</span>
      </div>`;
    }
    paintHoldersCard(h);
  } catch (err) {
    holdersData = null;
    if (box) box.innerHTML = `<div class="empty">持股数据加载失败
      <div style="margin-top:10px"><button class="btn ghost" type="button" onclick="loadHolders()">重新加载</button></div></div>`;
    if (mini) mini.innerHTML = `<div class="hold-mini"><span class="muted">持股摘要加载失败</span>
      <button class="btn ghost small" type="button" onclick="loadHolders()">重试</button></div>`;
    console.warn(err);
  }
}
window.loadHolders = loadHolders;

window.refreshStockBrief = async (code) => {
  const target = code || (currentStock && currentStock.code);
  if (!target || holdBriefBusy) return;
  holdBriefBusy = true;
  holdTab = "brief";
  const btn = $("#briefAiBtn");
  const banner = $("#briefAiBanner");
  const body = $("#briefAiBody");
  if (btn) { btn.disabled = true; btn.textContent = "分析中…"; }
  if (body) body.innerHTML = '<div class="empty">分析中，大模型最长约 1 分钟，请稍候…</div>';
  if (banner) banner.innerHTML = "";
  try {
    const d = await api("/api/ai/brief", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: target }),
    });
    applyBriefToHolders(target, d);
    const b = $("#briefAiBanner");
    if (b) {
      if (d.applied) {
        b.innerHTML = `<div class="muted" style="margin-bottom:8px">已保存本次简明诊断 · ${esc(d.analyzed_at || d.updated_at || "")}</div>`;
      } else {
        const err = d.error || "未写入简明诊断";
        const extra = d.kept ? "。已保留上次成功诊断，未覆盖。" : "。未假装成功。";
        b.innerHTML = aiErrBanner({ error: err, hint: d.hint }, extra);
      }
    }
    return d;
  } catch (err) {
    if (holdersData && currentStock && currentStock.code === target) {
      const prev = holdersData.brief || {};
      holdersData.brief = {
        ...prev,
        error: err.message || String(err),
        hint: "请确认从 http 页面打开，并已在 AI 分析页配置密钥。",
        kept: !!(prev.applied && prev.text),
      };
    }
    const body2 = $("#briefAiBody");
    if (body2) body2.innerHTML = `<div class="empty">分析失败：${esc(err.message || err)}</div>`;
    const b = $("#briefAiBanner");
    if (b) b.innerHTML = aiErrBanner({ error: err.message || String(err), hint: "请确认从 http 页面打开，并已在 AI 分析页配置密钥。" });
    return null;
  } finally {
    holdBriefBusy = false;
    const b2 = $("#briefAiBtn");
    if (b2) { b2.disabled = false; b2.textContent = "更新AI简明诊断"; }
  }
};

function applyBriefToHolders(code, d) {
  if (!d || !holdersData) return;
  const page = (holdersData.code || (currentStock && currentStock.code) || "").toLowerCase();
  const got = (d.code || code || "").toLowerCase();
  if (!page || !got || page !== got) return;
  holdersData.brief = {
    applied: !!(d.applied || (d.kept && d.text)),
    ai: !!d.ai,
    text: d.text || "",
    source: d.source || "",
    updated_at: d.updated_at || "",
    analyzed_at: d.analyzed_at || d.updated_at || "",
    error: d.error || "",
    hint: d.hint || "",
    kept: !!d.kept,
  };
  if (d.wuxing) holdersData.wuxing_ai = d.wuxing;
  paintHoldersCard(holdersData);
}

window.refreshHolderAi = async () => {
  if (!currentStock || holdAiBusy) return;
  holdAiBusy = true;
  holdTab = "ai";
  const btn = $("#holderAiBtn");
  const banner = $("#holderAiBanner");
  const body = $("#holderAiBody");
  if (btn) { btn.disabled = true; btn.textContent = "分析中…"; }
  if (body) body.innerHTML = '<div class="empty">分析中，大模型最长约 1 分钟，请稍候…</div>';
  if (banner) banner.innerHTML = "";
  try {
    const d = await api("/api/holders/ai", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: currentStock.code }),
    });
    if (!holdersData || holdersData.empty) {
      await loadHolders();
    }
    if (holdersData) {
      holdersData.ai = {
        applied: !!(d.applied || (d.kept && d.text)),
        text: d.text || "", source: d.source || "",
        updated_at: d.updated_at || "", analyzed_at: d.analyzed_at || d.updated_at || "",
        asof: d.asof || "", error: d.error || "", hint: d.hint || "", kept: !!d.kept,
      };
    }
    holdTab = "ai";
    if (holdersData) paintHoldersCard(holdersData);
    const b = $("#holderAiBanner");
    if (b) {
      if (d.applied) {
        b.innerHTML = `<div class="muted" style="margin-bottom:8px">已保存本次分析 · ${esc(d.analyzed_at || d.updated_at || "")}</div>`;
      } else {
        const err = d.error || (String(d.source || "").includes("未配置") ? "未配置AI大模型" : "未写入分析结果");
        const extra = d.kept ? "。已保留上次成功分析，未覆盖。" : "。未假装成功。";
        b.innerHTML = aiErrBanner({ error: err, hint: d.hint }, extra);
      }
    }
  } catch (err) {
    if (holdersData) {
      const prev = holdersData.ai || {};
      holdersData.ai = {
        ...prev,
        error: err.message || String(err),
        hint: "请确认从 http 页面打开，并已在 AI 分析页配置密钥。",
        kept: !!(prev.applied && prev.text),
      };
    }
    const body2 = $("#holderAiBody");
    if (body2) body2.innerHTML = `<div class="empty">分析失败：${esc(err.message || err)}</div>`;
    const b = $("#holderAiBanner");
    if (b) b.innerHTML = aiErrBanner({ error: err.message || String(err), hint: "请确认从 http 页面打开，并已在 AI 分析页配置密钥。" });
  } finally {
    holdAiBusy = false;
    const b2 = $("#holderAiBtn");
    if (b2) { b2.disabled = false; b2.textContent = "更新AI分析"; }
  }
};

document.addEventListener("click", (e) => {
  const tab = e.target.closest(".js-hold-tab");
  if (tab && $("#holdersSec") && $("#holdersSec").contains(tab)) {
    e.preventDefault();
    e.stopPropagation();
    holdTab = tab.dataset.tab || "structure";
    if (holdersData) paintHoldersCard(holdersData);
    return;
  }
  const sub = e.target.closest(".js-hold-struct");
  if (sub && $("#holdersSec") && $("#holdersSec").contains(sub)) {
    e.preventDefault();
    e.stopPropagation();
    holdTab = "structure";
    holdStructSub = sub.dataset.sub || "top10";
    if (holdersData) paintHoldersCard(holdersData);
    return;
  }
  const org = e.target.closest(".js-hold-org");
  if (org && $("#holdersSec") && $("#holdersSec").contains(org)) {
    e.preventDefault();
    e.stopPropagation();
    holdOrgType = org.dataset.org || "";
    if (holdersData) paintHoldersCard(holdersData);
    return;
  }
  if (e.target.closest("#holderAiBtn")) {
    e.preventDefault();
    e.stopPropagation();
    refreshHolderAi();
    return;
  }
  if (e.target.closest("#briefAiBtn")) {
    e.preventDefault();
    e.stopPropagation();
    refreshStockBrief();
  }
});

/* ---------------- 宏观情报 ---------------- */
let macroSub = "news";
$("#macroTabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  macroSub = btn.dataset.sub;
  $$("#macroTabs .opt").forEach((b) => b.classList.toggle("active", b === btn));
  if (macroSub === "major" && !newsFilter._majorDefaulted) {
    newsFilter.level = 3;
    newsFilter._majorDefaulted = true;
  }
  loadMacro();
});

const stars = (lv) => `<span class="${lv >= 4 ? "up" : "flat"}" style="letter-spacing:1px">${"★".repeat(lv)}${"☆".repeat(5 - lv)}</span>`;
let outlookHorizon = "week";
let calRange = 0;      // 0=全部, 1=今天, 3, 7, 31 天
let calMinLevel = 0;   // 0=全部, 4, 5
const newsFilter = { level: 0, direction: "", region: "", days: 0 };  // FR5-01-1 + FR8-02-1
let sectorEvGroup = "day";
const policyFilter = { days: 180, scope: "", country: "", doc_type: "" };
let hotKind = "";
let hotAiToken = 0;
const hotFocus = { term: "", sector: "" };
let intelAiIndex = {};
let ctxIntel = null;
let ctxKb = null;
const hotIntelFocus = { key: "", sector: "" };
let hotIntelSort = "heat";
let hotIntelOrder = "desc";
let hotStockLimit = 20;

function hotStockLimitBtns(active) {
  const cur = Number(active) || hotStockLimit || 20;
  return `<span class="btn-group" id="hotStockLimitBtns" style="margin-left:8px">
    ${[20, 30, 50].map((n) =>
      `<button type="button" class="opt ${cur === n ? "active" : ""}" data-n="${n}">TOP${n}</button>`).join("")}
  </span>`;
}
function bindHotStockLimit(reload) {
  $("#hotStockLimitBtns")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".opt");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    hotStockLimit = Number(btn.dataset.n) || 20;
    if (typeof reload === "function") reload();
  });
}

function intelKey(source, ident) {
  return `${source}:${String(ident || "").trim()}`;
}
function newsIdent(n) {
  return String(n.id || n.event_id || `${n.time || ""}:${(n.text || n.title || "").slice(0, 40)}`);
}
async function refreshIntelAiIndex() {
  try {
    const d = await api("/api/macro/intel-ai/index");
    intelAiIndex = d.items || {};
  } catch { intelAiIndex = {}; }
}
function intelFlagHtml(key) {
  const it = intelAiIndex[key];
  if (!it || !(it.has_boards || it.has_reading || it.has_keywords)) return "";
  const bits = [];
  if (it.has_boards) bits.push("利好利空");
  if (it.has_reading) bits.push("解读");
  if (it.has_keywords) bits.push("词库");
  return `<button type="button" class="ai-flag js-intel-ai" data-key="${esc(key)}" title="点击回显已保存的AI分析">AI已分析${bits.length ? "·" + bits.join("/") : ""}</button>`;
}
function mergeIntelSectors(key, fallback, direction) {
  const orig = parseSectors(fallback);
  const it = intelAiIndex[key] || {};
  const skip = new Set(["", "无明显利空", "未映射影响板块", "政策", "A股", "大盘"]);
  const aliases = { 房产: "地产", 房地产: "地产", 楼市: "地产", 券商股: "金融", 银行股: "金融",
    黄金股: "有色", 芯片: "半导体", AI: "科技", 人工智能: "科技" };
  const canon = (name) => {
    let n = String(name || "").trim().replace(/板块$/g, "");
    if (aliases[n]) n = aliases[n];
    return n;
  };
  const bull = [];
  const bear = [];
  const seenB = new Set();
  const seenW = new Set();
  const add = (arr, seen, name) => {
    const n = canon(name);
    if (!n || skip.has(n) || seen.has(n) || seenB.has(n) || seenW.has(n)) return;
    seen.add(n);
    arr.push(n);
  };
  for (const s of (it.bull || [])) add(bull, seenB, s);
  for (const s of (it.bear || [])) add(bear, seenW, s);
  const dir = direction || "";
  for (const s of orig) {
    if (dir === "利空") add(bear, seenW, s);
    else add(bull, seenB, s);
  }
  return exclusiveBoards(bull, bear);
}
function exclusiveBoards(bull, bear) {
  const skip = new Set(["", "无明显利空", "未映射影响板块", "政策", "A股", "大盘"]);
  const aliases = { 房产: "地产", 房地产: "地产", 楼市: "地产", 券商股: "金融", 银行股: "金融",
    黄金股: "有色", 芯片: "半导体", AI: "科技", 人工智能: "科技" };
  const canon = (name) => {
    let n = String(name || "").trim().replace(/板块$/g, "");
    if (aliases[n]) n = aliases[n];
    return n;
  };
  const outB = [];
  const outW = [];
  const seen = new Set();
  for (const s of bull || []) {
    const n = canon(s);
    if (!n || skip.has(n) || seen.has(n)) continue;
    seen.add(n);
    outB.push(n);
  }
  for (const s of bear || []) {
    const n = canon(s);
    if (!n || skip.has(n) || seen.has(n)) continue;
    seen.add(n);
    outW.push(n);
  }
  return { bull: outB, bear: outW };
}
function intelSectorBadges(key, fallback, title, direction) {
  const { bull, bear } = mergeIntelSectors(key, fallback, direction);
  if (!bull.length && !bear.length) return "";
  return bull.map((s) =>
    `<span class="badge dir-利好 js-sector" data-sector="${esc(s)}" data-title="${esc(title || s)}" title="利好 · 点击看个股 TOP20–50">${esc(s)}</span>`
  ).join("") + bear.map((s) =>
    `<span class="badge dir-利空 js-sector" data-sector="${esc(s)}" data-title="${esc(title || s)}" title="利空 · 点击看个股 TOP20–50">${esc(s)}</span>`
  ).join("");
}
function intelReasonLine(key) {
  const it = intelAiIndex[key];
  if (!it || !it.reason) return "";
  return `<div class="muted" style="font-size:calc(12px * var(--font-scale));margin-top:3px">🤖 ${esc(it.reason)}</div>`;
}

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
        <span class="muted" style="font-size:calc(12px * var(--font-scale))">时辰方位：${a.shichen.map((s) => `${esc(s.name.split(" ")[0])}${esc(s.direction)}`).join(" · ")}</span>
      </div>`;
  } catch { return ""; }
}

async function loadMacro() {
  const box = $("#macroContent");
  try {
    if (macroSub) await refreshIntelAiIndex();
    if (macroSub === "hotintel") {
      await renderHotIntel(box);
      return;
    }
    if (macroSub === "sectorEvents") {
      const d = await api(`/api/macro/sector-events?group=${encodeURIComponent(sectorEvGroup)}`);
      const rows = Array.isArray(d.items) ? d.items : [];
      const st = d.stats || {};
      const groups = Array.isArray(d.groups) && d.groups.length
        ? d.groups
        : [];
      const PREVIEW = 6;
      const evRow = (ev) => {
        const ident = ev.id || `${ev.date || ""}:${ev.title || ""}`;
        const key = intelKey("sector_event", ident);
        const secs = ev.sectors || "";
        const ymd = String(ev.date || "").slice(0, 10);
        const dshow = ymd.length === 10 ? ymd.slice(5) : (ymd || "—");
        const desc = String(ev.cycle_desc || ev.source || "").replace(/\s+/g, " ").trim();
        return `
        <div class="se-event js-event-row js-intel-row" data-title="${esc(ev.title)}" data-sectors="${esc(secs)}"
          data-intel-source="sector_event" data-intel-id="${escAttr(ident)}" data-intel-key="${escAttr(key)}"
          data-intel-title="${escAttr(ev.title)}" data-intel-text="${escAttr((desc || ev.title || "").slice(0, 400))}"
          data-intel-time="${escAttr(ev.date || "")}" data-attention="${escAttr((ev.impact_level || 0) * 20)}"
          title="点击查看该条影响板块与相关个股">
          <div class="se-event-date">${esc(dshow)}</div>
          <div class="se-event-main">
            <div class="se-event-title">${esc(ev.title)} ${intelFlagHtml(key)}</div>
            <div class="se-event-meta">
              ${stars(ev.impact_level)}
              ${ev.city ? `<span class="flag">${esc(ev.city)}</span>` : ""}
              <span class="muted">${esc(ev.source || "")}</span>
              ${intelSectorBadges(key, secs, ev.title)}
            </div>
            ${desc ? `<div class="se-event-desc">${esc(desc)}</div>` : ""}
          </div>
        </div>`;
      };
      const groupCard = (g) => {
        const label = g.label || g.key || "";
        const secs = g.sectors_text || (Array.isArray(g.sectors) ? g.sectors.join("、") : "");
        const items = Array.isArray(g.items) ? g.items : [];
        const gkey = intelKey("sector_event", `group:${g.key || label}`);
        const more = items.length > PREVIEW ? items.length - PREVIEW : 0;
        return `
        <div class="se-group">
          <div class="se-group-head js-event-row js-intel-row" data-title="${esc(label)}" data-sectors="${esc(secs)}"
            data-intel-source="sector_event" data-intel-id="${escAttr(g.key || label)}" data-intel-key="${escAttr(gkey)}"
            data-intel-title="${escAttr(label)}" data-intel-text="${escAttr((label + " " + secs).slice(0, 400))}"
            title="点击查看本${sectorEvGroup === "week" ? "周" : sectorEvGroup === "month" ? "月" : "日"}合并后的影响板块">
            <div class="se-group-title">${esc(label)} <span class="muted">${items.length} 条</span></div>
            <div class="se-group-secs">${intelSectorBadges(gkey, secs, label) || (secs ? sectorBadges(secs, label) : '<span class="muted">暂无映射板块</span>')}</div>
          </div>
          <div class="se-group-body">
            ${items.slice(0, PREVIEW).map(evRow).join("")}
            ${more ? `<div class="se-more" hidden>${items.slice(PREVIEW).map(evRow).join("")}</div>
              <button type="button" class="btn small ghost se-more-btn" data-n="${more}">展开其余 ${more} 条</button>` : ""}
          </div>
        </div>`;
      };
      box.innerHTML = (await almanacCard()) + '<div id="eventDetailBox"></div>' +
        `<div class="btn-group" style="margin-bottom:10px" id="seGroupBtns">
          ${[["day", "按日"], ["week", "按周"], ["month", "按月"]].map(([v, t]) =>
            `<button class="opt ${v === sectorEvGroup ? "active" : ""}" data-v="${v}">${t}</button>`).join("")}
        </div>
        <div class="muted" style="margin-bottom:10px">同一${sectorEvGroup === "week" ? "周" : sectorEvGroup === "month" ? "月" : "日"}的事件与影响板块已合并到该时间分组。板块情报已缓存本地 ${st.total || rows.length} 条 · ${esc(d.note || "")}</div>` +
        (groups.length ? groups.map(groupCard).join("")
          : (rows.length ? '<div class="empty">分组结果为空</div>' : '<div class="empty">暂无板块事件</div>'));
      $("#seGroupBtns")?.addEventListener("click", (e) => {
        const btn = e.target.closest(".opt");
        if (!btn) return;
        sectorEvGroup = btn.dataset.v;
        loadMacro();
      });
      box.querySelectorAll(".se-more-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          const more = btn.parentElement && btn.parentElement.querySelector(".se-more");
          if (!more) return;
          const open = !more.hidden;
          more.hidden = open;
          btn.textContent = open ? `展开其余 ${btn.dataset.n} 条` : "收起";
        });
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
          const evHtml = (ev) => {
            const ident = ev.id || `${ev.date || ""}:${ev.title || ""}`;
            const key = intelKey("outlook", ident);
            const secs = ev.sectors || "";
            return `
            <div class="cal-item js-event-row js-intel-row" data-title="${esc(ev.title)}" data-sectors="${esc(secs)}"
              data-intel-source="outlook" data-intel-id="${esc(ident)}" data-intel-key="${esc(key)}"
              data-intel-title="${esc(ev.title)}" data-intel-text="${esc((ev.note || ev.title || "").slice(0, 400))}"
              data-intel-time="${esc(ev.date || "")}" data-attention="${esc((ev.impact_level || 0) * 20)}"
              style="cursor:pointer" title="点击查看影响板块与相关个股">
              <span class="cal-date">${ev.date.slice(5)}</span>
              ${stars(ev.impact_level)}
              <span class="badge level-${ev.impact_level}">${ev.impact_desc}</span>
              <span class="flag">${esc(ev.region)}</span>
              <span style="flex:1">${esc(ev.title)} ${intelFlagHtml(key)} ${intelSectorBadges(key, secs, ev.title)}${ev.custom ? ` <button class="btn small danger" onclick="event.stopPropagation();delCustomEvent(${ev.id.replace("custom_", "")})">删</button>` : ""}</span>
              <span class="muted">${esc(ev.category)}</span>
            </div>`;
          };
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
          <input id="ceSectors" placeholder="影响板块（可选，逗号分隔）" style="width:220px">
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
      const today = beijingYMD();
      const rangeEnd = calRange === 1 ? today : (calRange ? addCalendarDays(today, calRange) : "9999");
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
        ${filtered.length ? filtered.map((ev) => {
          const ident = ev.id || `${ev.date || ""}:${ev.title || ""}`;
          const key = intelKey("calendar", ident);
          const secs = ev.sectors || "";
          return `
        <div class="cal-item js-event-row js-intel-row" data-title="${esc(ev.title)}" data-sectors="${esc(secs)}"
          data-intel-source="calendar" data-intel-id="${esc(ident)}" data-intel-key="${esc(key)}"
          data-intel-title="${esc(ev.title)}" data-intel-text="${esc((ev.note || ev.title || "").slice(0, 400))}"
          data-intel-time="${esc(ev.date || "")}" data-attention="${esc((ev.impact_level || 0) * 20)}"
          style="cursor:pointer" title="点击查看影响板块与相关个股">
          <span class="cal-date" style="width:108px">${esc(ev.date)}</span>
          ${stars(ev.impact_level)}
          <span class="badge level-${ev.impact_level}">${ev.impact_desc}</span>
          <span class="flag">${esc(ev.region)}</span>
          <span style="flex:1">${esc(ev.title)} ${intelFlagHtml(key)} ${intelSectorBadges(key, secs, ev.title)}</span>
          <span class="muted">${esc(ev.category)}</span>
        </div>`;
        }).join("") : '<div class="empty">该筛选条件下无事件</div>'}`;
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
          <input id="kbSearch" placeholder="搜索术语，如 量比 / 换手率 / 打板" style="flex:1">
        </div>
        <div class="muted" style="margin-bottom:8px">分「股票常识」与「选股票小技巧」。右键词条「AI 更新知识」改写解释；右键分类标题可整节更新。失败不覆盖已有解释。</div>
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
        <div id="eventDetailBox"></div>
        <div id="annRatings"></div>
        <div id="annList"><div class="empty">加载中…</div></div>`;
      loadAnnouncements();
      return;
    }
    if (macroSub === "official") {
      await renderOfficialPolicy(box);
      return;
    }
    if (macroSub === "hotwords") {
      await renderHotWords(box);
      return;
    }
    const path = macroSub === "policy" ? "/api/macro/policies" : macroSub === "major" ? "/api/macro/major" : "/api/macro/news";
    const qs = (macroSub === "news" || macroSub === "policy") && newsFilter.days
      ? `?days=${newsFilter.days}` : "";
    const rows = applyNewsFilter(await api(path + qs));
    const offline = rows.length && rows[0].offline;
    const pageNote = macroSub === "policy"
      ? '<div class="muted" style="margin-bottom:8px">政策条来自 7x24 快讯关键词识别，非政策库全文。</div>'
      : macroSub === "major"
        ? '<div class="muted" style="margin-bottom:8px">高等级快讯专题，默认筛选中等及以上。</div>'
        : "";
    box.innerHTML = pageNote + newsFilterBar() + '<div id="eventDetailBox"></div>' +
      (offline ? '<div class="offline-banner">当前展示本地缓存数据（离线）</div>' : "") +
      (rows.length ? rows.map((n) => {
        const ident = newsIdent(n);
        const src = macroSub === "policy" ? "policy" : macroSub === "major" ? "major" : "news";
        const key = intelKey(src, ident);
        const title = (n.text || "").slice(0, 40);
        const secs = (n.affected_sectors || []).join(",");
        const att = n.score != null ? n.score : (n.impact_level || 0) * 20;
        return `
      <div class="news-item js-news-item js-intel-row" data-sectors="${esc(secs)}" data-title="${esc(title)}"
        data-news="${esc(n.text)}" data-impact="${esc(n.impact_desc || "")}" data-direction="${esc(n.impact_direction || "")}"
        data-intel-source="${esc(src)}" data-intel-id="${esc(ident)}" data-intel-key="${esc(key)}"
        data-intel-title="${esc(title)}" data-intel-text="${esc((n.text || "").slice(0, 500))}"
        data-intel-time="${esc(n.time || "")}" data-attention="${esc(att)}">
        <span class="time">${esc((n.time || "").slice(5, 16))}</span>
        <div class="body">${esc(n.text)}
          <div class="meta">
            ${n.score !== undefined ? `<span class="badge ${n.score >= 70 ? "level-4" : n.score >= 50 ? "level-3" : "level-2"}">关注度 ${fmt(n.score, 0)}</span>` : ""}
            <span class="badge level-${n.impact_level}">${n.impact_desc || ""}</span>
            <span class="badge dir-${n.impact_direction}">${n.impact_direction}</span>
            ${n.is_policy ? '<span class="badge sector-tag">政策</span>' : ""}
            ${intelFlagHtml(key)}
            ${intelSectorBadges(key, n.affected_sectors, title, n.impact_direction)}
            ${(function () {
              const mrg = mergeIntelSectors(key, n.affected_sectors, n.impact_direction);
              return (mrg.bull.length || mrg.bear.length)
                ? `<button class="btn small ghost js-news-stocks" data-sectors="${esc([...mrg.bull, ...mrg.bear].join(","))}" data-title="${esc(title)}">相关个股 ›</button>`
                : "";
            })()}
          </div>
          ${n.brief ? `<div class="muted" style="font-size:calc(12px * var(--font-scale));margin-top:3px">💡 ${esc(n.brief)}</div>` : ""}
          ${intelReasonLine(key)}
          ${n.commentary ? `<div class="desc-hl" style="font-size:calc(13px * var(--font-scale));margin-top:3px">💬 ${esc(n.commentary)}</div>` : ""}
        </div>
      </div>`;
      }).join("") : '<div class="empty">该筛选条件下暂无数据</div>');
    bindNewsFilters();
  } catch (err) { box.innerHTML = '<div class="empty">加载失败，稍后自动重试</div>'; console.warn(err); }
}

async function renderOfficialPolicy(box) {
  const qs = new URLSearchParams({
    days: String(policyFilter.days), scope: policyFilter.scope,
    country: policyFilter.country, doc_type: policyFilter.doc_type, limit: "80",
  });
  const d = await api(`/api/macro/official-policy?${qs}`);
  const st = d.stats || {};
  const rows = d.items || [];
  const countries = ["", ...(st.countries || [])];
  const optBar = (id, items, cur) =>
    `<div class="btn-group" style="margin-bottom:6px" id="${id}">${items.map(([v, t]) =>
      `<button class="opt ${String(v) === String(cur) ? "active" : ""}" data-v="${esc(String(v))}">${t}</button>`).join("")}</div>`;
  const byScope = st.by_scope || {};
  const byType = st.by_type || {};
  box.innerHTML = `
    <div class="muted" style="margin-bottom:8px">
      近半年官方政策归档（本地存储）· 共 ${st.total || 0} 条
      · 国内 ${byScope["国内"] || 0} / 国外 ${byScope["国外"] || 0}
      · 文件 ${byType["政策文件"] || 0} · 大会 ${byType["大会会议"] || 0}
      · 通知 ${byType["通知意见"] || 0} · 监管 ${byType["监管动态"] || 0}<br>
      ${esc(st.update || "增量每4小时，每日回补历史页")}
      · 上次同步 ${esc(st.last_sync || "从未")}
      ${st.oldest ? ` · 最早 ${esc((st.oldest || "").slice(0, 10))}` : ""}
      · 与「政策追踪」不同：本页只收录可识别的官方政策/大会会议，不编造文件。
    </div>
    ${optBar("pfDays", [[7, "近7天"], [30, "近30天"], [90, "近90天"], [180, "近半年"]], policyFilter.days)}
    ${optBar("pfScope", [["", "全部范围"], ["国内", "国内"], ["国外", "国外"]], policyFilter.scope)}
    ${optBar("pfCountry", countries.map((c) => [c, c || "全部国家"]), policyFilter.country)}
    ${optBar("pfType", [["", "全部类型"], ["政策文件", "政策文件"], ["大会会议", "大会会议"], ["通知意见", "通知意见"], ["监管动态", "监管动态"]], policyFilter.doc_type)}
    <div id="eventDetailBox"></div>
    ${rows.length ? rows.map((n) => {
      const secs = (n.affected_sectors || []).join(",");
      const ident = n.id || `${n.time || ""}:${n.title || ""}`;
      const key = intelKey("official", ident);
      const href = n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener" style="color:var(--accent);font-size:calc(12px * var(--font-scale));margin-left:6px">原文</a>` : "";
      return `<div class="policy-item js-news-item js-intel-row" data-title="${esc(n.title)}" data-sectors="${esc(secs)}"
        data-direction="${esc(n.impact_direction || "")}"
        data-intel-source="official" data-intel-id="${esc(ident)}" data-intel-key="${esc(key)}"
        data-intel-title="${esc(n.title)}" data-intel-text="${esc((n.summary || n.title || "").slice(0, 500))}"
        data-intel-time="${esc(n.time || "")}" data-attention="${esc((n.impact_level || 0) * 20)}">
        <div class="p-head">
          <span class="cal-date" style="width:108px">${esc((n.time || "").slice(0, 16))}</span>
          <span class="flag">${esc(n.scope || "")}</span>
          <span class="flag">${esc(n.country || "")}</span>
          <span class="badge level-${n.impact_level || 2}">${esc(n.doc_type || "")}</span>
          ${n.impact_direction ? `<span class="badge dir-${n.impact_direction}">${esc(n.impact_direction)}</span>` : ""}
          ${intelFlagHtml(key)}
          <span class="muted">${esc(n.source || "")}</span>${href}
        </div>
        <div class="p-title">${esc(n.title)}</div>
        ${n.summary && n.summary !== n.title ? `<div class="p-sum">${esc((n.summary || "").slice(0, 220))}</div>` : ""}
        <div class="meta" style="margin-top:4px">${intelSectorBadges(key, n.affected_sectors, n.title, n.impact_direction)}</div>
        ${intelReasonLine(key)}
      </div>`;
    }).join("") : `<div class="empty">${esc(d.empty_reason || "暂无官方政策")}</div>`}`;
  const bind = (id, key, isNum) => {
    $(`#${id}`)?.addEventListener("click", (e) => {
      const btn = e.target.closest(".opt");
      if (!btn) return;
      policyFilter[key] = isNum ? Number(btn.dataset.v) : btn.dataset.v;
      loadMacro();
    });
  };
  bind("pfDays", "days", true);
  bind("pfScope", "scope", false);
  bind("pfCountry", "country", false);
  bind("pfType", "doc_type", false);
}

async function renderHotWords(box) {
  const d = await api(`/api/macro/hot-terms?kind=${encodeURIComponent(hotKind)}`);
  const rows = d.items || [];
  box.innerHTML = `
    <div class="muted" style="margin-bottom:8px">
      近两周热门词汇（板块区域）· ${esc(d.update || "可手动更新")}
      · 上次 ${esc(d.last_sync || "从未")} · 点击热词看利好/利空板块，再点板块看个股（默认 TOP20）
      · 右键可 AI 回填利好/利空、解读并保存；点击「AI已分析」回显
    </div>
    <div class="hot-toolbar">
      <div class="btn-group" id="hotKindBtns">
        ${[["", "全部"], ["rise", "热度上升"], ["fall", "热度下降"]].map(([v, t]) =>
          `<button type="button" class="opt ${v === hotKind ? "active" : ""}" data-v="${v}">${t}</button>`).join("")}
      </div>
      <button type="button" class="btn ghost small" id="hotRebuildBtn">手动更新全部热词</button>
      <button type="button" class="btn small" id="hotAiBatchBtn">一键AI回填利好/利空</button>
    </div>
    <div id="hotBatchStatus" class="muted" style="margin-bottom:8px"></div>
    <div id="hotDetailBox"></div>
    ${rows.length ? `<div class="hot-grid">${rows.map((t) => {
      const clsName = t.trend === "上升" ? "rising" : t.trend === "下降" ? "falling" : "";
      const on = t.term === hotFocus.term ? "active" : "";
      const key = intelKey("hot_term", t.term);
      return `<div class="hot-tile ${clsName} ${on} js-hot-term js-intel-row" data-term="${esc(t.term)}" data-ai="${t.ai_applied ? "1" : ""}"
        data-intel-source="hot_term" data-intel-id="${esc(t.term)}" data-intel-key="${esc(key)}"
        data-intel-title="${esc(t.term)}" data-intel-text="${esc((t.impact_summary || t.term || "").slice(0, 400))}"
        data-heat="${esc(t.heat ?? "")}">
        <div class="term">${esc(t.term)}${intelFlagHtml(key) || (t.ai_applied ? '<span class="ai-flag">AI已分析</span>' : "")}</div>
        <div class="metrics">
          <span class="heat">热度 ${fmt(t.heat, 0)}</span>
          <span class="rise">↑${fmt(t.rise, 0)}</span>
          <span class="fall">↓${fmt(t.fall, 0)}</span>
        </div>
        <div class="muted" style="margin-top:6px;font-size:calc(11px * var(--font-scale))">${esc(t.trend)} · 近两周 ${t.count_now || 0} 次 / 前两周 ${t.count_prev || 0} 次</div>
        ${t.impact_summary ? `<div class="impact">${esc(t.impact_summary)}</div>` : ""}
      </div>`;
    }).join("")}</div>` : `<div class="empty">${esc(d.empty_reason || "暂无热词")}</div>`}`;
  $("#hotKindBtns")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".opt");
    if (!btn) return;
    hotAiToken += 1;
    hotKind = btn.dataset.v || "";
    loadMacro();
  });
  $("#hotRebuildBtn")?.addEventListener("click", () => rebuildAllHotTerms());
  $("#hotAiBatchBtn")?.addEventListener("click", () => batchHotTermAi());
  if (hotFocus.term) {
    await openHotTerm(hotFocus.term, false);
    if (hotFocus.sector) await openHotSector(hotFocus.sector, hotFocus.term);
  }
}

async function renderHotIntel(box) {
  const d = await api(`/api/macro/hot-intel?sort=${encodeURIComponent(hotIntelSort)}&order=${encodeURIComponent(hotIntelOrder)}&limit=80`);
  const rows = d.items || [];
  const sorts = (d.sorts && d.sorts.length) ? d.sorts : [
    { id: "heat", name: "热度" }, { id: "attention", name: "关注度" },
    { id: "updated_at", name: "更新时间" }, { id: "event_time", name: "事件时间" },
  ];
  const curSort = d.sort || hotIntelSort;
  const curOrder = d.order || hotIntelOrder;
  hotIntelSort = curSort;
  hotIntelOrder = curOrder;
  box.innerHTML = `
    <div class="muted" style="margin-bottom:8px">
      热门信息：汇总已保存的 AI 分析词库（快讯/政策/日历/公告/持股/热词）。
      ${esc(d.note || "")} 共 ${d.total || 0} 条。点击卡片看板块，再点板块看个股（默认 TOP20，可选 TOP30/TOP50）。右键可再次分析。
    </div>
    <div class="cond-inline" style="margin-bottom:10px;gap:16px">
      <span><span class="g-label muted">排序</span>
        <span class="btn-group" id="hotIntelSort">
          ${sorts.map((s) => {
            const id = s.id || s;
            const name = s.name || s;
            return `<button type="button" class="opt ${id === curSort ? "active" : ""}" data-sort="${esc(id)}">${esc(name)}</button>`;
          }).join("")}
        </span></span>
      <span><span class="g-label muted">方向</span>
        <span class="btn-group" id="hotIntelOrder">
          <button type="button" class="opt ${curOrder === "desc" ? "active" : ""}" data-order="desc">倒序</button>
          <button type="button" class="opt ${curOrder === "asc" ? "active" : ""}" data-order="asc">正序</button>
        </span></span>
    </div>
    <div id="hotIntelDetail"></div>
    ${rows.length ? `<div class="intel-grid">${rows.map((it) => {
      const on = it.item_key === hotIntelFocus.key ? "active" : "";
      const att = it.attention != null ? `关注度 ${fmt(it.attention, 0)}` : "关注度 —";
      const heat = it.heat != null ? `热度 ${fmt(it.heat, 0)}` : "热度 —";
      const kws = (it.keywords || []).slice(0, 8);
      const { bull, bear } = exclusiveBoards(it.bull, it.bear);
      const ident = it.ident || String(it.item_key || "").split(":").slice(1).join(":");
      const key = it.item_key || intelKey(it.source || "news", ident);
      return `<div class="intel-card ${on} js-hot-intel js-intel-row" data-key="${esc(key)}" data-source="${esc(it.source || "")}"
        data-intel-source="${esc(it.source || "")}" data-intel-id="${esc(ident)}" data-intel-key="${esc(key)}"
        data-intel-title="${esc(it.title || "")}" data-intel-text="${esc((it.reason || it.title || "").slice(0, 400))}"
        data-intel-time="${esc(it.event_time || "")}" data-attention="${esc(it.attention ?? "")}" data-heat="${esc(it.heat ?? "")}">
        <div class="ic-title">${esc(it.title || "未命名")} ${intelFlagHtml(key)}</div>
        <div class="ic-meta">
          <span class="badge level-3">${esc(it.source_label || it.source || "")}</span>
          <span class="badge level-2">${esc(att)}</span>
          <span class="badge ${it.heat != null && it.heat > 0 ? "level-4" : "level-2"}">${esc(heat)}</span>
        </div>
        ${kws.length ? `<div class="ic-kws">${kws.map((k) => `<span class="kw-chip">${esc(k)}</span>`).join("")}</div>` : ""}
        <div class="ic-kws">
          ${bull.map((s) => `<span class="badge dir-利好 js-hot-intel-sec" data-sector="${esc(s)}" data-key="${esc(it.item_key)}">利好 ${esc(s)}</span>`).join("")}
          ${bear.map((s) => `<span class="badge dir-利空 js-hot-intel-sec" data-sector="${esc(s)}" data-key="${esc(it.item_key)}">利空 ${esc(s)}</span>`).join("")}
        </div>
        ${it.reason ? `<div class="ic-src">${esc(it.reason)}</div>` : ""}
        <div class="ic-src">来源：${esc(it.source_label || "")}${it.updated_at ? " · " + esc(it.updated_at) : ""}${it.ai_source ? " · " + esc(it.ai_source) : ""}</div>
      </div>`;
    }).join("")}</div>` : `<div class="empty">${esc(d.empty_reason || "暂无热门信息")}</div>`}`;
  $("#hotIntelSort")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".opt");
    if (!btn) return;
    hotIntelSort = btn.dataset.sort || "heat";
    loadMacro();
  });
  $("#hotIntelOrder")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".opt");
    if (!btn) return;
    hotIntelOrder = btn.dataset.order || "desc";
    loadMacro();
  });
  if (hotIntelFocus.key) await openHotIntelCard(hotIntelFocus.key, false);
}

window.openHotIntelCard = async (key, resetSector = true) => {
  hotIntelFocus.key = key || "";
  if (resetSector) hotIntelFocus.sector = "";
  const box = $("#hotIntelDetail");
  if (!box || !key) return;
  $$(".js-hot-intel").forEach((el) => el.classList.toggle("active", el.dataset.key === key));
  box.innerHTML = '<div class="empty">加载分析…</div>';
  try {
    const d = await api(`/api/macro/intel-ai?item_key=${encodeURIComponent(key)}`);
    const split = exclusiveBoards(
      (d.bull || []).map((x) => (typeof x === "string" ? x : x.name)),
      (d.bear || []).map((x) => (typeof x === "string" ? x : x.name)),
    );
    const bull = split.bull.map((name) => ({ name }));
    const bear = split.bear.map((name) => ({ name }));
    const tile = (s, kind) => {
      const name = s.name || s;
      const clsName = kind === "利好" ? "bull" : "bear";
      return `<button type="button" class="hot-chip ${clsName} js-hot-intel-sec ${name === hotIntelFocus.sector ? "active" : ""}" data-sector="${esc(name)}" data-key="${esc(key)}">${esc(name)}</button>`;
    };
    const chips = [];
    if (bull.length) chips.push(`<span class="hot-chip-lab bull">利好</span>`, ...bull.map((s) => tile(s, "利好")));
    if (bear.length) chips.push(`<span class="hot-chip-lab bear">利空</span>`, ...bear.map((s) => tile(s, "利空")));
    const kws = d.keywords || [];
    box.innerHTML = `
      <div class="outlook-summary" style="border-color:rgba(74,158,255,.45)">
        <b>${esc(d.title || key)}</b>
        <span class="ai-flag">AI已分析</span>
        <button class="btn small ghost" style="float:right" onclick="hotIntelFocus.key='';hotIntelFocus.sector='';this.closest('#hotIntelDetail').innerHTML=''">收起</button>
        <div class="muted" style="margin:6px 0">分析来源：${esc(d.source_label || d.source || "")} · ${esc(d.ai_source || "")} · ${esc(d.updated_at || "")}</div>
        ${kws.length ? `<div class="ic-kws">${kws.map((k) => `<span class="kw-chip">${esc(k)}</span>`).join("")}</div>` : ""}
        ${d.reason ? `<div style="margin:6px 0"><b>${esc(d.reason)}</b></div>` : ""}
        ${d.reading ? `<div class="hold-ai-text" style="margin:8px 0">${esc(d.reading).replace(/\n/g, "<br>")}</div>` : ""}
        ${chips.length ? `<div class="hot-chip-row">${chips.join("")}</div>` : '<div class="muted">尚无利好/利空板块</div>'}
        <div id="hotIntelStockBox"></div>
      </div>`;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    if (hotIntelFocus.sector) await openHotIntelSector(hotIntelFocus.sector, key);
  } catch (err) {
    box.innerHTML = '<div class="empty">加载失败</div>';
  }
};

window.loadSectorStocks = async (sector, box) => {
  if (!box) box = $("#eventStockBox") || $("#hotIntelStockBox") || $("#hotStockBox");
  if (!box || !sector) return;
  if (sector === "无明显利空" || sector === "未映射影响板块" || sector === "政策") {
    box.innerHTML = `<div class="empty">「${esc(sector)}」不是可交易板块，不展示个股。</div>`;
    return;
  }
  box.innerHTML = `<div class="empty">正在加载「${esc(sector)}」个股 TOP${hotStockLimit || 20}…</div>`;
  try {
    const d = await api(`/api/macro/hot-sector-stocks?sector=${encodeURIComponent(sector)}&limit=${hotStockLimit || 20}`);
    const stocks = d.stocks || [];
    box.innerHTML = `
      <div class="muted" style="margin:8px 0 4px">板块「${esc(sector)}」相关个股
        ${hotStockLimitBtns(hotStockLimit)}${d.match ? " · " + esc(d.match) : ""}</div>
      ${stocks.length ? renderRelatedStockTable(stocks) : `<div class="empty">${esc(d.empty_reason || "无相关个股")}</div>`}
      <div class="muted" style="font-size:calc(11px * var(--font-scale))">${esc(d.disclaimer || "")}</div>`;
    bindHotStockLimit(() => loadSectorStocks(sector, box));
  } catch (err) {
    box.innerHTML = '<div class="empty">个股加载失败</div>';
  }
};

window.openHotIntelSector = async (sector, key) => {
  hotIntelFocus.sector = sector || "";
  hotIntelFocus.key = key || hotIntelFocus.key;
  await loadSectorStocks(sector, $("#hotIntelStockBox") || $("#eventStockBox") || $("#hotStockBox"));
};

window.showSavedIntelAi = async (key) => {
  if (!key) return;
  const modal = $("#aiModal");
  modal.style.display = "";
  $("#aiModalTitle").textContent = "🤖 已保存的 AI 分析";
  $("#aiModalBody").innerHTML = '<div class="empty">读取中…</div>';
  try {
    const d = await api(`/api/macro/intel-ai?item_key=${encodeURIComponent(key)}`);
    if (!d.applied && !d.has_boards && !d.has_reading && !d.reading) {
      $("#aiModalBody").innerHTML = `<div class="empty">${esc(d.hint || "尚未保存 AI 分析")}</div>`;
      return;
    }
    const split = exclusiveBoards(
      (d.bull || []).map((x) => x.name || x),
      (d.bear || []).map((x) => x.name || x),
    );
    const bull = split.bull;
    const bear = split.bear;
    const kws = d.keywords || [];
    $("#aiModalBody").innerHTML =
      `<div class="muted" style="margin-bottom:6px">来源：${esc(d.source_label || "")} · ${esc(d.ai_source || "")} · ${esc(d.updated_at || "")}</div>`
      + (d.reason ? `<div style="margin-bottom:8px"><b>${esc(d.reason)}</b></div>` : "")
      + (kws.length ? `<div class="ic-kws" style="margin-bottom:8px">${kws.map((k) => `<span class="kw-chip">${esc(k)}</span>`).join("")}</div>` : "")
      + (bull.length ? `<div style="margin-bottom:4px">利好：${bull.map((s) => `<span class="badge dir-利好">${esc(s)}</span>`).join(" ")}</div>` : "")
      + (bear.length ? `<div style="margin-bottom:4px">利空：${bear.map((s) => `<span class="badge dir-利空">${esc(s)}</span>`).join(" ")}</div>` : "")
      + (d.reading ? `<div class="hold-ai-text">${esc(d.reading).replace(/\n/g, "<br>")}</div>` : "")
      + (!d.reading && d.text ? `<div class="hold-ai-text">${esc(d.text).replace(/\n/g, "<br>")}</div>` : "");
  } catch (err) {
    $("#aiModalBody").innerHTML = `<div class="empty">读取失败：${esc(err.message || err)}</div>`;
  }
};

window.rebuildAllHotTerms = async () => {
  hotAiToken += 1;
  const btn = $("#hotRebuildBtn");
  const st = $("#hotBatchStatus");
  if (btn) { btn.disabled = true; btn.textContent = "正在更新…"; }
  if (st) st.textContent = "正在根据近两周本地快讯/官方政策重算全部热词（不编造热度）…";
  try {
    const d = await post("/api/macro/hot-terms-rebuild");
    await loadMacro();
    const st2 = $("#hotBatchStatus");
    if (st2) st2.textContent = `已更新 ${d.count || 0} 个热词 · ${esc(d.last_sync || "")}。已回填的 AI 利好/利空不会被冲掉。`;
  } catch (err) {
    if (st) st.innerHTML = `<span class="down">更新失败：${esc(err.message || err)}</span>`;
    if (btn) { btn.disabled = false; btn.textContent = "手动更新全部热词"; }
  }
};

window.batchHotTermAi = async () => {
  const my = ++hotAiToken;
  const btn = $("#hotAiBatchBtn");
  const st = $("#hotBatchStatus");
  const rebuild = $("#hotRebuildBtn");
  if (btn) { btn.disabled = true; btn.textContent = "回填中…"; }
  if (rebuild) rebuild.disabled = true;
  if (st) st.textContent = "正在读取当前热词列表…";
  try {
    const listed = await api(`/api/macro/hot-terms?kind=${encodeURIComponent(hotKind)}`);
    const terms = (listed.items || []).map((t) => t.term).filter(Boolean);
    if (!terms.length) {
      if (st) st.textContent = listed.empty_reason || "当前没有可回填的热词。请先手动更新全部热词。";
      return;
    }
    if (st) st.textContent = `AI回填 1/${terms.length}「${terms[0]}」…`;
    const cfgProbe = await api("/api/macro/hot-term-ai", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ term: terms[0] }),
    });
    if (my !== hotAiToken) return;
    if (!cfgProbe.applied && String(cfgProbe.source || "").includes("未配置")) {
      if (st) st.innerHTML = aiErrBanner({ error: "未配置AI大模型", hint: cfgProbe.hint }, "。未改写任何热词。")
        || `<div class="muted">${esc(cfgProbe.hint || "请先配置大模型")}</div>`;
      return;
    }
    let applied = cfgProbe.applied ? 1 : 0;
    let failed = cfgProbe.applied ? 0 : 1;
    if (st) {
      st.textContent = `AI回填 1/${terms.length}「${terms[0]}」${cfgProbe.applied ? "已写入" : "未覆盖词库"}`;
    }
    for (let i = 1; i < terms.length; i++) {
      if (my !== hotAiToken) return;
      if (st) st.textContent = `AI回填 ${i + 1}/${terms.length}「${terms[i]}」…`;
      const d = await api("/api/macro/hot-term-ai", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ term: terms[i] }),
      });
      if (d.applied) applied += 1;
      else failed += 1;
    }
    if (my !== hotAiToken) return;
    if (st) st.textContent = `完成：成功回填 ${applied} 个，未覆盖 ${failed} 个（失败保留词库，不假装成功）。`;
    await loadMacro();
    const st2 = $("#hotBatchStatus");
    if (st2) st2.textContent = `完成：成功回填 ${applied} 个，未覆盖 ${failed} 个（失败保留词库，不假装成功）。`;
  } catch (err) {
    if (st) st.innerHTML = `<span class="down">一键回填失败：${esc(err.message || err)}</span>`;
  } finally {
    if (my !== hotAiToken) return;
    const b1 = $("#hotAiBatchBtn");
    const b2 = $("#hotRebuildBtn");
    if (b1) { b1.disabled = false; b1.textContent = "一键AI回填利好/利空"; }
    if (b2) b2.disabled = false;
  }
};

window.openHotTerm = async (term, resetSector = true) => {
  hotFocus.term = term || "";
  if (resetSector) hotFocus.sector = "";
  const box = $("#hotDetailBox");
  if (!box || !term) return;
  box.dataset.term = term;
  $$(".js-hot-term").forEach((el) => el.classList.toggle("active", el.dataset.term === term));
  box.innerHTML = '<div class="empty">加载关联板块…</div>';
  try {
    const d = await api(`/api/macro/hot-term-sectors?term=${encodeURIComponent(term)}`);
    const bull = d.bull_sectors || [];
    const bear = d.bear_sectors || [];
    const mid = d.neutral_sectors || [];
    const tile = (s, kind) => {
      const dir = s.direction || kind || "";
      const clsName = dir === "利好" ? "bull" : dir === "利空" ? "bear" : "mid";
      const title = [dir, s.why, s.hot_score != null ? `热度 ${fmt(s.hot_score, 1)}` : ""].filter(Boolean).join(" · ");
      return `<button type="button" class="hot-chip ${clsName} js-hot-sector ${s.name === hotFocus.sector ? "active" : ""}" data-sector="${esc(s.name)}" data-term="${esc(term)}" data-dir="${esc(dir)}" title="${esc(title)}">${esc(s.name)}</button>`;
    };
    const chips = [];
    if (bull.length) chips.push(`<span class="hot-chip-lab bull">利好</span>`, ...bull.map((s) => tile(s, "利好")));
    if (bear.length) chips.push(`<span class="hot-chip-lab bear">利空</span>`, ...bear.map((s) => tile(s, "利空")));
    if (mid.length) chips.push(`<span class="hot-chip-lab mid">仅关联</span>`, ...mid.map((s) => tile(s, "中性")));
    box.innerHTML = `
      <div class="outlook-summary" style="border-color:rgba(255,169,64,.4)" data-term="${esc(term)}">
        <b>🔥 热词「${esc(term)}」关联板块${d.ai_applied ? (intelFlagHtml(intelKey("hot_term", term)) || '<span class="hot-ai-flag">AI已分析</span>') : ""}</b>
        <button class="btn small ghost" style="float:right" onclick="hotFocus.term='';hotFocus.sector='';this.closest('#hotDetailBox').innerHTML=''">收起</button>
        <div style="margin:8px 0 4px;font-size:calc(14px * var(--font-scale))"><b>${esc(d.impact_summary || "利好 / 利空板块待映射")}</b></div>
        ${d.ai_applied && d.ai_reason ? `<div class="muted" style="margin:4px 0">AI总述：${esc(d.ai_reason)} · ${esc(d.ai_updated_at || "")}</div>` : ""}
        ${d.ai_reading ? `<div class="hold-ai-text" style="margin:8px 0">${esc(d.ai_reading).replace(/\n/g, "<br>")}</div>` : ""}
        ${(d.samples || []).length ? `<div class="muted" style="margin:6px 0">样例：${d.samples.map((s) => esc(s)).join(" · ")}</div>` : ""}
        ${chips.length ? `<div class="hot-chip-row">${chips.join("")}</div>` : `<div class="empty">${esc(d.empty_reason || "无关联板块")}</div>`}
        <div class="muted" style="margin-top:8px;font-size:calc(12px * var(--font-scale))">${esc(d.note || "")} ${esc(d.disclaimer || "")}</div>
        <div id="hotStockBox"></div>
      </div>`;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) {
    box.innerHTML = '<div class="empty">板块加载失败</div>';
  }
};

let hotStockSeq = 0;
window.openHotSector = async (sector, term) => {
  const seq = ++hotStockSeq;
  hotFocus.sector = sector || "";
  hotFocus.term = term || hotFocus.term;
  const box = $("#hotStockBox");
  if (!box || !sector) return;
  $$(".js-hot-sector").forEach((el) => el.classList.toggle("active", el.dataset.sector === sector));
  const dir = document.querySelector(`.js-hot-sector[data-sector="${CSS && CSS.escape ? CSS.escape(sector) : sector}"]`)?.dataset.dir || "";
  const dirTxt = dir === "利好" ? "利好" : dir === "利空" ? "利空" : "";
  box.innerHTML = `<div class="empty">正在加载「${esc(sector)}」个股 TOP${hotStockLimit || 20}…</div>`;
  try {
    const d = await api(`/api/macro/hot-sector-stocks?sector=${encodeURIComponent(sector)}&limit=${hotStockLimit || 20}&_=${seq}`);
    if (seq !== hotStockSeq) return;
    if ((d.sector || sector) !== sector) {
      box.innerHTML = `<div class="empty">板块串了，已忽略过期结果</div>`;
      return;
    }
    const match = d.match ? ` · ${esc(d.match)}` : "";
    box.innerHTML = `
      <div class="muted" style="margin:10px 0 6px">${dirTxt ? `<span class="hot-chip-lab ${dir === "利好" ? "bull" : "bear"}">${esc(dirTxt)}</span>` : ""}板块「${esc(sector)}」个股
        ${hotStockLimitBtns(hotStockLimit)}${match}</div>
      ${d.stocks && d.stocks.length ? renderHotSectorStockTable(d.stocks, sector) : `<div class="empty">${esc(d.empty_reason || "无个股")}</div>`}
      <div class="muted" style="font-size:calc(11px * var(--font-scale));margin-top:4px">${esc(d.disclaimer || "")}</div>`;
    bindHotStockLimit(() => openHotSector(sector, term));
  } catch (err) {
    if (seq !== hotStockSeq) return;
    box.innerHTML = `<div class="empty">「${esc(sector)}」个股加载失败</div>`;
  }
};

function renderHotSectorStockTable(stocks, sector) {
  if (!stocks || !stocks.length) return '<div class="muted">未匹配到相关个股</div>';
  return `<table><thead><tr>
    <th>名称</th><th>代码</th><th>现价</th><th>行业</th><th>财报</th><th>涨跌幅</th><th>量比</th>${flowMetricHeaders()}<th>购买指数</th><th>标签</th>
  </tr></thead><tbody>${stocks.map((r) => `
    <tr data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
      <td>${esc(r.name)}</td>
      <td class="muted">${esc(r.code)}</td>
      <td class="num">${pxHtml(r.price, r.pct)}</td>
      <td class="muted">${esc(r.industry || sector || "-")}</td>
      <td>${finBadge(r)}</td>
      <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
      <td class="num">${fmt(r.volume_ratio)}</td>${flowMetricCells(r)}
      <td class="num ${buyCls(r.buy_index)}">${r.buy_index !== null && r.buy_index !== undefined ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
      <td>${relatedTags(r, false)}</td>
    </tr>`).join("")}</tbody></table>
    <div class="muted" style="margin-top:6px;font-size:calc(12px * var(--font-scale))">「${esc(sector)}」${stocks.length} 只。${FLOW_NOTE} 点击行进入个股分析。</div>`;
}

function parseSectors(v) {
  if (!v) return [];
  if (Array.isArray(v)) return v.map((s) => String(s).trim()).filter(Boolean);
  return String(v).split(/[,，、/|]+/).map((s) => s.trim()).filter(Boolean);
}
function sectorBadges(sectors, title) {
  return parseSectors(sectors).map((s) =>
    `<span class="badge sector-tag js-sector" data-sector="${esc(s)}" data-title="${esc(title || s)}" title="点击查看「${esc(s)}」相关个股 TOP20–50">${esc(s)}</span>`
  ).join("");
}

const relatedQuery = { title: "", sectors: "", limit: 20 };

function buyCls(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "flat";
  return v >= 65 ? "up" : v <= 35 ? "down" : "flat";
}
function relatedTags(r, withFin = true) {
  const bits = [wxBadges(r.wuxing)];
  if (r.buy_level) {
    bits.push(`<span class="badge ${buyCls(r.buy_index)}" style="font-size:calc(11px * var(--font-scale));padding:2px 7px">${esc(r.buy_level)}</span>`);
  }
  if (r.advice) {
    const ac = r.advice === "增持" ? "advice-buy" : r.advice === "减持" ? "advice-sell" : "advice-hold";
    bits.push(`<span class="badge ${ac}" style="font-size:calc(11px * var(--font-scale));padding:2px 7px">${esc(r.advice)}</span>`);
  }
  if (withFin) bits.push(finBadge(r));
  return bits.filter(Boolean).join(" ");
}
function renderRelatedStockTable(stocks) {
  if (!stocks || !stocks.length) return '<div class="muted">未匹配到相关个股</div>';
  return `<table><thead><tr>
    <th>名称</th><th>代码</th><th>现价</th><th>财报</th><th>涨跌幅</th><th>量比</th>${flowMetricHeaders()}<th>购买指数</th><th>标签</th>
  </tr></thead><tbody>${stocks.map((r) => `
    <tr data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
      <td>${esc(r.name)}</td>
      <td class="muted">${esc(r.code)}</td>
      <td class="num">${pxHtml(r.price, r.pct)}</td>
      <td>${finBadge(r)}</td>
      <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
      <td class="num">${fmt(r.volume_ratio)}</td>${flowMetricCells(r)}
      <td class="num ${buyCls(r.buy_index)}">${r.buy_index !== null && r.buy_index !== undefined ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
      <td>${relatedTags(r, false)}</td>
    </tr>`).join("")}</tbody></table>
    <div class="muted" style="margin-top:6px;font-size:calc(12px * var(--font-scale))">展示 ${stocks.length} 只（TOP20–50）。${FLOW_NOTE} 点击行进入个股分析。</div>`;
}

window.showNewsStocks = (sectors, title) => showEventDetail(title, sectors);

window.showMergedBoards = async (title, merged, focusSector) => {
  const box = $("#eventDetailBox");
  if (!box) return;
  const split = exclusiveBoards((merged && merged.bull) || [], (merged && merged.bear) || []);
  const bull = split.bull;
  const bear = split.bear;
  const chip = (s, kind) => {
    const on = s === focusSector ? " active" : "";
    return `<span class="badge dir-${kind} js-sector${on}" data-sector="${esc(s)}" data-title="${esc(title || s)}" title="点击查看该板块个股 TOP20–50">${esc(s)}</span>`;
  };
  box.innerHTML = `
    <div class="outlook-summary" style="border-color:rgba(255,169,64,.4)">
      <b>📌 ${esc(title || "影响板块")} — 利好 / 利空</b>
      <button class="btn small ghost" style="float:right" onclick="this.closest('#eventDetailBox').innerHTML=''">收起</button>
      <div class="kv"><span class="k">利好板块</span><span>${bull.length ? bull.map((s) => chip(s, "利好")).join("") : '<span class="muted">暂无明确利好板块</span>'}</span></div>
      <div class="kv"><span class="k">利空板块</span><span>${bear.length ? bear.map((s) => chip(s, "利空")).join("") : '<span class="muted">暂无明确利空板块</span>'}</span></div>
      <div class="muted" style="margin:8px 0 4px">已合并情报拉取映射与 AI 回填。点击板块查看相关个股，默认 TOP20，可选 TOP30 / TOP50。</div>
      <div id="eventStockBox">${focusSector ? "" : '<div class="muted">请点击上方利好或利空板块</div>'}</div>
    </div>`;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  if (focusSector) await loadSectorStocks(focusSector, $("#eventStockBox"));
};

window.showEventDetail = async (title, sectors = "", extra = {}) => {
  const merged = mergeIntelSectors(
    (extra && extra.key) || "",
    (extra && extra.bull) || sectors,
    (extra && extra.direction) || "",
  );
  await showMergedBoards(title, merged, (extra && extra.sector) || "");
};

$("#page-macro")?.addEventListener("click", (e) => {
  const aiBtn = e.target.closest(".js-intel-ai");
  if (aiBtn) {
    e.preventDefault();
    e.stopPropagation();
    showSavedIntelAi(aiBtn.dataset.key || "");
    return;
  }
  const hiSec = e.target.closest(".js-hot-intel-sec");
  if (hiSec) {
    e.preventDefault();
    e.stopPropagation();
    openHotIntelSector(hiSec.dataset.sector || "", hiSec.dataset.key || "");
    return;
  }
  const hi = e.target.closest(".js-hot-intel");
  if (hi) {
    e.preventDefault();
    e.stopPropagation();
    openHotIntelCard(hi.dataset.key || "");
    return;
  }
  const hotT = e.target.closest(".js-hot-term");
  if (hotT) {
    e.preventDefault();
    e.stopPropagation();
    openHotTerm(hotT.dataset.term || "");
    return;
  }
  const hotS = e.target.closest(".js-hot-sector");
  if (hotS) {
    e.preventDefault();
    e.stopPropagation();
    openHotSector(hotS.dataset.sector || "", hotS.dataset.term || "");
    return;
  }
  const tag = e.target.closest(".js-sector");
  if (tag) {
    e.preventDefault();
    e.stopPropagation();
    const sec = tag.dataset.sector || "";
    if (!sec || sec === "无明显利空" || sec === "政策" || sec === "未映射影响板块") return;
    if (tag.closest("#eventDetailBox") && $("#eventStockBox")) {
      $$("#eventDetailBox .js-sector").forEach((el) => el.classList.toggle("active", el === tag));
      loadSectorStocks(sec, $("#eventStockBox"));
      return;
    }
    const row = tag.closest(".js-intel-row, .js-news-item, .js-event-row");
    const key = (row && row.dataset.intelKey) || "";
    const title = (row && (row.dataset.intelTitle || row.dataset.title)) || tag.dataset.title || sec;
    showMergedBoards(title, mergeIntelSectors(key, row && row.dataset.sectors, row && row.dataset.direction), sec);
    return;
  }
  const allBtn = e.target.closest(".js-news-stocks");
  if (allBtn) {
    e.preventDefault();
    e.stopPropagation();
    const row = allBtn.closest(".js-intel-row, .js-news-item, .js-event-row");
    const key = (row && row.dataset.intelKey) || "";
    const title = allBtn.dataset.title || (row && (row.dataset.intelTitle || row.dataset.title)) || "相关个股";
    showMergedBoards(title, mergeIntelSectors(key, (row && row.dataset.sectors) || allBtn.dataset.sectors, row && row.dataset.direction), "");
    return;
  }
  if (e.target.closest("button, input, select, a, table")) return;
  if (e.target.closest(".js-kb-item, .js-kb-section")) return;
  const row = e.target.closest(".js-event-row, .js-news-item");
  if (row && !row.closest("#eventDetailBox") && (row.dataset.title || row.dataset.sectors || row.dataset.intelKey)) {
    const key = row.dataset.intelKey || "";
    const title = row.dataset.intelTitle || row.dataset.title || "相关个股";
    const merged = mergeIntelSectors(key, row.dataset.sectors, row.dataset.direction);
    if (!(merged.bull || []).length && !(merged.bear || []).length) return;
    showMergedBoards(title, merged, "");
  }
});

window.addCustomEvent = async () => {
  const res = await api("/api/macro/custom-event", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ date: $("#ceDate").value.trim(), title: $("#ceTitle").value.trim(),
                           impact_level: Number($("#ceLevel").value),
                           sectors: ($("#ceSectors")?.value || "").trim() }),
  });
  if (!res.ok) { alert(res.error); return; }
  loadMacro();
};
window.delCustomEvent = async (id) => { await post(`/api/macro/custom-event/delete?event_id=${id}`); loadMacro(); };

/* ---------------- 个股筛选器（FR2-01） ---------------- */
let screenerMeta = null;
const screenerState = { boards: [], industries: [], mv: [], pct_today: [], pct_d5: [], pct_d20: [],
  turnover: [], volume_ratio: [], main_flow: [], main_flow_d5: [], pe: [], pb: [], tech: [],
  finance_grade: [],
  price_min: "", price_max: "", exclude_st: true, order_by: "buy_index" };
const GROUP_LABELS = { mv: "流通市值", pct_today: "今日涨跌", pct_d5: "5日涨跌", pct_d20: "20日涨跌",
  turnover: "换手率", volume_ratio: "量能", main_flow: "主力资金", main_flow_d5: "5日资金",
  pe: "PE(TTM)", pb: "PB", tech: "技术形态", finance_grade: "财报评级" };

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
    <div class="g-label">${label}${count ? ` <span class="badge sector-tag" style="font-size:calc(10px * var(--font-scale));padding:0 6px">${count}</span>` : ""}
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
    <label style="color:var(--muted);font-size:calc(13px * var(--font-scale))">
      <input type="checkbox" id="excludeSt" ${screenerState.exclude_st ? "checked" : ""}> 剔除 ST/退市
    </label>
    <span class="muted">已选条件 ${totalConds} 项
      ${m.finance_universe ? ` · 已评级 ${m.finance_graded || 0} / 全市场 ${m.finance_universe}` : ""}</span></div>`;
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
  $("#condChips").innerHTML = chips.join("") || '<span class="muted" style="font-size:calc(12px * var(--font-scale))">未设置条件（默认展示全市场按购买指数排序）</span>';
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
      <th>#</th><th>名称</th><th>财报</th><th>五行</th><th>行业</th><th>最新价</th><th>涨跌幅</th><th>购买指数</th>
      <th>情绪</th><th>暗盘力量</th><th>量比</th>${flowMetricHeaders()}<th>PE</th><th>企稳</th>
    </tr></thead><tbody>${d.items.map((r, i) => `
      <tr data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${i + 1}</td><td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td>${finBadge(r)}</td>
        <td>${wxBadges(r.wuxing)}</td>
        <td>${esc(r.industry || "-")}</td>
        <td class="num">${pxHtml(r.price, r.pct)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num">${r.buy_index !== null ? `<b>${fmt(r.buy_index, 0)}</b> <span class="muted">${esc(r.buy_level || "")}</span>` : "-"}</td>
        <td>${r.sent_level ? esc(r.sent_level) : "-"}</td>
        <td class="num">${fmt(r.dark_power, 0)}${r.divergence && r.divergence !== "无" ? ` <span class="badge sector-tag" style="font-size:calc(10px * var(--font-scale))">${esc(r.divergence)}</span>` : ""}</td>
        <td class="num">${fmt(r.volume_ratio)}</td>${flowMetricCells(r)}
        <td class="num">${fmt(r.pe_ttm, 1)}</td>
        <td>${r.stabilize_score !== null ? `<span class="badge level-4" style="font-size:calc(11px * var(--font-scale))">${fmt(r.stabilize_score, 0)}</span>` : "-"}</td>
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:8px;font-size:calc(12px * var(--font-scale))">指标为量化参考，不构成投资建议。${FLOW_NOTE}</div>`
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
  for (const g of Object.keys(GROUP_LABELS)) screenerState[g] ||= [];
  screenerState.boards ||= []; screenerState.industries ||= [];
  renderScreenerConditions();
  runScreener();
};
window.deletePlan = async (id) => { await post(`/api/screener/plans/delete?plan_id=${id}`); loadPlans(); };

/* ---------------- 板块资金（FR3-01） ---------------- */
let sectorDim = "industry";
let sectorView = "map";
let sectorChart = null;
let sectorPage = "flow";

function showSectorPage(page) {
  if (!page) return;
  sectorPage = page;
  $$("#sectorPageTabs .opt").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  const flow = $("#sectorFlowView"), rec = $("#sectorRecView"), bar = $("#sectorBarView");
  if (flow) flow.style.display = page === "flow" ? "" : "none";
  if (rec) rec.style.display = page === "recommend" ? "" : "none";
  if (bar) bar.style.display = page === "flowbar" ? "" : "none";
  try {
    if (page === "flow") { loadSector(); sectorChart?.resize(); }
    else if (page === "recommend") loadSectorRecommend();
    else if (page === "flowbar") {
      loadFlowBar();
      setTimeout(() => flowTrendChart?.resize(), 300);
    }
  } catch (err) { console.warn(err); }
}
$("#sectorPageTabs")?.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-page]");
  if (!btn) return;
  showSectorPage(btn.dataset.page);
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
  if (sectorPage === "flowbar") { loadFlowBar(); return; }
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
          <th>名称</th><th>财报</th><th>五行</th><th>现价</th><th>涨跌幅</th><th>量比</th>${flowMetricHeaders()}<th>购买指数</th><th>评分</th><th>提示</th>
        </tr></thead><tbody>${(sec.stocks || []).map((r) => `
          <tr data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
            <td>${esc(r.name)} <span class="muted">${r.code}</span></td>
            <td>${finBadge(r)}</td>
            <td>${wxBadges(r.wuxing)}</td>
            <td class="num">${pxHtml(r.price, r.pct)}</td>
            <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
            <td class="num">${fmt(r.volume_ratio)}</td>${flowMetricCells(r)}
            <td class="num">${r.buy_index !== null ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
            <td class="num"><b>${fmt(r.score, 1)}</b></td>
            <td><span class="badge ${r.advice === "增持" ? "advice-buy" : r.advice === "减持" ? "advice-sell" : "advice-hold"}" style="font-size:calc(11px * var(--font-scale));padding:2px 7px">${esc(r.advice || "-")}</span></td>
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
  sectorChart ||= makeChart($("#sectorMap"));
  const pal = cp();
  const data = items.map((it) => ({
    name: it.name,
    value: it.amount_yi || 0,
    itemStyle: { color: flowColor(it.net_in_ratio) },
    _meta: it,
  }));
  sectorChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      ...ttStyle(),
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
        show: true, fontSize: fs(12), color: pal.text,
        formatter: (p) => {
          const m = p.data._meta || {};
          return `${p.name}\n${m.pct > 0 ? "+" : ""}${m.pct}%  ${m.net_in_yi > 0 ? "流入" : "流出"}${Math.abs(m.net_in_yi)}亿`;
        },
      },
      itemStyle: { borderColor: pal.bg || cssVar("--bg"), borderWidth: 2, gapWidth: 2 },
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
      <th>名称</th><th>财报</th><th>最新价</th><th>涨跌幅</th><th>量比</th>${flowMetricHeaders()}<th>购买指数</th><th>情绪</th><th>暗盘力量</th>
    </tr></thead><tbody>${rows.map((r) => `
      <tr onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td>${finBadge(r)}</td>
        <td class="num">${pxHtml(r.price, r.pct)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num">${fmt(r.volume_ratio)}</td>${flowMetricCells(r)}
        <td class="num">${r.buy_index !== null ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
        <td>${r.sent_level ? esc(r.sent_level) : "-"}</td>
        <td class="num">${fmt(r.dark_power, 0)}</td>
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:6px;font-size:calc(12px * var(--font-scale))">${FLOW_NOTE}</div>` : '<div class="empty">暂无成分股数据</div>';
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { $("#sectorDrillTable").innerHTML = '<div class="empty">加载失败</div>'; }
};

/* ---------------- 板块资金分析（FR10-03） ---------------- */
const flowBarState = { dim: "industry", range: "1d", sort: "inflow", selected: "", limit: 50,
  view: "hbar", dir: "", q: "", minStocks: 0, fin: "", day: "", trendGrain: "1d" };
let flowBarChart = null;
let flowTrendChart = null;
let flowBarRawItems = [];
let flowBarStockRows = [];

for (const [id, key] of [["flowBarDim", "dim"], ["flowBarRange", "range"]]) {
  const el = $(`#${id}`);
  if (!el) continue;
  el.addEventListener("click", (e) => {
    const btn = e.target.closest(".opt");
    if (!btn) return;
    const val = btn.dataset[key] || btn.getAttribute(`data-${key}`);
    if (!val) return;
    flowBarState[key] = val;
    flowBarState.selected = "";
    flowBarState.day = "";
    flowBarState.limit = 50;
    $$(`#${id} .opt`).forEach((b) => b.classList.toggle("active", b === btn));
    const stockBox = $("#flowBarStocks");
    if (stockBox) stockBox.style.display = "none";
    loadFlowBar();
  });
}
$("#flowBarSort")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  const val = btn.dataset.sort || "";
  if (!val) return;
  flowBarState.sort = val;
  $$("#flowBarSort .opt").forEach((b) => b.classList.toggle("active", b === btn));
  loadFlowBar();
});
for (const [id, key] of [["flowBarDir", "dir"], ["flowBarView", "view"]]) {
  const el = $(`#${id}`);
  if (!el) continue;
  el.addEventListener("click", (e) => {
    const btn = e.target.closest(".opt");
    if (!btn) return;
    const raw = btn.getAttribute(`data-${key}`);
    flowBarState[key] = raw === "all" ? "" : (raw || "");
    $$(`#${id} .opt`).forEach((b) => b.classList.toggle("active", b === btn));
    if (key === "view") paintFlowBar();
    else loadFlowBar();
  });
}
$("#flowBarMin")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  flowBarState.minStocks = Number(btn.dataset.min || 0);
  $$("#flowBarMin .opt").forEach((b) => b.classList.toggle("active", b === btn));
  loadFlowBar();
});
$("#flowBarSearch")?.addEventListener("input", debounce(() => {
  flowBarState.q = ($("#flowBarSearch").value || "").trim();
  loadFlowBar();
}, 200));
$("#flowTrendGrain")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  const val = btn.dataset.grain || "";
  if (!val) return;
  flowBarState.trendGrain = val;
  $$("#flowTrendGrain .opt").forEach((b) => b.classList.toggle("active", b === btn));
  loadFlowTrend();
});
$("#flowBarFin")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  flowBarState.fin = btn.dataset.fin || "";
  $$("#flowBarFin .opt").forEach((b) => b.classList.toggle("active", b === btn));
  if (flowBarState.selected) renderFlowBarStockTable(flowBarStockRows);
});

function filterFlowBarItems(items) {
  const q = (flowBarState.q || "").toLowerCase();
  return (items || []).filter((it) => {
    const v = Number(it.net_in_yi) || 0;
    if (flowBarState.dir === "in" && v <= 0) return false;
    if (flowBarState.dir === "out" && v >= 0) return false;
    if ((flowBarState.minStocks || 0) > 0 && (it.stocks || 0) < flowBarState.minStocks) return false;
    if (q && !String(it.name || "").toLowerCase().includes(q)) return false;
    return true;
  });
}

function renderFlowBarHtml(items, note) {
  const maxAbs = Math.max(...items.map((it) => Math.abs(Number(it.net_in_yi) || 0)), 0.01);
  return (note ? `<div class="muted" style="margin-bottom:8px">${esc(note)}</div>` : "") +
    `<div class="muted" style="margin-bottom:6px">共 ${items.length} 个板块 · 点击横条查看个股</div>` +
    `<div class="hbar-axis"><span class="hbar-name">-</span><span class="hbar-track"></span><span class="hbar-val">亿</span></div>` +
    items.map((it) => {
      const v = Number(it.net_in_yi) || 0;
      const pctw = Math.min(100, Math.abs(v) / maxAbs * 100);
      const selected = flowBarState.selected === it.name ? " selected" : "";
      const fill = v >= 0
        ? `<div class="hbar-neg"></div><div class="hbar-pos"><div class="hbar-fill in" style="width:${pctw}%"></div></div>`
        : `<div class="hbar-neg"><div class="hbar-fill out" style="width:${pctw}%"></div></div><div class="hbar-pos"></div>`;
      const tip = `${it.name} 账本 ${v > 0 ? "+" : ""}${fmt(v, 2)}亿`
        + (it.snap_d1_yi != null ? ` · 快照当日对照 ${Number(it.snap_d1_yi) > 0 ? "+" : ""}${fmt(it.snap_d1_yi, 2)}亿` : "")
        + (it.snap_d5_yi != null ? ` · 快照5日对照 ${fmt(it.snap_d5_yi, 2)}亿（不计入柱高）` : "");
      return `<div class="hbar-row${selected}" data-name="${esc(it.name)}" title="${esc(tip)}">
        <span class="hbar-name" title="${esc(tip)}">${esc(it.name)}</span>
        <span class="hbar-track">${fill}</span>
        <span class="hbar-val num ${cls(v)}">${v > 0 ? "+" : ""}${fmt(v, 1)}亿</span>
      </div>`;
    }).join("");
}

function bindFlowBarClicks(box) {
  box.querySelectorAll(".hbar-row").forEach((el) => {
    el.addEventListener("click", () => selectFlowBar(el.dataset.name || ""));
  });
}

function renderFlowBarEcharts(items) {
  const host = $("#flowBarEchart");
  if (!host || typeof echarts === "undefined") return;
  const h = Math.max(420, items.length * 26 + 40);
  host.style.height = h + "px";
  host.style.display = "";
  try { flowBarChart?.dispose(); } catch { /* ignore */ }
  flowBarChart = makeChart(host);
  const names = items.map((it) => it.name);
  const values = items.map((it) => Number(it.net_in_yi) || 0);
  const pal = cp();
  flowBarChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis", axisPointer: { type: "shadow" },
      ...ttStyle(),
      formatter: (ps) => {
        const p = ps[0] || {};
        const v = p.value || 0;
        const it = items[p.dataIndex] || {};
        let extra = "";
        if (it.snap_d1_yi != null) extra += `<br>快照当日对照 ${Number(it.snap_d1_yi).toFixed(2)} 亿`;
        if (it.snap_d5_yi != null) extra += `<br>快照5日对照 ${Number(it.snap_d5_yi).toFixed(2)} 亿（不计入柱高）`;
        return `${p.name}<br>账本净流入 <b>${v > 0 ? "+" : ""}${Number(v).toFixed(2)} 亿</b>${extra}`;
      },
    },
    grid: { left: 88, right: 56, top: 8, bottom: 8 },
    xAxis: {
      type: "value", splitLine: { lineStyle: { color: pal.split } },
      axisLabel: { color: pal.muted, formatter: (v) => `${v}亿` },
    },
    yAxis: {
      type: "category", data: names, inverse: true,
      axisLabel: { color: pal.text, fontSize: fs(12) },
      axisLine: { lineStyle: { color: pal.border } },
    },
    series: [{
      type: "bar", data: values, barMaxWidth: 16,
      itemStyle: { color: (p) => (p.value >= 0 ? pal.up : pal.down) },
      label: {
        show: true,
        position: (p) => (p.value >= 0 ? "right" : "left"),
        color: pal.muted, fontSize: fs(11),
        formatter: (p) => `${p.value > 0 ? "+" : ""}${Number(p.value).toFixed(1)}`,
      },
    }],
  }, true);
  flowBarChart.off("click");
  flowBarChart.on("click", (p) => { if (p && p.name) selectFlowBar(p.name); });
  requestAnimationFrame(() => flowBarChart?.resize());
}

function paintFlowBar(note) {
  const box = $("#flowBarList");
  const chart = $("#flowBarEchart");
  const items = filterFlowBarItems(flowBarRawItems);
  const dimLabel = flowBarState.dim === "concept" ? "题材概念" : "行业板块";
  const noteEl = $("#flowBarNote");
  if (noteEl) {
    const extra = noteEl.dataset.base || "";
    noteEl.textContent = `${extra} · 当前显示 ${items.length} 个${dimLabel}`;
  }
  if (!flowBarRawItems.length) {
    if (box) box.innerHTML = `<div class="empty">${esc(note) || "暂无板块资金（请先在设置页执行全量同步）"}</div>`;
    if (chart) chart.style.display = "none";
    return;
  }
  if (flowBarState.view === "chart") {
    if (box) box.innerHTML = "";
    if (chart) chart.style.display = "";
    renderFlowBarEcharts(items);
  } else {
    if (chart) chart.style.display = "none";
    try { flowBarChart?.dispose(); } catch { /* ignore */ }
    flowBarChart = null;
    if (box) {
      box.innerHTML = renderFlowBarHtml(items, note);
      bindFlowBarClicks(box);
    }
  }
}

function paintFlowDayChip() {
  const chip = $("#flowBarDayChip");
  if (!chip) return;
  if (!flowBarState.day) {
    chip.style.display = "none";
    chip.innerHTML = "";
    return;
  }
  chip.style.display = "";
  chip.innerHTML = `正在看账本 <b>${esc(flowBarState.day)}</b> 当日横截面`
    + ` <button class="btn small ghost" type="button" onclick="clearFlowDay()">看区间合计</button>`;
}

function flowFilterQuery() {
  let qs = `dim=${encodeURIComponent(flowBarState.dim)}&range=${encodeURIComponent(flowBarState.range)}&sort=${encodeURIComponent(flowBarState.sort)}`;
  if (flowBarState.day) qs += `&day=${encodeURIComponent(flowBarState.day)}`;
  if (flowBarState.dir) qs += `&dir=${encodeURIComponent(flowBarState.dir)}`;
  if (flowBarState.minStocks) qs += `&min_stocks=${encodeURIComponent(flowBarState.minStocks)}`;
  if (flowBarState.q) qs += `&q=${encodeURIComponent(flowBarState.q)}`;
  return qs;
}

function flowFilterLabel() {
  const dimLabel = flowBarState.dim === "concept" ? "题材概念" : "行业板块";
  const rangeMap = { "1d": "当天", "3d": "过去3天", "5d": "过去5天", "10d": "过去10天", "20d": "过去20天", "1m": "过去一月", "3m": "过去3个月" };
  const sortMap = { inflow: "流入最多", outflow: "流出最多", abs: "绝对额" };
  const dirMap = { in: "仅流入", out: "仅流出", "": "全部方向" };
  const bits = [dimLabel, rangeMap[flowBarState.range] || flowBarState.range, sortMap[flowBarState.sort] || "", dirMap[flowBarState.dir] || "全部方向"];
  if (flowBarState.minStocks) bits.push(`成分股≥${flowBarState.minStocks}`);
  if (flowBarState.q) bits.push(`名称含「${flowBarState.q}」`);
  if (flowBarState.day) bits.push(`横截面 ${flowBarState.day}`);
  return bits.filter(Boolean).join(" · ");
}

async function loadFlowBar() {
  const box = $("#flowBarList");
  if (!box && !$("#flowBarChart")) return;
  if (box) box.innerHTML = '<div class="empty">正在汇总全市场板块资金…</div>';
  paintFlowDayChip();
  try {
    const d = await api(`/api/sector/flow-bar?${flowFilterQuery()}`);
    const cov = d.coverage || {};
    const note = d.note || "";
    const noteEl = $("#flowBarNote");
    if (noteEl) {
      noteEl.dataset.base = `${flowFilterLabel()} · ${d.count || (d.items || []).length} 个板块`
        + ` · 账本 ${d.asof || "-"} · ${cov.have || 0}/${cov.target || 0} 日`
        + (d.value_source === "snapshot_d5" ? " · 柱高=快照近5日" : " · 柱高=账本合计");
    }
    flowBarRawItems = d.items || [];
    paintFlowBar(note);
    loadFlowTrend();
    if (flowBarState.selected) loadFlowBarStocks(flowBarState.selected);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    if (box) box.innerHTML = `<div class="empty">板块资金加载失败：${esc(msg)}</div>`;
    console.warn(err);
  }
}

window.selectFlowBar = (name) => {
  if (!name) return;
  flowBarState.selected = flowBarState.selected === name ? "" : name;
  $$("#flowBarList .hbar-row").forEach((el) =>
    el.classList.toggle("selected", el.dataset.name === flowBarState.selected));
  loadFlowTrend();
  const stockBox = $("#flowBarStocks");
  if (!flowBarState.selected) {
    if (stockBox) stockBox.style.display = "none";
    return;
  }
  loadFlowBarStocks(flowBarState.selected);
};

window.clearFlowDay = () => {
  if (!flowBarState.day) return;
  flowBarState.day = "";
  loadFlowBar();
};

window.selectFlowDay = (day) => {
  if (!day) return;
  flowBarState.day = flowBarState.day === day ? "" : day;
  loadFlowBar();
};

function kpiNum(v, digits) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
  const n = Number(v);
  return `${n > 0 ? "+" : ""}${fmt(n, digits)}亿`;
}
function yiWan(v, digits) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
  const n = Number(v);
  if (Math.abs(n) >= 10000) return `${(n / 10000).toFixed(digits == null ? 2 : digits)}万亿`;
  return `${fmt(n, digits == null ? 0 : digits)}亿`;
}

async function loadFlowTrend() {
  const host = $("#flowTrendChart");
  const noteEl = $("#flowTrendNote");
  const kpisEl = $("#flowTrendKpis");
  if (!host) return;
  if (noteEl) noteEl.textContent = "加载中…";
  try {
    let url = `/api/sector/flow-trend?dim=${encodeURIComponent(flowBarState.dim)}&grain=${encodeURIComponent(flowBarState.trendGrain || "1d")}`;
    if (flowBarState.selected) url += `&name=${encodeURIComponent(flowBarState.selected)}`;
    const d = await api(url);
    if (noteEl) noteEl.textContent = d.title ? `· ${d.title}` : "";
    const k = d.kpis || {};
    if (kpisEl) {
      const oneDay = (d.dates || []).length < 2;
      kpisEl.innerHTML = `
        <div class="flow-kpi"><span>横轴</span><b>${esc(k.grain_label || d.grain_label || "日")}</b></div>
        <div class="flow-kpi"><span>${oneDay ? "板块数" : "折线条数"}</span><b>${k.line_count || (d.lines || []).length}</b></div>
        <div class="flow-kpi"><span>账本交易日</span><b>${k.days_have || 0}</b></div>
        <div class="flow-kpi"><span>全A成交额</span><b>${esc(yiWan(k.market_amount_yi))}</b></div>
        <div class="flow-kpi"><span>上证成交额</span><b>${esc(yiWan(k.sh_amount_yi))}</b></div>
        <div class="flow-kpi"><span>深证成交额</span><b>${esc(yiWan(k.sz_amount_yi))}</b></div>
        <div class="flow-kpi"><span>北证成交额</span><b>${esc(yiWan(k.bj_amount_yi, 1))}</b></div>
        <div class="flow-kpi"><span>全A成交量</span><b>${k.market_volume_yi != null ? fmt(k.market_volume_yi, 2) + "亿手" : "-"}</b></div>
        <div class="flow-kpi"><span>量比</span><b>${k.vol_ratio != null ? fmt(k.vol_ratio, 2) : "-"}</b></div>`;
    }
    paintFlowTrend(d);
  } catch (err) {
    if (noteEl) noteEl.textContent = "走势加载失败";
    if (kpisEl) kpisEl.innerHTML = "";
    if (host) host.innerHTML = `<div class="empty">日走势加载失败：${esc(err.message || err)}</div>`;
  }
}

function renderFlowTrendTable(lines) {
  const box = $("#flowTrendTable");
  if (!box) return;
  const rows = (lines || []).slice().sort((a, b) => Math.abs(b.sum_yi || 0) - Math.abs(a.sum_yi || 0));
  if (!rows.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<table><thead><tr><th>板块</th><th class="num">区间净流入</th></tr></thead><tbody>${
    rows.slice(0, 24).map((r) => {
      const v = Number(r.sum_yi) || 0;
      return `<tr class="js-trend-row" data-name="${esc(r.local_name || r.name)}" style="cursor:pointer">
        <td>${esc(r.name)}${r.local_name && r.local_name !== r.name ? ` <span class="muted">${esc(r.local_name)}</span>` : ""}</td>
        <td class="num ${cls(v)}">${v > 0 ? "+" : ""}${fmt(v, 2)} 亿</td>
      </tr>`;
    }).join("")
  }</tbody></table>
  <div class="muted" style="margin-top:6px;font-size:calc(12px * var(--font-scale))">共 ${rows.length} 个板块 · 点行可选中并联动直方图</div>`;
  box.querySelectorAll(".js-trend-row").forEach((el) => {
    el.addEventListener("click", () => selectFlowBar(el.dataset.name || ""));
  });
}

function resetFlowTrendHost(host) {
  try { flowTrendChart?.dispose(); } catch { /* ignore */ }
  flowTrendChart = null;
  host.innerHTML = "";
}

function paintFlowTrendOneDay(host, lines, dateLabel) {
  resetFlowTrendHost(host);
  host.style.height = "auto";
  host.style.minHeight = "0";
  host.style.maxHeight = "640px";
  host.style.overflow = "auto";
  const items = (lines || []).map((l) => ({
    name: l.name,
    local_name: l.local_name || l.name,
    net_in_yi: Number((l.data && l.data[0]) != null ? l.data[0] : l.sum_yi) || 0,
  }));
  const maxAbs = Math.max(...items.map((it) => Math.abs(it.net_in_yi)), 0.01);
  host.innerHTML = `<div class="muted" style="margin-bottom:6px">${esc(dateLabel || "当日")} · 共 ${items.length} 个板块 · 账本仅 1 日，先画当日净流入横条</div>`
    + `<div class="hbar-axis"><span class="hbar-name">-</span><span class="hbar-track"></span><span class="hbar-val">亿</span></div>`
    + items.map((it) => {
      const v = it.net_in_yi;
      const pctw = Math.min(100, Math.abs(v) / maxAbs * 100);
      const selected = flowBarState.selected === it.local_name || flowBarState.selected === it.name ? " selected" : "";
      const fill = v >= 0
        ? `<div class="hbar-neg"></div><div class="hbar-pos"><div class="hbar-fill in" style="width:${pctw}%"></div></div>`
        : `<div class="hbar-neg"><div class="hbar-fill out" style="width:${pctw}%"></div></div><div class="hbar-pos"></div>`;
      return `<div class="hbar-row${selected}" data-name="${esc(it.local_name || it.name)}" title="${esc(it.name)}">
        <span class="hbar-name">${esc(it.name)}</span>
        <span class="hbar-track">${fill}</span>
        <span class="hbar-val num ${cls(v)}">${v > 0 ? "+" : ""}${fmt(v, 1)}亿</span>
      </div>`;
    }).join("");
  host.querySelectorAll(".hbar-row").forEach((el) => {
    el.addEventListener("click", () => selectFlowBar(el.dataset.name || ""));
  });
}

function paintFlowTrend(d) {
  const host = $("#flowTrendChart");
  if (!host) return;
  const dates = d.dates || [];
  const lines = d.lines || [];
  const yName = d.y_name || "净流入(亿)";
  renderFlowTrendTable(lines);
  const noteEl = $("#flowTrendNote");
  if (noteEl && d.note) noteEl.textContent = `· ${d.title || ""} · ${d.note}`;
  const mv0 = d.market_vol || {};
  const hasVol0 = (mv0.volume || []).some((v) => v != null);
  if (!dates.length || (!lines.length && !hasVol0)) {
    resetFlowTrendHost(host);
    host.style.height = "220px";
    host.style.maxHeight = "";
    host.style.overflow = "";
    host.innerHTML = `<div class="empty">${esc(d.note || "尚无日频点。设置页可点「拉取板块资金」。")}</div>`;
    return;
  }
  if (dates.length < 2 && lines.length) {
    paintFlowTrendOneDay(host, lines, dates[0] || d.asof);
    return;
  }
  if (typeof echarts === "undefined") {
    resetFlowTrendHost(host);
    host.style.height = "auto";
    host.innerHTML = `<div class="empty">图表库未加载，下方表格仍可看各板块净流入。</div>`;
    return;
  }
  const mv = d.market_vol || {};
  const volData = mv.volume || [];
  const amtData = mv.amount || [];
  const volUp = mv.up || [];
  const volName = mv.name || "大A量能";
  const draw = (tries) => {
    if (host.offsetWidth < 40 && tries < 25) {
      setTimeout(() => draw(tries + 1), 80);
      return;
    }
    resetFlowTrendHost(host);
    host.style.height = "560px";
    host.style.minHeight = "560px";
    host.style.maxHeight = "";
    host.style.overflow = "";
    flowTrendChart = makeChart(host);
    const palette = ["#ff5252", "#4a9eff", "#26c281", "#ffa940", "#c084fc", "#22d3ee", "#f472b6", "#a3e635", "#fb7185", "#60a5fa", "#fbbf24", "#34d399", "#e879f9", "#38bdf8", "#f97316", "#84cc16"];
    const hasVol = volData.some((v) => v != null);
    flowTrendChart.setOption({
      backgroundColor: "transparent",
      color: palette,
      axisPointer: { link: [{ xAxisIndex: "all" }], type: "cross" },
      tooltip: {
        trigger: "axis",
        ...ttStyle(),
        formatter: (ps) => {
          if (!ps || !ps.length) return "";
          const idx = ps[0].dataIndex;
          const head = [`${ps[0].axisValue || ""}`];
          ps.filter((p) => p.seriesType === "line").forEach((p) => {
            if (p.value == null) return;
            const v = Number(p.value);
            head.push(`${p.marker}${p.seriesName} <b>${v > 0 ? "+" : ""}${v.toFixed(2)} 亿</b>`);
          });
          if (volData[idx] != null) head.push(`大A量能 <b>${Number(volData[idx]).toFixed(2)} 亿手</b>`);
          if (amtData[idx] != null) head.push(`全A成交额 <b>${yiWan(amtData[idx])}</b>`);
          return head.join("<br>");
        },
      },
      legend: {
        type: "scroll", top: 0, textStyle: { color: "#9aa8bc", fontSize: 11 },
        data: lines.map((l) => l.name).concat(hasVol ? [volName] : []),
      },
      grid: hasVol ? [
        { left: 58, right: 24, top: 44, height: "48%" },
        { left: 58, right: 24, top: "68%", height: "16%" },
      ] : { left: 58, right: 24, top: 48, bottom: dates.length >= 4 ? 72 : 36 },
      dataZoom: dates.length >= 4 ? [
        { type: "inside", xAxisIndex: hasVol ? [0, 1] : 0, filterMode: "none" },
        { type: "slider", xAxisIndex: hasVol ? [0, 1] : 0, height: 16, bottom: 6,
          borderColor: "#2a3548", fillerColor: "rgba(74,158,255,.15)",
          textStyle: { color: "#7d8aa0" } },
      ] : [],
      xAxis: hasVol ? [
        { type: "category", data: dates, gridIndex: 0, boundaryGap: true,
          axisLabel: { show: false }, axisTick: { show: false },
          axisLine: { lineStyle: { color: "#2a3548" } } },
        { type: "category", data: dates, gridIndex: 1, boundaryGap: true,
          axisLabel: { color: "#7d8aa0", fontSize: 11, hideOverlap: true },
          axisLine: { lineStyle: { color: "#2a3548" } } },
      ] : {
        type: "category", data: dates, boundaryGap: true,
        axisLabel: { color: "#7d8aa0", fontSize: 11, hideOverlap: true },
        axisLine: { lineStyle: { color: "#2a3548" } },
      },
      yAxis: hasVol ? [
        { type: "value", name: yName, gridIndex: 0, scale: true,
          splitLine: { lineStyle: { color: "#202a3b" } },
          axisLabel: { color: "#7d8aa0" },
          nameTextStyle: { color: "#7d8aa0", fontSize: 11 },
        },
        { type: "value", name: "亿手", gridIndex: 1, scale: true,
          splitNumber: 2,
          splitLine: { lineStyle: { color: "#202a3b" } },
          axisLabel: { color: "#7d8aa0", fontSize: 10 },
          nameTextStyle: { color: "#7d8aa0", fontSize: 11 },
        },
      ] : {
        type: "value", name: yName, scale: true,
        splitLine: { lineStyle: { color: "#202a3b" } },
        axisLabel: { color: "#7d8aa0" },
        nameTextStyle: { color: "#7d8aa0", fontSize: 11 },
      },
      series: lines.map((l, i) => ({
        name: l.name, type: "line", data: l.data,
        xAxisIndex: 0, yAxisIndex: 0,
        showSymbol: true, connectNulls: false,
        smooth: false, symbol: "circle", symbolSize: l.selected ? 8 : 5,
        lineStyle: { width: l.selected ? 2.6 : 1.5, color: palette[i % palette.length] },
        itemStyle: { color: palette[i % palette.length] },
        markLine: i === 0 ? {
          silent: true, symbol: "none",
          data: [{ yAxis: 0 }],
          lineStyle: { color: "#5a6a80", type: "dashed", width: 1 },
          label: { show: false },
        } : undefined,
        emphasis: { focus: "series" },
      })).concat(hasVol ? [{
        name: volName, type: "bar", data: volData,
        xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 18,
        itemStyle: {
          color: (p) => (volUp[p.dataIndex] ? "rgba(255,82,82,.75)" : "rgba(38,194,129,.75)"),
        },
      }] : []),
    }, true);
    flowTrendChart.off("click");
    flowTrendChart.on("click", (p) => {
      if (!p || p.seriesType === "bar") return;
      const nm = p.seriesName;
      if (!nm) return;
      const hit = lines.find((l) => l.name === nm);
      selectFlowBar((hit && hit.local_name) || nm);
    });
    requestAnimationFrame(() => flowTrendChart?.resize());
    setTimeout(() => flowTrendChart?.resize(), 200);
  };
  draw(0);
}

async function loadFlowBarStocks(name) {
  const wrap = $("#flowBarStocks");
  if (!wrap) return;
  wrap.style.display = "";
  const label = $("#flowBarStocksLabel");
  const dimLabel = flowBarState.dim === "concept" ? "题材" : "行业";
  if (label) label.textContent = `「${name}」个股 · ${dimLabel} · 加载中`;
  const table = $("#flowBarStocksTable");
  if (table) table.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const d = await api(`/api/sector/flow-bar/stocks?dim=${flowBarState.dim}&name=${encodeURIComponent(name)}&range=${flowBarState.range}&limit=${flowBarState.limit}`);
    flowBarStockRows = d.items || [];
    flowBarStockNote = d.note || "";
    if (label) label.textContent = `「${name}」个股 · ${dimLabel} · ${flowBarStockRows.length} 只`;
    renderFlowBarStockTable(flowBarStockRows);
    wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { if (table) table.innerHTML = `<div class="empty">个股加载失败：${esc(err.message || err)}</div>`; }
}
let flowBarStockNote = "";
function renderFlowBarStockTable(rows) {
  const table = $("#flowBarStocksTable");
  if (!table) return;
  const fin = flowBarState.fin;
  const filtered = (rows || []).filter((r) => {
    const g = r.finance_grade || "";
    if (!fin) return true;
    if (fin === "none") return !g;
    if (fin === "AB") return g === "A" || g === "B";
    return g === fin;
  });
  table.innerHTML = filtered.length ? `<table><thead><tr>
      <th>名称</th><th>代码</th><th>涨跌幅</th><th>量比</th>${flowMetricHeaders()}<th>购买指数</th><th>财报</th><th>标签</th><th>区间贡献(万)</th>
    </tr></thead><tbody>${filtered.map((r) => `
      <tr data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${esc(r.name)}</td>
        <td class="muted">${esc(r.code)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num">${fmt(r.volume_ratio)}</td>${flowMetricCells(r)}
        <td class="num ${buyCls(r.buy_index)}">${r.buy_index !== null && r.buy_index !== undefined ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
        <td>${finBadge(r)}</td>
        <td>${relatedTags(r, false)}</td>
        <td class="num ${cls(r.contrib)}">${fmt(r.contrib, 0)}</td>
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:6px;font-size:calc(12px * var(--font-scale))">${esc(flowBarStockNote || "")} ${FLOW_NOTE}
        ${flowBarState.limit < 200 && rows.length >= flowBarState.limit
          ? ` <button class="btn small ghost" onclick="flowBarMore()">显示更多</button>` : ""}</div>`
    : `<div class="empty">${rows && rows.length ? "该财报筛选下无个股" : "该板块暂无个股"}</div>`;
}
window.flowBarMore = () => { flowBarState.limit = 200; if (flowBarState.selected) loadFlowBarStocks(flowBarState.selected); };

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
        <div><div class="name">${esc(c.name)} <span class="muted" style="font-size:calc(10px * var(--font-scale))">K线 ›</span></div><div class="sub">${esc(c.category)} · ${esc(c.unit)}</div></div>
        <span class="star ${c.watched ? "on" : ""}" onclick="event.stopPropagation();toggleCommodity('${c.symbol}')">${c.watched ? "★" : "☆"}</span>
      </div>
      <div class="price">${pxHtml(c.price, c.pct, 3)}</div>
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
      <th>名称</th><th>财报</th><th>所属板块</th><th>现价</th><th>涨跌幅</th><th>量比</th>${flowMetricHeaders()}<th>购买指数</th><th>评分</th><th>情绪</th><th>暗盘力量</th><th>提示</th>
    </tr></thead><tbody>${d.items.map((r) => `
      <tr class="${scoreRowClass(r.score)}" onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td>${finBadge(r)}</td>
        <td>${esc(r.industry || "-")}</td>
        <td class="num">${pxHtml(r.price, r.pct)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num">${fmt(r.volume_ratio)}</td>${flowMetricCells(r)}
        <td class="num">${r.buy_index !== null ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
        <td class="num"><b>${fmt(r.score, 1)}</b></td>
        <td>${r.sent_level ? esc(r.sent_level) : "-"}</td>
        <td class="num">${fmt(r.dark_power, 0)}</td>
        <td><span class="badge ${r.advice === "增持" ? "advice-buy" : r.advice === "减持" ? "advice-sell" : "advice-hold"}" style="font-size:calc(11px * var(--font-scale));padding:2px 7px">${esc(r.advice || "-")}</span></td>
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:6px;font-size:calc(12px * var(--font-scale))">行背景按评分五档着色。${FLOW_NOTE}</div>` : '<div class="empty">暂无关联个股</div>';
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
  ckChart ||= makeChart($("#ckChart"));
  ckChart.showLoading({ maskColor: cssVar("--bg") + "99", textColor: cssVar("--text") });
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
    const pal = cp();
    ckChart.setOption({
      backgroundColor: "transparent", animation: false,
      tooltip: { trigger: "axis", axisPointer: { type: "cross" }, ...ttStyle() },
      legend: { data: maSeries.map((s) => s.name), textStyle: { color: pal.muted }, top: 0 },
      grid: [{ left: 60, right: 20, top: 28, height: "62%" }, { left: 60, right: 20, top: "78%", height: "16%" }],
      xAxis: [
        { type: "category", data: d.dates, gridIndex: 0, axisLine: { lineStyle: { color: pal.border } } },
        { type: "category", data: d.dates, gridIndex: 1, show: false },
      ],
      yAxis: [
        { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: pal.split } } },
        { gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
      ],
      dataZoom: [{ type: "inside", xAxisIndex: [0, 1], start: zoomStart((d.dates || []).length), end: 100 },
                 { type: "slider", xAxisIndex: [0, 1], top: "96%", height: 12, borderColor: pal.border,
                   start: zoomStart((d.dates || []).length), end: 100 }],
      series: [
        { name: "K线", type: "candlestick", data: (d.kline || []).map(candleOHLC),
          itemStyle: candleStyle() },
        ...maSeries,
        { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: d.volumes,
          itemStyle: { color: (p) => (d.kline[p.dataIndex][1] >= d.kline[p.dataIndex][0] ? pal.up : pal.down) } },
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
          <td class="num">${pxHtml(r.price, r.pct, 3)}</td>
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
          <div class="head"><div class="name">${esc(q.name)}${clickable ? ' <span class="muted" style="font-size:calc(10px * var(--font-scale))">K线 ›</span>' : ""}</div><span class="flag">${esc(q.country)}</span></div>
          <div class="price">${pxHtml(q.price, q.pct)}</div>
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
      <th>#</th><th>持仓个股</th><th>财报</th><th>所属板块</th><th>近似权重</th><th>现价</th><th>涨跌幅</th>
      <th>量比</th>${flowMetricHeaders()}<th>购买指数</th><th>评分</th><th>情绪</th><th>暗盘力量</th><th>提示</th>
    </tr></thead><tbody>${d.holdings.map((r, i) => `
      <tr class="${scoreRowClass(r.score)}" onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${i + 1}</td>
        <td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td>${finBadge(r)}</td>
        <td>${esc(r.industry || "-")}</td>
        <td class="num"><b>${fmt(r.weight)}%</b></td>
        <td class="num">${pxHtml(r.price, r.pct)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num">${fmt(r.volume_ratio)}</td>${flowMetricCells(r)}
        <td class="num">${r.buy_index !== null ? `<b>${fmt(r.buy_index, 0)}</b> <span class="muted">${esc(r.buy_level || "")}</span>` : "-"}</td>
        <td class="num"><b>${fmt(r.score, 1)}</b></td>
        <td>${r.sent_level ? esc(r.sent_level) : "-"}</td>
        <td class="num">${fmt(r.dark_power, 0)}</td>
        <td><span class="badge ${r.advice === "增持" ? "advice-buy" : r.advice === "减持" ? "advice-sell" : "advice-hold"}" style="font-size:calc(11px * var(--font-scale));padding:2px 7px">${esc(r.advice)}</span></td>
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:6px;font-size:calc(12px * var(--font-scale))">行背景按评分五档着色（≥55 起）。${FLOW_NOTE}</div>` : `<div class="empty">${esc(d.note || "暂无持仓数据")}</div>`;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { $("#etfHoldings").innerHTML = '<div class="empty">加载失败</div>'; }
};

/* ---------------- 个股推荐 ---------------- */
let recommendBoard = "composite";
const recState = { page: 1, page_size: 20, advice: "", min_score: 0, vol_filter: "", order_by: "",
  mv_filter: "", turn_filter: "", finance_grade: "" };

const scoreRowClass = (s) => s >= 95 ? "score-95" : s >= 85 ? "score-85" : s >= 75 ? "score-75" : s >= 65 ? "score-65" : s >= 55 ? "score-55" : "";

for (const [id, key] of [["recAdvice", "advice"], ["recScore", "min_score"], ["recVol", "vol_filter"],
  ["recOrder", "order_by"], ["recMv", "mv_filter"], ["recTurn", "turn_filter"],
  ["recFin", "finance_grade"]]) {
  const el = $(`#${id}`);
  if (!el) continue;
  el.addEventListener("click", (e) => {
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
      mv_filter: recState.mv_filter, turn_filter: recState.turn_filter,
      finance_grade: recState.finance_grade });
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
    const finNote = d.finance_note
      ? `<div class="muted" style="margin-bottom:8px;font-size:calc(12px * var(--font-scale))">${esc(d.finance_note)}</div>` : "";
    box.innerHTML = d.items.length ? demonBanner + statsHtml + finNote + `<table><thead><tr>
      <th>#</th><th>名称</th><th>五行</th><th>财报</th><th>所属板块</th><th>现价</th><th>涨跌幅</th><th>${esc(d.items[0].metric_name)}</th>
      ${isStab ? "<th>闸门</th><th>星级</th>" : ""}<th>购买指数</th><th>情绪</th>
      <th>量比</th>${flowMetricHeaders()}<th>评分</th><th>提示</th><th>入选理由</th>
    </tr></thead><tbody>${d.items.map((r, i) => `
      <tr class="${scoreRowClass(r.score)}" data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${(d.page - 1) * d.page_size + i + 1}</td><td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td>${wxBadges(r.wuxing)}</td>
        <td>${finBadge(r)}</td>
        <td>${esc(r.industry || "-")}</td>
        <td class="num">${pxHtml(r.price, r.pct)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num">${fmt(r.metric_value)}</td>
        ${isStab ? `<td>${(r.gates || []).map((g, gi) => `<span class="gate ${g ? "on" : ""}">G${gi + 1}</span>`).join("")}</td>
        <td class="up">${r.stars || ""}</td>` : ""}
        <td class="num">${r.buy_index !== null && r.buy_index !== undefined ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
        <td>${r.sent_level ? esc(r.sent_level) : "-"}</td>
        <td class="num" title="${esc(r.volume_desc || "")}">${fmt(r.volume_ratio)}</td>${flowMetricCells(r)}
        <td class="num"><b>${fmt(r.score, 1)}</b></td>
        <td><span class="badge ${r.advice === "增持" ? "advice-buy" : r.advice === "减持" ? "advice-sell" : "advice-hold"}" style="font-size:calc(12px * var(--font-scale));padding:2px 8px">${r.advice}</span></td>
        <td class="desc-hl" style="white-space:normal;min-width:220px;max-width:340px">${esc(r.reason || "")}</td>
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:8px;font-size:calc(12px * var(--font-scale))">评分行背景：≥55 淡橙 → ≥95 深红 递进。榜单为量化参考，不构成投资建议。</div>`
      : finNote + `<div class="empty">该筛选条件下无个股${
        recState.finance_grade && recState.finance_grade !== "none"
          ? "（财报评级覆盖尚少，可改选「全部」或「无评级」，或在设置页重建财报评级）"
          : "（企稳/指标类榜单需先在设置页执行「重建指标」）"
      }</div>`;
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
  await refreshIntelAiIndex();
  try {
    const d = await api(`/api/announcements?code=${encodeURIComponent(code)}`);
    const note = $("#annNote");
    if (note) note.textContent = (d.note || "") + (d.target_name ? ` · 当前筛选：${d.target_name}` : "");
    const ratings = $("#annRatings");
    const dist = (d.ratings && d.ratings.distribution) || {};
    const ritems = Array.isArray(d.ratings && d.ratings.items) ? d.ratings.items : [];
    if (ratings) ratings.innerHTML = d.ratings ? `
      <div class="outlook-summary" style="margin-bottom:10px">
        <b>📊 ${esc(d.target_name)} 机构评级</b> <span class="muted">${d.ratings.simulated ? "规则模拟·仅供参考" : ""}</span><br>
        评级分布：${Object.entries(dist).filter(([, n]) => n > 0).map(([k, n]) => `${k} ${n} 家`).join(" · ") || "暂无"}
        ｜ 一致目标价 <b>${fmt(d.ratings.consensus_target)}</b><br>
        <span class="muted" style="font-size:calc(12px * var(--font-scale))">${ritems.slice(0, 5).map((it) =>
          `${esc(it.broker)}:${it.rating}(${fmt(it.target_price)})`).join("　")}</span>
      </div>` : "";
    const items = Array.isArray(d.items) ? d.items : [];
    list.innerHTML = items.length ? items.map((a) => {
      const ident = a.id || `${a.time || ""}:${(a.text || "").slice(0, 40)}`;
      const key = intelKey("announce", ident);
      const secs = Array.isArray(a.affected_sectors)
        ? a.affected_sectors.filter((s) => typeof s === "string").join(",")
        : String(a.affected_sectors || "");
      const dir = a.direction || "中性";
      const dirCls = dir === "利空" ? "dir-利空" : dir === "利好" ? "dir-利好" : "level-2";
      const t = String(a.time || "");
      const tshow = t.length >= 16 ? t.slice(5, 16) : t;
      return `
      <div class="news-item js-intel-row js-news-item" data-news="${escAttr(a.text)}" data-impact="${escAttr(a.impact_desc || "")}"
        data-title="${escAttr((a.text || "").slice(0, 40))}" data-sectors="${escAttr(secs)}" data-direction="${escAttr(dir)}"
        data-intel-source="announce" data-intel-id="${escAttr(ident)}" data-intel-key="${escAttr(key)}"
        data-intel-title="${escAttr((a.text || "").slice(0, 40))}" data-intel-text="${escAttr((a.text || "").slice(0, 500))}"
        data-intel-time="${escAttr(t)}" data-attention="${escAttr((a.impact_level || 0) * 20)}">
        <span class="time">${esc(tshow)}</span>
        <div class="body">${esc(a.text)}
          <div class="meta">
            <span class="badge sector-tag">${esc(a.tag || "公告")}</span>
            <span class="badge ${dirCls}">${esc(dir)}</span>
            ${a.impact_desc ? `<span class="badge level-${esc(a.impact_level || 1)}">${esc(a.impact_desc)}</span>` : ""}
            ${intelFlagHtml(key)}
            ${intelSectorBadges(key, secs, a.tag, dir)}
          </div>
          ${a.brief ? `<div class="desc-hl" style="font-size:calc(13px * var(--font-scale));margin-top:3px">💡 ${esc(a.brief)}</div>` : ""}
          ${intelReasonLine(key)}
        </div>
      </div>`;
    }).join("") : `<div class="empty">${esc(d.empty_reason || "暂无匹配公告（公告源为7x24快讯识别）")}</div>`;
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
  await refreshIntelAiIndex();
  try {
    const groups = await api(`/api/knowledge?q=${encodeURIComponent(kw)}`);
    const list = Array.isArray(groups) ? groups : [];
    const sections = {};
    for (const g of list) {
      const sec = g.section || "股票常识";
      (sections[sec] ||= []).push(g);
    }
    const order = ["股票常识", "选股票小技巧"].filter((s) => sections[s] && sections[s].length);
    box.innerHTML = order.length ? order.map((sec) => `
      <div class="kb-section js-kb-section" data-section="${escAttr(sec)}">
        <div class="region-title kb-section-title js-kb-section" data-section="${escAttr(sec)}" title="右键可整节 AI 更新知识">${esc(sec)}</div>
        ${sections[sec].map((g) => `
          <div class="muted" style="margin:8px 0 4px">${esc(g.group)}（${(g.items || []).length}）</div>
          ${(g.items || []).map((it) => {
            const term = it.term || "";
            const itemSec = it.section || sec;
            return `<div class="kb-item js-kb-item" data-term="${escAttr(term)}" data-section="${escAttr(itemSec)}"
              data-ai="${it.ai_updated ? "1" : "0"}">
            <div class="term">${esc(term)}${it.ai_updated ? ' <span class="badge level-3">AI已更新</span>' : ""}</div>
            <div class="desc">${esc(it.desc)}</div>
            </div>`;
          }).join("")}
        `).join("")}
      </div>`).join("")
      : '<div class="empty">未找到相关词条</div>';
  } catch (err) {
    box.innerHTML = `<div class="empty">加载失败：${esc(err.message || err)}</div>`;
    console.warn(err);
  }
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
      <th>#</th><th>名称</th><th>五行</th><th>财报</th><th>所属板块</th><th>现价</th><th>涨跌幅</th>
      <th>${esc(d.items[0].metric_name)}</th><th>量比</th>${flowMetricHeaders()}<th>购买指数</th><th>评分</th><th>提示</th>
    </tr></thead><tbody>${d.items.map((r, i) => `
      <tr class="${scoreRowClass(r.score)}" data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${i + 1}</td><td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td>${wxBadges(r.wuxing)}</td>
        <td>${finBadge(r)}</td>
        <td>${esc(r.industry || "-")}</td>
        <td class="num">${pxHtml(r.price, r.pct)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num">${fmt(r.metric_value)}</td>
        <td class="num">${fmt(r.volume_ratio)}</td>${flowMetricCells(r)}
        <td class="num">${r.buy_index !== null ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
        <td class="num"><b>${fmt(r.score, 1)}</b></td>
        <td><span class="badge ${r.advice === "增持" ? "advice-buy" : r.advice === "减持" ? "advice-sell" : "advice-hold"}" style="font-size:calc(11px * var(--font-scale));padding:2px 7px">${esc(r.advice || "-")}</span></td>
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:8px;font-size:calc(12px * var(--font-scale))">行背景按评分五档着色。${FLOW_NOTE} 榜单为本地异动口径，不构成投资建议。</div>`
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
    } else if (d.error) {
      $("#aiPickComment").innerHTML = aiErrBanner(d, "。已展示本地语义筛选结果。");
    }
    $("#aiPickTable").innerHTML = d.items && d.items.length ? `<table><thead><tr>
      <th>#</th><th>名称</th><th>财报</th><th>五行</th><th>行业</th><th>现价</th><th>涨跌幅</th>
      <th>购买指数</th><th>PE</th><th>量比</th>${flowMetricHeaders()}
    </tr></thead><tbody>${d.items.map((r, i) => `
      <tr data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
        <td>${i + 1}</td><td>${esc(r.name)} <span class="muted">${r.code}</span></td>
        <td>${finBadge(r)}</td>
        <td>${wxBadges(r.wuxing)}</td>
        <td>${esc(r.industry || "-")}</td>
        <td class="num">${pxHtml(r.price, r.pct)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td class="num">${r.buy_index !== null ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
        <td class="num">${fmt(r.pe_ttm, 1)}</td>
        <td class="num">${fmt(r.volume_ratio)}</td>${flowMetricCells(r)}
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:6px;font-size:calc(12px * var(--font-scale))">${FLOW_NOTE}</div>` : '<div class="empty">未命中个股，可换个描述或放宽条件</div>';
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
    const presets = (c.presets && c.presets.length) ? c.presets : [
      {name: "OpenAI", api_base: "https://api.openai.com/v1", model: "gpt-4o-mini"},
      {name: "DeepSeek", api_base: "https://api.deepseek.com/v1", model: "deepseek-chat"},
      {name: "通义千问", api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus"},
      {name: "月之暗面", api_base: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k"},
      {name: "智谱 GLM", api_base: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-flash"},
    ];
    const box = $("#aiPresets");
    if (box && !box.dataset.ready) {
      box.dataset.ready = "1";
      box.innerHTML = presets.map((p) =>
        `<button class="opt" type="button" data-base="${esc(p.api_base)}" data-model="${esc(p.model)}">${esc(p.name)}</button>`
      ).join("");
      box.addEventListener("click", (e) => {
        const btn = e.target.closest(".opt");
        if (!btn) return;
        $("#aiBase").value = btn.dataset.base || "";
        $("#aiModel").value = btn.dataset.model || "";
        $$("#aiPresets .opt").forEach((b) => b.classList.toggle("active", b === btn));
      });
    }
    const testBox = $("#aiTestResult");
    if (testBox && c.last_error) {
      const polished = polishAiError({ error: c.last_error });
      testBox.textContent = "上次调用失败：" + (polished.error || c.last_error);
    }
  } catch (err) { console.warn(err); }
}

window.saveAiConfig = async () => {
  const res = await api("/api/ai/config", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_base: $("#aiBase").value, api_key: $("#aiKey").value, model: $("#aiModel").value }),
  });
  $("#aiStatus").textContent = res.configured ? "✅ 已配置" : "未配置（使用本地规则分析）";
  if (res.api_base) $("#aiBase").value = res.api_base;
  const testBox = $("#aiTestResult");
  if (testBox) testBox.textContent = res.configured ? "已保存。建议点「测试连通」确认密钥与地址可用。" : "已保存，但尚未填写完整地址/密钥。";
};

window.testAiConfig = async () => {
  const box = $("#aiTestResult");
  if (box) box.textContent = "正在探测，最长约 1 分钟…";
  try {
    await api("/api/ai/config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_base: $("#aiBase").value, api_key: $("#aiKey").value, model: $("#aiModel").value }),
    });
    const d = polishAiError(await api("/api/ai/test", { method: "POST" }));
    if (box) {
      box.style.color = d.ok ? "var(--up)" : "var(--down)";
      const lines = [];
      if (d.ok) lines.push(`连通成功（${d.model || ""} @ ${d.api_base || ""}）回复：${d.reply || "ok"}`);
      else {
        lines.push(`连通失败：${d.error || "未知错误"}`);
        if (d.hint) lines.push("建议：" + d.hint);
      }
      (d.steps || []).forEach((s) => {
        const detail = polishAiError({ error: s.detail || "" }).error || s.detail || "";
        lines.push(`${s.ok ? "✓" : "✗"} ${s.label || s.id}：${detail}`);
      });
      box.textContent = lines.join("\n");
    }
    $("#aiStatus").textContent = d.ok ? "✅ 已配置且连通" : "⚠️ 已配置但调用失败";
  } catch (err) {
    if (box) {
      box.style.color = "var(--down)";
      box.textContent = "测试请求失败：" + (err.message || err);
    }
  }
};

window.runAiAnalyze = async () => {
  const input = $("#aiInput").value.trim();
  $("#aiOutput").innerHTML = '<div class="empty">分析中，大模型最长约 1 分钟，请稍候…</div>';
  try {
    const d = await api("/api/ai/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: aiMode, code: aiMode === "stock" ? input : "", question: aiMode === "custom" ? input : "" }),
    });
    const errHtml = aiErrBanner(d, "。以下为本地规则分析结果。");
    const savedNote = (aiMode === "stock" && d.applied)
      ? " · 已保存到个股分析 → AI简明诊断"
      : (aiMode === "stock" && d.kept ? " · 已保留上次诊断" : "");
    $("#aiOutput").innerHTML = errHtml
      + `<div class="muted" style="margin-bottom:8px">来源：${esc(d.source || "")}${savedNote}</div>`
      + esc(d.text || "").replace(/\n/g, "<br>");
    if (aiMode === "stock" && d.code && currentStock && currentStock.code === d.code) {
      applyBriefToHolders(currentStock.code, d);
    }
  } catch (err) {
    $("#aiOutput").innerHTML = `<div class="empty">分析失败：${esc(err.message || "请检查配置与网络")}。可在左侧点「测试连通」查看具体原因。</div>`;
  }
};

/* AI 小窗 + 右键菜单（FR7-07-4 / FR8-07 / 12.0.2 热词利好利空回填） */
let ctxStock = null;
let ctxNews = null;
let ctxHot = null;
function hideCtxMenu() { $("#ctxMenu").style.display = "none"; }
function showStockCtxItems(show) {
  ["ctxAiItem", "ctxOpenItem", "ctxWatchItem", "ctxWxAi"].forEach((id) => {
    const el = $(`#${id}`); if (el) el.style.display = show ? "" : "none";
  });
  const wx = $("#ctxWx"); if (wx) wx.style.display = show ? "" : "none";
  const sub = document.querySelector(".ctx-sub"); if (sub) sub.style.display = show ? "" : "none";
  const sep = $("#ctxSep"); if (sep) sep.style.display = show ? "" : "none";
}
function showHotCtxItems(show) {
  const revert = $("#ctxHotRevert");
  if (revert) revert.style.display = show ? "" : "none";
  const ai = $("#ctxHotAi");
  if (ai) ai.style.display = "none";
}
function showIntelCtxItems(show) {
  ["ctxIntelBoards", "ctxIntelReading"].forEach((id) => {
    const el = $(`#${id}`); if (el) el.style.display = show ? "" : "none";
  });
}
function showKbCtxItems(show, canRevert) {
  const ai = $("#ctxKbAi"); if (ai) ai.style.display = show ? "" : "none";
  const rv = $("#ctxKbRevert"); if (rv) rv.style.display = show && canRevert ? "" : "none";
}

document.addEventListener("contextmenu", async (e) => {
  const hotEl = e.target.closest(".js-hot-term, .js-hot-sector, #hotDetailBox");
  const kbEl = e.target.closest(".js-kb-item, .js-kb-section");
  const intelEl = e.target.closest(".js-intel-row");
  const newsEl = e.target.closest(".news-item[data-news]");
  const holdEl = e.target.closest("#holdersCard");
  const el = e.target.closest("[data-code], [onclick]");
  const m = el && ((el.dataset && el.dataset.code && { code: el.dataset.code, name: el.dataset.name })
    || (() => { const g = /openStock\('([^']+)','([^']*)'\)/.exec(el.getAttribute("onclick") || "");
      return g ? { code: g[1], name: g[2] } : null; })());
  const holderKw = !!(holdEl && currentStock && !m && !hotEl && !kbEl);
  if (!m && !newsEl && !hotEl && !intelEl && !holderKw && !kbEl) { hideCtxMenu(); return; }
  e.preventDefault();
  const menu = $("#ctxMenu");
  menu.style.display = "";
  menu.style.left = Math.min(e.clientX, window.innerWidth - 240) + "px";
  menu.style.top = Math.min(e.clientY, window.innerHeight - 320) + "px";
  showHotCtxItems(false);
  showIntelCtxItems(false);
  showKbCtxItems(false, false);
  ctxKb = null;
  $("#ctxNewsAi").style.display = "none";
  const hk = $("#ctxHolderKw"); if (hk) hk.style.display = "none";
  if (kbEl && !m) {
    ctxStock = null;
    ctxNews = null;
    ctxHot = null;
    ctxIntel = null;
    const term = (kbEl.dataset && kbEl.dataset.term) || "";
    const section = (kbEl.dataset && kbEl.dataset.section) || "";
    ctxKb = { term, section, aiUpdated: kbEl.dataset && kbEl.dataset.ai === "1" };
    showStockCtxItems(false);
    showKbCtxItems(true, !!(term && ctxKb.aiUpdated));
    return;
  }
  if (hotEl && !m) {
    const term = (hotEl.dataset && hotEl.dataset.term) || (typeof hotFocus !== "undefined" && hotFocus.term) || "";
    if (!term) { hideCtxMenu(); return; }
    ctxStock = null;
    ctxNews = null;
    ctxHot = { term };
    ctxIntel = {
      source: "hot_term", ident: term, title: term,
      text: (hotEl.dataset && hotEl.dataset.intelText) || term,
      time: "", heat: (hotEl.dataset && hotEl.dataset.heat) || "",
      key: intelKey("hot_term", term),
    };
    showStockCtxItems(false);
    showHotCtxItems(true);
    showIntelCtxItems(true);
    return;
  }
  ctxHot = null;
  if (holderKw) {
    ctxStock = null;
    ctxNews = null;
    ctxIntel = {
      source: "holders", ident: currentStock.code, title: currentStock.name || currentStock.code,
      text: `持股情况 ${currentStock.name || ""} ${currentStock.code}`, time: "",
    };
    showStockCtxItems(false);
    if (hk) hk.style.display = "";
    return;
  }
  if ((intelEl || newsEl) && !m) {
    const row = intelEl || newsEl;
    ctxStock = null;
    ctxNews = newsEl ? { text: newsEl.dataset.news, impact: newsEl.dataset.impact || "" } : null;
    ctxIntel = row.dataset.intelKey ? {
      source: row.dataset.intelSource || "news",
      ident: row.dataset.intelId || "",
      title: row.dataset.intelTitle || row.dataset.title || "",
      text: row.dataset.intelText || row.dataset.news || "",
      time: row.dataset.intelTime || "",
      attention: row.dataset.attention || "",
      heat: row.dataset.heat || "",
      key: row.dataset.intelKey,
      sectors: row.dataset.sectors || "",
      direction: row.dataset.direction || "",
    } : null;
    showStockCtxItems(false);
    showIntelCtxItems(!!ctxIntel);
    $("#ctxNewsAi").style.display = ctxNews && !ctxIntel ? "" : "none";
    return;
  }
  ctxNews = newsEl ? { text: newsEl.dataset.news, impact: newsEl.dataset.impact || "" } : null;
  ctxIntel = intelEl && intelEl.dataset.intelKey ? {
    source: intelEl.dataset.intelSource || "news",
    ident: intelEl.dataset.intelId || "",
    title: intelEl.dataset.intelTitle || "",
    text: intelEl.dataset.intelText || "",
    time: intelEl.dataset.intelTime || "",
    attention: intelEl.dataset.attention || "",
    key: intelEl.dataset.intelKey,
    sectors: intelEl.dataset.sectors || "",
    direction: intelEl.dataset.direction || "",
  } : null;
  ctxStock = m;
  showStockCtxItems(true);
  showIntelCtxItems(!!ctxIntel);
  $("#ctxNewsAi").style.display = ctxNews && !ctxIntel ? "" : "none";
  if (hk && holdEl) hk.style.display = "";
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
    await post(`/api/watchlist/remove?code=${encodeURIComponent(ctxStock.code)}`);
    watchCodes.delete(ctxStock.code);
  } else {
    const res = await post(`/api/watchlist/add?code=${encodeURIComponent(ctxStock.code)}`);
    if (res && res.ok === false) { alert(res.error || "添加失败"); hideCtxMenu(); return; }
    watchCodes.add((res && res.code) || ctxStock.code);
  }
  watchCodesLoaded = true;
  hideCtxMenu();
  syncWatchButtons();
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
      ${d.applied ? " · 已回填标签 " + wxBadges(d.tags) : " · 建议 " + wxBadges(d.tags)}${d.saved ? " · 已保存到 AI简明诊断" : ""}</div>`
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
    if (d.saved && holdersData && currentStock && currentStock.code === code) {
      holdersData.wuxing_ai = {
        applied: true, text: d.text || "", source: d.source || "",
        tags: d.tags || [], analyzed_at: d.saved_at || "",
      };
      paintHoldersCard(holdersData);
    }
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
    $("#aiModalBody").innerHTML = aiErrBanner(d)
      + `<div class="muted" style="margin-bottom:6px">来源：${esc(d.source)}</div>`
      + esc(d.text).replace(/\n/g, "<br>");
  } catch (err) { $("#aiModalBody").innerHTML = `<div class="empty">分析失败：${esc(err.message || err)}</div>`; }
});

async function runIntelAi(mode, title) {
  if (!ctxIntel) return;
  const payload = {
    mode,
    source: ctxIntel.source,
    ident: ctxIntel.ident,
    title: ctxIntel.title,
    text: ctxIntel.text,
    time: ctxIntel.time,
    attention: ctxIntel.attention,
    heat: ctxIntel.heat,
    orig_sectors: ctxIntel.sectors,
    direction: ctxIntel.direction,
  };
  hideCtxMenu();
  const modal = $("#aiModal");
  modal.style.display = "";
  $("#aiModalTitle").textContent = title;
  $("#aiModalBody").innerHTML = '<div class="empty">分析中，大模型最长约 1 分钟，请稍候…</div>';
  try {
    const d = await api("/api/macro/intel-ai", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const applied = d.applied ? " · 已保存到本地" : (d.kept ? " · 已保留上次结果" : " · 未覆盖");
    const split = exclusiveBoards(
      (d.bull || []).map((x) => x.name || x),
      (d.bear || []).map((x) => x.name || x),
    );
    const bull = split.bull;
    const bear = split.bear;
    const kws = d.keywords || [];
    $("#aiModalBody").innerHTML = aiErrBanner(d, "。未假装成功。")
      + `<div class="muted" style="margin-bottom:6px">${esc(d.source_call || d.ai_source || d.source || "")}${applied}</div>`
      + (d.reason ? `<div style="margin-bottom:8px"><b>${esc(d.reason)}</b></div>` : "")
      + (kws.length ? `<div class="ic-kws" style="margin-bottom:8px">${kws.map((k) => `<span class="kw-chip">${esc(k)}</span>`).join("")}</div>` : "")
      + (bull.length ? `<div style="margin-bottom:4px">利好：${bull.map((s) => `<span class="badge dir-利好">${esc(s)}</span>`).join(" ")}</div>` : "")
      + (bear.length ? `<div style="margin-bottom:4px">利空：${bear.map((s) => `<span class="badge dir-利空">${esc(s)}</span>`).join(" ")}</div>` : "")
      + (d.reading ? `<div class="hold-ai-text">${esc(d.reading).replace(/\n/g, "<br>")}</div>` : esc(d.text || "").replace(/\n/g, "<br>"));
    if (d.applied) {
      await refreshIntelAiIndex();
      if (payload.source === "holders" && currentStock) loadHolders();
      else if (macroSub === "announce") loadAnnouncements();
      else if (macroSub === "knowledge") loadKnowledge();
      else loadMacro();
    }
  } catch (err) {
    $("#aiModalBody").innerHTML = `<div class="empty">分析失败：${esc(err.message || err)}</div>`;
  }
}
$("#ctxIntelBoards")?.addEventListener("click", () => {
  runIntelAi("boards", `🤖 AI 分析利好/利空「${(ctxIntel && ctxIntel.title) || ""}」`);
});
$("#ctxIntelReading")?.addEventListener("click", () => {
  runIntelAi("reading", `📖 AI 解读「${(ctxIntel && ctxIntel.title) || ""}」`);
});
$("#ctxHolderKw")?.addEventListener("click", () => {
  if (currentStock && (!ctxIntel || ctxIntel.source !== "holders")) {
    ctxIntel = {
      source: "holders", ident: currentStock.code,
      title: currentStock.name || currentStock.code,
      text: `持股情况 ${currentStock.name || ""} ${currentStock.code}`,
    };
  }
  runIntelAi("keywords", `🔑 AI 提取关键信息词库「${(ctxIntel && ctxIntel.title) || ""}」`);
});
async function runKbAi() {
  const kb = ctxKb;
  if (!kb || (!kb.term && !kb.section)) {
    hideCtxMenu();
    return;
  }
  const term = (kb.term || "").trim();
  const section = (kb.section || "").trim();
  hideCtxMenu();
  const modal = $("#aiModal");
  modal.style.display = "";
  const title = term ? `📚 AI 更新知识「${term}」` : `📚 AI 更新知识「${section || ""}」`;
  $("#aiModalTitle").textContent = title;
  $("#aiModalBody").innerHTML = '<div class="empty">正在更新知识，大模型最长约 1 分钟，失败不覆盖已有解释…</div>';
  const qs = new URLSearchParams();
  if (term) qs.set("term", term);
  else if (section) qs.set("section", section);
  const path = `/api/knowledge/ai?${qs.toString()}`;
  try {
    let d;
    try {
      d = await api(path, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ term: term || "", section: term ? "" : (section || "") }),
      });
    } catch (first) {
      d = await api(path, { method: "POST" });
    }
    const n = d.applied_n || (d.applied ? 1 : 0);
    const fail = d.failed_n || 0;
    const applied = d.applied
      ? ` · 已更新 ${n} 条`
      : (d.kept ? " · 已保留上次解释" : " · 未覆盖");
    const extra = fail ? `，${fail} 条未覆盖` : "";
    $("#aiModalBody").innerHTML = aiErrBanner(d, "。未假装成功，未覆盖已有解释。")
      + `<div class="muted" style="margin-bottom:6px">${esc(d.source || "")}${applied}${extra}</div>`
      + esc(d.text || d.desc || d.error || "").replace(/\n/g, "<br>");
    if (d.applied) loadKnowledge();
  } catch (err) {
    $("#aiModalBody").innerHTML = `<div class="empty">更新失败：${esc(err.message || err)}</div>`;
  }
}
$("#ctxKbAi")?.addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  runKbAi();
});
$("#ctxKbRevert")?.addEventListener("click", async () => {
  if (!ctxKb || !ctxKb.term) return;
  const term = ctxKb.term;
  hideCtxMenu();
  try {
    await api("/api/knowledge/revert", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ term }),
    });
    loadKnowledge();
  } catch (err) {
    alert("还原失败：" + (err.message || err));
  }
});
$("#ctxHotAi")?.addEventListener("click", async () => {
  if (!ctxHot) return;
  const term = ctxHot.term;
  hideCtxMenu();
  const modal = $("#aiModal");
  modal.style.display = "";
  $("#aiModalTitle").textContent = `🤖 AI 分析热词「${term}」利好/利空`;
  $("#aiModalBody").innerHTML = '<div class="empty">分析中，大模型最长约 1 分钟，请稍候…</div>';
  try {
    const d = await api("/api/macro/hot-term-ai", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ term }),
    });
    const applied = d.applied ? " · 已回填利好/利空板块" : " · 未覆盖词库映射";
    $("#aiModalBody").innerHTML = aiErrBanner(d, "。已保留词库利好/利空。")
      + `<div class="muted" style="margin-bottom:6px">来源：${esc(d.source || "")}${applied}</div>`
      + (d.impact_summary ? `<div style="margin-bottom:8px"><b>${esc(d.impact_summary)}</b></div>` : "")
      + esc(d.text || "").replace(/\n/g, "<br>");
    if (d.applied) {
      hotFocus.term = term;
      loadMacro();
    }
  } catch (err) {
    $("#aiModalBody").innerHTML = `<div class="empty">分析失败：${esc(err.message || err)}</div>`;
  }
});
$("#ctxHotRevert")?.addEventListener("click", async () => {
  if (!ctxHot) return;
  const term = ctxHot.term;
  hideCtxMenu();
  try {
    await api("/api/macro/hot-term-ai", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ term, revert: true }),
    });
    hotFocus.term = term;
    loadMacro();
  } catch (err) {
    alert("还原失败：" + (err.message || err));
  }
});

window.openAiModal = async (code, name) => {
  const modal = $("#aiModal");
  modal.style.display = "";
  $("#aiModalTitle").textContent = `🤖 AI 简明诊断：${name}（${code}）`;
  $("#aiModalBody").innerHTML = '<div class="empty">分析中，请稍候…已保存的结果可在个股分析 → AI简明诊断回显</div>';
  try {
    const d = await api("/api/ai/brief", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    const saved = d.applied ? " · 已保存到个股分析 → AI简明诊断" : (d.kept ? " · 已保留上次诊断" : " · 未覆盖");
    $("#aiModalBody").innerHTML = aiErrBanner(d, d.kept ? "。已保留上次成功诊断。" : "。未假装成功。")
      + `<div class="muted" style="margin-bottom:6px">来源：${esc(d.source || "")}${saved}</div>`
      + esc(d.text || "").replace(/\n/g, "<br>");
    applyBriefToHolders(code, d);
  } catch (err) { $("#aiModalBody").innerHTML = `<div class="empty">分析失败：${esc(err.message || err)}</div>`; }
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
let settingsSub = "ops";
let strategyDirty = false;
let strategyLoaded = false;

$("#settingsTabs")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  settingsSub = btn.dataset.view || "ops";
  $$("#settingsTabs .opt").forEach((b) => b.classList.toggle("active", b === btn));
  const ops = $("#settingsPaneOps");
  const st = $("#settingsPaneStrategy");
  const en = $("#settingsPaneEngine");
  const ui = $("#settingsPaneUi");
  if (ops) ops.style.display = settingsSub === "ops" ? "" : "none";
  if (st) st.style.display = settingsSub === "strategy" ? "" : "none";
  if (en) en.style.display = settingsSub === "engine" ? "" : "none";
  if (ui) ui.style.display = settingsSub === "ui" ? "" : "none";
  if (settingsSub === "strategy") loadStrategyPage();
  else if (settingsSub === "engine") loadEngineBlueprint();
  else if (settingsSub === "ui") syncAppearanceUi();
  else loadSettings();
});

function selectedStrategyIds(kind) {
  const box = kind === "sell" ? $("#strategyPlansSell") : $("#strategyPlansBuy");
  if (!box) return [];
  return [...box.querySelectorAll("input[type=checkbox][data-plan-id]")]
    .filter((el) => el.checked)
    .map((el) => el.dataset.planId);
}

function renderStrategyExec(d) {
  const exe = (d && d.executing) || {};
  const titleEl = $("#strategyExecTitle");
  const body = $("#strategyExecBody");
  if (titleEl) titleEl.textContent = exe.title || "";
  if (body) body.textContent = exe.detail || d.note || "暂无说明";
}

function renderPlanList(box, plans, kind) {
  if (!box) return;
  box.innerHTML = (plans || []).map((p) => {
    const on = !!p.enabled;
    const docs = [p.formula, p.extra_docs].filter(Boolean).join("\n\n");
    return `
      <div class="plan-card ${kind}${on ? " on" : ""}" data-plan-id="${esc(p.id)}" data-kind="${kind}">
        <div class="plan-head">
          <label>
            <input type="checkbox" data-plan-id="${esc(p.id)}" data-kind="${kind}" ${on ? "checked" : ""}>
            <span class="plan-id">${kind === "buy" ? "买点" : "卖点"}方案 ${esc(p.id)}</span>${esc(p.name)}
            ${p.is_default ? '<span class="muted">默认</span>' : ""}
          </label>
          <span class="plan-counts" data-plan-counts="${kind}-${esc(p.id)}">命中 ${p.count ?? 0}</span>
        </div>
        <div class="plan-summary">${esc(p.summary || "")}</div>
        <div class="plan-formula">${esc(docs)}</div>
      </div>`;
  }).join("") || '<div class="empty">暂无策略方案</div>';
}

function renderStrategyPlans(d) {
  renderPlanList($("#strategyPlansBuy"), d.buy_plans || d.plans || [], "buy");
  renderPlanList($("#strategyPlansSell"), d.sell_plans || [], "sell");
}

function patchStrategyCounts(d) {
  for (const [kind, plans] of [["buy", d.buy_plans || []], ["sell", d.sell_plans || []]]) {
    for (const p of plans) {
      const el = document.querySelector(`[data-plan-counts="${kind}-${p.id}"]`);
      if (el) el.textContent = `命中 ${p.count ?? 0}`;
    }
  }
  $$("#strategyPlansBuy .plan-card, #strategyPlansSell .plan-card").forEach((card) => {
    const cb = card.querySelector("input[type=checkbox]");
    card.classList.toggle("on", !!(cb && cb.checked));
  });
}

async function loadStrategyPage(force) {
  const box = $("#strategyPlansBuy");
  if (!box) return;
  try {
    const d = await api("/api/strategy/plans");
    renderStrategyExec(d);
    if (!strategyLoaded || force || !strategyDirty) {
      renderStrategyPlans(d);
      strategyDirty = false;
      strategyLoaded = true;
    } else {
      patchStrategyCounts(d);
    }
  } catch (err) {
    if (!strategyLoaded && box) box.innerHTML = `<div class="empty">策略加载失败：${esc(err.message || err)}</div>`;
  }
}

function bindStrategyChecks(root) {
  root?.addEventListener("change", (e) => {
    if (!e.target.closest("input[type=checkbox][data-plan-id]")) return;
    strategyDirty = true;
    const card = e.target.closest(".plan-card");
    if (card) {
      const cb = card.querySelector("input[type=checkbox]");
      card.classList.toggle("on", !!(cb && cb.checked));
    }
    const msg = $("#strategySaveMsg");
    if (msg) msg.textContent = "已勾选，正在分别应用买点/卖点方案…";
    saveStrategySoon();
  });
}
bindStrategyChecks($("#strategyPlansBuy"));
bindStrategyChecks($("#strategyPlansSell"));

const saveStrategySoon = (() => {
  let t;
  return () => {
    clearTimeout(t);
    t = setTimeout(() => saveStrategyPlans(), 450);
  };
})();

window.saveStrategyPlans = async (preset) => {
  const msg = $("#strategySaveMsg");
  const buyIds = preset && preset.buy_ids ? preset.buy_ids : selectedStrategyIds("buy");
  const sellIds = preset && preset.sell_ids ? preset.sell_ids : selectedStrategyIds("sell");
  if (msg) msg.textContent = "保存中…";
  try {
    const d = await api("/api/strategy/enable", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ buy_ids: buyIds, sell_ids: sellIds }),
    });
    strategyDirty = false;
    strategyLoaded = true;
    renderStrategyExec(d);
    renderStrategyPlans(d);
    const title = (d.executing && d.executing.title) || "";
    if (msg) msg.textContent = `已应用：${title}。买点红色、卖点绿色，两侧互不混淆。`;
    loadBuyPoints();
    loadAlerts();
  } catch (err) {
    if (msg) msg.textContent = "保存失败：" + (err.message || err);
  }
};

$("#btnResetBuy")?.addEventListener("click", () => saveStrategyPlans({ buy_ids: ["A"], sell_ids: selectedStrategyIds("sell") }));
$("#btnResetSell")?.addEventListener("click", () => saveStrategyPlans({ buy_ids: selectedStrategyIds("buy"), sell_ids: ["A"] }));

let engineBlueprint = null;
async function ensureEngineBlueprint(force) {
  if (!force && engineBlueprint) return engineBlueprint;
  engineBlueprint = await api("/api/engine/blueprint");
  return engineBlueprint;
}

function collectEngineConfig() {
  const domains = {};
  $$("#engineDomains input[data-dom]").forEach((el) => { domains[el.dataset.dom] = el.checked; });
  const weights = {};
  $$("#engineWeights input[data-w]").forEach((el) => { weights[el.dataset.w] = Number(el.value); });
  return {
    enabled: !!$("#engineEnabled")?.checked,
    intraday: !!$("#engineIntraday")?.checked,
    domains,
    weights,
    consume: {
      smartpick_vector: !!$("#engConsVector")?.checked,
      smartpick_signals: !!$("#engConsSignals")?.checked,
      smartpick_catalyst: !!$("#engConsCatalyst")?.checked,
    },
  };
}

window.saveEngineConfig = async () => {
  const msg = $("#engineSaveMsg");
  if (msg) msg.textContent = "保存中…";
  try {
    const d = await api("/api/engine/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectEngineConfig()),
    });
    engineBlueprint = null;
    if (msg) {
      msg.textContent = d.enabled
        ? "已保存并启用。交易日 15:50 将自动拍；策略选股可消费向量。"
        : "已保存。引擎未启用，策略选股仍现场计算。手动「跑一次」仍可预览快照。";
    }
    loadEngineBlueprint();
  } catch (err) {
    if (msg) msg.textContent = "保存失败：" + (err.message || err);
  }
};

window.runEngineOnce = async () => {
  const msg = $("#engineSaveMsg");
  const btn = $("#engineRunBtn");
  if (msg) msg.textContent = "正在采集本地库并写快照，请稍候…";
  if (btn) btn.disabled = true;
  try {
    const d = await api("/api/engine/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "manual" }),
    });
    engineBlueprint = null;
    if (msg) {
      msg.textContent = d.ok
        ? `完成：${d.status} · ${d.stocks || 0} 只向量 · 买点 ${d.buy_hits || 0} · 卖点 ${d.sell_hits || 0}${d.note ? " · " + d.note : ""}`
        : (`失败：${d.note || d.error || "核心域 quote/metrics 不可用"}`);
    }
    loadEngineBlueprint();
  } catch (err) {
    if (msg) msg.textContent = "运行失败：" + (err.message || err);
  } finally {
    if (btn) btn.disabled = false;
  }
};

function paintEngineLive(d) {
  const box = $("#engineLiveStatus");
  if (!box) return;
  const run = d.run || {};
  if (!d.has_snapshot) {
    box.innerHTML = "尚无快照。点「跑一次引擎」将只读本地库生成向量与信号包，不编造评分。";
    return;
  }
  const missing = ((run.brief && run.brief.missing) || []).join("、");
  box.innerHTML = `最近快照 <b>${esc(run.run_id || "")}</b> · ${esc(run.kind || "")} · ${esc(run.asof || "")} · 状态 ${esc(run.status || "")}`
    + (run.finished_at ? ` · 完成 ${esc(run.finished_at)}` : "")
    + (missing ? `<div>缺失域：${esc(missing)}（保留空值，不编数据）</div>` : "")
    + (run.note ? `<div>${esc(run.note)}</div>` : "");
}

async function loadEngineBlueprint() {
  const d = await ensureEngineBlueprint(true);
  const tag = $("#engineStatusTag");
  if (tag) tag.textContent = d.enabled ? "已启用" : "未启用 · 可手动跑一次";
  const en = $("#engineEnabled");
  if (en) en.checked = !!d.enabled;
  const intra = $("#engineIntraday");
  if (intra) intra.checked = !!d.intraday;
  const intro = $("#engineIntro");
  if (intro) {
    intro.innerHTML = `${esc(d.subtitle || "")} 详细逻辑见仓库 <b>${esc(d.doc || "需求优化文档13.0.md")}</b>。${esc(d.note || "")}`;
  }
  const flow = $("#engineFlow");
  if (flow) {
    flow.innerHTML = (d.flow || []).map((s) =>
      `<div class="ef"><b>${esc(s.step)}</b>${esc(s.text)}</div>`).join("");
  }
  const dom = $("#engineDomains");
  if (dom) {
    dom.innerHTML = (d.domains || []).map((x) =>
      `<label class="engine-dom ${x.on ? "" : "off"}">
        <input type="checkbox" data-dom="${esc(x.id)}" ${x.on ? "checked" : ""}> ${esc(x.name)}
        <div class="ed-from">${esc(x.from)}</div>
      </label>`).join("");
  }
  const sch = $("#engineSchedule");
  if (sch) {
    sch.innerHTML = (d.schedule || []).map((s) =>
      `<div class="kv"><span class="k">${esc(s.name)}</span><span>${esc(s.when)} · ${esc(s.out)}</span></div>`).join("");
  }
  const outs = $("#engineOutputs");
  if (outs) {
    outs.innerHTML = (d.outputs || []).map((s) =>
      `<div class="kv"><span class="k">${esc(s.name)}</span><span>${esc(s.use)}</span></div>`).join("");
  }
  const wbox = $("#engineWeights");
  if (wbox) {
    wbox.innerHTML = (d.weights || []).map((w) =>
      `<label class="sp-w">${esc(w.name)} <b id="engw_${esc(w.id)}">${w.w}</b>
        <input type="range" min="0" max="40" data-w="${esc(w.id)}" value="${w.w}">
        <span class="muted" style="font-size:calc(11px * var(--font-scale))">${esc(w.note || "")}</span></label>`).join("");
  }
  const cons = $("#engineConsume");
  const c = d.consume || {};
  if (cons) {
    cons.innerHTML = `
      <label class="muted" style="font-size:calc(13px * var(--font-scale))"><input type="checkbox" id="engConsVector" ${c.smartpick_vector !== false ? "checked" : ""}> 策略选股综合分接入向量</label>
      <label class="muted" style="font-size:calc(13px * var(--font-scale))"><input type="checkbox" id="engConsSignals" ${c.smartpick_signals !== false ? "checked" : ""}> 策略命中页走信号包</label>
      <label class="muted" style="font-size:calc(13px * var(--font-scale))"><input type="checkbox" id="engConsCatalyst" ${c.smartpick_catalyst !== false ? "checked" : ""}> 宏观催化页走 Brief</label>
      <label class="muted" style="font-size:calc(13px * var(--font-scale))"><input type="checkbox" disabled ${d.llm_brief ? "checked" : ""}> LLM 写 Brief 句子（默认关）</label>`;
  }
  const rules = $("#engineRules");
  if (rules) rules.textContent = "铁律：" + (d.rules || []).join(" · ");
  paintEngineLive(d);
}

$("#engineWeights")?.addEventListener("input", (e) => {
  const inp = e.target.closest("input[type=range]");
  if (!inp) return;
  const lab = $(`#engw_${inp.dataset.w}`);
  if (lab) lab.textContent = inp.value;
});

window.jumpApp = (tab, extra = {}) => {
  const btn = $(`#mainTabs .tab[data-tab="${tab}"]`);
  if (btn) btn.click();
  if (tab === "settings" && extra.view) {
    const sbtn = $(`#settingsTabs .opt[data-view="${extra.view}"]`);
    if (sbtn) sbtn.click();
  }
  if (tab === "macro" && extra.sub) {
    const mbtn = $(`#macroTabs .opt[data-sub="${extra.sub}"]`);
    if (mbtn) mbtn.click();
  }
};

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
    loadFinanceState(d.finance, d.sector_flow, d.intel);
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
    if (settingsSub === "strategy") loadStrategyPage();
    else if (settingsSub === "engine") loadEngineBlueprint();
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

function loadFinanceState(fin, flow, intel) {
  const box = $("#financeState");
  if (!box) return;
  const f = fin || {};
  const fl = flow || {};
  box.innerHTML = `
    <div class="kv"><span class="k">已评级股票</span><span class="num">${f.graded || 0} / ${f.universe || 0}</span></div>
    <div class="kv"><span class="k">评级分布</span><span>${["A","B","C","D"].map((g) => `${g} ${(f.distribution || {})[g] || 0}`).join(" · ") || "-"}</span></div>
    <div class="kv"><span class="k">上次重建</span><span>${esc(f.last_rebuild || "从未")}</span></div>
    <div class="kv"><span class="k">本地资金账本</span><span>行业 ${fl.industry_days || 0} · 概念 ${fl.concept_days || 0}${fl.last_date ? `（至 ${esc(fl.last_date)}）` : ""}</span></div>
    <div class="kv"><span class="k">独立源账本</span><span>行业 ${fl.remote_hy_days || 0} · 概念 ${fl.remote_gn_days || 0}${fl.remote_last ? `（至 ${esc(fl.remote_last)}）` : ""}</span></div>
    <div class="kv"><span class="k">状态</span><span>${f.running ? `${esc(f.stage)} ${f.progress}/${f.total}` : esc(f.stage || "未开始")}</span></div>
    ${f.running ? `<div class="progress"><div class="p" style="width:${f.total ? f.progress / f.total * 100 : 30}%"></div></div>` : ""}`;
  const ib = $("#intelState");
  if (ib) {
    const it = intel || {};
    const by = it.by_kind || {};
    ib.innerHTML = `
      <div class="kv"><span class="k">情报缓存</span><span class="num">${it.total || 0} 条</span></div>
      <div class="kv"><span class="k">分类</span><span>快讯 ${by.news || 0} · 政策 ${by.policy || 0} · 日历 ${by.calendar || 0} · 板块事件 ${by.sector_event || 0}</span></div>
      <div class="kv"><span class="k">官方政策归档</span><span>${it.official_policy || 0} 条 · ${esc(it.policy_last_sync || "从未")}</span></div>
      <div class="kv"><span class="k">热度词汇</span><span>${it.hot_terms || 0} 个 · ${esc(it.hot_last_sync || "从未")}</span></div>
      <div class="kv"><span class="k">涉及板块</span><span>${it.sectors || 0}</span></div>
      <div class="kv"><span class="k">上次缓存</span><span>${esc(it.last_sync || "从未")}</span></div>`;
  }
}

window.syncSectorFlow = async () => {
  const ib = $("#intelState");
  if (ib) ib.innerHTML = '<div class="muted">正在拉取新浪板块资金…</div>';
  try {
    const d = await post("/api/sector/flow-sync");
    const st = d.stats || {};
    if (ib) ib.innerHTML = `<div class="muted">独立源已写入 ${d.remote?.industry || 0} 个行业、${d.remote?.concept || 0} 个概念（账本日 ${esc(d.remote?.date || st.last_date || "")}）</div>`;
    loadSettings();
  } catch (err) {
    if (ib) ib.innerHTML = `<div class="empty">拉取失败：${esc(err.message || err)}</div>`;
  }
};

window.syncIntel = async () => {
  const ib = $("#intelState");
  if (ib) ib.innerHTML = '<div class="muted">正在缓存宏观情报、官方政策与热词…</div>';
  try {
    const d = await post("/api/macro/intel-sync");
    const op = d.official_policy || {};
    const ht = d.hot_terms || {};
    if (ib) ib.innerHTML = `<div class="muted">已缓存快讯 ${d.news || 0} · 日历 ${d.calendar || 0} · 板块事件 ${d.sector_events || 0} · 官方政策写入 ${op.saved || 0} · 热词 ${ht.count || 0}</div>`;
    loadSettings();
  } catch (err) {
    if (ib) ib.innerHTML = `<div class="empty">缓存失败：${esc(err.message || err)}</div>`;
  }
};

window.syncOfficialPolicy = async () => {
  const ib = $("#intelState");
  if (ib) ib.innerHTML = '<div class="muted">正在同步官方政策并重算热词…</div>';
  try {
    const op = await post("/api/macro/official-policy-sync?mode=incremental");
    const ht = await post("/api/macro/hot-terms-rebuild");
    if (ib) ib.innerHTML = `<div class="muted">官方政策抓取 ${op.fetched || 0}、写入 ${op.saved || 0} · 热词 ${ht.count || 0}（${esc(op.note || "")}）</div>`;
    loadSettings();
  } catch (err) {
    if (ib) ib.innerHTML = `<div class="empty">同步失败：${esc(err.message || err)}</div>`;
  }
};

let finFetchN = 300;
$("#finFetch")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  finFetchN = Number(btn.dataset.n || 300);
  $$("#finFetch .opt").forEach((b) => b.classList.toggle("active", b === btn));
});
window.rebuildFinance = async () => {
  await post(`/api/system/rebuild-finance-grades?max_fetch=${finFetchN}`);
  const poll = setInterval(async () => {
    const m = await api("/api/system/finance-state");
    loadFinanceState(m, null);
    if (!m.running) {
      clearInterval(poll);
      loadSettings();
    }
  }, 3000);
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

/* ---------------- 策略选股（原智能选股综合打分 FR11-01） ---------------- */
const SP_HARD = [
  { key: "exclude_st", label: "剔除ST/退市", def: true },
  { key: "need_main_in", label: "必须主力净流入>0", def: false },
  { key: "need_finance", label: "必须已有财报评级", def: false },
  { key: "need_stabilize", label: "必须有企稳分", def: false },
  { key: "need_volume", label: "必须量比≥1.5", def: false },
  { key: "need_sector_in", label: "必须行业当日净流入>0", def: false },
];
const spState = {
  template: "balanced",
  hard: { exclude_st: true },
  weights: {},
  ranges: { buy_index_min: "", main_buy_ratio_min: "", volume_ratio_min: "" },
  last: null,
};
let spMeta = null;
let smartpickSub = "composite";

function spSyncPolicyButtons(p) {
  if (!p) return;
  $$("#spAiMode .opt").forEach((b) => b.classList.toggle("active", b.dataset.mode === p.mode));
  $$("#spAiInterval .opt").forEach((b) => b.classList.toggle("active", String(b.dataset.sec) === String(p.min_interval_sec)));
  $$("#spAiCap .opt").forEach((b) => b.classList.toggle("active", String(b.dataset.cap) === String(p.daily_cap)));
}
function spUsageText(u, p) {
  const cap = (p && p.daily_cap) || 12;
  const n = (u && u.count) || 0;
  return `今日 ${n}/${cap}`;
}
function renderSpControls() {
  if (!spMeta) return;
  const tpls = spMeta.templates || {};
  $("#spTemplates").innerHTML = Object.entries(tpls).map(([id, t]) =>
    `<button class="opt ${spState.template === id ? "active" : ""}" data-id="${id}">${esc(t.name)}</button>`).join("");
  const labels = spMeta.weight_labels || {};
  const w = spState.weights;
  $("#spWeights").innerHTML = Object.keys(spMeta.weights || {}).map((k) =>
    `<label class="sp-w">${esc(labels[k] || k)} <b id="spw_${k}">${w[k] ?? 0}</b>
      <input type="range" min="0" max="40" step="1" data-w="${k}" value="${w[k] ?? 0}"></label>`).join("");
  $("#spHard").innerHTML = `<span class="g-label muted">硬性条件</span>` + SP_HARD.map((h) =>
    `<label class="muted" style="font-size:calc(13px * var(--font-scale))"><input type="checkbox" data-hard="${h.key}" ${spState.hard[h.key] ? "checked" : ""}> ${h.label}</label>`).join("");
  const r = spState.ranges;
  $("#spRanges").innerHTML = `
    <span><span class="g-label muted">购买指数≥</span><input id="spBuyMin" value="${esc(r.buy_index_min)}" placeholder="不限" style="width:70px"></span>
    <span><span class="g-label muted">主力买比≥</span><input id="spRatioMin" value="${esc(r.main_buy_ratio_min)}" placeholder="%" style="width:70px"></span>
    <span><span class="g-label muted">量比≥</span><input id="spVolMin" value="${esc(r.volume_ratio_min)}" placeholder="不限" style="width:70px"></span>`;
  const note = $("#spFinNote");
  if (note) note.textContent = `已评级 ${spMeta.finance_graded || 0}/${spMeta.finance_universe || 0} · 未评级为 —，综合分不奖不罚。筛绩优为空请先重建财报或改「均衡综合」。`;
  const usage = $("#spAiUsage");
  if (usage) usage.textContent = spUsageText(spMeta.usage, spMeta.policy);
  spSyncPolicyButtons(spMeta.policy);
}

async function loadSmartpick() {
  applyAlertDock();
  loadAlerts();
  try {
    const bp = await ensureEngineBlueprint(true);
    const banner = $("#spEngineBanner");
    const run = bp.run || {};
    const consume = (bp.config && bp.config.consume) || {};
    if (banner) {
      if (smartpickSub === "composite") {
        banner.textContent = (bp.enabled && consume.smartpick_vector && bp.has_snapshot)
          ? `综合分可消费引擎向量 · 快照 ${run.run_id || ""} · ${run.asof || ""}。未写入快照的个股仍用现场分。`
          : "综合分来自现场计算 · 启用引擎并打开「综合分接入向量」后将消费 StockVector。";
      } else if (bp.has_snapshot) {
        banner.textContent = `引擎快照 ${run.kind || ""} ${run.asof || ""} · ${run.run_id || ""} · 量化参考，不构成投资建议`;
      } else {
        banner.textContent = "尚无引擎快照。可到设置→策略引擎配置点「跑一次引擎」。不编造名单。";
      }
    }
    if (smartpickSub === "composite") {
      spMeta = await api("/api/smartpick/meta");
      if (!Object.keys(spState.weights).length) spState.weights = { ...(spMeta.weights || {}) };
      renderSpControls();
    } else {
      await renderSmartpickEnginePane(bp);
    }
  } catch (err) { console.warn(err); }
}

function spEngineJumps(tab) {
  return (tab.jumps || []).map((j) => {
    const extra = j.view ? `{view:'${j.view}'}` : (j.sub ? `{sub:'${j.sub}'}` : "{}");
    return `<button class="btn small" type="button" onclick="jumpApp('${esc(j.tab)}', ${extra})">${esc(j.label)}</button>`;
  }).join("");
}

function spEngineEmpty(tab, reason) {
  return `<div class="card">
      <div class="card-title">${esc(tab.name || "引擎结果")} <span class="muted">不编造名单</span></div>
      <div class="muted" style="margin-bottom:8px">${esc(tab.blurb || "")}</div>
      <div class="sp-engine-empty">
        <div class="empty" style="padding:12px 0">${esc(reason || tab.empty || "尚无引擎数据")}</div>
        <div class="jumps">${spEngineJumps(tab)}</div>
      </div>
    </div>`;
}

async function renderSmartpickEnginePane(bp) {
  const box = $("#spPaneEngine");
  if (!box) return;
  const tab = (bp.smartpick_tabs || []).find((t) => t.id === smartpickSub) || {};
  box.innerHTML = `<div class="card"><div class="empty">加载中…</div></div>`;
  try {
    if (smartpickSub === "signals") {
      const [buy, sell] = await Promise.all([
        api("/api/engine/signals?side=buy&limit=40"),
        api("/api/engine/signals?side=sell&limit=40"),
      ]);
      const paint = (d, side) => {
        const items = d.items || [];
        if (!items.length) return `<div class="empty">${esc(d.empty_reason || "无命中")}</div>`;
        return `<table><thead><tr>
          <th>名称</th><th>现价</th><th>涨跌</th><th>综合分</th><th>方案</th><th>入场/止盈/止损</th><th>跟踪</th>
        </tr></thead><tbody>${items.map((r) => `
          <tr data-code="${esc(r.code)}" data-name="${esc(r.name)}" onclick="openStock('${esc(r.code)}','${esc(r.name)}')">
            <td>${esc(r.name)} <span class="muted">${esc(r.code)}</span></td>
            <td class="num">${pxHtml(r.price, r.pct)}</td>
            <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
            <td class="num">${fmt(r.score, 1)}</td>
            <td>${esc(r.plan_id || "")}</td>
            <td>${signalLevelsHtml(r)}</td>
            <td>${esc(r.task_status || "")}</td>
          </tr>`).join("")}</tbody></table>`;
      };
      box.innerHTML = `
        <div class="card">
          <div class="card-title">策略命中 <span class="muted">${esc((buy.run_id || sell.run_id || "") + " " + (buy.asof || ""))}</span></div>
          <div class="muted" style="margin-bottom:8px">${esc(tab.blurb || "")} 买/卖分列，不混用。</div>
          <div class="card-title">买点 ${buy.count || 0}</div>
          ${paint(buy, "buy")}
          <div class="card-title" style="margin-top:16px">卖点 ${sell.count || 0}</div>
          ${paint(sell, "sell")}
          <div class="jumps" style="margin-top:12px">${spEngineJumps(tab)}</div>
        </div>`;
      return;
    }
    if (smartpickSub === "catalyst") {
      const d = await api("/api/engine/catalysts");
      const items = d.items || [];
      if (!items.length) {
        box.innerHTML = spEngineEmpty(tab, d.empty_reason);
        return;
      }
      box.innerHTML = `
        <div class="card">
          <div class="card-title">宏观催化 <span class="muted">${esc(d.run_id || "")} · ${esc(d.asof || "")}</span></div>
          <div class="muted" style="margin-bottom:8px">${esc(tab.blurb || "")}</div>
          <div class="engine-cat-grid">${items.map((c) => `
            <div class="engine-cat" data-board="${esc((c.sectors || [])[0] || "")}">
              <div class="k">${esc(c.kind || "")} · ${esc(c.event_time || "").slice(0, 10)}</div>
              <div><b>${esc(c.title || "")}</b></div>
              <div class="muted">${esc((c.sectors || []).slice(0, 6).join("、") || "未映射板块")}</div>
            </div>`).join("")}</div>
          <div id="spCatStocks" class="muted" style="margin-top:12px">点击有板块的卡片查看快照内 TOP20。</div>
          <div class="jumps" style="margin-top:12px">${spEngineJumps(tab)}</div>
        </div>`;
      box.querySelectorAll(".engine-cat").forEach((el) => {
        el.addEventListener("click", () => showEngineBoard(el.dataset.board || ""));
      });
      return;
    }
    if (smartpickSub === "snapshot") {
      const d = await api("/api/engine/snapshot");
      if (d.empty) {
        box.innerHTML = spEngineEmpty(tab, d.empty_reason);
        return;
      }
      const regime = d.regime || {};
      const domains = d.domains || {};
      const facts = (d.brief && d.brief.key_facts) || [];
      box.innerHTML = `
        <div class="card">
          <div class="card-title">引擎快照 <span class="muted">${esc(d.run_id || "")} · ${esc(d.kind || "")} · ${esc(d.status || "")}</span></div>
          <div>${esc(regime.sentence || regime.label || "体制未知")}</div>
          <div class="muted">${esc(d.note || "")} ${esc(d.disclaimer || "")}</div>
        </div>
        <div class="card">
          <div class="card-title">域新鲜度</div>
          ${(Object.entries(domains).map(([k, v]) =>
            `<div class="kv"><span class="k">${esc(k)}</span><span>${v.available ? (v.rows + " 条 · " + (v.asof || "")) : ("缺失 · " + (v.error || ""))}</span></div>`).join("")) || '<div class="empty">无域记录</div>'}
        </div>
        <div class="card">
          <div class="card-title">关键事实</div>
          ${facts.length ? facts.map((f) => `<div>· ${esc(f.title || f.term || f.name || JSON.stringify(f))}</div>`).join("") : '<div class="empty">窗口内无政策/热词/日历（不编造）</div>'}
        </div>
        <div class="card">
          <div class="card-title">向量 Top</div>
          ${(d.stocks || []).length ? `<table><thead><tr><th>名称</th><th>现价</th><th>综合分</th><th>购买指数</th><th>说明</th></tr></thead><tbody>
            ${d.stocks.map((r) => `<tr data-code="${esc(r.code)}" onclick="openStock('${esc(r.code)}','${esc(r.name)}')">
              <td>${esc(r.name)} <span class="muted">${esc(r.code)}</span></td>
              <td class="num">${pxHtml(r.price, r.pct)}</td>
              <td class="num"><b>${fmt(r.score, 1)}</b></td>
              <td class="num">${fmt(r.d_buy, 0)}</td>
              <td>${esc(r.reason_bits || "")}</td>
            </tr>`).join("")}</tbody></table>` : '<div class="empty">无个股向量</div>'}
        </div>`;
      return;
    }
    if (smartpickSub === "verify") {
      const d = await api("/api/engine/tasks?limit=80");
      const items = d.items || [];
      const retTxt = (v) => (v == null || v === "") ? "—" : pct(v);
      box.innerHTML = `
        <div class="card">
          <div class="card-title">信号验证 <span class="muted">${items.length} 条任务</span></div>
          <div class="muted" style="margin-bottom:8px">${esc(d.note || tab.blurb || "")}</div>
          <button class="btn" type="button" disabled title="${esc(d.backtest_reason || "")}">日K回测（关闭）</button>
          <div class="muted" style="margin:8px 0 12px">${esc(d.backtest_reason || "缺少按日因子表，回测入口关闭。")}</div>
          ${items.length ? `<table><thead><tr>
            <th>日期</th><th>名称</th><th>方向</th><th>方案</th><th>入场</th><th>止盈</th><th>止损</th>
            <th>状态</th><th>+1日</th><th>+5日</th>
          </tr></thead><tbody>${items.map((r) => `
            <tr data-code="${esc(r.code)}" onclick="openStock('${esc(r.code)}','${esc(r.name || "")}')">
              <td>${esc(r.asof || "")}</td>
              <td>${esc(r.name || "")} <span class="muted">${esc(r.code)}</span></td>
              <td>${esc(r.side || "")}</td>
              <td>${esc(r.plan_id || "")}</td>
              <td class="num">${fmt(r.entry_px)}</td>
              <td class="num">${fmt(r.take_px)}</td>
              <td class="num">${fmt(r.stop_px)}</td>
              <td><span class="task-st ${esc(r.status || "")}">${esc(r.status || "")}</span></td>
              <td class="num">${retTxt(r.ret_1)}</td>
              <td class="num">${retTxt(r.ret_5)}</td>
            </tr>`).join("")}</tbody></table>` : `<div class="empty">${esc(d.empty_reason || tab.empty || "尚无信号任务")}</div>`}
          <div class="jumps" style="margin-top:12px">${spEngineJumps(tab)}</div>
        </div>`;
      return;
    }
    box.innerHTML = spEngineEmpty(tab);
  } catch (err) {
    box.innerHTML = `<div class="card"><div class="empty">加载失败：${esc(err.message || err)}</div></div>`;
  }
}

window.showEngineBoard = async (board) => {
  const box = $("#spCatStocks");
  if (!box) return;
  if (!board) {
    box.innerHTML = '<div class="empty">该条催化未映射板块，不猜测个股。</div>';
    return;
  }
  box.innerHTML = `<div class="empty">加载「${esc(board)}」快照个股…</div>`;
  try {
    const d = await api(`/api/engine/stocks?board=${encodeURIComponent(board)}&limit=20`);
    const items = d.items || [];
    box.innerHTML = items.length
      ? `<div class="card-title">${esc(board)} TOP${items.length}</div>
         <table><thead><tr><th>名称</th><th>现价</th><th>综合分</th><th>说明</th></tr></thead><tbody>
         ${items.map((r) => `<tr onclick="openStock('${esc(r.code)}','${esc(r.name)}')">
           <td>${esc(r.name)} <span class="muted">${esc(r.code)}</span></td>
           <td class="num">${pxHtml(r.price, r.pct)}</td>
           <td class="num"><b>${fmt(r.score, 1)}</b></td>
           <td>${esc(r.reason_bits || "")}</td>
         </tr>`).join("")}</tbody></table>`
      : `<div class="empty">${esc(d.empty_reason || "该板块无个股向量")}</div>`;
  } catch (err) {
    box.innerHTML = `<div class="empty">加载失败：${esc(err.message || err)}</div>`;
  }
}

$("#smartpickTabs")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  smartpickSub = btn.dataset.view || "composite";
  $$("#smartpickTabs .opt").forEach((b) => b.classList.toggle("active", b === btn));
  const live = smartpickSub === "composite";
  const comp = $("#spPaneComposite");
  const eng = $("#spPaneEngine");
  if (comp) comp.style.display = live ? "" : "none";
  if (eng) eng.style.display = live ? "none" : "";
  loadSmartpick();
});

$("#spTemplates")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  spState.template = btn.dataset.id;
  const t = (spMeta && spMeta.templates || {})[spState.template];
  if (t) {
    spState.weights = { ...(t.weights || spMeta.weights) };
    spState.hard = { exclude_st: true, ...(t.hard || {}) };
  }
  renderSpControls();
});
$("#spWeights")?.addEventListener("input", (e) => {
  const inp = e.target.closest("input[type=range]");
  if (!inp) return;
  spState.weights[inp.dataset.w] = Number(inp.value);
  const lab = $(`#spw_${inp.dataset.w}`);
  if (lab) lab.textContent = inp.value;
});
$("#spHard")?.addEventListener("change", (e) => {
  const inp = e.target.closest("input[data-hard]");
  if (!inp) return;
  spState.hard[inp.dataset.hard] = inp.checked;
});
function readSpRanges() {
  spState.ranges.buy_index_min = $("#spBuyMin")?.value.trim() || "";
  spState.ranges.main_buy_ratio_min = $("#spRatioMin")?.value.trim() || "";
  spState.ranges.volume_ratio_min = $("#spVolMin")?.value.trim() || "";
  return spState.ranges;
}
async function saveSpPolicy(patch) {
  const d = await api("/api/smartpick/ai-policy", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (spMeta) { spMeta.policy = { mode: d.mode, min_interval_sec: d.min_interval_sec, daily_cap: d.daily_cap }; spMeta.usage = d.usage; }
  const usage = $("#spAiUsage");
  if (usage) usage.textContent = spUsageText(d.usage, d);
  spSyncPolicyButtons(d);
}
$("#spAiMode")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  saveSpPolicy({ mode: btn.dataset.mode });
});
$("#spAiInterval")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  saveSpPolicy({ min_interval_sec: Number(btn.dataset.sec) });
});
$("#spAiCap")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  saveSpPolicy({ daily_cap: Number(btn.dataset.cap) });
});

function renderSpResult(d) {
  spState.last = d;
  $("#spCount").textContent = `共 ${d.total || 0} 只`;
  $("#spLocal").style.display = "";
  $("#spLocal").textContent = d.local_summary || "";
  if (d.score_source === "engine" && d.engine) {
    $("#spLocal").textContent += ` 快照 ${d.engine.run_id || ""} · ${d.engine.asof || ""}。`;
  }
  const ai = d.ai || {};
  const box = $("#spAiBox");
  if (ai.text) {
    box.innerHTML = `<div class="outlook-summary" style="white-space:pre-wrap">${esc(ai.text)}</div>`;
  } else if (ai.error) {
    box.innerHTML = aiErrBanner(ai);
  } else if (ai.skipped && ai.reason && ai.reason !== "not_requested" && ai.reason !== "manual") {
    const why = { off: "已关闭 AI", cap: "今日次数已用完", interval: "未到自动间隔", duplicate: "相同名单间隔内不重复调用", unconfigured: "未配置大模型", empty: "名单为空" }[ai.reason] || ai.reason;
    box.innerHTML = `<div class="muted" style="margin-bottom:8px">未调用 AI：${esc(why)}。名单仍为本地结果。</div>`;
  } else {
    box.innerHTML = "";
  }
  const items = d.items || [];
  $("#spTable").innerHTML = items.length ? `<table><thead><tr>
    <th>#</th><th>名称</th><th>财报</th><th>行业</th><th>现价</th><th>涨跌幅</th><th>综合分</th>
    <th>购买指数</th><th>量比</th>${flowMetricHeaders()}<th>提示</th><th>本地理由</th>
  </tr></thead><tbody>${items.map((r, i) => `
    <tr class="${scoreRowClass(r.smart_score || r.score)}" data-code="${r.code}" data-name="${esc(r.name)}" onclick="openStock('${r.code}','${esc(r.name)}')">
      <td>${i + 1}</td><td>${esc(r.name)} <span class="muted">${r.code}</span></td>
      <td>${finBadge(r)}</td>
      <td>${esc(r.industry || "-")}</td>
      <td class="num">${pxHtml(r.price, r.pct)}</td>
      <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
      <td class="num"><b>${fmt(r.smart_score, 1)}</b></td>
      <td class="num">${r.buy_index != null ? `<b>${fmt(r.buy_index, 0)}</b>` : "-"}</td>
      <td class="num">${fmt(r.volume_ratio)}</td>${flowMetricCells(r)}
      <td><span class="badge ${r.advice === "增持" ? "advice-buy" : r.advice === "减持" ? "advice-sell" : "advice-hold"}" style="font-size:calc(11px * var(--font-scale));padding:2px 7px">${esc(r.advice || "-")}</span></td>
      <td class="desc-hl" style="white-space:normal;min-width:200px">${esc(r.local_reason || "")}</td>
    </tr>`).join("")}</tbody></table>
    <div class="muted" style="margin-top:6px;font-size:calc(12px * var(--font-scale))">${FLOW_NOTE} 综合分为本地加权，不构成投资建议。</div>`
    : `<div class="empty">${esc(d.local_summary || "无命中个股")}</div>`;
  if (d.usage) {
    const el = $("#spAiUsage");
    if (el) el.textContent = spUsageText(d.usage, d.policy || (spMeta && spMeta.policy));
  }
}

window.parseSmartpickSemantic = async () => {
  /* 解析在 run 时服务端完成，这里只提示已填入 */
  const t = ($("#spSemantic")?.value || "").trim();
  if (!t) return;
  $("#spLocal").style.display = "";
  $("#spLocal").textContent = "已记录一句话，将在「开始选股」时解析为条件。";
};

window.runSmartpick = async () => {
  $("#spTable").innerHTML = '<div class="empty">本地综合计算中…</div>';
  try {
    const d = await api("/api/smartpick/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        template: spState.template,
        hard: spState.hard,
        weights: spState.weights,
        ranges: readSpRanges(),
        semantic: ($("#spSemantic")?.value || "").trim(),
      }),
    });
    renderSpResult(d);
  } catch (err) {
    $("#spTable").innerHTML = `<div class="empty">选股失败：${esc(err.message || err)}</div>`;
  }
};

window.runSmartpickAi = async () => {
  if (!spState.last || !(spState.last.items || []).length) {
    $("#spAiBox").innerHTML = '<div class="muted">请先开始选股得到名单。</div>';
    return;
  }
  $("#spAiBox").innerHTML = '<div class="empty">正在生成点评（受日上限与间隔约束）…</div>';
  try {
    const d = await api("/api/smartpick/ai-comment", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        items: spState.last.items,
        template_name: spState.last.template_name,
        summary: spState.last.local_summary,
      }),
    });
    renderSpResult({ ...spState.last, ai: d, usage: d.usage || spState.last.usage, policy: spState.last.policy });
  } catch (err) {
    $("#spAiBox").innerHTML = `<div class="offline-banner">点评失败：${esc(err.message || err)}</div>`;
  }
};

/* ---------------- 全局最佳买点/卖点浮窗 ---------------- */
const BUY_FLASH_KEY = "daatool_buy_flash_v11";
function readBuyFlashState() {
  try { return JSON.parse(localStorage.getItem(BUY_FLASH_KEY) || "") || {}; }
  catch { return {}; }
}
const buyFlashState = (() => {
  const s = readBuyFlashState();
  return {
    collapsed: s.collapsed === true,
    win: s.win && Number.isFinite(s.win.left) ? s.win : null,
    logo: s.logo && Number.isFinite(s.logo.left) ? s.logo : null,
  };
})();
function saveBuyFlashState() {
  try { localStorage.setItem(BUY_FLASH_KEY, JSON.stringify(buyFlashState)); } catch { /* ignore */ }
}
function clampSoulPos(left, top, w, h) {
  const maxL = Math.max(0, window.innerWidth - w);
  const maxT = Math.max(0, window.innerHeight - h);
  return { left: Math.min(maxL, Math.max(0, left)), top: Math.min(maxT, Math.max(0, top)) };
}
function applyBuyFlashPos() {
  const panel = $("#buyFlash");
  const fab = $("#buyFlashFab");
  if (panel && buyFlashState.win) {
    const p = clampSoulPos(buyFlashState.win.left, buyFlashState.win.top, panel.offsetWidth || 720, Math.min(panel.offsetHeight || 200, window.innerHeight));
    panel.style.left = p.left + "px";
    panel.style.top = p.top + "px";
    panel.style.right = "auto";
    panel.style.bottom = "auto";
    buyFlashState.win = p;
  }
  if (fab && buyFlashState.logo) {
    const p = clampSoulPos(buyFlashState.logo.left, buyFlashState.logo.top, 48, 48);
    fab.style.left = p.left + "px";
    fab.style.top = p.top + "px";
    fab.style.right = "auto";
    fab.style.bottom = "auto";
    buyFlashState.logo = p;
  }
}
function setBuyFlashCollapsed(v) {
  buyFlashState.collapsed = !!v;
  const panel = $("#buyFlash");
  const fab = $("#buyFlashFab");
  if (panel) panel.style.display = v ? "none" : "";
  if (fab) fab.style.display = v ? "" : "none";
  if (v && panel && !buyFlashState.logo) {
    const r = panel.getBoundingClientRect();
    buyFlashState.logo = { left: Math.max(0, r.right - 48), top: Math.max(0, r.top) };
  }
  applyBuyFlashPos();
  saveBuyFlashState();
}
function bindSoulDrag(el, kind) {
  if (!el) return;
  let start = null, moved = false;
  el.addEventListener("pointerdown", (e) => {
    if (kind === "win" && e.target.closest("button")) return;
    if (kind === "win" && !e.target.closest("#buyFlashHead")) return;
    const rect = el.getBoundingClientRect();
    start = { x: e.clientX, y: e.clientY, left: rect.left, top: rect.top };
    moved = false;
    el.classList.add("dragging");
    try { el.setPointerCapture(e.pointerId); } catch { /* ignore */ }
    e.preventDefault();
  });
  el.addEventListener("pointermove", (e) => {
    if (!start) return;
    const dx = e.clientX - start.x, dy = e.clientY - start.y;
    if (Math.abs(dx) + Math.abs(dy) > 12) moved = true;
    if (!moved) return;
    const pos = clampSoulPos(start.left + dx, start.top + dy, el.offsetWidth, el.offsetHeight);
    el.style.left = pos.left + "px";
    el.style.top = pos.top + "px";
    el.style.right = "auto";
    el.style.bottom = "auto";
    if (kind === "win") buyFlashState.win = pos;
    else buyFlashState.logo = pos;
  });
  const end = () => {
    if (!start) return;
    start = null;
    el.classList.remove("dragging");
    saveBuyFlashState();
  };
  el.addEventListener("pointerup", (e) => {
    const wasMoved = moved;
    end();
    if (kind === "logo" && !wasMoved) {
      setBuyFlashCollapsed(false);
      loadBuyPoints();
    }
  });
  el.addEventListener("pointercancel", end);
}

let buyFlashKind = "buy";

async function loadBuyPoints() {
  const body = $("#buyFlashBody");
  const countEl = $("#buyFlashCount");
  const noteEl = $("#buyFlashNote");
  const badge = $("#buyFlashBadge");
  const kind = buyFlashKind === "sell" ? "sell" : "buy";
  const kindLabel = kind === "sell" ? "最佳卖点" : "最佳买点";
  const titleEl = $("#buyFlashKindLabel");
  if (titleEl) titleEl.textContent = kindLabel;
  const panel = $("#buyFlash");
  const fab = $("#buyFlashFab");
  const fabLabel = $("#buyFlashFabLabel");
  if (panel) {
    panel.classList.toggle("kind-buy", kind === "buy");
    panel.classList.toggle("kind-sell", kind === "sell");
  }
  if (fab) {
    fab.classList.toggle("kind-buy", kind === "buy");
    fab.classList.toggle("kind-sell", kind === "sell");
    fab.title = "打开" + kindLabel;
  }
  if (fabLabel) fabLabel.textContent = kind === "sell" ? "卖" : "买";
  const paintEmpty = (text, countText) => {
    if (countEl) countEl.textContent = countText;
    if (noteEl) { noteEl.textContent = ""; noteEl.style.display = "none"; }
    if (badge) {
      badge.textContent = "!";
      badge.style.display = "";
    }
    if (body) body.innerHTML = `<div class="empty">${esc(text || "暂无" + kindLabel)}</div>`;
  };
  try {
    const path = kind === "sell" ? "/api/alerts/sell-points?limit=16" : "/api/alerts/buy-points?limit=16";
    const d = await api(path);
    const items = d.items || [];
    const source = d.source || "";
    const note = d.note || "";
    if (countEl) countEl.textContent = `${items.length} 只`;
    if (noteEl) {
      noteEl.textContent = "";
      noteEl.style.display = "none";
    }
    if (badge) {
      badge.textContent = String(items.length);
      badge.style.display = "";
    }
    if (!body) return;
    if (!items.length) {
      body.innerHTML = `<div class="empty">${esc(note || "暂无" + kindLabel)}</div>`;
      return;
    }
    if (!watchCodesLoaded) {
      for (const r of items) {
        if (r.in_watchlist && r.code) watchCodes.add(r.code);
      }
    }
    const adviceBadge = (r) => {
      const a = r.op_advice || r.advice || "";
      const short = a.includes("减持") ? "减持" : a.includes("增持") ? "增持" : (a.includes("持有") || a.includes("观望") ? "观望" : (kind === "sell" ? "减持" : "关注"));
      const clsName = short === "增持" ? "advice-buy" : short === "减持" ? "advice-sell" : "advice-hold";
      return `<span class="badge ${clsName} flash-tip">${esc(short)}</span>`;
    };
    body.innerHTML = `<div class="flash-list">${items.map((r) => `
      <div class="flash-card ${scoreRowClass(r.score)}" data-code="${escAttr(r.code)}" data-name="${escAttr(r.name)}" onclick="openStock('${esc(r.code)}','${esc(r.name)}')">
        <div class="flash-card-top">
          <div>
            <span class="hl-name">${esc(r.name)}</span>
            <span class="muted stock-code-text">${esc(stockCodeDigits(r.code))}</span>
            ${planPickedHtml(r, kind)}
          </div>
          ${watchBtnHtml(r.code, r.name, r.in_watchlist)}
        </div>
        <div class="flash-metrics">
          <span class="m">现价 ${pxHtml(r.price, r.pct)}</span>
          <span class="m ${cls(r.pct)}">${pct(r.pct)}</span>
          <span class="m">量比 <b>${fmt(r.volume_ratio)}</b></span>
          <span class="m">购买指数 <b>${fmt(r.buy_index, 0)}</b></span>
          ${adviceBadge(r)}
        </div>
        <div class="flash-advice">${esc(r.advice_summary || r.advice || r.op_advice || r.hit_action || "暂无操作建议")}</div>
      </div>`).join("")}</div>`;
    syncWatchButtons();
  } catch (err) {
    paintEmpty(kindLabel + "加载失败，请检查服务是否在运行", "失败");
  }
}
function bindBuyFlash() {
  $("#buyFlashKind")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".opt");
    if (!btn) return;
    e.stopPropagation();
    buyFlashKind = btn.dataset.kind === "sell" ? "sell" : "buy";
    $$("#buyFlashKind .opt").forEach((b) => b.classList.toggle("active", b === btn));
    loadBuyPoints();
  });
  $("#buyFlashMin")?.addEventListener("click", (e) => {
    e.stopPropagation();
    setBuyFlashCollapsed(true);
  });
  $("#buyFlashReset")?.addEventListener("click", (e) => {
    e.stopPropagation();
    buyFlashState.win = null;
    buyFlashState.logo = null;
    const panel = $("#buyFlash");
    if (panel) {
      panel.style.left = "";
      panel.style.top = "";
      panel.style.right = "18px";
      panel.style.bottom = "auto";
    }
    setBuyFlashCollapsed(false);
    saveBuyFlashState();
    loadBuyPoints();
  });
  $("#buyFlashBody")?.addEventListener("dblclick", (e) => {
    const row = e.target.closest("[data-code]");
    if (row) openStock(row.dataset.code, row.dataset.name);
  });
  bindSoulDrag($("#buyFlash"), "win");
  bindSoulDrag($("#buyFlashFab"), "logo");
  setBuyFlashCollapsed(buyFlashState.collapsed);
  applyBuyFlashPos();
}

/* ---------------- 智能选股（股价未来涨跌方向，菜单骨架） ---------------- */
let intelpickSub = "up";
async function loadIntelpick() {
  const box = $("#ipTable");
  const marketBox = $("#ipMarket");
  const note = $("#ipNote");
  if (!box) return;
  box.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const d = await api(`/api/intelpick?side=${encodeURIComponent(intelpickSub)}`);
    if (note) note.textContent = (d.note || "") + " " + (d.disclaimer || "");
    const m = d.market || {};
    const prob = m.prob_up;
    const factors = Array.isArray(m.factors) ? m.factors : [];
    if (marketBox) {
      marketBox.innerHTML = (prob == null)
        ? '<div class="empty">大盘方向暂无数据</div>'
        : `<div style="margin-bottom:8px">
            <span class="prob-num ${prob >= 58 ? "up" : prob <= 42 ? "down" : "flat"}">${esc(prob)}%</span>
            <span class="badge ${prob >= 58 ? "level-4" : prob <= 42 ? "level-1" : "level-2"}">明日大盘${esc(m.view || "")}</span>
            <span class="muted" style="margin-left:8px">${esc(m.desc || "")}</span>
          </div>
          <div class="prob-bar"><div class="p" style="width:${Number(prob) || 0}%"></div></div>
          ${factors.map((x) => `<div class="kv"><span class="k">${esc(x.name)}</span>
            <span>${esc(x.value)} <span class="num ${cls(x.impact)}">${sign(x.impact)}${fmt(x.impact, 1)}</span></span></div>`).join("")}
          <div class="muted" style="font-size:calc(12px * var(--font-scale));margin-top:6px">${esc(m.disclaimer || "")}</div>`;
    }
    const sideName = intelpickSub === "down" ? "下跌预测" : "上涨预测";
    const title = $("#ipListTitle");
    if (title) title.innerHTML = `${sideName} <span class="muted" id="ipCount"></span>`;
    const items = Array.isArray(d.items) ? d.items : [];
    const countEl = $("#ipCount");
    if (countEl) countEl.textContent = items.length ? `共 ${items.length} 只` : "";
    if (!items.length) {
      box.innerHTML = `<div class="empty">${esc(d.empty_reason || "暂无预测名单")}</div>`;
      return;
    }
    box.innerHTML = `<table><thead><tr>
      <th>#</th><th>名称</th><th>现价</th><th>涨跌幅</th><th>方向</th><th>依据</th>
    </tr></thead><tbody>${items.map((r, i) => `
      <tr data-code="${escAttr(r.code)}" data-name="${escAttr(r.name)}" onclick="openStock('${esc(r.code)}','${esc(r.name)}')">
        <td>${i + 1}</td>
        <td>${esc(r.name)} <span class="muted">${esc(r.code)}</span></td>
        <td class="num">${pxHtml(r.price, r.pct)}</td>
        <td class="num ${cls(r.pct)}">${pct(r.pct)}</td>
        <td><span class="badge ${intelpickSub === "down" ? "dir-利空" : "dir-利好"}">${esc(r.direction || sideName)}</span></td>
        <td>${esc(r.reason || "")}</td>
      </tr>`).join("")}</tbody></table>
      <div class="muted" style="margin-top:8px;font-size:calc(12px * var(--font-scale))">${esc(d.disclaimer || "")}</div>`;
  } catch (err) {
    box.innerHTML = '<div class="empty">加载失败</div>';
    console.warn(err);
  }
}
$("#intelpickTabs")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".opt");
  if (!btn) return;
  intelpickSub = btn.dataset.side || "up";
  $$("#intelpickTabs .opt").forEach((b) => b.classList.toggle("active", b === btn));
  loadIntelpick();
});

/* ---------------- 工具与启动 ---------------- */
function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

loaders.dashboard = loadDashboard;
loaders.stock = () => { if (currentStock) { loadKline(); } };
loaders.screener = initScreener;
loaders.sector = () => showSectorPage(sectorPage || "flow");
loaders.macro = loadMacro;
loaders.commodity = loadCommodities;
loaders.global = loadGlobal;
loaders.recommend = loadRecommend;
loaders.ranks = loadRanks;
loaders.smartpick = loadSmartpick;
loaders.intelpick = loadIntelpick;
loaders.aipick = loadAiPick;
loaders.ai = loadAiConfig;
loaders.settings = () => {
  if (settingsSub === "strategy") loadStrategyPage();
  else if (settingsSub === "engine") loadEngineBlueprint();
  else if (settingsSub === "ui") syncAppearanceUi();
  else loadSettings();
};

/* 自动刷新（仅刷新当前页，避免无谓请求） */
schedule("dashboard", loadDashboard, 10000);
schedule("stock", () => { if (currentStock && currentPeriod === "minute") loadKline(); }, 30000);
schedule("macro", () => { if (macroSub !== "calendar" && macroSub !== "outlook") loadMacro(); }, 60000);
schedule("sector", loadSector, 60000);
schedule("commodity", loadCommodities, 60000);
schedule("global", loadGlobal, 60000);
schedule("recommend", loadRecommend, 60000);
schedule("ranks", loadRanks, 60000);
schedule("smartpick", loadAlerts, 10000);
setInterval(loadBuyPoints, 15000);
schedule("settings", loadSettings, 10000);
setInterval(loadSettingsHealthOnly, 30000);
async function loadSettingsHealthOnly() {
  try {
    const d = await api("/api/system/status");
    const anyCircuit = d.sources.some((s) => s.circuit_open);
    $("#healthDot").className = "dot " + (anyCircuit ? "bad" : "ok");
  } catch { $("#healthDot").className = "dot bad"; }
}

window.addEventListener("resize", () => { klineChart?.resize(); fundKlineChart?.resize(); sectorChart?.resize(); ckChart?.resize(); miniChart?.resize(); flowBarChart?.resize(); flowTrendChart?.resize(); applyAlertDock(); applyBuyFlashPos(); });

function bindAppearance() {
  $("#themeQuickBtn")?.addEventListener("click", () => {
    const light = uiPrefs.theme === "day" || uiPrefs.theme === "dawn";
    setTheme(light ? "night" : "day");
  });
  $("#uiPrefsBtn")?.addEventListener("click", () => {
    window.jumpApp("settings", { view: "ui" });
  });
  $("#fontScaleRange")?.addEventListener("input", (e) => {
    uiPrefs.fontScale = clampFontScale(Number(e.target.value) / 100);
    applyUiPrefs(false);
  });
  $("#fontScaleRange")?.addEventListener("change", () => applyUiPrefs(true));
  $("#fontScaleBtns")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-scale]");
    if (!btn) return;
    setFontScale(btn.dataset.scale);
  });
  $("#themeGrid")?.addEventListener("click", (e) => {
    const card = e.target.closest("[data-theme]");
    if (!card) return;
    setTheme(card.dataset.theme);
  });
  syncAppearanceUi();
}

/* 首屏 */
loadDashboard();
initCommodityCats();
loadSettingsHealthOnly();
loadTopAlmanac();
bindAlertDock();
applyAlertDock();
bindBuyFlash();
loadBuyPoints();
bindAppearance();
