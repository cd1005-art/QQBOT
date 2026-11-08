/* QQBOT 控制台 —— 原生 JS，无构建步骤 */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const state = { me: null, overview: null, groups: [], group: null, gtab: "overview", timer: null };

/* ---------------- 基础工具 ---------------- */

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function ts(t, withDate = true) {
  if (!t) return "-";
  const d = new Date(t * 1000);
  const p = n => String(n).padStart(2, "0");
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  return withDate ? `${p(d.getMonth() + 1)}-${p(d.getDate())} ${hm}` : hm;
}
function ago(t) {
  if (!t) return "-";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - t));
  if (s < 60) return `${s}s 前`;
  if (s < 3600) return `${Math.floor(s / 60)}m 前`;
  if (s < 86400) return `${Math.floor(s / 3600)}h 前`;
  return `${Math.floor(s / 86400)}d 前`;
}
function pretty(s) { try { return JSON.stringify(JSON.parse(s), null, 2); } catch (e) { return String(s || ""); } }

async function api(path, opt = {}) {
  const r = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opt });
  const txt = await r.text();
  let j; try { j = JSON.parse(txt); } catch (e) { j = { ok: false, error: txt.slice(0, 300) || `HTTP ${r.status}` }; }
  if (r.status === 401) { showLogin(); throw new Error("未登录"); }
  return j;
}

/* ---------------- 浮层组件 ---------------- */

let toastTimer = null;
function toast(msg, kind = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast"; }, 3600);
}

function confirmBox(title, text, danger = false) {
  return new Promise(resolve => {
    const mask = document.createElement("div");
    mask.className = "mask";
    mask.innerHTML = `<div class="modal">
      <h3>${esc(title)}</h3><p>${esc(text)}</p>
      <div class="row"><button class="btn ghost" data-x="0">取消</button>
      <button class="btn ${danger ? "bad" : "pri"}" data-x="1">确认执行</button></div></div>`;
    mask.addEventListener("click", e => {
      const b = e.target.closest("[data-x]");
      if (b) { document.body.removeChild(mask); resolve(b.dataset.x === "1"); }
      else if (e.target === mask) { document.body.removeChild(mask); resolve(false); }
    });
    document.body.appendChild(mask);
  });
}

function drawer(title, html) {
  const mask = document.createElement("div");
  mask.className = "drawer-mask";
  mask.innerHTML = `<div class="drawer"><header><b>${esc(title)}</b><span class="sp" style="flex:1"></span>
    <button class="btn sm" data-close>关闭</button></header><div class="body">${html}</div></div>`;
  mask.addEventListener("click", e => {
    if (e.target === mask || e.target.closest("[data-close]")) document.body.removeChild(mask);
  });
  document.body.appendChild(mask);
}

function skeleton(rows = 5) {
  return `<div class="panel-b">${Array.from({ length: rows }, () => `<div class="skel"></div>`).join("")}</div>`;
}
function empty(title, hint = "") {
  return `<div class="empty"><b>${esc(title)}</b>${esc(hint)}</div>`;
}

/* ---------------- 折线图 ---------------- */

function lineChart(data, opt = {}) {
  const w = 720, h = opt.height || 150, pad = { l: 30, r: 8, t: 10, b: 18 };
  const pts = data && data.length ? data : [];
  if (!pts.length) return empty("暂无数据");
  const max = Math.max(1, ...pts.map(d => d.n));
  const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
  const x = i => pad.l + (pts.length === 1 ? iw / 2 : (i / (pts.length - 1)) * iw);
  const y = v => pad.t + ih - (v / max) * ih;
  const line = pts.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d.n).toFixed(1)}`).join(" ");
  const area = `${line} L${x(pts.length - 1).toFixed(1)},${(pad.t + ih).toFixed(1)} L${x(0).toFixed(1)},${(pad.t + ih).toFixed(1)} Z`;
  const labels = [0, 1, 2].map(k => {
    const v = Math.round((max / 2) * (2 - k));
    const yy = pad.t + (ih / 2) * k;
    return `<line class="grid" x1="${pad.l}" y1="${yy}" x2="${w - pad.r}" y2="${yy}"/>
            <text x="4" y="${yy + 3}">${v}</text>`;
  }).join("");
  const ticks = pts.map((d, i) => i % 6 === 0
    ? `<text x="${x(i) - 12}" y="${h - 4}">${ts(d.bucket, false)}</text>` : "").join("");
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <defs><linearGradient id="grad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#4f8cff" stop-opacity=".55"/>
      <stop offset="100%" stop-color="#4f8cff" stop-opacity="0"/>
    </linearGradient></defs>
    ${labels}<path class="area" d="${area}"/><path class="line" d="${line}"/>${ticks}</svg>`;
}

/* ---------------- 路由 ---------------- */

const ROUTES = [
  { id: "dashboard", name: "总览", ico: "▦" },
  { id: "groups", name: "群管理", ico: "◉" },
  { id: "rules", name: "规则", ico: "⚑", soon: true },
  { id: "schedules", name: "定时", ico: "◷", soon: true },
  { id: "events", name: "事件", ico: "≡" },
  { id: "audit", name: "审计", ico: "✓" },
  { id: "settings", name: "设置", ico: "⚙" },
];

function navHtml(active) {
  return ROUTES.map(r => `<a href="#/${r.id}" class="${r.id === active ? "on" : ""}">
    <span class="ico">${r.ico}</span>${r.name}${r.soon ? '<span class="soon">P1</span>' : ""}</a>`).join("");
}

async function route() {
  const id = (location.hash.replace(/^#\/?/, "") || "dashboard").split("/")[0];
  $$("#nav a").forEach(a => a.classList.toggle("on", a.getAttribute("href") === `#/${id}`));
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
  const fn = { dashboard: viewDashboard, groups: viewGroups, rules: viewPlaceholder("rules"),
    schedules: viewPlaceholder("schedules"), events: viewEvents, audit: viewAudit, settings: viewSettings }[id];
  await (fn || viewDashboard)();
}

/* ---------------- 总览 ---------------- */

async function viewDashboard() {
  $("#main").innerHTML = `<div class="topbar"><h1>总览</h1></div>${skeleton(6)}`;
  const j = await api("/panel/api/overview");
  if (!j.ok) { $("#main").innerHTML = empty("读取失败", j.error); return; }
  const d = j.data, b = d.bot || {}, st = d.stats || {};
  state.overview = d;

  $("#main").innerHTML = `
    <div class="topbar">
      <h1>总览</h1>
      <span class="badge ${d.sandbox ? "warn" : "info"}">${d.sandbox ? "沙箱环境" : "正式环境"}</span>
      <span class="badge ${d.dry_run ? "warn" : "ok"}">${d.dry_run ? "dry_run 锁定" : "可执行"}</span>
      <span class="sp"></span>
      <span class="meta">${esc(b.name || "未连接")} · ${esc(b.id || "-")}</span>
      <button class="btn sm" onclick="route()">刷新</button>
    </div>

    <div class="cards">
      ${card("群", st.groups, "已采集 openid")}
      ${card("成员索引", st.members, "来自消息事件")}
      ${card("消息", st.messages, "含已撤回")}
      ${card("今日事件", st.today_events, "自然日累计")}
      ${card("操作审计", st.audit, "写操作留痕")}
      ${card("事件总数", st.events, "含未知类型")}
    </div>

    <div class="panel">
      <div class="panel-h">24 小时消息量<span class="sp"></span><span class="hint">按小时聚合</span></div>
      ${lineChart(d.hourly || [])}
    </div>

    <div class="grid2" style="display:grid;grid-template-columns:1.15fr 1fr;gap:16px">
      <div class="panel" style="margin:0">
        <div class="panel-h">实时事件<span class="sp"></span>
          <label class="hint" style="display:flex;gap:6px;align-items:center;cursor:pointer">
            <input type="checkbox" id="autoRef" style="width:auto" onchange="toggleAuto()"> 自动刷新</label>
        </div>
        <div class="flush" id="evStream">${eventTable(d.events || [])}</div>
      </div>
      <div class="panel" style="margin:0">
        <div class="panel-h">事件类型分布</div>
        <div class="flush">${typeTable(d.types || [])}</div>
      </div>
    </div>`;
}

function card(k, v, d) {
  return `<div class="card"><div class="k">${esc(k)}</div><div class="v">${v == null ? 0 : v}</div>
    <div class="d">${esc(d || "")}</div></div>`;
}
function eventTable(rows) {
  if (!rows.length) return empty("还没有事件", "确认平台回调地址已保存，并在群里发一条消息");
  return `<table><thead><tr><th style="width:96px">时间</th><th>类型</th><th>群</th></tr></thead><tbody>
    ${rows.map(e => `<tr><td class="meta" style="color:var(--muted)">${ts(e.received_at)}</td>
      <td>${esc(e.event_type)}</td><td class="mono" style="color:var(--muted)">${esc((e.group_openid || "-").slice(0, 10))}…</td></tr>`).join("")}
  </tbody></table>`;
}
function typeTable(rows) {
  if (!rows.length) return empty("暂无数据");
  return `<table><thead><tr><th>类型</th><th style="width:60px">数量</th><th style="width:80px">最近</th></tr></thead><tbody>
    ${rows.map(t => `<tr><td>${esc(t.event_type)}</td><td class="num">${t.n}</td>
      <td style="color:var(--muted)">${ago(t.last_at)}</td></tr>`).join("")}
  </tbody></table>`;
}
function toggleAuto() {
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
  if ($("#autoRef").checked) {
    state.timer = setInterval(async () => {
      const j = await api("/panel/api/overview");
      if (j.ok && $("#evStream")) $("#evStream").innerHTML = eventTable(j.data.events || []);
    }, 12000);
  }
}

/* ---------------- 群管理 ---------------- */

async function viewGroups() {
  $("#main").innerHTML = `<div class="topbar"><h1>群管理</h1></div>${skeleton(5)}`;
  const j = await api("/panel/api/groups");
  state.groups = j.ok ? j.data : [];
  if (!state.groups.length) {
    $("#main").innerHTML = `<div class="topbar"><h1>群管理</h1></div>
      <div class="panel">${empty("还没有群记录", "机器人加入群后，群里任意一条消息即可采集 group_openid")}</div>`;
    return;
  }
  if (!state.group || !state.groups.some(g => g.group_openid === state.group)) state.group = state.groups[0].group_openid;
  $("#main").innerHTML = `
    <div class="topbar"><h1>群管理</h1><span class="sp"></span>
      <select id="gsel" style="width:auto;min-width:280px" onchange="state.group=this.value;state.gtab='overview';loadGroup()">
        ${state.groups.map(g => `<option value="${g.group_openid}" ${g.group_openid === state.group ? "selected" : ""}>${esc(g.group_openid)}（成员 ${g.members} · 消息 ${g.messages}）</option>`).join("")}
      </select>
      <button class="btn sm" onclick="loadGroup()">刷新</button>
    </div>
    <div id="gtabs" class="panel" style="margin-bottom:16px">
      <div class="tabs">
        ${[["overview", "概览"], ["members", "成员"], ["messages", "消息"], ["disposal", "处置"]]
          .map(([k, n]) => `<button data-tab="${k}" class="${state.gtab === k ? "on" : ""}" onclick="state.gtab='${k}';loadGroup()">${n}</button>`).join("")}
      </div>
      <div id="gbody">${skeleton(4)}</div>
    </div>`;
  loadGroup();
}

async function loadGroup() {
  const g = state.group;
  $("#gtabs .tabs").innerHTML = [["overview", "概览"], ["members", "成员"], ["messages", "消息"], ["disposal", "处置"]]
    .map(([k, n]) => `<button data-tab="${k}" class="${state.gtab === k ? "on" : ""}" onclick="state.gtab='${k}';loadGroup()">${n}</button>`).join("");
  $("#gbody").innerHTML = skeleton(4);
  if (state.gtab === "overview") return groupOverview(g);
  if (state.gtab === "members") return groupMembers(g);
  if (state.gtab === "messages") return groupMessages(g);
  if (state.gtab === "disposal") return groupDisposal(g);
}

async function groupOverview(g) {
  const j = await api(`/panel/api/groups/${g}/stats`);
  const d = j.ok ? j.data : {};
  const info = d.info || {}, st = d.state || {};
  $("#gbody").innerHTML = `
    <div class="panel-b">
      <div class="row" style="justify-content:space-between;align-items:flex-start">
        <div>
          <div style="font-size:16px;font-weight:600;margin-bottom:4px">${esc(info.group_name || "未知群")}</div>
          <div class="meta" style="color:var(--muted);font-size:12.5px">
            群人数 ${info.group_member_num ?? "-"} · 分类 ${esc(info.group_class_text || "-")}
            ${st.member_role ? ` · 机器人角色 <span class="badge ${st.member_role === "admin" ? "ok" : ""}">${esc(st.member_role)}</span>` : ""}
            ${st.recv_msg_setting ? ` · 消息范围 <span class="badge info">${esc(st.recv_msg_setting)}</span>` : ""}
            ${st.allow_proactive_msg != null ? ` · 主动发言 <span class="badge ${st.allow_proactive_msg ? "ok" : "warn"}">${st.allow_proactive_msg ? "允许" : "关闭"}</span>` : ""}
          </div>
        </div>
        <div class="mono" style="color:var(--muted);font-size:11.5px;max-width:320px;text-align:right">${esc(g)}</div>
      </div>
      ${d.error ? `<div class="mt12 badge warn">${esc(d.error)}</div>` : ""}
    </div>
    <div style="border-top:1px solid var(--line)">${lineChart(d.hourly || [], { height: 130 })}</div>
    <div class="panel-b" style="border-top:1px solid var(--line)">
      <div class="row" style="justify-content:space-between;margin-bottom:8px">
        <b style="font-size:13px">快捷操作</b><span class="hint" style="color:var(--muted);font-size:12px">受 dry_run 与群白名单约束</span>
      </div>
      <div class="row">
        <input id="sendText" class="grow" placeholder="发送到该群的文本消息">
        <button class="btn pri" onclick="act('send',{text:$('#sendText').value}).then(()=>{if(!$('#toast').classList.contains('bad'))$('#sendText').value=''})">发送</button>
        <button class="btn" onclick="act('at-all',{text:$('#sendText').value})">发 @全体</button>
        <button class="btn bad" onclick="act('recall',{})">撤回最近一条</button>
        <button class="btn" onclick="act('mute-all',{enable:true})">全员禁言</button>
        <button class="btn" onclick="act('mute-all',{enable:false})">解除全员</button>
      </div>
    </div>`;
}

async function groupMembers(g) {
  const j = await api(`/panel/api/groups/${g}/members`);
  const rows = j.ok ? j.data : [];
  $("#gbody").innerHTML = `
    <div class="panel-b" style="padding-bottom:0">
      <div class="row"><input id="msearch" class="grow" placeholder="按 openid 或昵称过滤" oninput="filterMembers()"></div>
      <div class="hint mt8" style="color:var(--muted);font-size:12px">
        数据来自消息事件；平台未开放成员列表接口（11253），未发言成员不会出现在这里。</div>
    </div>
    <div class="flush" id="mtab">${membersTable(rows)}</div>`;
  state.members = rows;
}
function membersTable(rows) {
  if (!rows.length) return empty("暂无成员记录", "等群成员发一条消息");
  return `<table><thead><tr><th>member_openid</th><th>昵称</th><th style="width:60px">发言</th>
    <th style="width:90px">最近</th><th style="width:150px">操作</th></tr></thead><tbody>
    ${rows.map(m => `<tr data-k="${esc((m.member_openid + " " + (m.nickname || "")).toLowerCase())}">
      <td class="mono">${esc(m.member_openid)}</td><td>${esc(m.nickname || "-")}</td>
      <td class="num">${m.msg_count}</td><td style="color:var(--muted)">${ago(m.last_seen)}</td>
      <td><button class="btn sm" onclick="askMute('${esc(m.member_openid)}')">禁言</button>
          <button class="btn sm" onclick="act('unmute',{member:'${esc(m.member_openid)}'})">解禁</button></td></tr>`).join("")}
  </tbody></table>`;
}
function filterMembers() {
  const q = ($("#msearch")?.value || "").trim().toLowerCase();
  $$("#mtab tbody tr").forEach(tr => { tr.style.display = !q || tr.dataset.k.includes(q) ? "" : "none"; });
}
function askMute(member) {
  const min = prompt("禁言时长（分钟，1~43200）：", "10");
  if (min == null) return;
  const n = parseInt(min, 10);
  if (!n || n < 1 || n > 43200) return toast("时长无效", "bad");
  act("mute", { member, minutes: n });
}

async function groupMessages(g) {
  const j = await api(`/panel/api/groups/${g}/messages?limit=60`);
  const rows = j.ok ? j.data : [];
  $("#gbody").innerHTML = `
    <div class="panel-b" style="padding-bottom:0">
      <div class="row"><input id="msearch2" class="grow" placeholder="按内容搜索" onkeydown="if(event.key==='Enter')searchMsgs()">
      <button class="btn" onclick="searchMsgs()">搜索</button></div>
    </div>
    <div class="flush" id="msgbox">${messagesTable(rows)}</div>`;
  state.messages = rows;
}
function messagesTable(rows) {
  if (!rows.length) return empty("暂无消息");
  const now = Date.now() / 1000;
  return `<table><thead><tr><th style="width:90px">时间</th><th style="width:120px">成员</th><th>内容</th>
    <th style="width:110px">可撤回</th><th style="width:80px">操作</th></tr></thead><tbody>
    ${rows.map(m => {
      const left = Math.floor(120 - (now - m.created_at));
      const able = !m.recalled && left > 0;
      return `<tr><td style="color:var(--muted)">${ts(m.created_at, false)}</td>
        <td class="mono" style="color:var(--muted)">${esc((m.member_openid || "-").slice(0, 8))}…</td>
        <td>${esc(m.content || "")}${m.recalled ? ' <span class="badge">已撤回</span>' : ""}</td>
        <td>${m.recalled ? '<span class="badge">—</span>' : able ? `<span class="badge ok">${left}s</span>` : '<span class="badge warn">已超时</span>'}</td>
        <td>${able ? `<button class="btn sm" onclick="act('recall',{message_id:'${esc(m.message_id)}'})">撤回</button>` : ""}</td></tr>`;
    }).join("")}
  </tbody></table>`;
}
async function searchMsgs() {
  const q = encodeURIComponent(($("#msearch2").value || "").trim());
  const j = await api(`/panel/api/groups/${state.group}/messages?limit=60&q=${q}`);
  $("#msgbox").innerHTML = messagesTable(j.ok ? j.data : []);
}

async function groupDisposal(g) {
  const j = await api("/panel/api/audit?limit=100");
  const rows = (j.ok ? j.data : []).filter(r => r.group_openid === g);
  $("#gbody").innerHTML = rows.length ? auditTable(rows) : empty("该群暂无处置记录");
}

/* ---------------- 事件 ---------------- */

async function viewEvents() {
  $("#main").innerHTML = `
    <div class="topbar"><h1>事件</h1><span class="sp"></span>
      <span class="meta">原始留档 · 重推自动去重</span></div>
    <div class="panel"><div class="panel-b">
      <div class="row">
        <select id="evType" class="grow"><option value="">全部类型</option></select>
        <input id="evLimit" value="80" style="width:90px" title="条数">
        <button class="btn pri" onclick="loadEvents()">查询</button>
      </div>
    </div><div class="flush" id="evBox">${skeleton(4)}</div></div>`;
  const o = await api("/panel/api/overview");
  if (o.ok) {
    $("#evType").innerHTML = `<option value="">全部类型</option>` +
      (o.data.types || []).map(t => `<option value="${esc(t.event_type)}">${esc(t.event_type)}（${t.n}）</option>`).join("");
  }
  loadEvents();
}

async function loadEvents() {
  const t = $("#evType")?.value || "";
  const n = parseInt($("#evLimit")?.value || "80", 10) || 80;
  const j = await api(`/panel/api/events?limit=${n}${t ? `&type=${encodeURIComponent(t)}` : ""}`);
  const rows = j.ok ? j.data : [];
  if (!rows.length) { $("#evBox").innerHTML = empty("没有匹配的事件"); return; }
  $("#evBox").innerHTML = `<table><thead><tr><th style="width:104px">时间</th><th style="width:240px">类型</th>
    <th>群</th><th style="width:70px"></th></tr></thead><tbody>
    ${rows.map((e, i) => `<tr><td style="color:var(--muted)">${ts(e.received_at)}</td><td>${esc(e.event_type)}</td>
      <td class="mono" style="color:var(--muted)">${esc(e.group_openid || "-")}</td>
      <td><button class="btn sm" onclick='showPayload(${i})'>详情</button></td></tr>`).join("")}
  </tbody></table>`;
  state.evRows = rows;
}
function showPayload(i) {
  const e = state.evRows[i];
  drawer(e.event_type, `<div class="meta" style="color:var(--muted);margin-bottom:8px">${ts(e.received_at)} · ${esc(e.group_openid || "-")}</div>
    <pre>${esc(pretty(e.payload))}</pre>`);
}

/* ---------------- 审计 ---------------- */

function auditTable(rows) {
  if (!rows.length) return empty("暂无记录");
  return `<table><thead><tr><th style="width:100px">时间</th><th style="width:110px">动作</th>
    <th>群</th><th>对象</th><th style="width:150px">结果</th></tr></thead><tbody>
    ${rows.map(r => `<tr><td style="color:var(--muted)">${ts(r.ts)}</td><td>${esc(r.action)}</td>
      <td class="mono" style="color:var(--muted)">${esc((r.group_openid || "-").slice(0, 12))}</td>
      <td class="mono" style="color:var(--muted)">${esc((r.target_openid || "-").slice(0, 18))}</td>
      <td>${r.ok ? '<span class="badge ok">成功</span>' : '<span class="badge bad">失败</span>'}
        <span style="color:var(--muted);font-size:12px">${esc(r.result || "")}</span></td></tr>`).join("")}
  </tbody></table>`;
}

async function viewAudit() {
  $("#main").innerHTML = `<div class="topbar"><h1>审计</h1><span class="sp"></span>
    <button class="btn sm" onclick="exportAudit()">导出 CSV</button></div>
    <div class="panel"><div class="flush" id="auBox">${skeleton(5)}</div></div>`;
  const j = await api("/panel/api/audit?limit=200");
  state.audit = j.ok ? j.data : [];
  $("#auBox").innerHTML = auditTable(state.audit);
}
function exportAudit() {
  const rows = state.audit || [];
  if (!rows.length) return toast("没有可导出的记录", "bad");
  const head = "时间,动作,群,对象,结果,详情";
  const body = rows.map(r => [new Date(r.ts * 1000).toLocaleString("zh-CN"), r.action,
    r.group_openid || "", r.target_openid || "", r.ok ? "成功" : "失败", (r.result || "").replace(/,/g, "，")]
    .map(v => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob(["\ufeff" + head + "\n" + body], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `qqbot-audit-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ---------------- 设置 ---------------- */

async function viewSettings() {
  $("#main").innerHTML = `<div class="topbar"><h1>设置</h1></div>${skeleton(4)}`;
  const j = await api("/panel/api/settings");
  if (!j.ok) { $("#main").innerHTML = empty("读取失败", j.error); return; }
  const d = j.data;
  $("#main").innerHTML = `
    <div class="topbar"><h1>设置</h1><span class="sp"></span>
      <span class="badge ${d.dry_run ? "warn" : "ok"}">dry_run ${d.dry_run ? "开启" : "关闭"}</span></div>

    <div class="panel"><div class="panel-h">机器人凭证</div><div class="panel-b">
      <div class="row" style="gap:24px">
        <div><div class="k" style="color:var(--muted);font-size:12px">AppID</div><div class="mono">${esc(d.app_id || "-")}</div></div>
        <div><div class="k" style="color:var(--muted);font-size:12px">AppSecret</div><div class="mono">${esc(d.app_secret || "-")}</div></div>
        <div><div class="k" style="color:var(--muted);font-size:12px">环境</div><div>${d.sandbox ? "沙箱" : "正式"}</div></div>
      </div>
      <div class="hint mt12" style="color:var(--muted);font-size:12px">凭证以 config.toml 为准，修改请直接编辑文件后重启服务。</div>
    </div></div>

    <div class="panel"><div class="panel-h">安全</div><div class="panel-b">
      <div class="row" style="justify-content:space-between;margin-bottom:12px">
        <div><b>写操作硬锁</b><div class="hint" style="color:var(--muted);font-size:12px">
          开启后所有群管理操作只预览不执行；关闭会立即生效并记入审计。</div></div>
        <button class="btn ${d.dry_run ? "pri" : "bad"}" onclick="toggleDry(${d.dry_run})">${d.dry_run ? "解除锁定" : "重新锁定"}</button>
      </div>
      <div style="border-top:1px solid var(--line);padding-top:14px">
        <b>群白名单</b>
        <div class="hint mt8" style="color:var(--muted);font-size:12px">
          ${d.allowed_groups && d.allowed_groups.length ? d.allowed_groups.map(g => `<code class="mono">${esc(g)}</code>`).join("<br>") : "未设置（不限制），生产环境建议填写"}</div>
      </div>
    </div></div>

    <div class="panel"><div class="panel-h">修改面板密码</div><div class="panel-b">
      <div class="row" style="align-items:flex-end">
        <label class="field grow" style="margin:0"><span>原密码</span><input id="pwOld" type="password"></label>
        <label class="field grow" style="margin:0"><span>新密码（≥6 位）</span><input id="pwNew" type="password"></label>
        <button class="btn pri" onclick="changePw()">保存</button>
      </div>
    </div></div>

    <div class="panel"><div class="panel-h">运行信息</div><div class="panel-b">
      <table><tbody>
        <tr><td style="color:var(--muted);width:150px">Webhook 监听</td><td class="mono">${esc(d.listen)}</td></tr>
        <tr><td style="color:var(--muted)">面板监听</td><td class="mono">${esc(d.panel)}</td></tr>
        <tr><td style="color:var(--muted)">数据库</td><td class="mono">${esc(d.db)}</td></tr>
        <tr><td style="color:var(--muted)">配置文件</td><td class="mono">${esc(d.config)}</td></tr>
        <tr><td style="color:var(--muted)">@全体模板</td><td class="mono">${esc(d.at_all_template)}</td></tr>
      </tbody></table>
    </div></div>`;
}

async function toggleDry(current) {
  const enable = !current;
  const ok = await confirmBox(enable ? "重新锁定写操作" : "解除写操作锁定",
    enable ? "开启后所有群管理操作只预览不执行。" : "关闭后面板和 CLI 的写操作会真实发往 QQ 平台，包括禁言、撤回、发消息。", !enable);
  if (!ok) return;
  const j = await api("/panel/api/settings/dry-run", { method: "POST", body: JSON.stringify({ enable }) });
  if (j.ok) { toast(`dry_run 已${enable ? "开启" : "关闭"}`, "ok"); viewSettings(); }
  else toast(j.error || "操作失败", "bad");
}

async function changePw() {
  const old = $("#pwOld").value, nw = $("#pwNew").value;
  if (!old || !nw) return toast("请填写完整", "bad");
  const j = await api("/panel/api/settings/password", { method: "POST", body: JSON.stringify({ old, new: nw }) });
  if (j.ok) { toast("密码已更新", "ok"); $("#pwOld").value = $("#pwNew").value = ""; }
  else toast(j.error || "修改失败", "bad");
}

/* ---------------- 占位页 ---------------- */

function viewPlaceholder(kind) {
  return async function () {
    const conf = {
      rules: {
        title: "规则（自动处置）", badge: "P1 第一优先",
        items: ["关键词命中自动撤回", "正则匹配 + 频率阈值", "命中后自动禁言 N 分钟", "按群独立启用与白名单", "每次处置写入 disposals 留痕"],
      },
      schedules: {
        title: "定时任务", badge: "P1 第二优先",
        items: ["每日固定时间发文本公告", "指定多个目标群", "Cron 表达式与下次执行预览", "执行历史与失败原因", "失败重试与告警"],
      },
    }[kind];
    $("#main").innerHTML = `
      <div class="topbar"><h1>${conf.title}</h1><span class="badge info">${conf.badge}</span></div>
      <div class="panel"><div class="panel-b">
        <div style="color:var(--sub);margin-bottom:12px">本页为 P0 占位，P1 阶段落地。计划包含：</div>
        <ul style="margin:0;padding-left:20px;color:var(--text);line-height:2">
          ${conf.items.map(t => `<li>${esc(t)}</li>`).join("")}
        </ul>
        <div class="hint mt16" style="color:var(--muted);font-size:12px">
          数据表已预留：${kind === "rules" ? "rules / disposals" : "schedules"}。</div>
      </div></div>`;
  };
}

/* ---------------- 动作 ---------------- */

async function act(action, params) {
  if (!state.group) return toast("请先选择群", "bad");
  const label = { send: "发送消息", "at-all": "发 @全体", recall: "撤回", mute: "禁言", unmute: "解禁",
    "mute-all": "全员禁言", kick: "移出成员", approve: "审批" }[action] || action;
  if (action !== "send" && action !== "unmute") {
    const ok = await confirmBox(`确认${label}`, `目标群：${state.group}\n此操作会真实发往 QQ 平台。`, ["recall", "mute", "mute-all", "kick"].includes(action));
    if (!ok) return;
  }
  const j = await api("/panel/api/action", { method: "POST", body: JSON.stringify({ action, group: state.group, ...params }) });
  if (j.ok) { toast(`${label} 成功`, "ok"); setTimeout(() => loadGroup(), 600); }
  else toast(`${label} 失败：${j.error}`, "bad");
}

/* ---------------- 启动 ---------------- */

function showLogin() {
  $("#login").classList.remove("hide");
  $("#app").classList.add("hide");
}

async function doLogin() {
  const pw = $("#pwd").value;
  const j = await api("/panel/api/login", { method: "POST", body: JSON.stringify({ password: pw }) });
  if (j.ok) { $("#pwd").value = ""; await boot(); }
  else { $("#loginHint").textContent = j.error || "登录失败"; }
}

async function boot() {
  const me = await api("/panel/api/me").catch(() => ({ ok: false }));
  if (!me.ok) return showLogin();
  state.me = me.data;
  $("#login").classList.add("hide");
  $("#app").classList.remove("hide");
  $("#nav").innerHTML = navHtml((location.hash.replace(/^#\/?/, "") || "dashboard").split("/")[0]);
  $("#lockState").innerHTML = me.data.dry_run
    ? '<span class="badge warn">dry_run 锁定</span>'
    : '<span class="badge ok">可执行</span>';
  const o = await api("/panel/api/overview");
  if (o.ok) {
    $("#botName").textContent = o.data.bot?.name || "未连接";
    $("#dotState").className = "dot" + (o.data.bot?.error ? " warn" : "");
  }
  await route();
}

window.addEventListener("hashchange", () => { if (!$("#app").classList.contains("hide")) route(); });
window.addEventListener("DOMContentLoaded", async () => {
  const me = await api("/panel/api/me").catch(() => ({ ok: false }));
  if (me.ok) boot(); else showLogin();
});
window.addEventListener("keydown", e => { if (e.key === "Enter" && $("#login") && !$("#login").classList.contains("hide")) doLogin(); });
