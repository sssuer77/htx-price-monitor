"use strict";

const $ = (id) => document.getElementById(id);
const state = { ticks: {}, bad: {}, rules: [], events: [], cfg: null, lastEventSeq: 0, notifyOk: false };

/* ---------------------------------------------------------------- 工具 */
function fmt(v, digits) {
  if (v === null || v === undefined || isNaN(v)) return "-";
  const a = Math.abs(v);
  if (digits !== undefined) return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  if (a >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (a >= 1) return v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  return v.toFixed(8).replace(/0+$/, "").replace(/\.$/, "");
}
function pct(v) { return (v === null || v === undefined || isNaN(v)) ? "-" : (v >= 0 ? "+" : "") + v.toFixed(2) + "%"; }
function cls(v) { return (v === null || v === undefined) ? "" : (v >= 0 ? "up" : "dn"); }
function hhmmss(ts) { const d = new Date(ts * 1000); return d.toTimeString().slice(0, 8); }
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

function toast(title, body, kind) {
  const wrap = $("toast-wrap");
  const el = document.createElement("div");
  el.className = "toast " + (kind || "");
  el.innerHTML = `<div class="tt">${esc(title)}</div><div>${esc(body)}</div>`;
  wrap.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .4s"; }, 5200);
  setTimeout(() => el.remove(), 5800);
}

async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return res.json();
}

/* ---------------------------------------------------------------- 渲染 */
function renderTickers() {
  const box = $("tickers");
  // 以「监控列表」为准渲染：刚加入还没拿到行情的合约也要显示出来，
  // 否则用户会以为添加失败了（之前就是这个 bug）。
  const watch = (state.cfg && state.cfg.symbols) || [];
  const syms = Array.from(new Set([...watch, ...Object.keys(state.ticks)])).sort();
  if (!syms.length) { box.innerHTML = '<div class="hint">还没有监控任何合约</div>'; return; }
  box.innerHTML = syms.map((s) => {
    const del = `<button class="tick-del" data-sym="${esc(s)}" title="移出监控">×</button>`;
    const t = state.ticks[s];
    if (!t) {
      const err = (state.bad || {})[s];
      return `<div class="tick pending">
        <div class="top"><span class="sym">${esc(s)}</span><span class="src">等待</span></div>
        ${del}
        <div class="price dim">--</div>
        <div class="meta"><span class="${err ? "err-text" : ""}">${err ? "拉取失败：" + esc(err) : "正在拉取行情…"}</span></div>
      </div>`;
    }
    const chg = t.change_pct24;
    const lat = t.delivery_ms === null || t.delivery_ms === undefined ? "-" : t.delivery_ms.toFixed(0) + "ms";
    return `<div class="tick">
      <div class="top"><span class="sym">${esc(s)}</span><span class="src">${esc(t.src.toUpperCase())}</span></div>
      ${del}
      <div class="price ${cls(chg)}">${fmt(t.price)}</div>
      <div class="meta"><span class="${cls(chg)}">${pct(chg)}</span>
        <span>H ${fmt(t.high24)}</span><span>L ${fmt(t.low24)}</span><span>延迟 ${lat}</span></div>
    </div>`;
  }).join("");
}

async function removeSymbol(sym) {
  const n = state.rules.filter((r) => r.symbol === sym).length;
  const tail = n ? `\n同时会删除该币种的 ${n} 条监控规则。` : "";
  if (!confirm(`把 ${sym} 移出实时行情监控？${tail}`)) return;
  const r = await api("/api/symbol/remove", { symbol: sym, purge_rules: true });
  if (!r.ok) return toast("删除失败", r.error || "未知错误", "err");
  delete state.ticks[sym];
  renderTickers();
  toast("已移出监控", sym + (r.removed_rules ? `，并删除 ${r.removed_rules} 条规则` : ""), "ok");
  refresh();
}

function ruleStateText(r) {
  const t = state.ticks[r.symbol];
  const st = r.state || {};
  const bits = [];
  if (r.status === "triggered") bits.push("已触发");
  else if (r.status === "disabled") bits.push("已暂停");
  else bits.push("监控中");
  if (t && r.level) {
    const d = (t.price / r.level - 1) * 100;
    bits.push(`距价位 ${pct(d)}`);
  }
  if (st.fired_count) bits.push(`累计触发 ${st.fired_count} 次`);
  if (st.last_fire_ts) bits.push(`上次 ${hhmmss(st.last_fire_ts)}`);
  return bits.join(" · ");
}

function renderRules() {
  const box = $("rules");
  $("rule-count").textContent = `共 ${state.rules.length} 条`;
  if (!state.rules.length) { box.innerHTML = '<div class="hint">还没有规则，在上面输入一句话即可添加。</div>'; return; }
  box.innerHTML = state.rules.map((r) => `
    <div class="rule ${esc(r.status)}">
      <div class="line1">
        <span class="desc">${esc(r.note || r.raw || r.symbol)}</span>
        <span class="actions">
          <button data-act="toggle" data-id="${esc(r.id)}">${r.status === "active" ? "暂停" : "恢复"}</button>
          <button data-act="del" data-id="${esc(r.id)}">删除</button>
        </span>
      </div>
      <div class="state">${esc(r.symbol)} · ${esc(r.type)} · 目标 ${fmt(r.level)}${r.level2 ? " ~ " + fmt(r.level2) : ""}${r.pct ? " · " + r.pct + "%" : ""}${r.window_sec ? " / " + (r.window_sec / 60) + "分钟" : ""} · ${r.repeat === "once" ? "仅一次" : "可重复"}</div>
      <div class="state">${esc(ruleStateText(r))}</div>
    </div>`).join("");
}

function renderEvents() {
  const box = $("events");
  const items = state.events.slice(-80).reverse();
  if (!items.length) { box.innerHTML = '<div class="hint">暂无记录</div>'; return; }
  box.innerHTML = items.map((e) => `
    <div class="ev ${esc(e.kind)}">
      <div class="t">${hhmmss(e.ts)} · ${esc(e.kind)}</div>
      <div>${esc(e.message)}</div>
    </div>`).join("");
}

function renderStatus(feed, cert, stats) {
  const sse = $("pill-sse");
  sse.textContent = "界面 已连接"; sse.className = "pill ok";
  const ws = feed.ws || {};
  const wsEl = $("pill-ws");
  if (!state.cfg || state.cfg.enable_ws === false) { wsEl.textContent = "WS 已关闭"; wsEl.className = "pill warn"; }
  else if (ws.connected) {
    const age = ws.last_msg_ts ? (Date.now() / 1000 - ws.last_msg_ts) : 999;
    wsEl.textContent = `WS 已连接${age > 20 ? "（静默）" : ""}`;
    wsEl.className = "pill " + (age > 20 ? "warn" : "ok");
  } else { wsEl.textContent = "WS 未连接"; wsEl.className = "pill bad"; }
  const rest = feed.rest || {};
  const restEl = $("pill-rest");
  restEl.textContent = rest.last_error ? "REST 异常" : "REST 正常";
  restEl.className = "pill " + (rest.last_error ? "bad" : "ok");
  $("pill-lat").textContent = `延迟 ${rest.latency_ms !== null && rest.latency_ms !== undefined ? rest.latency_ms + "ms" : "-"}`;
  const certEl = $("pill-cert");
  certEl.textContent = `证书 ${cert.ca_count}${cert.verifying ? "" : "（未校验）"}`;
  certEl.className = "pill " + (cert.verifying ? "ok" : "warn");
  const lat = state.ticks[Object.keys(state.ticks)[0]];
  $("tick-src").textContent = lat && lat.delivery_ms !== null && lat.delivery_ms !== undefined
    ? `WS 单程延迟约 ${lat.delivery_ms.toFixed(0)}ms（已做时钟校正）` : "";
}

function renderConfig() {
  const c = state.cfg; if (!c) return;
  $("cfg-ws").checked = !!c.enable_ws;
  $("cfg-poll").value = c.poll_interval_sec;
  $("cfg-ui").checked = !!c.notify.ui;
  $("cfg-ball").checked = !!c.notify.ball;
  $("cfg-sound").checked = !!c.notify.sound;
  $("cfg-toast").checked = !!c.notify.toast;
  $("cfg-hook").value = c.notify.webhook || "";
  $("cfg-hook-kind").value = c.notify.webhook_kind || "generic";
  $("cfg-llm").checked = !!c.llm.enabled;
  $("cfg-llm-model").value = c.llm.model || "";
  if (c.ball) {
    $("cfg-ball-enabled").checked = !!c.ball.enabled;
    $("cfg-ball-size").value = c.ball.size || 72;
  }
}

/* ---------------------------------------------------------------- 数据 */
async function refresh() {
  const snap = await api("/api/state");
  state.ticks = {};
  for (const [k, v] of Object.entries(snap.feed.ticks || {})) state.ticks[k] = v;
  state.bad = snap.feed.bad || {};
  state.rules = snap.rules || [];
  state.events = snap.events || [];
  state.cfg = snap.config;
  renderTickers(); renderRules(); renderEvents(); renderStatus(snap.feed, snap.cert, snap.stats); renderConfig();
}

function connectStream() {
  const es = new EventSource("/api/stream");
  es.onmessage = (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === "tick") { state.ticks = msg.ticks || {}; renderTickers(); }
    else if (msg.type === "rules") { state.rules = msg.rules || []; renderRules(); }
    else if (msg.type === "events") {
      for (const e of msg.events) {
        state.events.push(e);
        if (e.kind === "alert") { onAlert(e); }
      }
      if (state.events.length > 400) state.events = state.events.slice(-400);
      renderEvents();
      if (msg.events.some((e) => e.kind === "rule")) refresh();
    }
  };
  es.onerror = () => {
    const el = $("pill-sse"); el.textContent = "界面 重连中"; el.className = "pill bad";
    setTimeout(refresh, 1500);
  };
}

function onAlert(e) {
  toast("⚡ " + (e.symbol || "触发提醒"), e.message || "", "");
  document.body.classList.add("flash");
  setTimeout(() => document.body.classList.remove("flash"), 1300);
  if (state.notifyOk && document.hidden) {
    try { new Notification("⚡ " + (e.symbol || "HTX 提醒"), { body: e.message || "" }); } catch (err) {}
  }
}

/* ---------------------------------------------------------------- 交互 */
async function addRule() {
  const text = $("nl").value.trim();
  if (!text) return;
  const msg = $("parse-msg");
  msg.textContent = "解析中…";
  const res = await api("/api/parse", { text });
  if (res.ok) {
    const lines = res.rules.map((r) => `✅ ${r.describe}　（${r.hint}）`);
    msg.textContent = lines.join("\n") + (res.warnings.length ? "\n⚠ " + res.warnings.join("\n⚠ ") : "");
    $("nl").value = "";
    toast("已添加监控", res.rules.map((r) => r.describe).join("；"), "ok");
    refresh();
  } else {
    msg.textContent = "❌ 没解析成功：" + (res.warnings.join("；") || "换种说法试试，例如「BTC 跌破 83000 提醒我」");
  }
}

function bind() {
  $("btn-add").onclick = addRule;
  $("nl").addEventListener("keydown", (e) => { if (e.key === "Enter") addRule(); });
  $("examples").addEventListener("click", (e) => {
    if (e.target.classList.contains("chip")) { $("nl").value = e.target.textContent.trim(); $("nl").focus(); }
  });
  $("rules").addEventListener("click", async (e) => {
    const btn = e.target.closest("button"); if (!btn) return;
    const id = btn.dataset.id;
    if (btn.dataset.act === "del") await api("/api/rule/delete", { id });
    else if (btn.dataset.act === "toggle") await api("/api/rule/toggle", { id });
    refresh();
  });
  $("tickers").addEventListener("click", (e) => {
    const btn = e.target.closest(".tick-del");
    if (btn) removeSymbol(btn.dataset.sym);
  });
  $("btn-clear-done").onclick = async () => { await api("/api/rules/clear", { status: "triggered" }); refresh(); };
  $("btn-test").onclick = async () => {
    const r = await api("/api/test-notify", {});
    toast("测试提醒已发送", JSON.stringify(r.channels), "ok");
  };
  $("btn-sym").onclick = async () => {
    const v = $("sym-input").value.trim(); if (!v) return;
    const r = await api("/api/symbol/add", { symbol: v });
    if (r.ok) {
      $("sym-input").value = "";
      toast(r.warning ? "已加入（有提示）" : "已加入监控",
            (r.symbol || v.toUpperCase()) + (r.warning ? "：" + r.warning : ""),
            r.warning ? "" : "ok");
      refresh();
    } else toast("加入失败", r.error || "未知错误", "err");
  };
  $("btn-save").onclick = async () => {
    await api("/api/config", {
      enable_ws: $("cfg-ws").checked,
      poll_interval_sec: parseFloat($("cfg-poll").value) || 3,
      notify: {
        ui: $("cfg-ui").checked, sound: $("cfg-sound").checked, toast: $("cfg-toast").checked,
        ball: $("cfg-ball").checked,
        webhook: $("cfg-hook").value.trim(), webhook_kind: $("cfg-hook-kind").value,
      },
      ball: { enabled: $("cfg-ball-enabled").checked, size: parseInt($("cfg-ball-size").value, 10) || 72 },
      llm: {
        enabled: $("cfg-llm").checked, model: $("cfg-llm-model").value.trim(),
        api_key: $("cfg-llm-key").value.trim(), base_url: $("cfg-llm-url").value.trim(),
      },
    });
    toast("设置已保存", "部分设置重启后完全生效", "ok");
    $("cfg-llm-key").value = "";
    refresh();
  };
  $("btn-notify-perm").onclick = async () => {
    if (!("Notification" in window)) return toast("不支持", "当前浏览器不支持通知", "err");
    const p = await Notification.requestPermission();
    state.notifyOk = p === "granted";
    toast("浏览器通知", p === "granted" ? "已开启" : "未授权", p === "granted" ? "ok" : "err");
  };
  if ("Notification" in window && Notification.permission === "granted") state.notifyOk = true;
}

/* ---------------------------------------------------------------- 启动 */
bind();
refresh().then(connectStream);
setInterval(() => { if (!state.cfg) refresh(); }, 20000);
