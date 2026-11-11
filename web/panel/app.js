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
  { id: "rules", name: "规则", ico: "⚑" },
  { id: "schedules", name: "定时", ico: "◷" },
  { id: "chat", name: "对话", ico: "✦" },
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
  const fn = { dashboard: viewDashboard, groups: viewGroups, rules: viewRules,
    schedules: viewSchedules, chat: viewChat, events: viewEvents, audit: viewAudit,
    settings: viewSettings }[id];
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

function who(nick, openid) {
  const o = openid || "";
  if (!o) return "—";
  const name = (nick || "").trim() || "未知";
  const short = o.length > 6 ? o.slice(-6) : o;
  return `<span title="${esc(o)}" class="who" data-openid="${esc(o)}"
    style="cursor:copy" onclick="copyText(this.dataset.openid)">${esc(name)}
    <span style="color:var(--muted);font-size:11.5px">（${esc(short)}）</span></span>`;
}

function copyText(t) {
  navigator.clipboard?.writeText(t).then(() => toast("已复制 openid", "ok")).catch(() => {});
}

function groupLabel(g) {
  const t = (g.title || "").trim();
  const short = g.group_openid ? g.group_openid.slice(0, 8) + "…" : "";
  return t ? `${t}（${short}）` : short;
}
function groupName(gid) {
  const g = (state.groups || []).find(x => x.group_openid === gid);
  return g && g.title ? `${g.title}（${gid.slice(0, 8)}…）` : gid.slice(0, 10) + "…";
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
      <select id="gsel" style="width:auto;min-width:280px" onchange="state.group=this.value;state.gtab='overview';loadGroup();if($('#gidHint'))$('#gidHint').textContent=this.value">
        ${state.groups.map(g => `<option value="${g.group_openid}" title="${esc(g.group_openid)}" ${g.group_openid === state.group ? "selected" : ""}>${esc(groupLabel(g))}　成员 ${g.members} · 消息 ${g.messages}</option>`).join("")}
      </select>
      <button class="btn sm" onclick="loadGroup()">刷新</button>
      <span class="meta mono" id="gidHint" style="color:var(--muted);font-size:11.5px"></span>
    </div>
    <div id="gtabs" class="panel" style="margin-bottom:16px">
      <div class="tabs">
        ${[["overview", "概览"], ["members", "成员"], ["messages", "消息"], ["disposal", "处置"]]
          .map(([k, n]) => `<button data-tab="${k}" class="${state.gtab === k ? "on" : ""}" onclick="state.gtab='${k}';loadGroup()">${n}</button>`).join("")}
      </div>
      <div id="gbody">${skeleton(4)}</div>
    </div>`;
  $("#gidHint").textContent = state.group ? state.group : "";
  loadGroup();
}

async function loadGroup() {
  const g = state.group;
  const hint = document.getElementById("gidHint");
  if (hint) hint.textContent = g || "";
  $("#gtabs .tabs").innerHTML = [["overview", "概览"], ["members", "成员"], ["messages", "消息"], ["disposal", "处置"]]
    .map(([k, n]) => `<button data-tab="${k}" class="${state.gtab === k ? "on" : ""}" onclick="state.gtab='${k}';loadGroup()">${n}</button>`).join("");
  $("#gbody").innerHTML = skeleton(4);
  if (state.gtab === "overview") return groupOverview(g);
  if (state.gtab === "members") return groupMembers(g);
  if (state.gtab === "messages") return groupMessages(g);
  if (state.gtab === "disposal") return groupDisposal(g);
}

async function groupOverview(g) {
  const [j, setting, me] = await Promise.all([
    api(`/panel/api/groups/${g}/stats`),
    api(`/panel/api/groups/${g}/setting`),
    api("/panel/api/me"),
  ]);
  const locked = me.ok ? !!me.data.dry_run : true;
  const d = j.ok ? j.data : {};
  const autoOn = setting.ok ? !!setting.data.auto_rules : true;
  const info = d.info || {}, st = d.state || {};
  $("#gbody").innerHTML = `
    <div class="panel-b">
      <div class="row" style="justify-content:space-between;align-items:flex-start">
        <div>
          <div style="font-size:16px;font-weight:600;margin-bottom:4px">${esc(info.group_name || groupName(g))}</div>
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
      <div class="mt12 row" style="border-top:1px solid var(--line);padding-top:12px">
        <span style="font-size:12.5px;color:var(--sub)">自动处置规则</span>
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:12.5px">
          <input type="checkbox" id="autoRules" style="width:auto" onchange="toggleGroupRules(this.checked)">
          对本群生效</label>
        <span class="hint" style="color:var(--muted);font-size:12px" id="autoRulesHint"></span>
      </div>
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
  return `<table><thead><tr><th style="width:220px">成员</th><th>member_openid</th><th style="width:60px">发言</th>
    <th style="width:90px">最近</th><th style="width:150px">操作</th></tr></thead><tbody>
    ${rows.map(m => `<tr data-k="${esc((m.member_openid + " " + (m.nickname || "")).toLowerCase())}">
      <td>${who(m.nickname, m.member_openid)}</td>
      <td class="mono" style="font-size:11.5px;color:var(--muted)">${esc(m.member_openid)}</td>
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
  return `<table><thead><tr><th style="width:90px">时间</th><th style="width:160px">成员</th><th>内容</th>
    <th style="width:110px">可撤回</th><th style="width:80px">操作</th></tr></thead><tbody>
    ${rows.map(m => {
      const left = Math.floor(120 - (now - m.created_at));
      const able = !m.recalled && left > 0;
      return `<tr><td style="color:var(--muted)">${ts(m.created_at, false)}</td>
        <td>${who(m.nickname, m.member_openid)}</td>
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
  const j = await api("/panel/api/disposals?limit=100");
  const rows = (j.ok ? j.data : []).filter(r => r.group_openid === g);
  $("#gbody").innerHTML = rows.length ? disposalTable(rows) : empty("该群暂无自动处置记录", "命中规则后在这里留痕");
}

function disposalTable(rows) {
  return `<table><thead><tr><th style="width:96px">时间</th><th style="width:70px">规则</th>
    <th>成员</th><th style="width:100px">动作</th><th style="width:70px">结果</th><th>详情</th></tr></thead><tbody>
    ${rows.map(d => `<tr><td style="color:var(--muted)">${ts(d.created_at)}</td>
      <td class="num">#${d.rule_id ?? "-"}</td>
      <td>${who(d.nickname, d.member_openid)}</td>
      <td>${esc(d.action)}</td>
      <td>${d.ok ? '<span class="badge ok">成功</span>' : '<span class="badge warn">未执行/失败</span>'}</td>
      <td style="color:var(--muted);font-size:12px">${esc(d.result || "")}</td></tr>`).join("")}
  </tbody></table>`;
}

async function toggleGroupRules(enabled) {
  const j = await api("/panel/api/groups/auto-rules", { method: "POST",
    body: JSON.stringify({ group: state.group, enabled }) });
  if (j.ok) toast(`本群自动处置已${enabled ? "开启" : "关闭"}`, "ok");
  else { toast(j.error || "操作失败", "bad"); $("#autoRules").checked = !enabled; }
}

/* ---------------- 对话 ---------------- */

async function viewChat() {
  $("#main").innerHTML = `<div class="topbar"><h1>对话</h1></div>${skeleton(4)}`;
  const j = await api("/panel/api/llm");
  if (!j.ok) { $("#main").innerHTML = empty("读取失败", j.error); return; }
  const d = j.data;
  const usage = d.usage || { calls: 0, tokens: 0 };
  $("#main").innerHTML = `
    <div class="topbar"><h1>对话</h1>
      <span class="badge ${d.enabled ? "ok" : "warn"}">${d.enabled ? "已启用" : "未启用"}</span>
      <span class="badge info">${esc(d.model || "-")}</span>
      <span class="sp"></span>
      <span class="meta">今日 ${usage.calls} 次 · ${usage.tokens} tokens</span>
    </div>

    <div class="panel"><div class="panel-h">模型接入<span class="sp"></span>
      <span class="hint">OpenAI 兼容接口，填 base_url + api_key + model 即可</span></div>
      <div class="panel-b">
        <div class="row" style="align-items:flex-end;margin-bottom:12px">
          <label class="field grow" style="margin:0"><span>base_url</span>
            <input id="llmBase" value="${esc(d.base_url)}" placeholder="https://api.deepseek.com/v1"></label>
          <label class="field grow" style="margin:0"><span>api_key（留空表示不修改）</span>
            <input id="llmKey" type="password" placeholder="${esc(d.api_key || "sk-...")}"></label>
          <label class="field" style="margin:0;width:200px"><span>model</span>
            <input id="llmModel" value="${esc(d.model)}" placeholder="deepseek-chat"></label>
        </div>
        <div class="row" style="align-items:flex-end;margin-bottom:12px">
          <label class="field" style="margin:0;width:150px"><span>触发方式</span>
            <select id="llmTrigger">
              <option value="at" ${d.trigger === "at" ? "selected" : ""}>仅 @ 机器人</option>
              <option value="all" ${d.trigger === "all" ? "selected" : ""}>所有消息（慎用）</option>
            </select></label>
          <label class="field grow" style="margin:0"><span>额外触发词（逗号分隔，可空）</span>
            <input id="llmKeys" value="${esc((d.keywords || []).join(","))}" placeholder="机器人,小助手"></label>
          <label class="field" style="margin:0;width:110px"><span>冷却（秒）</span>
            <input id="llmCool" value="${d.cooldown}"></label>
          <label class="field" style="margin:0;width:120px"><span>每日上限</span>
            <input id="llmDaily" value="${d.daily_limit}"></label>
        </div>
        <label class="field"><span>系统提示词</span>
          <textarea id="llmSystem">${esc(d.system)}</textarea></label>
        <div class="row" style="align-items:flex-end;margin-bottom:12px">
          <label class="field" style="margin:0;width:120px"><span>max_tokens</span>
            <input id="llmMax" value="${d.max_tokens}"></label>
          <label class="field" style="margin:0;width:120px"><span>temperature</span>
            <input id="llmTemp" value="${d.temperature}"></label>
          <label class="field" style="margin:0;width:150px"><span>上下文轮数</span>
            <input id="llmCtx" value="${d.context_turns}"></label>
          <label style="display:flex;gap:8px;align-items:center;font-size:13px;margin-bottom:4px">
            <input type="checkbox" id="llmOn" style="width:auto" ${d.enabled ? "checked" : ""}> 启用对话</label>
          <span class="sp" style="flex:1"></span>
          <button class="btn pri" onclick="saveLLM()">保存配置</button>
        </div>
        <div class="hint" style="color:var(--muted);font-size:12px">
          回复走被动消息（带原 msg_id），平台限制：5 分钟内、每条消息最多回 5 次。超出窗口失败属正常。</div>
      </div>
    </div>

    <div class="panel" id="genCard"><div class="panel-h">文生图<span class="sp"></span>
      <span class="hint">与对话模型是两套接口，可共存</span></div>
      <div class="panel-b">
        <div class="row" style="align-items:flex-end;margin-bottom:12px">
          <label class="field grow" style="margin:0"><span>base_url（留空继承对话）</span>
            <input id="gBase" placeholder="${esc(d.base_url || "https://api.openai.com/v1")}"></label>
          <label class="field grow" style="margin:0"><span>api_key（留空继承对话）</span>
            <input id="gKey" type="password" placeholder="sk-..."></label>
          <label class="field" style="margin:0;width:190px"><span>model</span>
            <input id="gModel" placeholder="dall-e-3 / flux.1"></label>
        </div>
        <div class="row" style="align-items:flex-end;margin-bottom:12px">
          <label class="field" style="margin:0;width:150px"><span>默认尺寸</span>
            <input id="gSize" value="1024x1024"></label>
          <label class="field grow" style="margin:0"><span>提示词前缀（可空）</span>
            <input id="gPrefix" placeholder="动漫风格，高清"></label>
          <label style="display:flex;gap:8px;align-items:center;font-size:13px;margin-bottom:6px">
            <input type="checkbox" id="gOn" style="width:auto"> 启用文生图</label>
          <button class="btn pri" id="gSaveBtn" onclick="saveImagegen()">保存</button>
        </div>
        <div class="row">
          <input id="gProbe" class="grow" value="一只橘猫坐在窗台上，阳光">
          <button class="btn" onclick="testImagegen()">试画一张</button>
        </div>
        <div class="hint mt8" style="font-size:12px;color:var(--muted)">
          群友说「竖版」「横图」「768x1344」时会临时覆盖默认尺寸；没提就按上面这个值。</div>
        <div class="hint mt8" id="gOut" style="font-size:12.5px;color:var(--muted)"></div>
      </div>
    </div>

    <div class="panel"><div class="panel-h">回复行为<span class="sp"></span>
      <span class="hint">影响规则回复与模型回复</span></div><div class="panel-b">
      <div class="row" style="justify-content:space-between">
        <div><b>回复时 @ 提问者</b>
          <div class="hint" style="color:var(--muted);font-size:12px">
            群聊里 @ 某人只支持 markdown 消息；开启后回复会带上 @，失败自动退回纯文本。</div></div>
        <button class="btn ${d.reply_at_user ? "pri" : ""}" onclick="toggleReplyAt(${d.reply_at_user})">
          ${d.reply_at_user ? "已开启" : "已关闭"}</button>
      </div>
    </div></div>

    <div class="panel"><div class="panel-h">连通性测试</div><div class="panel-b">
      <div class="row">
        <input id="llmProbe" class="grow" value="用一句话介绍你自己">
        <button class="btn" onclick="testLLM()">发送测试</button>
      </div>
      <div class="hint mt8" id="llmOut" style="color:var(--muted);font-size:12.5px"></div>
    </div></div>

    <div class="panel"><div class="panel-h">最近对话<span class="sp"></span>
      <span class="hint">本地留档</span></div>
      <div class="flush" id="chatBox">${skeleton(3)}</div></div>`;
  loadChats();
  loadImagegen();
}

async function loadImagegen() {
  const j = await api("/panel/api/imagegen");
  if (!j.ok) return;
  const g = j.data;
  $("#gOn").checked = !!g.enabled;
  $("#gBase").value = g.base_url || "";
  $("#gKey").value = "";
  $("#gKey").placeholder = g.api_key || "留空继承对话配置";
  $("#gModel").value = g.model || "";
  $("#gSize").value = g.size || "1024x1024";
  $("#gPrefix").value = g.prefix || "";
  $("#gOut").textContent = g.effective_base
    ? `当前生效地址：${g.effective_base}` : "尚未配置 base_url";
}

async function saveImagegen() {
  const btn = $("#gSaveBtn");
  if (btn) { btn.disabled = true; btn.textContent = "保存中…"; }
  try {
    const j = await api("/panel/api/imagegen", { method: "POST", body: JSON.stringify({
      enabled: $("#gOn").checked, base_url: $("#gBase").value.trim(),
      api_key: $("#gKey").value.trim(), model: $("#gModel").value.trim(),
      size: $("#gSize").value.trim(), prefix: $("#gPrefix").value.trim() }) });
    if (j.ok) { toast("文生图配置已保存", "ok"); loadImagegen(); }
    else toast(j.error || "保存失败", "bad");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "保存"; }
  }
}

async function testImagegen() {
  const out = $("#gOut");
  out.textContent = "生成中，最多等 60 秒…";
  const j = await api("/panel/api/imagegen/test", { method: "POST",
    body: JSON.stringify({ prompt: $("#gProbe").value, size: $("#gSize").value.trim() }) });
  if (j.ok) {
    const d = j.data;
    out.innerHTML = `<span class="badge ok">成功</span> ${esc(d.model || "")}
      · ${d.kind === "url" ? "返回图片地址" : "返回二进制"}${d.bytes ? " " + Math.round(d.bytes/1024) + " KB" : ""}
      ${d.url ? `<br><span class="mono" style="font-size:11.5px;word-break:break-all">${esc(d.url)}</span>` : ""}`;
  } else {
    out.innerHTML = `<span class="badge bad">失败</span> ${esc(j.error)}`;
  }
}

async function saveLLM() {
  const body = {
    enabled: $("#llmOn").checked,
    base_url: $("#llmBase").value.trim(),
    api_key: $("#llmKey").value.trim(),
    model: $("#llmModel").value.trim(),
    trigger: $("#llmTrigger").value,
    keywords: $("#llmKeys").value,
    system: $("#llmSystem").value,
    max_tokens: parseInt($("#llmMax").value || "300", 10),
    temperature: parseFloat($("#llmTemp").value || "0.7"),
    context_turns: parseInt($("#llmCtx").value || "6", 10),
    cooldown: parseInt($("#llmCool").value || "3", 10),
    daily_limit: parseInt($("#llmDaily").value || "200", 10),
  };
  const j = await api("/panel/api/llm", { method: "POST", body: JSON.stringify(body) });
  if (j.ok) { toast("对话配置已保存", "ok"); viewChat(); }
  else toast(j.error || "保存失败", "bad");
}

async function testLLM() {
  const out = $("#llmOut");
  out.innerHTML = "请求中…";
  const j = await api("/panel/api/llm/test", { method: "POST",
    body: JSON.stringify({ prompt: $("#llmProbe").value }) });
  if (j.ok) {
    out.innerHTML = `<div class="panel" style="margin:8px 0 0"><div class="panel-b">
      <div style="white-space:pre-wrap">${esc(j.data.answer)}</div>
      <div class="hint mt8" style="color:var(--muted);font-size:12px">
        ${esc(j.data.model)} · ${j.data.tokens} tokens</div></div></div>`;
  } else {
    out.innerHTML = `<span class="badge bad">失败</span> ${esc(j.error)}`;
  }
}

async function loadChats() {
  const j = await api("/panel/api/chat?limit=40");
  const rows = j.ok ? j.data : [];
  if (!rows.length) { $("#chatBox").innerHTML = empty("还没有对话记录", "在群里 @机器人 说句话试试"); return; }
  const KIND = { chat: "对话", imagegen: "生图", search: "找图", rule: "规则" };
  $("#chatBox").innerHTML = `<table><thead><tr><th style="width:92px">时间</th>
    <th style="width:58px">角色</th><th>内容</th><th style="width:170px">成员</th>
    <th style="width:190px">模型 / 来源</th><th style="width:110px">群</th></tr></thead><tbody>
    ${rows.map(r => `<tr>
      <td style="color:var(--muted)">${ts(r.created_at)}</td>
      <td>${r.role === "user" ? '<span class="badge">群友</span>' : '<span class="badge ok">机器人</span>'}</td>
      <td style="white-space:pre-wrap">${esc(r.content)}</td>
      <td>${r.role === "user" ? who(r.member_nickname, r.member_openid) : "—"}</td>
      <td style="font-size:12px">
        ${r.model
          ? `<span class="badge ${r.kind === "chat" ? "info" : ""}" style="font-size:11px">${esc(KIND[r.kind] || "对话")}</span>
             <span class="mono" style="color:var(--muted)">${esc(r.model)}</span>`
          : '<span style="color:var(--muted)">—</span>'}
      </td>
      <td style="color:var(--muted);font-size:12px">${esc(r.group_title || (r.group_openid || "").slice(0, 8) + "…")}</td>
    </tr>`).join("")}</tbody></table>`;
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
  const [j, gj] = await Promise.all([api("/panel/api/settings"), api("/panel/api/groups")]);
  if (!j.ok) { $("#main").innerHTML = empty("读取失败", j.error); return; }
  const d = j.data;
  const groups = gj.ok ? gj.data : [];
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
        <div class="row" style="justify-content:space-between;margin-bottom:8px">
          <b>群白名单</b>
          <span class="hint" style="color:var(--muted);font-size:12px">
            不勾选 = 不限制；勾选后只允许在这些群执行写操作</span>
        </div>
        <div class="row" id="wgList" style="gap:14px;margin-bottom:10px"></div>
        <button class="btn pri" id="wgBtn" onclick="saveWhitelist()">保存白名单</button>
      </div>
    </div></div>

    <div class="panel"><div class="panel-h">开源项目</div><div class="panel-b">
      <div class="row" style="justify-content:space-between;align-items:center">
        <div>
          <b style="font-size:13px">${esc(d.repo || "-")}</b>
          <div class="hint" style="color:var(--muted);font-size:12px">
            ${d.is_author
              ? "弹窗提示：关闭 · 尊敬的作者您好，祝您身体健康，全家幸福安康"
              : (d.starred === true
                  ? "弹窗提示：关闭 · 感谢您的支持"
                  : `弹窗提示：${d.soft_prompt ? "开启" : "关闭"} · 间隔：${d.soft_interval_hours ? d.soft_interval_hours + " 小时" : "仅一次"}`)}</div>
        </div>
        <a class="btn" style="text-decoration:none" target="_blank" rel="noopener"
           href="https://github.com/${esc(d.repo || "cd1005-art/QQBOT")}">打开仓库</a>
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
  renderWhitelist(groups, d.allowed_groups || []);
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

async function toggleReplyAt(current) {
  const enable = !current;
  const j = await api("/panel/api/settings/reply-at", { method: "POST", body: JSON.stringify({ enable }) });
  if (j.ok) { toast(`回复${enable ? "已带上 @" : "已改为纯文本"}`, "ok"); viewChat(); }
  else toast(j.error || "操作失败", "bad");
}

function renderWhitelist(groups, picked) {
  const el = $("#wgList");
  if (!el) return;
  if (!groups.length) { el.innerHTML = '<span class="hint" style="color:var(--muted);font-size:12px">库里还没有群，先让群里有人发言</span>'; return; }
  el.innerHTML = groups.map(g => `
    <label style="display:flex;gap:6px;align-items:center;font-size:12.5px" title="${esc(g.group_openid)}">
      <input type="checkbox" class="wg" value="${esc(g.group_openid)}" style="width:auto"
             ${picked.includes(g.group_openid) ? "checked" : ""}>
      ${esc(g.title || g.group_openid.slice(0, 10) + "…")}</label>`).join("");
}

async function saveWhitelist() {
  const btn = $("#wgBtn");
  const groups = $$(".wg:checked").map(x => x.value);
  if (btn) { btn.disabled = true; btn.textContent = "保存中…"; }
  try {
    const j = await api("/panel/api/settings/allowed-groups", { method: "POST",
      body: JSON.stringify({ groups }) });
    if (j.ok) toast(groups.length ? `白名单已保存（${groups.length} 个群）` : "已清空，当前不限制群", "ok");
    else toast(j.error || "保存失败", "bad");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "保存白名单"; }
  }
}

async function changePw() {
  const old = $("#pwOld").value, nw = $("#pwNew").value;
  if (!old || !nw) return toast("请填写完整", "bad");
  const j = await api("/panel/api/settings/password", { method: "POST", body: JSON.stringify({ old, new: nw }) });
  if (j.ok) { toast("密码已更新", "ok"); $("#pwOld").value = $("#pwNew").value = ""; }
  else toast(j.error || "修改失败", "bad");
}

/* ---------------- 规则 ---------------- */

async function viewRules() {
  $("#main").innerHTML = `<div class="topbar"><h1>规则</h1><span class="sp"></span>
    <span class="meta">命中即处置，处置结果写入「处置记录」</span>
    <button class="btn pri" onclick="toggleRuleForm()">新建规则</button></div>
    <div class="panel" id="visionChatCard"><div class="panel-h">图片识别<span class="sp"></span>
      <span class="hint">让模型看懂图，参与群聊回答</span></div>
      <div class="panel-b" style="border-bottom:1px solid var(--line)">
        <div class="row" style="justify-content:space-between">
          <div>
            <b style="font-size:13px">网络找图</b>
            <div class="hint" style="color:var(--muted);font-size:12px">
              群友说「来张二次元图片」「找张搞笑图」时，从图源找一张发到群里。支持：二次元、动漫、搞笑、沙雕、恐怖、风景、宠物、猫、狗、游戏、汽车、美食、美女。</div>
          </div>
          <label style="display:flex;gap:8px;align-items:center;font-size:13px">
            <input type="checkbox" id="vSearchOn" style="width:auto" onchange="saveVisionLite()"> 启用找图</label>
        </div>
        <div class="mt12" style="border-top:1px solid var(--line);padding-top:10px">
          <div class="hint" style="color:var(--muted);font-size:12px;margin-bottom:6px">
            允许的类别（<b style="color:#f7dca0">必须勾选才生效</b>；一个都不勾 = 全部禁止。美女/恐怖/沙雕易触发平台审核）</div>
          <div class="row" id="vCats" style="gap:12px"></div>
        </div>
      </div>
      <div class="panel-b">
        <div class="row" style="justify-content:space-between">
          <div>
            <b style="font-size:13px">对话时理解图片</b>
            <div class="hint" style="color:var(--muted);font-size:12px">
              开启后，@机器人 发图（或图配文字）会把图片一起交给模型；关闭则只按文字回答。</div>
          </div>
          <label style="display:flex;gap:8px;align-items:center;font-size:13px">
            <input type="checkbox" id="vChatOn" style="width:auto" onchange="saveVisionLite()"> 启用识别</label>
        </div>
      </div>
    </div>

    <div class="panel" id="visionCard"><div class="panel-h">图片审核<span class="sp"></span>
      <span class="hint">视觉模型判断违规，命中即处置</span></div>
      <div class="panel-b">
        <div class="row" style="align-items:flex-end;margin-bottom:10px">
          <label style="display:flex;gap:8px;align-items:center;font-size:13px;margin-bottom:6px">
            <input type="checkbox" id="vOn" style="width:auto"> 启用</label>
          <label class="field" style="margin:0;width:190px"><span>模型</span>
            <input id="vModel" placeholder="留空跟随对话模型"></label>
          <label class="field" style="margin:0;width:160px"><span>违规动作</span>
            <select id="vAction">
              <option value="recall_mute">撤回 + 禁言</option>
              <option value="recall">仅撤回</option>
              <option value="mute">仅禁言</option>
              <option value="none">只记录</option>
            </select></label>
          <label class="field" style="margin:0;width:110px"><span>禁言分钟</span>
            <input id="vMinutes" value="10"></label>
          <label class="field grow" style="margin:0"><span>违规提示（留空不发言）</span>
            <input id="vNotice" placeholder="图片违规，已处理"></label>
        </div>
        <label class="field" style="margin-bottom:8px"><span>判定提示词（留空用内置）</span>
          <textarea id="vPrompt" placeholder="留空使用内置提示词：只输出 JSON"></textarea></label>
        <div class="row">
          <button class="btn pri" id="vSaveBtn" onclick="saveVision()">保存审核配置</button>
          <span class="hint" id="vStat" style="color:var(--muted);font-size:12px"></span>
        </div>
      </div>
      <div class="flush" id="vResults"></div>
    </div>
    <div class="panel hide" id="ruleForm"><div class="panel-h">新建规则</div><div class="panel-b">
      <div class="row" style="align-items:flex-end">
        <label class="field grow" style="margin:0"><span>名称</span><input id="rName" placeholder="例如：广告词拦截"></label>
        <label class="field" style="margin:0;width:130px"><span>匹配方式</span>
          <select id="rType"><option value="contains">包含</option><option value="regex">正则</option></select></label>
        <label class="field grow" style="margin:0"><span>匹配内容</span><input id="rPattern" placeholder="广告 / 加群 / https?://"></label>
        <label class="field" style="margin:0;width:170px"><span>动作</span>
          <select id="rAction" onchange="syncMinutes()">
            <option value="recall">撤回</option>
            <option value="mute">禁言</option>
            <option value="recall_mute">撤回 + 禁言</option>
            <option value="reply">关键词回复</option>
            <option value="recall_reply">撤回 + 回复</option>
          </select></label>
        <label class="field" style="margin:0;width:110px"><span>禁言分钟</span><input id="rMinutes" value="10" disabled></label>
      </div>
      <label class="field mt12 hide" id="rReplyWrap"><span>回复内容（被动回复，随原消息发出）</span>
        <textarea id="rReply" placeholder="例如：本群禁止发广告，请自重。"></textarea></label>
      <div class="mt12" style="font-size:12px;color:var(--sub);margin-bottom:6px">作用群（不勾选 = 全部群）</div>
      <div id="rGroups" class="row" style="gap:14px"></div>
      <div class="row mt12">
        <input id="rTestText" class="grow" placeholder="测试文本：粘贴一段话验证是否命中">
        <button class="btn" onclick="testRule()">测试命中</button>
        <button class="btn pri" onclick="createRule()">保存规则</button>
      </div>
      <div class="hint mt8" id="rTestOut" style="font-size:12px;color:var(--muted)"></div>
    </div></div>
    <div class="panel"><div class="flush" id="ruleBox">${skeleton(4)}</div></div>`;
  const g = await api("/panel/api/groups");
  state.groups = g.ok ? g.data : [];
  $("#rGroups").innerHTML = state.groups.length
    ? state.groups.map(x => `<label style="display:flex;gap:6px;align-items:center;font-size:12.5px">
        <input type="checkbox" class="rg" value="${esc(x.group_openid)}" style="width:auto" title="${esc(x.group_openid)}">
        <span>${esc(groupLabel(x))}</span></label>`).join("")
    : '<span class="hint" style="color:var(--muted);font-size:12px">暂无群</span>';
  loadRules();
  loadVision();
}

async function loadVision() {
  const j = await api("/panel/api/vision");
  if (!j.ok) return;
  const d = j.data;
  $("#vOn").checked = !!d.enabled;
  $("#vChatOn").checked = !!d.chat_enabled;
  $("#vSearchOn").checked = !!d.image_search;
  const RISKY = ["美女", "恐怖", "沙雕"];
  const picked = (d.categories || []);
  $("#vCats").innerHTML = (d.all_categories || []).map(k => `
    <label style="display:flex;gap:5px;align-items:center;font-size:12.5px;color:${RISKY.includes(k) ? "#f7dca0" : "inherit"}">
      <input type="checkbox" class="vcat" value="${esc(k)}" style="width:auto" ${(d.categories || []).includes(k) ? "checked" : ""}>
      ${esc(k)}${RISKY.includes(k) ? " ⚠" : ""}</label>`).join("")
    + (picked.length ? "" : `<span style="font-size:12px;color:#f7dca0">当前没有勾选任何类别，找图不会生效</span>`);
  $("#vModel").value = d.model_custom ? d.model : "";
  $("#vModel").placeholder = d.model_custom ? "" : `留空跟随：${d.model || "-"}`;
  $("#vAction").value = d.action || "recall_mute";
  $("#vMinutes").value = d.minutes || 10;
  $("#vNotice").value = d.notice || "";
  $("#vPrompt").value = d.prompt || "";
  $("#vStat").textContent = `近 200 次操作中：识别 ${(d.usage || {}).checked || 0} 次，拦截 ${(d.usage || {}).blocked || 0} 次`;
  loadVisionResults();
}

async function saveVisionLite() {
  const j = await api("/panel/api/vision", { method: "POST",
    body: JSON.stringify({ chat_enabled: $("#vChatOn").checked,
                           image_search: $("#vSearchOn").checked }) });
  if (j.ok) toast("图片能力已更新", "ok");
  else { toast(j.error || "保存失败", "bad"); $("#vChatOn").checked = !$("#vChatOn").checked; }
}

async function saveVision() {
  const btn = $("#vSaveBtn");
  const label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "保存中…"; }
  const cats = $$(".vcat:checked").map(x => x.value);
  const body = { enabled: $("#vOn").checked, chat_enabled: $("#vChatOn").checked,
    categories: cats,
    model: $("#vModel").value.trim(),
    action: $("#vAction").value, minutes: parseInt($("#vMinutes").value || "10", 10),
    notice: $("#vNotice").value.trim(), prompt: $("#vPrompt").value.trim() };
  try {
    const j = await api("/panel/api/vision", { method: "POST", body: JSON.stringify(body) });
    if (j.ok) { toast("图片审核配置已保存", "ok"); loadVision(); }
    else toast(j.error || "保存失败", "bad");
  } catch (e) {
    toast("保存失败：" + e.message, "bad");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

async function loadVisionResults() {
  const j = await api("/panel/api/vision/results?limit=8");
  const rows = (j.ok ? j.data : []).filter(r => (r.action || "").startsWith("vision") || r.action === "vision-ok");
  $("#vResults").innerHTML = rows.length
    ? disposalTable(rows)
    : empty("暂无识别记录", "群里有图片消息时会自动判断");
}

function toggleRuleForm() { $("#ruleForm").classList.toggle("hide"); }
function syncMinutes() {
  const a = $("#rAction").value;
  const needMute = a === "mute" || a === "recall_mute";
  const needReply = a === "reply" || a === "recall_reply";
  $("#rMinutes").disabled = !needMute;
  $("#rReplyWrap").classList.toggle("hide", !needReply);
}
function ruleGroups() { return $$(".rg:checked").map(x => x.value); }

async function testRule() {
  const j = await api("/panel/api/rules/test", { method: "POST", body: JSON.stringify({
    match_type: $("#rType").value, pattern: $("#rPattern").value, text: $("#rTestText").value }) });
  $("#rTestOut").innerHTML = j.ok
    ? (j.data.hit ? '<span class="badge ok">命中</span>' : '<span class="badge">未命中</span>')
    : `<span class="badge bad">${esc(j.error)}</span>`;
}

async function createRule() {
  const body = { name: $("#rName").value.trim(), match_type: $("#rType").value,
    pattern: $("#rPattern").value.trim(), action: $("#rAction").value,
    minutes: parseInt($("#rMinutes").value || "10", 10),
    reply: $("#rReply").value.trim(), groups: ruleGroups() };
  const j = await api("/panel/api/rules", { method: "POST", body: JSON.stringify(body) });
  if (j.ok) { toast("规则已创建", "ok"); $("#ruleForm").classList.add("hide"); loadRules(); }
  else toast(j.error || "创建失败", "bad");
}

const ACTION_TEXT = { recall: "撤回", mute: "禁言", recall_mute: "撤回+禁言",
                      reply: "回复", recall_reply: "撤回+回复" };

async function loadRules() {
  const j = await api("/panel/api/rules");
  const rows = j.ok ? j.data : [];
  if (!rows.length) { $("#ruleBox").innerHTML = empty("还没有规则", "点右上角「新建规则」开始"); return; }
  $("#ruleBox").innerHTML = `<table><thead><tr><th>名称</th><th style="width:80px">方式</th>
    <th>匹配</th><th style="width:100px">动作</th><th style="width:110px">作用群</th>
    <th style="width:64px">命中</th><th style="width:90px">最近</th><th style="width:130px">操作</th></tr></thead><tbody>
    ${rows.map(r => {
      const groups = safeJson(r.groups, []);
      const params = safeJson(r.params, {});
      return `<tr>
        <td>${esc(r.name)}</td>
        <td>${r.match_type === "regex" ? "正则" : "包含"}</td>
        <td class="mono">${esc(r.pattern)}</td>
        <td>${ACTION_TEXT[r.action] || esc(r.action)}${["mute", "recall_mute"].includes(r.action) ? `<span class="muted" style="font-size:11px"> ${params.minutes || 10}m</span>` : ""}${["reply", "recall_reply"].includes(r.action) ? `<div class="muted" style="font-size:11px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">“${esc(params.reply || "")}”</div>` : ""}</td>
        <td style="color:var(--muted);font-size:12px">${groups.length ? groups.length + " 个群" : "全部"}</td>
        <td class="num">${r.hits}</td>
        <td style="color:var(--muted)">${r.last_hit_at ? ago(r.last_hit_at) : "-"}</td>
        <td><button class="btn sm" onclick="toggleRule(${r.id},${r.enabled ? 0 : 1})">${r.enabled ? "停用" : "启用"}</button>
            <button class="btn sm bad" onclick="delRule(${r.id},'${esc(r.name)}')">删除</button></td>
      </tr>`;
    }).join("")}
  </tbody></table>`;
}
function safeJson(s, d) { try { return JSON.parse(s); } catch (e) { return d; } }

async function toggleRule(id, enabled) {
  const j = await api("/panel/api/rules/toggle", { method: "POST", body: JSON.stringify({ id, enabled: !!enabled }) });
  if (j.ok) { toast("已更新", "ok"); loadRules(); } else toast(j.error || "失败", "bad");
}
async function delRule(id, name) {
  if (!await confirmBox("删除规则", `确认删除「${name}」？历史处置记录保留。`, true)) return;
  const j = await api("/panel/api/rules/delete", { method: "POST", body: JSON.stringify({ id }) });
  if (j.ok) { toast("已删除", "ok"); loadRules(); } else toast(j.error || "失败", "bad");
}

/* ---------------- 定时 ---------------- */

async function viewSchedules() {
  $("#main").innerHTML = `<div class="topbar"><h1>定时任务</h1><span class="sp"></span>
    <span class="meta">cron 五段：分 时 日 月 周</span>
    <button class="btn pri" onclick="toggleSchedForm()">新建任务</button></div>
    <div class="panel hide" id="schedForm"><div class="panel-h">新建任务</div><div class="panel-b">
      <div class="row" style="align-items:flex-end">
        <label class="field grow" style="margin:0"><span>名称</span><input id="sName" placeholder="每日公告"></label>
        <label class="field" style="margin:0;width:180px"><span>cron</span><input id="sCron" value="0 9 * * *"></label>
        <label class="field grow" style="margin:0"><span>发送内容</span><input id="sText" placeholder="今天维护，请提前下线"></label>
      </div>
      <div class="hint mt8" style="font-size:12px;color:var(--muted)">
        示例：<code>0 9 * * *</code> 每天 09:00　<code>*/30 * * * *</code> 每 30 分钟　<code>0 9 * * 1-5</code> 工作日 09:00</div>
      <div class="mt12" style="font-size:12px;color:var(--sub);margin-bottom:6px">目标群（不勾选 = 库内全部群）</div>
      <div id="sGroups" class="row" style="gap:14px"></div>
      <div class="row mt12"><button class="btn pri" onclick="createSchedule()">保存任务</button></div>
    </div></div>
    <div class="panel"><div class="flush" id="schedBox">${skeleton(4)}</div></div>`;
  const g = await api("/panel/api/groups");
  state.groups = g.ok ? g.data : [];
  $("#sGroups").innerHTML = state.groups.length
    ? state.groups.map(x => `<label style="display:flex;gap:6px;align-items:center;font-size:12.5px">
        <input type="checkbox" class="sg" value="${esc(x.group_openid)}" style="width:auto" title="${esc(x.group_openid)}">
        <span>${esc(groupLabel(x))}</span></label>`).join("")
    : '<span class="hint" style="color:var(--muted);font-size:12px">暂无群</span>';
  loadSchedules();
}

function toggleSchedForm() { $("#schedForm").classList.toggle("hide"); }

async function createSchedule() {
  const body = { name: $("#sName").value.trim(), cron: $("#sCron").value.trim(),
    text: $("#sText").value.trim(), groups: $$(".sg:checked").map(x => x.value) };
  const j = await api("/panel/api/schedules", { method: "POST", body: JSON.stringify(body) });
  if (j.ok) { toast("任务已创建", "ok"); $("#schedForm").classList.add("hide"); loadSchedules(); }
  else toast(j.error || "创建失败", "bad");
}

async function loadSchedules() {
  const j = await api("/panel/api/schedules");
  const rows = j.ok ? j.data : [];
  if (!rows.length) { $("#schedBox").innerHTML = empty("还没有定时任务"); return; }
  $("#schedBox").innerHTML = `<table><thead><tr><th>名称</th><th style="width:120px">cron</th>
    <th>内容</th><th style="width:90px">目标群</th><th style="width:130px">下次执行</th>
    <th style="width:140px">上次结果</th><th style="width:120px">操作</th></tr></thead><tbody>
    ${rows.map(r => {
      const groups = safeJson(r.groups, []);
      return `<tr>
        <td>${esc(r.name)}</td>
        <td><code class="mono">${esc(r.cron)}</code></td>
        <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.text)}</td>
        <td style="color:var(--muted);font-size:12px">${groups.length ? groups.length + " 个群" : "全部"}</td>
        <td style="color:var(--muted)">${r.enabled ? (r.next_run ? ts(r.next_run) : "待排程") : '<span class="badge">已停用</span>'}</td>
        <td style="color:var(--muted);font-size:12px">${esc(r.last_result || "-")}</td>
        <td><button class="btn sm" onclick="toggleSched(${r.id},${r.enabled ? 0 : 1})">${r.enabled ? "停用" : "启用"}</button>
            <button class="btn sm bad" onclick="delSched(${r.id},'${esc(r.name)}')">删除</button></td>
      </tr>`;
    }).join("")}
  </tbody></table>`;
}

async function toggleSched(id, enabled) {
  const j = await api("/panel/api/schedules/toggle", { method: "POST", body: JSON.stringify({ id, enabled: !!enabled }) });
  if (j.ok) { toast("已更新", "ok"); loadSchedules(); } else toast(j.error || "失败", "bad");
}
async function delSched(id, name) {
  if (!await confirmBox("删除任务", `确认删除「${name}」？`, true)) return;
  const j = await api("/panel/api/schedules/delete", { method: "POST", body: JSON.stringify({ id }) });
  if (j.ok) { toast("已删除", "ok"); loadSchedules(); } else toast(j.error || "失败", "bad");
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

async function softStarPrompt() {
  const j = await api("/panel/api/prompt/star").catch(() => ({ ok: false }));
  if (!j.ok || !j.data || !j.data.show) return;
  const d = j.data;
  const repo = d.repo || "cd1005-art/QQBOT";
  const mask = document.createElement("div");
  mask.className = "mask";
  mask.innerHTML = `<div class="modal">
    <h3>这个小工具如果对你有用</h3>
    <p>去 GitHub 点个 Star 就是最好的支持。提示间隔 ${d.interval_hours ? d.interval_hours + " 小时" : "仅此一次"}，
    点「以后再说」不会影响任何功能。</p>
    <div class="row" style="justify-content:flex-end">
      <button class="btn ghost" data-x="0">以后再说</button>
      <a class="btn pri" style="text-decoration:none" target="_blank" rel="noopener"
         href="https://github.com/${esc(repo)}" data-x="1">去点 Star</a>
    </div></div>`;
  mask.addEventListener("click", async e => {
    const b = e.target.closest("[data-x]");
    if (!b) return;
    document.body.removeChild(mask);
    await api("/panel/api/prompt/star/dismiss", { method: "POST" }).catch(() => {});
  });
  document.body.appendChild(mask);
}

async function starGate() {
  const j = await api("/panel/api/star").catch(() => ({ ok: false }));
  if (!j.ok || !j.data || !j.data.required) return true;
  const d = j.data;
  if (d.starred) return true;

  const box = $("#stargate");
  box.classList.remove("hide");
  $("#login").classList.add("hide");
  $("#app").classList.add("hide");
  const repo = d.repo || "cd1005-art/QQBOT";
  $("#starRepo").textContent = `本项目完全免费，使用前请为 ${repo} 点一个 Star`;
  $("#starLink").href = `https://github.com/${repo}`;
  $("#starMsg").textContent = d.user
    ? `正在校验 GitHub 用户：${d.user}${d.error ? " · " + d.error : ""}`
    : (d.error || "未配置 GitHub 用户名");
  return false;
}

async function recheckStar() {
  $("#starMsg").textContent = "检测中…";
  const j = await api("/panel/api/star/check", { method: "POST" });
  if (j.ok && j.data && j.data.starred) {
    $("#stargate").classList.add("hide");
    toast("验证通过，感谢支持", "ok");
    boot();
  } else {
    $("#starMsg").textContent = (j.ok && j.data && j.data.error) || "还没检测到 Star，点完稍等几秒再试";
  }
}

async function boot() {
  const me = await api("/panel/api/me").catch(() => ({ ok: false }));
  if (!me.ok) return showLogin();
  state.me = me.data;
  softStarPrompt();
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
  if (!(await starGate())) return;
  const me = await api("/panel/api/me").catch(() => ({ ok: false }));
  if (me.ok) boot(); else showLogin();
});
window.addEventListener("keydown", e => { if (e.key === "Enter" && $("#login") && !$("#login").classList.contains("hide")) doLogin(); });
