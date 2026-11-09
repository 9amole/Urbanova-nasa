/* Help assistant widget (stage S9): on every page, outside <main> so page renders never remove it.
   Uses app.js globals: API helpers api(), esc(), state, TABS. All text is escaped; links are in-app (#/...) only. */
"use strict";

const help = { open: false, tab: "chat", conv: null, messages: [], profile: null, plans: null, busy: false };
const HELP_PAGES = { projects: "Projects", login: "Sign in" };
const safeHref = (h) => (/^#\/[A-Za-z0-9_/\-]{0,120}$/.test(h || "") ? h : null);

function helpContext() {
  const parts = (location.hash || "#/projects").slice(2).split("/");
  if (!state.token) return { page: "login" };
  if (parts[0] === "p" && parts[1]) {
    const page = parts[2] || "overview";
    return { page, project_id: parts[1], run_id: page === "results" ? parts[3] : undefined };
  }
  return { page: "projects" };
}

function pageLabel(page) {
  const tab = (typeof TABS !== "undefined" ? TABS : []).find(([k]) => k === page);
  return tab ? tab[1] : HELP_PAGES[page] || page;
}

function resetHelpUser() { Object.assign(help, { owner: state.token, profile: null, conv: null, messages: [], loaded: false }); }

function helpStore(open) { try { sessionStorage.setItem("urbanova_help_open", open ? "1" : "0"); } catch (e) { /* storage may be blocked */ } }
function helpStored() { try { return sessionStorage.getItem("urbanova_help_open") === "1"; } catch (e) { return false; } }

function mountHelp() {
  const root = document.createElement("div");
  root.id = "help";
  root.innerHTML = `<button id="help-btn" class="help-btn" aria-expanded="false" aria-controls="help-panel">Help</button>
  <section id="help-panel" class="help-panel" role="dialog" aria-modal="false" aria-labelledby="help-title" hidden>
    <header class="help-head"><h2 id="help-title">Help centre</h2><button id="help-close" class="small" aria-label="Close help">✕</button></header>
    <nav class="help-tabs" role="tablist">
      <button role="tab" data-t="chat">Assistant</button><button role="tab" data-t="faq">Questions &amp; answers</button><button role="tab" data-t="plans">Plans</button>
    </nav>
    <div id="help-body" class="help-body"></div>
  </section>`;
  document.body.appendChild(root);
  document.getElementById("help-btn").onclick = () => toggleHelp(!help.open);
  document.getElementById("help-close").onclick = () => toggleHelp(false);
  root.querySelectorAll(".help-tabs button").forEach((b) => (b.onclick = () => { help.tab = b.dataset.t; renderHelp(); }));
  root.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && help.open) { toggleHelp(false); return; }
    if (e.key !== "Tab" || !help.open) return;
    const f = [...root.querySelectorAll("#help-panel button, #help-panel input, #help-panel select, #help-panel textarea, #help-panel a[href]")].filter((x) => !x.disabled && x.offsetParent);
    if (!f.length) return;
    if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
  });
  window.addEventListener("hashchange", () => {
    if (help.owner !== undefined && help.owner !== state.token) {  // signed out or another account: never show the previous user's chat
      resetHelpUser();
      if (help.open) renderHelp();
    } else if (help.open) renderHelpContext();
  });
  if (helpStored()) toggleHelp(true);
}

function toggleHelp(open) {
  help.open = open;
  helpStore(open);
  document.getElementById("help-panel").hidden = !open;
  const btn = document.getElementById("help-btn");
  btn.setAttribute("aria-expanded", String(open));
  if (open) { renderHelp(); setTimeout(() => document.getElementById("help-close").focus(), 0); } else btn.focus();
}

function renderHelpContext() {
  const el = document.getElementById("help-where");
  if (el) el.textContent = `You're on: ${pageLabel(helpContext().page)}`;
}

async function renderHelp() {
  document.querySelectorAll(".help-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.t === help.tab)));
  const body = document.getElementById("help-body");
  try {
    if (help.tab === "faq") return await renderFaq(body);
    if (help.tab === "plans") return await renderPlans(body);
    return await renderChat(body);
  } catch (e) { body.innerHTML = `<p class="err" role="alert">${esc(e.message)}</p>`; }
}

// ------------------------------------------------------------------ assistant
async function renderChat(body) {
  if (!state.token) {
    body.innerHTML = `<p>Sign in to chat with the assistant. You can read the <button class="linklike" data-go="faq">questions &amp; answers</button> and the <button class="linklike" data-go="plans">plans</button> now.</p>`;
    body.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => { help.tab = b.dataset.go; renderHelp(); }));
    return;
  }
  if (help.owner !== state.token) resetHelpUser();  // another session: drop the previous user's state
  if (!help.profile) help.profile = await api("/support/profile");
  if (!help.profile.role) return renderOnboarding(body);
  if (!help.conv && !help.loaded) {
    help.loaded = true;
    const convs = await api("/support/conversations");
    if (convs.length) { const c = await api(`/support/conversations/${convs[0].id}`); help.conv = c.id; help.messages = c.messages; }
  }
  body.innerHTML = `<p id="help-where" class="hint"></p>
    <div id="help-log" class="help-log" aria-live="polite">${help.messages.length ? help.messages.map(helpMsg).join("") : helpGreeting()}</div>
    <form id="help-form" class="help-form"><label for="help-q" class="sr">Your question</label>
      <textarea id="help-q" rows="2" maxlength="1000" placeholder="Ask about this page, your projects or our plans…"></textarea>
      <div class="row"><button class="primary" type="submit">Send</button><button type="button" id="help-new" class="small">New conversation</button>
      <button type="button" id="help-me" class="small">Change my role</button></div></form>`;
  renderHelpContext();
  const log = document.getElementById("help-log");
  log.scrollTop = log.scrollHeight;
  wireLinks(log);
  document.getElementById("help-new").onclick = () => { help.conv = null; help.messages = []; help.loaded = true; renderHelp(); };
  document.getElementById("help-me").onclick = () => renderOnboarding(body);
  const q = document.getElementById("help-q");
  q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); document.getElementById("help-form").requestSubmit(); } });
  document.getElementById("help-form").onsubmit = (e) => { e.preventDefault(); sendHelp(q.value); };
}

function helpGreeting() {
  const r = help.profile.roles[help.profile.role] || "";
  return `<div class="msg assistant"><p>Hello${state.user ? " " + esc(state.user.name) : ""}! I'm the URBANOVA help assistant${r ? ` — I'll keep in mind you're ${esc(r.toLowerCase())}` : ""}.
  Ask me about the page you're on, your projects, or which plan suits you.</p></div>`;
}

function helpMsg(m) {
  const links = (m.links || []).map((l) => (safeHref(l.href) ? `<a class="chip" href="${esc(l.href)}">${esc(l.label)}</a>` : "")).join("");
  const plan = m.suggest_plan ? `<button class="chip" data-plan="${esc(m.suggest_plan)}">See the ${esc(m.suggest_plan)} plan</button>` : "";
  const label = m.role === "assistant" && m.generated_by ? `<p class="hint">${esc(m.generated_by === "ai" ? "AI help assistant" : m.generated_by === "rule" ? "Fixed answer" : "Automatic answer from the help centre")}${m.reason ? " — " + esc(m.reason) : ""}</p>` : "";
  return `<div class="msg ${m.role === "user" ? "user" : "assistant"}"><p>${esc(m.text).replace(/\n/g, "<br>")}</p>${links || plan ? `<div class="chips">${links}${plan}</div>` : ""}${label}</div>`;
}

function wireLinks(log) {
  log.querySelectorAll("[data-plan]").forEach((b) => (b.onclick = () => { help.tab = "plans"; help.highlight = b.dataset.plan; renderHelp(); }));
}

async function sendHelp(text) {
  text = (text || "").trim();
  if (!text || help.busy) return;
  const ctx = helpContext();
  help.busy = true;
  help.messages.push({ role: "user", text });
  const log = document.getElementById("help-log");
  log.innerHTML = help.messages.map(helpMsg).join("") + `<div class="msg assistant thinking" role="status"><p>Thinking…</p></div>`;
  log.scrollTop = log.scrollHeight;
  document.getElementById("help-q").value = "";
  try {
    const r = await api("/support/messages", { body: { text, conversation_id: help.conv || undefined, page: ctx.page, project_id: ctx.project_id, run_id: ctx.run_id } });
    help.conv = r.conversation_id;
    help.messages.push({ role: "assistant", text: r.answer, links: r.links, suggest_plan: r.suggest_plan, generated_by: r.generated_by, reason: r.reason });
  } catch (e) {
    help.messages.push({ role: "assistant", text: e.message, generated_by: "rule" });
  } finally {
    help.busy = false;
    if (help.open && help.tab === "chat") renderHelp();
  }
}

function renderOnboarding(body) {
  const p = help.profile;
  body.innerHTML = `<form id="help-onb" class="help-onb"><p>Before we start: tell me a little about you, so I can help better.</p>
    <label for="help-role">Your role *</label><select id="help-role" required><option value="">Choose…</option>
    ${Object.entries(p.roles).map(([k, v]) => `<option value="${esc(k)}" ${k === p.role ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
    <label for="help-goal">Why are you using URBANOVA?</label><textarea id="help-goal" rows="3" maxlength="300" placeholder="e.g. check a new compound design against the planning conditions">${esc(p.goal || "")}</textarea>
    <p><button class="primary" type="submit">Save</button></p><p id="help-onb-err" class="err" role="alert"></p></form>`;
  document.getElementById("help-onb").onsubmit = async (e) => {
    e.preventDefault();
    try {
      help.profile = await api("/support/profile", { method: "PUT", body: { role: document.getElementById("help-role").value, goal: document.getElementById("help-goal").value } });
      renderHelp();
    } catch (err) { document.getElementById("help-onb-err").textContent = err.message; }
  };
}

// ------------------------------------------------------------------ FAQ and plans
async function renderFaq(body) {
  body.innerHTML = `<label for="help-faq-q">Search the questions</label><input id="help-faq-q" type="search" maxlength="200" placeholder="e.g. DXF, hotspot, report">
    <div id="help-faq-list" class="help-faq"></div>`;
  const list = document.getElementById("help-faq-list");
  const show = async (q) => {
    const items = await api(`/support/faq${q ? "?q=" + encodeURIComponent(q) : ""}`);
    list.innerHTML = items.length ? items.map((f) => `<details><summary>${esc(f.q)}</summary><p>${esc(f.a)}</p><p class="hint">${esc(f.topic)}</p></details>`).join("")
      : `<p>No question matches. ${state.token ? "Ask the assistant instead." : ""}</p>`;
  };
  let timer = null;
  document.getElementById("help-faq-q").addEventListener("input", (e) => { clearTimeout(timer); timer = setTimeout(() => show(e.target.value.trim()), 250); });
  await show("");
}

async function renderPlans(body) {
  if (!help.plans) help.plans = await api("/support/plans");
  const P = help.plans;
  const price = (p) => (p.price === null || p.price === undefined ? "Pricing to be announced" : `${esc(fmt(p.price, 2))} ${esc(P.currency)} / ${esc(p.period)}`);
  const LIMITS = [["projects", "project", "projects"], ["runs_per_month", "analysis run a month", "analysis runs a month"], ["designs_per_run", "design per run", "designs per run"],
    ["members_per_project", "person per project", "people per project"], ["ai_messages_per_day", "AI message a day", "AI messages a day"]];
  const lim = (l) => `<ul class="plain">${LIMITS.map(([k, one, many]) => (l[k] !== undefined ? `<li>${esc(l[k])} ${l[k] === 1 ? one : many}</li>` : "")).join("")}</ul>`;
  body.innerHTML = `${P.status === "draft" ? `<p class="hint">Draft plans — final prices and limits are not announced yet.</p>` : ""}
    <div class="help-plans">${P.plans.map((p) => `<article class="plan ${p.id === help.highlight ? "hl" : ""}"><h3>${esc(p.name)}</h3><p class="price">${price(p)}</p>
      <p>${esc(p.pitch)}</p>${lim(p.limits || {})}<ul class="plain">${(p.features || []).map((f) => `<li>✓ ${esc(f)}</li>`).join("")}</ul></article>`).join("")}</div>
    ${state.token ? `<p><button class="primary" id="help-choose">Help me choose</button></p>` : `<p class="hint">Sign in to get a recommendation.</p>`}`;
  const b = document.getElementById("help-choose");
  if (b) b.onclick = () => { help.tab = "chat"; renderHelp().then(() => sendHelp("Which subscription plan suits me best, and why?")); };
}

mountHelp();
