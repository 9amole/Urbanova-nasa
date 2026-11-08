/* URBANOVA Phase 1 web client. Plain JS, no build step. All data comes from /api/v1. */
"use strict";

const API = "/api/v1";
const state = { token: sessionStorage.getItem("urbanova_token"), user: null, meta: null, maps: [] };
const $main = document.getElementById("main");

// ------------------------------------------------------------------ utilities
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (v === null || v === undefined ? "" : Math.abs(v) >= 1e5 ? Math.round(v).toLocaleString() : Number(v).toLocaleString(undefined, { maximumFractionDigits: d }));
const short = (id) => esc(String(id || "").slice(-6));
const KIND = { observed: "Observed", reanalysis: "Reanalysis", calculated: "Calculated", classified: "Classified", modelled: "Model estimate", user_provided: "User-provided" };
const MOD_STATUS = { completed: ["Completed", "b-ok"], partial: ["Completed with limitations", "b-warn"], blocked: ["Blocked", "b-bad"], unavailable: ["Data unavailable", "b-bad"], failed: ["Failed", "b-bad"], skipped: ["Not selected", "b-mute"] };
const READY = { ready: ["Ready", "b-ok"], ready_with_limitations: ["Ready with limitations", "b-warn"], blocked: ["Blocked", "b-bad"], not_in_this_build: ["Not in this build", "b-mute"] };
const RUN_STATUS = { queued: ["Queued", "b-info"], running: ["Running", "b-info"], succeeded: ["Complete", "b-ok"], partially_succeeded: ["Partial", "b-warn"], failed: ["Failed", "b-bad"] };
const badge = (pair) => `<span class="badge ${pair[1]}">${esc(pair[0])}</span>`;
// Existing land (observations of today's site) vs proposed design (decision S-8).
const modCondition = (mod) => (state.meta.module_condition || {})[mod];
const metricCondition = (m) => m.condition || (m.design_dependent ? "design" : "existing"); // rows stored before the field existed
const condBadge = (mod) => { const c = modCondition(mod); return c ? `<span class="badge cond-${c}" title="${c === "design" ? "Describes the proposed design; differs between designs" : "Describes the land as it is today; the same for every design"}">${c === "design" ? "Proposed design" : "Existing land"}</span>` : ""; };
const kindBadge = (k) => `<span class="badge k-${esc(k)}" title="Kind of value">${esc(KIND[k] || k)}</span>`;
const FCOLORS = { site_boundary: "#c62828", building: "#555", road: "#d9a400", green_space: "#2e8b57", tree: "#1b5e20", parking: "#8f8f8f", open_space: "#c8b88a", water: "#3a7bd5", shade_structure: "#8d6e63", land_use_zone: "#7e57c2", unclassified: "#e91e63" };

function notify(msg, kind = "info", ms = 6000) {
  const box = document.getElementById("notice");
  const el = document.createElement("div");
  el.className = `alert ${kind}`;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let body = opts.body;
  if (body && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
  const resp = await fetch(API + path, { method: opts.method || (body ? "POST" : "GET"), headers, body });
  if (resp.status === 401) { logoutLocal(); throw new Error("Your session has ended. Please sign in again."); }
  if (opts.raw) { if (!resp.ok) throw new Error(`Download failed (${resp.status}).`); return resp; }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) { const e = new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `Request failed (${resp.status}).`); e.data = data; throw e; }
  return data;
}

async function guarded(fn, btn) {
  if (btn) btn.disabled = true;
  try { return await fn(); } catch (e) { notify(e.message, "bad", 10000); } finally { if (btn) btn.disabled = false; }
}

async function download(path, filename) {
  const resp = await api(path, { raw: true });
  const url = URL.createObjectURL(await resp.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

const LOADING = `<p class="loading" role="status" aria-busy="true">Loading…</p>`;


function clearMaps() { state.maps.forEach((m) => m.remove()); state.maps = []; }

function makeMap(el, lat, lon, zoom = 16) {
  const m = L.map(el).setView([lat, lon], zoom);
  // Tile source comes from server config (URBANOVA_TILE_URL). The referrer policy makes the browser send
  // this site's origin, which the OSM tile usage policy requires; attribution must stay visible.
  const bm = state.meta.basemap;
  L.tileLayer(bm.url, { maxZoom: bm.max_zoom, attribution: bm.attribution, referrerPolicy: "strict-origin-when-cross-origin" }).addTo(m);
  state.maps.push(m);
  return m;
}

function featureStyle(f) {
  const t = f.properties.feature_type;
  const c = FCOLORS[t] || "#999";
  const dashed = f.properties.review_status === "needs_review" || f.properties.review_status === "proposed";
  return { color: c, weight: t === "site_boundary" ? 3 : 1.5, fillColor: c, fillOpacity: t === "site_boundary" ? 0 : 0.45, dashArray: dashed ? "5 4" : null };
}

function popupFor(props) {
  const rows = Object.entries(props).filter(([k, v]) => v !== null && v !== "" && typeof v !== "object").map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("");
  return `<table class="mono">${rows}</table>`;
}

function addFeatures(map, fc, opts = {}) {
  const layer = L.geoJSON(fc, {
    style: opts.style || featureStyle,
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 5, color: FCOLORS[f.properties.feature_type] || "#333", fillOpacity: .8 }),
    onEachFeature: (f, l) => l.bindPopup(popupFor(f.properties || {})),
  });
  layer.addTo(map);
  return layer;
}

function legendControl(map, html) {
  const c = L.control({ position: "bottomright" });
  c.onAdd = () => { const d = L.DomUtil.create("div", "legend"); d.innerHTML = html; return d; };
  c.addTo(map);
  return c;
}

const typeLegend = () => Object.entries(state.meta.feature_types).map(([k, v]) => `<div><i style="background:${FCOLORS[k]}"></i>${esc(v)}</div>`).join("") + `<div class="muted">Dashed = needs review / proposed</div>`;

function metricValue(m) {
  if (m.state !== "available") return `<span class="state">${esc(m.state.replace(/_/g, " "))}: ${esc(m.reason)}</span>`;
  return `<strong>${fmt(m.value, 3)}</strong> ${esc(m.unit || "")}`;
}

function readFileText(input) {
  return new Promise((res, rej) => { const f = input.files[0]; if (!f) return res(null); const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsText(f); });
}

// ------------------------------------------------------------------ session
function logoutLocal() { state.token = null; state.user = null; sessionStorage.removeItem("urbanova_token"); renderSession(); location.hash = "#/login"; }

function renderSession() {
  const el = document.getElementById("session");
  if (!state.user) { el.innerHTML = ""; return; }
  el.innerHTML = `<span>${esc(state.user.name)}</span><button id="logout" class="small">Sign out</button>`;
  document.getElementById("logout").onclick = async () => { await api("/auth/logout", { method: "POST" }).catch(() => {}); logoutLocal(); };
}

function viewLogin() {
  $main.innerHTML = `
  <div class="grid">
    <section class="panel" aria-labelledby="h-in"><h1 id="h-in">Sign in</h1>
      <form id="f-login"><label for="li-email">Email</label><input id="li-email" type="email" autocomplete="username" required>
      <label for="li-pw">Password</label><input id="li-pw" type="password" autocomplete="current-password" required>
      <p><button class="primary" type="submit">Sign in</button></p><p id="li-err" class="err" role="alert"></p></form></section>
    <section class="panel" aria-labelledby="h-reg"><h2 id="h-reg">Create an account</h2>
      <form id="f-reg"><label for="re-name">Name</label><input id="re-name" type="text" autocomplete="name" required>
      <label for="re-email">Email</label><input id="re-email" type="email" autocomplete="email" required>
      <label for="re-pw">Password <span class="hint">(at least 10 characters)</span></label><input id="re-pw" type="password" autocomplete="new-password" minlength="10" required>
      <p><button type="submit">Create account</button></p><p id="re-err" class="err" role="alert"></p></form></section>
  </div>
  <section class="panel"><h2>What URBANOVA Phase 1 does</h2>
  <p>Create a project from coordinates (optionally a site boundary, an urban plan and planning documents). URBANOVA checks what can be analysed,
  retrieves regional climate, OpenStreetMap context and satellite surface-temperature and vegetation observations, calculates site indicators and shade,
  checks verified planning rules, and lets you compare your original design with alternatives — showing every source, method and limitation.</p></section>`;
  document.getElementById("f-login").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await api("/auth/login", { body: { email: e.target["li-email"].value, password: e.target["li-pw"].value } });
      state.token = r.token; state.user = r.user; sessionStorage.setItem("urbanova_token", r.token); renderSession(); location.hash = "#/projects";
    } catch (err) { document.getElementById("li-err").textContent = err.message; }
  };
  document.getElementById("f-reg").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/auth/register", { body: { name: e.target["re-name"].value, email: e.target["re-email"].value, password: e.target["re-pw"].value } });
      const r = await api("/auth/login", { body: { email: e.target["re-email"].value, password: e.target["re-pw"].value } });
      state.token = r.token; state.user = r.user; sessionStorage.setItem("urbanova_token", r.token); renderSession(); location.hash = "#/projects";
    } catch (err) { document.getElementById("re-err").textContent = err.message; }
  };
}

// ------------------------------------------------------------------ projects list / create
async function viewProjects() {
  const list = await api("/projects");
  const objectives = Object.entries(state.meta.objectives).map(([k, q]) => `<label class="check"><input type="checkbox" name="obj" value="${k}" ${k === "main_concerns" ? "checked" : ""}> ${esc(q)}</label>`).join("");
  $main.innerHTML = `
  <h1>Projects</h1>
  <section class="panel"><table><thead><tr><th>Name</th><th>Location</th><th>Boundary</th><th>Location confirmed</th><th>Your role</th><th>Updated</th></tr></thead><tbody>
  ${list.map((p) => `<tr><td><a href="#/p/${p.id}/overview">${esc(p.name)}</a></td><td class="mono">${fmt(p.latitude, 5)}, ${fmt(p.longitude, 5)}</td><td>${p.boundary ? "Yes" : "No"}</td>
    <td>${p.location_confirmed ? badge(["Confirmed", "b-ok"]) : badge(["Not confirmed", "b-warn"])}</td><td>${esc(p.role)}</td><td>${esc(p.updated_at)}</td></tr>`).join("") || `<tr><td colspan="6">No projects yet — create one below.</td></tr>`}
  </tbody></table></section>
  <section class="panel" aria-labelledby="h-new"><h2 id="h-new">New project</h2>
  <p class="hint">Only a name and coordinates are required. Click the map to set the location. Everything else can be added later.</p>
  <form id="f-new"><div class="grid"><div>
    <label for="np-name">Project name *</label><input id="np-name" type="text" required maxlength="160">
    <label for="np-desc">Description</label><input id="np-desc" type="text">
    <div class="row"><div style="flex:1"><label for="np-lat">Latitude * <span class="hint">decimal °</span></label><input id="np-lat" type="number" step="any" required></div>
    <div style="flex:1"><label for="np-lon">Longitude * <span class="hint">decimal °</span></label><input id="np-lon" type="number" step="any" required></div></div>
    <label for="np-type">Project type</label><select id="np-type"><option value="">Not specified</option>${state.meta.project_types.map((t) => `<option value="${t}">${esc(t.replace("_", " "))}</option>`).join("")}</select>
    <div class="row"><div style="flex:1"><label for="np-country">Country</label><input id="np-country" type="text"></div><div style="flex:1"><label for="np-city">City / address</label><input id="np-city" type="text"></div></div>
    <label for="np-boundary">Site boundary <span class="hint">optional GeoJSON polygon in WGS 84</span></label><input id="np-boundary" type="file" accept=".geojson,.json,application/geo+json">
    <fieldset><legend>What do you want to find out?</legend>${objectives}</fieldset>
    <p><button class="primary" type="submit">Create project</button></p><p id="np-err" class="err" role="alert"></p>
  </div><div>
    <label for="np-find">Find an address or place</label>
    <div class="row"><input id="np-find" type="search" maxlength="200" style="flex:1" placeholder="e.g. Fifth Settlement, New Cairo"><button id="np-find-btn" type="button">Search</button></div>
    <ul id="np-find-out" class="find-results" aria-live="polite"></ul>
    <div id="np-map" class="map" aria-label="Map: click to set the site location"></div><p class="hint">Use a sample: 30.1200, 31.8500 (synthetic sample site) with samples/sample_site_boundary.geojson.</p></div></div></form></section>`;
  clearMaps();
  const map = makeMap("np-map", 30.1200, 31.8500, 13);
  let marker = null;
  const place = (latlng) => {
    document.getElementById("np-lat").value = latlng.lat.toFixed(6);
    document.getElementById("np-lon").value = latlng.lng.toFixed(6);
    if (marker) marker.setLatLng(latlng); else marker = L.marker(latlng).addTo(map);
  };
  map.on("click", (e) => place(e.latlng));
  // Address search (Nominatim): only on Search or Enter, never while typing (usage policy).
  const out = document.getElementById("np-find-out");
  const find = () => guarded(async () => {
    const q = document.getElementById("np-find").value.trim();
    out.innerHTML = `<li class="muted" aria-busy="true">Searching…</li>`;
    try {
      const r = await api(`/geocode?q=${encodeURIComponent(q)}`);
      out.innerHTML = r.results.length
        ? r.results.map((x, i) => `<li><button type="button" class="linkish" data-i="${i}">${esc(x.name)}</button></li>`).join("") + `<li class="hint">Search by ${esc(r.attribution)}. Check the pin and move it to the exact site.</li>`
        : `<li class="muted">No places found. Try a different spelling, or click the map.</li>`;
      out.querySelectorAll("button[data-i]").forEach((b) => (b.onclick = () => {
        const x = r.results[+b.dataset.i];
        place(L.latLng(x.latitude, x.longitude));
        if (x.bbox) map.fitBounds([[x.bbox[0], x.bbox[2]], [x.bbox[1], x.bbox[3]]], { maxZoom: 17 }); else map.setView([x.latitude, x.longitude], 16);
        if (!document.getElementById("np-city").value) document.getElementById("np-city").value = x.name.slice(0, 200);
      }));
    } catch (err) { out.innerHTML = `<li class="err">${esc(err.message)}</li>`; }
  }, document.getElementById("np-find-btn"));
  document.getElementById("np-find-btn").onclick = find;
  document.getElementById("np-find").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); find(); } };
  document.getElementById("f-new").onsubmit = async (e) => {
    e.preventDefault();
    const errEl = document.getElementById("np-err"); errEl.textContent = "";
    try {
      const text = await readFileText(document.getElementById("np-boundary"));
      let boundary = null;
      if (text) { try { boundary = JSON.parse(text); } catch { throw new Error("The boundary file is not valid JSON."); } }
      const body = {
        name: e.target["np-name"].value, description: e.target["np-desc"].value, latitude: e.target["np-lat"].value, longitude: e.target["np-lon"].value,
        project_type: e.target["np-type"].value || null, country: e.target["np-country"].value, city_or_address: e.target["np-city"].value, boundary,
        objectives: [...document.querySelectorAll("input[name=obj]:checked")].map((x) => x.value),
      };
      const r = await api("/projects", { body });
      r.warnings.forEach((w) => notify(w, "warn"));
      location.hash = `#/p/${r.project.id}/overview`;
    } catch (err) { errEl.textContent = err.message; }
  };
}

// ------------------------------------------------------------------ project workspace
const TABS = [["overview", "1 · Site"], ["inputs", "2 · Inputs"], ["rules", "3 · Planning rules"], ["readiness", "4 · Readiness"], ["scenarios", "5 · Scenarios"], ["analysis", "6 · Run analysis"], ["results", "7 · Results"], ["compare", "8 · Compare"], ["report", "9 · Report"]];

async function viewProject(pid, tab, extra) {
  const data = await api(`/projects/${pid}`);
  const p = data.project;
  clearMaps();
  $main.innerHTML = `<h1>${esc(p.name)} <span class="hint">(${esc(p.role)})</span></h1>
    ${p.location_confirmed ? "" : `<div class="alert warn">The site location is not confirmed. Check it on the Site tab and confirm before running analyses.</div>`}
    <nav class="tabs" aria-label="Project steps">${TABS.map(([k, l]) => `<a href="#/p/${pid}/${k}" ${k === tab ? 'aria-current="page"' : ""}>${esc(l)}</a>`).join("")}</nav>
    <div id="tab">${LOADING}</div>`;
  const el = document.getElementById("tab");
  const views = { overview: tabOverview, inputs: tabInputs, rules: tabRules, readiness: tabReadiness, scenarios: tabScenarios, analysis: tabAnalysis, results: tabResults, compare: tabCompare, report: tabReport };
  await (views[tab] || tabOverview)(el, data, extra);
  document.getElementById("main").focus({ preventScroll: true });
}

async function tabOverview(el, data) {
  const p = data.project;
  const canEdit = p.role !== "viewer";
  el.innerHTML = `<div class="grid">
   <section class="panel"><h2>Site</h2>
    <table><tr><th>Project ID</th><td class="mono">${esc(p.id)}</td></tr><tr><th>Coordinates (WGS 84)</th><td class="mono">${fmt(p.latitude, 6)}, ${fmt(p.longitude, 6)}</td></tr>
    <tr><th>Local CRS for areas</th><td class="mono">${esc(p.local_crs)}</td></tr><tr><th>Site boundary</th><td>${p.boundary ? `Supplied — ${fmt(p.site_area_m2, 0)} m² (${fmt(p.site_area_m2 / 10000, 2)} ha)` : "Not supplied"}</td></tr>
    <tr><th>Area basis for ratios</th><td>${p.verified_site_area_m2 ? `Verified document plot area ${fmt(p.verified_site_area_m2, 0)} m²` : "Site boundary area"}</td></tr>
    <tr><th>Type</th><td>${esc(p.project_type || "Not specified")}</td></tr><tr><th>Country / address</th><td>${esc([p.country, p.city_or_address].filter(Boolean).join(" / ") || "Not specified")}</td></tr>
    <tr><th>Location</th><td>${p.location_confirmed ? badge(["Confirmed " + (p.location_confirmed_at || ""), "b-ok"]) : badge(["Not confirmed", "b-warn"])}</td></tr></table>
    ${canEdit && !p.location_confirmed ? `<p><button id="confirm" class="primary">The marker and boundary show the correct site — confirm location</button></p>` : ""}
    <h3>Missing information</h3><ul>${data.missing_information.map((m) => `<li><strong>${esc(m.item)}</strong> — ${esc(m.effect)}</li>`).join("") || "<li>None.</li>"}</ul>
    <h3>Analysis availability</h3><ul>${data.readiness.analyses.map((a) => `<li>${badge(READY[a.status])} ${esc(a.label)}</li>`).join("")}</ul>
   </section>
   <section class="panel"><h2>Map preview</h2><div id="ov-map" class="map"></div></section></div>
   ${canEdit ? `<section class="panel"><h2>Edit project</h2><form id="f-edit" class="grid"><div>
     <label for="ed-name">Name</label><input id="ed-name" type="text" value="${esc(p.name)}">
     <div class="row"><div style="flex:1"><label for="ed-lat">Latitude</label><input id="ed-lat" type="number" step="any" value="${p.latitude}"></div><div style="flex:1"><label for="ed-lon">Longitude</label><input id="ed-lon" type="number" step="any" value="${p.longitude}"></div></div>
     <label for="ed-boundary">Replace site boundary <span class="hint">GeoJSON; changing the location requires re-confirmation</span></label><input id="ed-boundary" type="file" accept=".geojson,.json">
     <label class="check"><input type="checkbox" id="ed-clear"> Remove the site boundary</label>
     <div class="row"><div style="flex:1"><label for="ed-sy">Climate period start year</label><input id="ed-sy" type="number" value="${p.analysis_period?.start_year || ""}" placeholder="default: last 5 full years"></div>
     <div style="flex:1"><label for="ed-ey">End year</label><input id="ed-ey" type="number" value="${p.analysis_period?.end_year || ""}"></div></div>
     </div><div><fieldset><legend>Analysis objectives</legend>${Object.entries(state.meta.objectives).map(([k, q]) => `<label class="check"><input type="checkbox" name="eobj" value="${k}" ${p.objectives.includes(k) ? "checked" : ""}> ${esc(q)}</label>`).join("")}</fieldset>
     <fieldset><legend>AI text</legend><label class="check"><input type="checkbox" id="ed-ai" ${p.ai_enabled ? "checked" : ""}> Allow AI-written insights and suggestions for this project</label>
      <p class="hint">When on, a compact summary of this project's results (metric values, hotspot summaries and record ids; for suggestions, the planning document's page text or CAD layer names) is sent to the configured AI provider (${esc((state.meta.ai?.providers || []).filter((x) => x.configured).map((x) => x.name).join(", ") || "none configured")}). Files, drawings and personal data are never sent. Every AI output is checked against the evidence and replaced by an automatic summary when it fails.</p></fieldset>
     <p><button type="submit">Save changes</button></p><p id="ed-err" class="err" role="alert"></p></div></form></section>` : ""}
   <section class="panel"><details><summary>Project history (${data.history.length} events)</summary><table>${data.history.map((h) => `<tr><td class="mono">${esc(h.at)}</td><td>${esc(h.event)}</td><td class="mono">${esc((h.fields || []).join(", "))}</td></tr>`).join("")}</table></details></section>`;
  const map = makeMap("ov-map", p.latitude, p.longitude, p.boundary ? 16 : 15);
  L.marker([p.latitude, p.longitude]).addTo(map).bindPopup("Entered coordinates");
  if (p.boundary) { const b = L.geoJSON(p.boundary, { style: { color: "#c62828", weight: 3, fillOpacity: 0.05 } }).addTo(map); map.fitBounds(b.getBounds(), { padding: [20, 20] }); }
  const btn = document.getElementById("confirm");
  if (btn) btn.onclick = () => guarded(async () => { await api(`/projects/${p.id}/confirm-location`, { method: "POST" }); notify("Location confirmed.", "ok"); route(); }, btn);
  const form = document.getElementById("f-edit");
  if (form) form.onsubmit = async (e) => {
    e.preventDefault();
    const errEl = document.getElementById("ed-err"); errEl.textContent = "";
    try {
      const body = { name: e.target["ed-name"].value, objectives: [...document.querySelectorAll("input[name=eobj]:checked")].map((x) => x.value), ai_enabled: document.getElementById("ed-ai").checked };
      const lat = parseFloat(e.target["ed-lat"].value), lon = parseFloat(e.target["ed-lon"].value);
      if (lat !== p.latitude || lon !== p.longitude) { body.latitude = lat; body.longitude = lon; }
      const text = await readFileText(document.getElementById("ed-boundary"));
      if (text) body.boundary = JSON.parse(text);
      if (document.getElementById("ed-clear").checked) body.boundary = null;
      const sy = e.target["ed-sy"].value, ey = e.target["ed-ey"].value;
      body.analysis_period = sy && ey ? { start_year: +sy, end_year: +ey } : null;
      const r = await api(`/projects/${p.id}`, { method: "PATCH", body });
      r.warnings.forEach((w) => notify(w, "warn"));
      notify("Project updated.", "ok"); route();
    } catch (err) { errEl.textContent = err.message; }
  };
}

function wireSuggest(el) {
  el.querySelectorAll(".ai-sug").forEach((b) => (b.onclick = () => guarded(async () => {
    const r = await api(`/files/${b.dataset.fid}/suggest-${b.dataset.kind}`, { method: "POST" });
    notify(b.dataset.kind === "rules" ? `${r.added?.length || 0} candidate rule(s) suggested; ${r.rejected?.length || 0} rejected (quote not found on the page or invalid). Verify them on the Planning rules screen.`
      : `${r.suggested || 0} feature(s) got a suggested type. Review them below.`, "ok");
    route();
  }, b)));
}

async function tabInputs(el, data) {
  const p = data.project;
  const pid = p.id;
  const canEdit = p.role !== "viewer";
  const [filesList, feats, rates] = await Promise.all([api(`/projects/${pid}/files`), api(`/projects/${pid}/features`), api(`/projects/${pid}/rates`)]);
  const cats = state.meta.upload_categories;
  const q = feats.quality;
  const active = feats.features.filter((f) => f.active);
  const baselineExists = (await api(`/projects/${pid}/scenarios`)).some((s) => s.kind === "baseline" && s.status === "active");
  el.innerHTML = `
  ${canEdit ? `<section class="panel"><h2>Upload a file</h2>
   <form id="f-up"><div class="row">
    <div style="flex:1;min-width:220px"><label for="up-cat">What are you uploading?</label><select id="up-cat">${Object.entries(cats).map(([k, v]) => `<option value="${k}">${esc(v.label)} (${v.types.join(", ").toUpperCase()})</option>`).join("")}</select></div>
    <div style="flex:1;min-width:220px"><label for="up-file">File <span class="hint">max ${state.meta.max_upload_mb} MB</span></label><input id="up-file" type="file" required></div>
    <div style="width:180px" id="up-epsg-wrap"><label for="up-epsg">EPSG code <span class="hint">DXF, or projected GeoJSON</span></label><input id="up-epsg" type="text" placeholder="e.g. 32636"></div>
    ${active.length ? `<div style="width:240px" id="up-mode-wrap"><label for="up-mode">This project already has ${active.length} plan feature(s) *</label><select id="up-mode"><option value="">Choose…</option><option value="replace">Replace them (kept in history)</option><option value="add">Add the new features alongside</option></select></div>` : ""}
    <div><button class="primary" type="submit">Upload</button></div></div>
    <div id="up-raster" hidden class="row"><div style="flex:1"><label for="up-src">Source *</label><input id="up-src" type="text"></div><div style="flex:1"><label for="up-var">Variable *</label><input id="up-var" type="text" placeholder="e.g. lst"></div>
     <div style="flex:1"><label for="up-unit">Unit *</label><input id="up-unit" type="text" placeholder="e.g. °C"></div><div style="flex:1"><label for="up-lic">Licence</label><input id="up-lic" type="text"></div><div style="flex:1"><label for="up-date">Observation date</label><input id="up-date" type="date"></div></div>
    <p id="up-cap" class="hint"></p><p id="up-err" class="err" role="alert"></p></form></section>` : ""}
  <section class="panel"><h2>Uploaded files</h2><table><thead><tr><th>File</th><th>Category</th><th>Detected type</th><th>Status</th><th>Messages</th></tr></thead><tbody>
   ${filesList.map((f) => `<tr><td>${esc(f.original_filename)}<div class="mono muted">${esc(f.checksum.slice(0, 12))}…</div></td><td>${esc(f.category)}</td><td>${esc(f.detected_type)}</td>
     <td>${badge([f.status, f.status === "failed" ? "b-bad" : f.status.startsWith("flagged") || f.status === "stored_reference_only" ? "b-warn" : "b-ok"])}</td>
     <td>${[...(f.validation?.errors || []).map((x) => `<div class="err">${esc(x)}</div>`), ...(f.validation?.warnings || []).map((x) => `<div>${esc(x)}</div>`),
        f.validation?.outcome?.message ? `<div>${esc(f.validation.outcome.message)}</div>` : "", ...(f.validation?.outcome?.notes || []).map((x) => `<div class="muted">${esc(x)}</div>`),
        ...(f.validation?.outcome?.issues || []).map((x) => `<div class="muted">${esc(typeof x === "string" ? x : x.message)}</div>`)].join("")}
       ${canEdit && p.ai_enabled && f.category === "planning_document" ? `<button class="small ai-sug" data-kind="rules" data-fid="${f.id}">Suggest rules with AI</button>` : ""}
       ${canEdit && p.ai_enabled && f.category === "urban_plan" ? `<button class="small ai-sug" data-kind="layers" data-fid="${f.id}">Suggest layer types with AI</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="5">No files. A coordinate-only project can still run climate, context and satellite analyses.</td></tr>`}
  </tbody></table></section>
  <section class="panel"><h2>Extracted plan features (${active.length})</h2>
   <p class="hint">Review the classification of every feature before creating the baseline. Corrections are stored as explicit changes; the original extraction is kept.</p>
   ${q.needs_review.length ? `<div class="alert warn">${q.needs_review.length} feature(s) need review (dashed on the map).</div>` : ""}
   ${q.unclassified.length ? `<div class="alert warn">${q.unclassified.length} feature(s) are unclassified and will not count towards any indicator.</div>` : ""}
   ${q.building_overlaps.length ? `<div class="alert warn">${q.building_overlaps.length} pair(s) of overlapping buildings found; overlapping area is not double counted.</div>` : ""}
   ${q.units_unconfirmed.length ? `<div class="alert bad">${q.units_unconfirmed.length} feature(s) come from a DXF drawing that does not declare its units, so the scale is unresolved. Areas are not used and the baseline cannot be created until you confirm the units.
     ${canEdit ? [...new Set(active.filter((f) => f.attributes?.units_unconfirmed).map((f) => f.file_id))].map((fid) => `<button class="small conf-units" data-fid="${fid}">The drawing ${esc(filesList.find((x) => x.id === fid)?.original_filename || "")} is in metres — confirm units</button>`).join(" ") : ""}</div>` : ""}
   ${q.boundary_conflict ? `<div class="alert warn">${esc(q.boundary_conflict.message)}</div>` : ""}
   ${q.buildings_without_height.length ? `<div class="alert info">${q.buildings_without_height.length} building(s) have no height. Footprint metrics still work, but building-cast shade cannot be calculated for them. Heights are never inferred from footprints.</div>` : ""}
   <div class="grid"><div id="in-map" class="map"></div><div style="max-height:440px;overflow:auto"><table><thead><tr><th>ID</th><th>Type</th><th>Height m</th><th>Floors</th><th>Review</th><th></th></tr></thead><tbody>
   ${active.map((f) => `<tr data-fid="${f.id}"><td class="mono" title="${esc(f.classification_source)}">…${short(f.id)}<div class="muted">${esc(f.attributes?.name || "")}</div></td>
     <td><select aria-label="Feature type" data-k="feature_type" ${canEdit ? "" : "disabled"}>${Object.entries(state.meta.feature_types).map(([k, v]) => `<option value="${k}" ${k === f.feature_type ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></td>
     <td><input aria-label="Height in metres" data-k="height_m" type="number" step="any" min="0" value="${f.attributes?.height_m ?? ""}" style="width:80px" ${canEdit ? "" : "disabled"}></td>
     <td><input aria-label="Floors" data-k="floors" type="number" step="1" min="0" value="${f.attributes?.floors ?? ""}" style="width:64px" ${canEdit ? "" : "disabled"}></td>
     <td>${badge([f.review_status.replace("_", " "), f.review_status === "needs_review" ? "b-warn" : "b-ok"])}<div class="muted">${esc(f.classification_confidence)}</div>
       ${f.suggestion ? `<div class="muted">AI suggests: <strong>${esc(state.meta.feature_types[f.suggestion.feature_type] || f.suggestion.feature_type)}</strong> — ${esc(f.suggestion.reason)} (choose it in the list and save to apply)</div>` : ""}</td>
     <td>${canEdit ? `<button class="small save-f">Save</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="6">No plan features. Upload a GeoJSON or DXF plan.</td></tr>`}
   </tbody></table></div></div>
   ${canEdit && active.length ? `<p><button id="mk-base" class="primary">${baselineExists ? "Replace baseline with current features" : "Create baseline scenario from these features"}</button>
     <span class="hint">The baseline is an immutable snapshot used as the reference for every comparison.</span></p>` : ""}
  </section>
  <section class="panel"><h2>Unit rates for cost estimates (${rates.length})</h2>
   <p class="hint">URBANOVA never invents prices. Add rates from a quotation or schedule of rates, or upload a CSV with columns quantity_type, unit, rate, currency, price_date, source.</p>
   <table><thead><tr><th>Quantity</th><th>Rate</th><th>Date</th><th>Source</th></tr></thead><tbody>${rates.map((r) => `<tr><td>${esc(state.meta.quantity_types[r.quantity_type]?.label || r.quantity_type)}</td><td>${fmt(r.rate)} ${esc(r.currency)} / ${esc(r.unit)}</td><td>${esc(r.price_date)}</td><td>${esc(r.source)}</td></tr>`).join("") || `<tr><td colspan="4">None.</td></tr>`}</tbody></table>
   ${canEdit ? `<form id="f-rate" class="row"><div><label for="rt-q">Quantity</label><select id="rt-q">${Object.entries(state.meta.quantity_types).map(([k, v]) => `<option value="${k}" data-unit="${v.unit}">${esc(v.label)} (${v.unit})</option>`).join("")}</select></div>
     <div style="width:110px"><label for="rt-rate">Rate</label><input id="rt-rate" type="number" step="any" min="0" required></div><div style="width:90px"><label for="rt-cur">Currency</label><input id="rt-cur" type="text" maxlength="3" required placeholder="EGP"></div>
     <div style="width:160px"><label for="rt-date">Price date</label><input id="rt-date" type="date" required></div><div style="flex:1"><label for="rt-src">Source</label><input id="rt-src" type="text" required placeholder="Quotation ref., supplier, schedule"></div>
     <div><button type="submit">Add rate</button></div></form>` : ""}
  </section>`;
  const map = makeMap("in-map", p.latitude, p.longitude, 16);
  if (p.boundary) L.geoJSON(p.boundary, { style: { color: "#c62828", weight: 3, fillOpacity: 0 } }).addTo(map);
  if (feats.geojson.features.length) { const lay = addFeatures(map, feats.geojson); try { map.fitBounds(lay.getBounds(), { padding: [20, 20] }); } catch (_) {} }
  legendControl(map, typeLegend());
  const upForm = document.getElementById("f-up");
  if (upForm) {
    const cat = document.getElementById("up-cat");
    const sync = () => {
      const c = cats[cat.value];
      document.getElementById("up-cap").textContent = Object.entries(c.capabilities).map(([t, d]) => `${t.toUpperCase()}: ${d}`).join("  ·  ");
      document.getElementById("up-epsg-wrap").hidden = cat.value !== "urban_plan";
      document.getElementById("up-raster").hidden = cat.value !== "supplementary_raster";
      const mw = document.getElementById("up-mode-wrap"); if (mw) mw.hidden = cat.value !== "urban_plan";
    };
    cat.onchange = sync; sync();
    upForm.onsubmit = async (e) => {
      e.preventDefault();
      const errEl = document.getElementById("up-err"); errEl.textContent = "";
      const fd = new FormData();
      fd.append("file", document.getElementById("up-file").files[0]);
      fd.append("category", cat.value);
      const epsg = document.getElementById("up-epsg").value.trim(); if (epsg && cat.value === "urban_plan") fd.append("epsg", epsg);
      const mode = document.getElementById("up-mode");
      if (mode && cat.value === "urban_plan") { if (!mode.value) { errEl.textContent = "Choose whether the new plan replaces the existing features or is added to them."; return; } fd.append("plan_mode", mode.value); }
      if (cat.value === "supplementary_raster") { [["source", "up-src"], ["variable", "up-var"], ["unit", "up-unit"], ["license", "up-lic"], ["observation_date", "up-date"]].forEach(([k, id]) => fd.append(k, document.getElementById(id).value)); }
      const btn = upForm.querySelector("button[type=submit]"); btn.disabled = true; btn.textContent = "Uploading…";
      try {
        const r = await api(`/projects/${pid}/uploads`, { body: fd });
        r.warnings.forEach((w) => notify(w, "warn"));
        notify(r.outcome.message || `Upload processed: ${r.outcome.status}.`, "ok");
        route();
      } catch (err) { errEl.textContent = err.message; btn.disabled = false; btn.textContent = "Upload"; }
    };
  }
  wireSuggest(el);
  el.querySelectorAll(".save-f").forEach((b) => (b.onclick = () => guarded(async () => {
    const tr = b.closest("tr");
    const f = active.find((x) => x.id === tr.dataset.fid);
    const changes = {};
    tr.querySelectorAll("[data-k]").forEach((inp) => {
      const k = inp.dataset.k, v = inp.value;
      const cur = k === "feature_type" ? f.feature_type : f.attributes?.[k] ?? "";
      if (String(cur) !== String(v)) changes[k] = v === "" ? null : v;
    });
    if (!Object.keys(changes).length) changes.review_status = "reviewed";
    await api(`/features/${f.id}`, { method: "PATCH", body: changes });
    notify("Feature saved and marked reviewed.", "ok"); route();
  }, b)));
  el.querySelectorAll(".conf-units").forEach((b) => (b.onclick = () => guarded(async () => {
    if (!confirm("Confirm that this drawing's coordinates are in metres? Areas will then be calculated from it.")) return;
    const r = await api(`/files/${b.dataset.fid}/confirm-units`, { method: "POST" });
    notify(`Units confirmed as metres for ${r.confirmed} feature(s).`, "ok"); route();
  }, b)));
  const mk = document.getElementById("mk-base");
  if (mk) mk.onclick = () => guarded(async () => {
    if (q.needs_review.length && !confirm(`${q.needs_review.length} feature(s) still need review. Create the baseline anyway?`)) return;
    await api(`/projects/${pid}/scenarios`, { body: { kind: "baseline" } });
    notify("Baseline scenario created.", "ok"); location.hash = `#/p/${pid}/scenarios`;
  }, mk);
  const rf = document.getElementById("f-rate");
  if (rf) rf.onsubmit = (e) => { e.preventDefault(); guarded(async () => {
    const sel = document.getElementById("rt-q");
    await api(`/projects/${pid}/rates`, { body: { quantity_type: sel.value, unit: sel.selectedOptions[0].dataset.unit, rate: document.getElementById("rt-rate").value,
      currency: document.getElementById("rt-cur").value, price_date: document.getElementById("rt-date").value, source: document.getElementById("rt-src").value } });
    notify("Rate added.", "ok"); route();
  }); };
}

async function tabRules(el, data) {
  const p = data.project, pid = p.id, canEdit = p.role !== "viewer";
  const r = await api(`/projects/${pid}/rules`);
  const types = r.rule_types;
  const statusBadge = (s) => badge([s, { verified: "b-ok", candidate: "b-warn", rejected: "b-mute", superseded: "b-mute" }[s] || "b-mute"]);
  const plot = r.rules.find((x) => x.rule_type === "plot_area_m2" && x.status === "verified");
  el.innerHTML = `
  <section class="panel"><h2>Planning conditions</h2>
   <p class="hint">Extracted conditions are candidates until you verify them against the source. Only verified rules are used in planning checks; nothing is applied silently.</p>
   ${r.ambiguities.map((a) => `<div class="alert warn">${esc(a.message)} Values: ${a.values.map((v) => fmt(v)).join(", ")}. Verify the correct one.</div>`).join("")}
   <table><thead><tr><th>Condition</th><th>Value</th><th>Source wording</th><th>Source</th><th>Confidence / notes</th><th>Status</th><th></th></tr></thead><tbody>
   ${r.rules.map((x) => `<tr data-rid="${x.id}"><td>${esc(types[x.rule_type]?.label || x.rule_type)}</td>
     <td>${x.value !== null ? `<input aria-label="Value" type="number" step="any" value="${x.value}" style="width:100px" ${canEdit ? "" : "disabled"}> ${esc(x.unit || "")}` : esc(x.value_text)}</td>
     <td><q>${esc(x.exact_text)}</q></td><td>${esc(x.document_name)}${x.page ? `, p. ${x.page}` : ""}<div class="muted">${esc(x.origin.replace(/_/g, " "))}</div>${x.file_id ? `<button class="small src-doc" data-fid="${x.file_id}" data-name="${esc(x.document_name)}">Open source document</button>` : ""}</td>
     <td>${esc(x.confidence)}<div class="muted">${esc(x.notes || "")}</div></td><td>${statusBadge(x.status)}</td>
     <td>${canEdit ? `<button class="small v-ok">Verify</button> <button class="small v-no">Reject</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="7">No planning conditions. Upload a planning document (PDF) on the Inputs tab, or enter a rule below. Without verified rules, compliance is reported as not assessed.</td></tr>`}
   </tbody></table></section>
  ${canEdit ? `<section class="panel"><h2>Enter a rule manually</h2><form id="f-rule" class="row">
    <div><label for="ru-type">Rule</label><select id="ru-type">${Object.entries(types).map(([k, v]) => `<option value="${k}">${esc(v.label)}${v.unit ? ` (${v.unit})` : ""}</option>`).join("")}</select></div>
    <div style="width:140px"><label for="ru-val">Value</label><input id="ru-val" type="text" required></div>
    <div style="flex:1"><label for="ru-src">Source (document, clause, authority) *</label><input id="ru-src" type="text" required></div>
    <div style="flex:1"><label for="ru-txt">Exact wording</label><input id="ru-txt" type="text"></div><div><button type="submit">Add as candidate</button></div></form></section>
   <section class="panel"><h2>Area basis for ratio checks</h2>
    <p>Coverage and green-space ratios use the <strong>${p.verified_site_area_m2 ? "verified document plot area" : "site boundary area"}</strong>${p.site_area_m2 ? ` (boundary: ${fmt(p.site_area_m2, 0)} m²)` : ""}.</p>
    <button id="ab-boundary" class="small">Use site boundary area</button> <button id="ab-doc" class="small" ${plot ? "" : "disabled"}>Use verified plot area${plot ? ` (${fmt(plot.value, 0)} m²)` : " (verify a plot-area rule first)"}</button></section>` : ""}`;
  el.querySelectorAll("tr[data-rid]").forEach((tr) => {
    const rid = tr.dataset.rid;
    const ok = tr.querySelector(".v-ok"), no = tr.querySelector(".v-no");
    if (ok) ok.onclick = () => guarded(async () => { const inp = tr.querySelector("input"); await api(`/rules/${rid}/decision`, { body: { decision: "verified", value: inp ? inp.value : undefined } }); notify("Rule verified.", "ok"); route(); }, ok);
    if (no) no.onclick = () => guarded(async () => { await api(`/rules/${rid}/decision`, { body: { decision: "rejected" } }); notify("Rule rejected.", "info"); route(); }, no);
  });
  el.querySelectorAll(".src-doc").forEach((b) => (b.onclick = () => guarded(() => download(`/files/${b.dataset.fid}/original`, b.dataset.name), b)));
  const fr = document.getElementById("f-rule");
  if (fr) fr.onsubmit = (e) => { e.preventDefault(); guarded(async () => {
    await api(`/projects/${pid}/rules`, { body: { rule_type: document.getElementById("ru-type").value, value: document.getElementById("ru-val").value, source: document.getElementById("ru-src").value, exact_text: document.getElementById("ru-txt").value } });
    notify("Rule added as a candidate — verify it to apply it.", "ok"); route();
  }); };
  const ab = (basis) => guarded(async () => { await api(`/projects/${pid}/site-area-basis`, { body: { basis } }); notify("Area basis updated.", "ok"); route(); });
  const b1 = document.getElementById("ab-boundary"), b2 = document.getElementById("ab-doc");
  if (b1) b1.onclick = () => ab("boundary");
  if (b2) b2.onclick = () => ab("document");
}

async function tabReadiness(el, data) {
  const pid = data.project.id;
  const scen = await api(`/projects/${pid}/scenarios`);
  const active = scen.filter((s) => s.status === "active");
  el.innerHTML = `<section class="panel"><h2>What can be analysed?</h2>
   <div class="row"><div><label for="rd-scen">For scenario</label><select id="rd-scen">${active.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}<option value="">No design (site only)</option></select></div></div>
   <div id="rd-out"></div></section>`;
  const render = async () => {
    const sid = document.getElementById("rd-scen").value;
    const r = await api(`/projects/${pid}/readiness-check`, { body: sid ? { scenario_id: sid } : {} });
    document.getElementById("rd-out").innerHTML = `${r.global_blockers.map((b) => `<div class="alert warn">${esc(b)}</div>`).join("")}
     <table><thead><tr><th>Analysis</th><th>Status</th><th>Missing inputs</th><th>What this prevents</th><th>Next step</th></tr></thead><tbody>
     ${r.analyses.map((a) => `<tr><td>${esc(a.label)}${a.limitations.length ? `<div class="muted">${a.limitations.map(esc).join("<br>")}</div>` : ""}</td><td>${badge(READY[a.status])}</td>
       <td>${a.missing_inputs.map(esc).join("<br>") || "—"}</td><td>${esc(a.what_missing_prevents) || "—"}</td><td>${esc(a.next_step)}</td></tr>`).join("")}</tbody></table>`;
  };
  document.getElementById("rd-scen").onchange = render;
  await render();
}

async function tabScenarios(el, data, extra) {
  const p = data.project, pid = p.id, canEdit = p.role !== "viewer";
  const list = await api(`/projects/${pid}/scenarios`);
  const base = list.find((s) => s.kind === "baseline" && s.status === "active");
  const selId = extra || (list.find((s) => s.kind === "alternative" && s.status === "active") || base || {}).id;
  const sel = selId ? await api(`/scenarios/${selId}`) : null;
  el.innerHTML = `
  <section class="panel"><h2>Scenarios</h2>
   <p class="hint">The baseline is the original design and cannot be edited. Alternatives start as copies of the baseline; every change is logged. Archiving keeps history.</p>
   <table><thead><tr><th>Name</th><th>Kind</th><th>Features</th><th>Status</th><th>Latest run</th><th></th></tr></thead><tbody>
   ${list.map((s) => `<tr><td><a href="#/p/${pid}/scenarios/${s.id}">${esc(s.name)}</a></td><td>${s.kind === "baseline" ? badge(["Baseline (reference)", "b-info"]) : "Alternative"}</td><td>${s.feature_count}</td>
     <td>${badge([s.status, s.status === "active" ? "b-ok" : "b-mute"])}</td>
     <td>${s.latest_run ? `${badge(RUN_STATUS[s.latest_run.status])} ${Object.keys(s.latest_run.stale).length ? badge(["out of date", "b-warn"]) : ""}` : '<span class="muted">not analysed</span>'}</td>
     <td>${canEdit && s.kind === "alternative" && s.status === "active" ? `<button class="small danger arch" data-sid="${s.id}">Archive</button>` : ""}
       ${canEdit && s.status === "draft" ? `<button class="small primary appr" data-sid="${s.id}">Approve draft</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="6">No scenarios. Upload plan geometry and create the baseline on the Inputs tab.</td></tr>`}
   </tbody></table>
   ${canEdit && base ? `<form id="f-alt" class="row"><div style="flex:1"><label for="alt-name">New alternative name</label><input id="alt-name" type="text" required placeholder="e.g. More shade and trees"></div>
     <div style="flex:2"><label for="alt-desc">Description</label><input id="alt-desc" type="text"></div><div><button class="primary" type="submit">Create alternative from baseline</button></div></form>` : ""}
  </section>
  ${sel ? `<section class="panel"><h2>${esc(sel.name)} ${sel.kind === "baseline" ? badge(["Baseline — read only", "b-info"]) : ""}</h2>
   <div class="grid"><div id="sc-map" class="map"></div><div style="max-height:440px;overflow:auto">
   <table><thead><tr><th>Feature</th><th>Type</th><th>Height m</th><th>Floors</th><th>Canopy r m</th><th></th></tr></thead><tbody>
   ${sel.features.features.map((f) => { const ed = canEdit && sel.kind === "alternative" && sel.status === "active"; const pr = f.properties; return `<tr data-fid="${f.id}">
     <td class="mono">…${short(f.id)}<div class="muted">${esc(pr.name || "")}</div>${pr.review_status === "proposed" ? badge(["proposed", "b-info"]) : ""}</td>
     <td><select aria-label="Feature type" data-k="feature_type" ${ed ? "" : "disabled"}>${Object.entries(state.meta.feature_types).map(([k, v]) => `<option value="${k}" ${k === pr.feature_type ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></td>
     <td><input aria-label="Height" data-k="height_m" type="number" step="any" value="${pr.height_m ?? ""}" style="width:72px" ${ed ? "" : "disabled"}></td>
     <td><input aria-label="Floors" data-k="floors" type="number" value="${pr.floors ?? ""}" style="width:60px" ${ed ? "" : "disabled"}></td>
     <td><input aria-label="Canopy radius" data-k="canopy_radius_m" type="number" step="any" value="${pr.canopy_radius_m ?? ""}" style="width:60px" ${ed ? "" : "disabled"}></td>
     <td>${ed ? `<button class="small upd">Apply</button> <button class="small danger rm">Remove</button>` : ""}</td></tr>`; }).join("")}
   </tbody></table></div></div>
   ${canEdit && sel.kind === "alternative" && sel.status === "active" ? `<form id="f-imp" class="row"><div style="flex:1"><label for="imp-file">Add features from GeoJSON <span class="hint">each feature needs a feature_type property (e.g. samples/sample_alternative_additions.geojson)</span></label><input id="imp-file" type="file" accept=".geojson,.json" required></div><div><button type="submit">Add to this alternative</button></div></form>` : ""}
   <details><summary>Change log (${sel.modifications.length})</summary><pre class="mono">${esc(JSON.stringify(sel.modifications, null, 1))}</pre></details>
  </section>` : ""}`;
  if (sel) {
    const map = makeMap("sc-map", p.latitude, p.longitude, 16);
    if (p.boundary) L.geoJSON(p.boundary, { style: { color: "#c62828", weight: 3, fillOpacity: 0 } }).addTo(map);
    const lay = addFeatures(map, sel.features); try { map.fitBounds(lay.getBounds(), { padding: [20, 20] }); } catch (_) {}
    legendControl(map, typeLegend());
  }
  el.querySelectorAll(".appr").forEach((b) => (b.onclick = () => guarded(async () => { if (!confirm("Approve this draft? It becomes an active alternative that can be analysed.")) return; await api(`/scenarios/${b.dataset.sid}/approve`, { method: "POST" }); route(); }, b)));
  el.querySelectorAll(".arch").forEach((b) => (b.onclick = () => guarded(async () => { if (!confirm("Archive this alternative? It stays in history but can no longer be edited.")) return; await api(`/scenarios/${b.dataset.sid}/archive`, { method: "POST" }); route(); }, b)));
  const fa = document.getElementById("f-alt");
  if (fa) fa.onsubmit = (e) => { e.preventDefault(); guarded(async () => {
    const s = await api(`/projects/${pid}/scenarios`, { body: { kind: "alternative", name: document.getElementById("alt-name").value, description: document.getElementById("alt-desc").value } });
    notify("Alternative created — edit it below.", "ok"); location.hash = `#/p/${pid}/scenarios/${s.id}`;
  }); };
  const edit = (ops) => guarded(async () => { await api(`/scenarios/${sel.id}`, { method: "PATCH", body: { changes: ops } }); notify("Scenario updated. Re-run the analysis to refresh its results.", "ok"); route(); });
  el.querySelectorAll("tr[data-fid] .upd").forEach((b) => (b.onclick = () => {
    const tr = b.closest("tr"); const f = sel.features.features.find((x) => x.id === tr.dataset.fid); const props = {};
    tr.querySelectorAll("[data-k]").forEach((inp) => { const k = inp.dataset.k; if (String(f.properties[k] ?? "") !== inp.value) props[k] = inp.value === "" ? null : inp.value; });
    if (!Object.keys(props).length) return notify("No changes to apply.", "info");
    edit([{ op: "update", feature_id: f.id, properties: props }]);
  }));
  el.querySelectorAll("tr[data-fid] .rm").forEach((b) => (b.onclick = () => edit([{ op: "remove", feature_id: b.closest("tr").dataset.fid }])));
  const fi = document.getElementById("f-imp");
  if (fi) fi.onsubmit = (e) => { e.preventDefault(); guarded(async () => {
    const fd = new FormData(); fd.append("file", document.getElementById("imp-file").files[0]);
    await api(`/scenarios/${sel.id}/import`, { body: fd }); notify("Features added to the alternative.", "ok"); route();
  }); };
}

async function tabAnalysis(el, data) {
  const p = data.project, pid = p.id, canEdit = p.role !== "viewer";
  const [scen, runs] = await Promise.all([api(`/projects/${pid}/scenarios`), api(`/projects/${pid}/analysis-runs`)]);
  const active = scen.filter((s) => s.status === "active");
  const names = Object.fromEntries(scen.map((s) => [s.id, s.name]));
  const dc = state.meta.default_config;
  el.innerHTML = `
  ${!p.location_confirmed ? `<div class="alert bad">Confirm the site location (Site tab) before running analyses.</div>` : ""}
  ${canEdit ? `<section class="panel"><h2>Run analysis</h2>
   <p class="hint">Heat analysis = regional climate baseline + satellite land surface temperature (only if its data source is approved — see Readiness), interpreted with the design's green, hard-surface and shade indicators. Modules run independently; a failed data source does not stop the others.</p>
   <form id="f-run"><div class="grid"><div>
    <label for="ru-scen">Design scenario</label><select id="ru-scen">${active.map((s) => `<option value="${s.id}">${esc(s.name)}${s.kind === "baseline" ? " (baseline)" : ""}</option>`).join("")}<option value="">No design — site context only</option></select>
    <fieldset><legend>Modules <span class="hint">(defaults follow your objectives)</span></legend>${state.meta.runnable_modules.map((m) => `<label class="check"><input type="checkbox" name="mod" value="${m}" checked> ${esc(state.meta.analyses[m])}</label>`).join("")}</fieldset>
   </div><div><details><summary>Advanced settings</summary>
    <label for="cf-ctx">Context study buffer (m)</label><input id="cf-ctx" type="number" value="${dc.context_buffer_m}" min="100" max="3000">
    <label for="cf-lst">Surface-temperature surroundings buffer (m)</label><input id="cf-lst" type="number" value="${dc.lst_buffer_m}" min="100" max="3000">
    <label for="cf-cloud">Maximum scene cloud cover for satellite search (%)</label><input id="cf-cloud" type="number" value="${dc.max_scene_cloud_pct}" min="1" max="80">
    <label for="cf-years">Satellite years (LST)</label><input id="cf-years" type="number" value="${dc.satellite_years}" min="1" max="10">
    <label for="cf-months">Hot months override <span class="hint">e.g. 6,7,8 — default from climate baseline</span></label><input id="cf-months" type="text">
    <label for="cf-date">Shade analysis date override</label><input id="cf-date" type="date"></details>
    <p><button id="run-one" class="primary" type="submit" ${p.location_confirmed ? "" : "disabled"}>Run for selected scenario</button>
    <button id="run-all" type="button" ${p.location_confirmed && active.length ? "" : "disabled"}>Run all active scenarios with these settings</button></p>
    <p class="hint">Use "Run all" before comparing so the baseline and alternatives share identical settings.</p></div></div></form>
   <div id="progress" aria-live="polite"></div></section>` : ""}
  <section class="panel"><h2>Analysis runs</h2><table><thead><tr><th>Run</th><th>Scenario</th><th>Status</th><th>Started</th><th>Completed</th><th></th></tr></thead><tbody>
  ${runs.map((r) => `<tr><td class="mono">${esc(r.id)}</td><td>${esc(names[r.scenario_id] || "site only")}</td><td>${badge(RUN_STATUS[r.status])} ${Object.keys(r.stale_modules).length ? badge(["out of date: " + Object.keys(r.stale_modules).join(", "), "b-warn"]) : ""}</td>
    <td>${esc(r.started_at || "")}</td><td>${esc(r.completed_at || "")}</td><td>${["queued", "running"].includes(r.status) ? "" : `<a href="#/p/${pid}/results/${r.id}">Open results</a>`}</td></tr>`).join("") || `<tr><td colspan="6">No runs yet.</td></tr>`}
  </tbody></table></section>`;
  const f = document.getElementById("f-run");
  if (!f) return;
  const config = () => {
    const c = { modules: [...document.querySelectorAll("input[name=mod]:checked")].map((x) => x.value), context_buffer_m: +document.getElementById("cf-ctx").value, lst_buffer_m: +document.getElementById("cf-lst").value,
      max_scene_cloud_pct: +document.getElementById("cf-cloud").value, satellite_years: +document.getElementById("cf-years").value };
    const months = document.getElementById("cf-months").value.trim(); if (months) c.hot_months = months.split(",").map((x) => parseInt(x, 10));
    const d = document.getElementById("cf-date").value; if (d) c.solar_date = d;
    if (!c.modules.length) throw new Error("Select at least one module.");
    return c;
  };
  const follow = async (runList) => {
    const box = document.getElementById("progress");
    for (;;) {
      const jobs = await Promise.all(runList.map((r) => api(`/jobs/${r.job_id}`)));
      box.innerHTML = jobs.map((j, i) => `<div class="panel"><strong>${esc(names[runList[i].scenario_id] || "site only")}</strong> — ${badge(RUN_STATUS[j.status] || [j.status, "b-info"])} ${esc(j.stage || "")}
        <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round((j.progress || 0) * 100)}"><div style="width:${Math.round((j.progress || 0) * 100)}%"></div></div></div>`).join("");
      if (jobs.every((j) => !["queued", "running"].includes(j.status))) break;
      await new Promise((res) => setTimeout(res, 2000));
    }
    notify("Analysis finished. Opening results.", "ok");
    location.hash = `#/p/${pid}/results`; // Results opens on the latest baseline run
  };
  f.onsubmit = (e) => { e.preventDefault(); guarded(async () => {
    const sid = document.getElementById("ru-scen").value;
    const r = await api(`/projects/${pid}/analysis-runs`, { body: { scenario_id: sid || null, config: config() } });
    await follow([r]);
  }, document.getElementById("run-one")); };
  document.getElementById("run-all").onclick = (e) => guarded(async () => { const rs = await api(`/projects/${pid}/analysis-runs/all-scenarios`, { body: { config: config() } }); await follow(rs); }, e.target);
}

const SEV_COLORS = { high: "#b2182b", moderate: "#ef8a62", low: "#e0a96d" };

const ROLE_LABELS = { best_value: "best value", largest_benefit: "largest benefit", cheapest_effective: "cheapest effective" };

function costText(c) {
  if (!c || c.status === "not_applicable") return "—";
  if (c.status !== "priced") return "price required";
  return `${fmt(c.low, 0)}–${fmt(c.high, 0)} ${esc(c.currency)} (typ. ${fmt(c.typical, 0)})${c.maintenance_per_year != null ? `, +${fmt(c.maintenance_per_year, 0)}/yr` : ""}${c.stale ? " — old price" : ""}`;
}

function optionsTable(recs) {
  if (!recs.length) return "";
  const rows = recs.flatMap((r) => r.options.map((o) => { const e = o.effect;
    return `<tr><td>${esc(o.title)}<div class="muted">${esc(o.intensity_label)}</div></td>
      <td>${o.separate ? `${fmt(e.treated_m2, 0)} m² treated <span class="muted">(not ranked; heat effect not modelled)</span>` : `${fmt(e.before, 0)} → ${fmt(e.after, 0)} ${esc(e.unit)} <strong>(−${fmt(e.change_pct, 1)} %)</strong>`}</td>
      <td>${fmt(o.quantity.value, 0)} ${esc(o.quantity.unit || "")}${o.lost_parking_spaces ? `<div class="muted">−${o.lost_parking_spaces} spaces</div>` : ""}${o.water ? `<div class="muted">${esc(o.water.note || `${fmt(o.water.litres_per_day, 0)} L/day`)}</div>` : ""}</td>
      <td>${costText(o.cost)}</td>
      <td>effect ${esc(o.effect_confidence.level)} · cost ${esc((o.cost.confidence || {}).level || "—")}</td>
      <td>${o.pareto ? badge(["Pareto", "b-ok"]) : ""} ${o.roles.map((x) => badge([ROLE_LABELS[x] || x, "b-info"])).join(" ")}</td></tr>`; })).join("");
  return `<details open><summary>Measured options (${recs.reduce((n, r) => n + r.options.length, 0)})</summary><table class="opts"><thead><tr><th>Option</th><th>Measured effect</th><th>Quantity, water</th><th>Cost</th><th>Confidence</th><th></th></tr></thead><tbody>${rows}</tbody></table>
    <p class="hint">${recs.some((r) => r.options.some((o) => o.ranking_mode === "benefit_and_cost")) ? "Priced options are ranked on measured benefit and typical cost. " : ""}${recs.some((r) => r.options.some((o) => o.ranking_mode === "benefit_per_quantity")) ? "Options without a comparable price are compared only within their own intervention, on benefit per unit of quantity." : ""}</p></details>`;
}

const BLOCK_TITLES = { site_status: "Site status", top_issues: "Main problems (priority order)", attention_first: "What needs attention first", limitations: "Limitations" };

function claimHtml(c) {
  const ids = (c.evidence_ids || []).map((i) => `<a href="#" class="ev mono" data-ev="${esc(i)}">${esc(i)}</a>`).join(" ");
  return `<li>${esc(c.text)} ${ids ? `<span class="muted">[${ids}]</span>` : ""}</li>`;
}

async function renderInsights(rid, p) {
  const box = document.getElementById("ins-body");
  if (!box) return;
  const ins = await api(`/analysis-runs/${rid}/insights`);
  const canEdit = p.role !== "viewer";
  const btn = canEdit ? `<button class="small" id="ins-make">${ins ? "Regenerate" : "Write insights"}</button>` : "";
  if (!ins) { box.innerHTML = `<p>No insights yet. ${p.ai_enabled ? "AI text is on for this project." : "AI text is off for this project, so an automatic summary is written."}</p>${btn}`; }
  else {
    const qs = Object.fromEntries((state.meta.objectives ? Object.entries(state.meta.objectives) : []).map(([k, v]) => [`q_${k}`, v]));
    box.innerHTML = `<p>${badge([ins.generated_by === "ai" ? `AI-written (${ins.model})` : "AI text unavailable — automatic summary", ins.generated_by === "ai" ? "b-info" : "b-mute"])}
      <span class="muted">${esc(ins.label)}${ins.reason ? ` — ${esc(ins.reason)}` : ""}</span></p>
      ${["site_status", "top_issues", "attention_first"].map((b) => (ins.blocks[b] || []).length ? `<h3>${BLOCK_TITLES[b]}</h3><ul>${ins.blocks[b].map(claimHtml).join("")}</ul>` : "").join("")}
      ${(ins.blocks.answers || []).length ? `<h3>Your questions</h3>${ins.blocks.answers.map((a) => `<p><strong>${esc(qs[a.question_id] || a.question_id)}</strong></p><ul>${a.claims.map(claimHtml).join("")}</ul>`).join("")}` : ""}
      <details><summary>${BLOCK_TITLES.limitations} (${(ins.blocks.limitations || []).length})</summary><ul>${(ins.blocks.limitations || []).map(claimHtml).join("")}</ul></details>
      <p class="hint">Generated ${esc(ins.created_at)} · evidence pack ${esc(ins.pack_hash)} · prompt ${esc(ins.prompt_version)}</p>${btn}`;
  }
  box.querySelectorAll(".ev").forEach((a) => (a.onclick = (e) => { e.preventDefault(); const t = document.getElementById(`ev-${a.dataset.ev}`); if (t) { t.scrollIntoView({ behavior: "smooth", block: "center" }); t.classList.add("flash"); setTimeout(() => t.classList.remove("flash"), 1500); } else notify("That record is listed in the evidence pack (see Metrics, Findings or Hotspots).", "info"); }));
  const mk = document.getElementById("ins-make");
  if (mk) mk.onclick = () => guarded(async () => { mk.textContent = "Writing…"; await api(`/analysis-runs/${rid}/insights`, { method: "POST" }); await renderInsights(rid, p); }, mk);
}

function hotspotsPanel(hotspots, context, profile, mod, recs = [], intv = null, runId = "", canEdit = false) {
  if (!mod || mod.status === "skipped") return "";
  const notes = mod.details?.notes || [];
  const sm = (h, n) => h.supporting_metrics.find((s) => s.name === n);
  return `<section class="panel"><h2>Design hotspots (${hotspots.length})</h2>
   <p class="hint">Places on the drawing with a measured problem, numbered by priority (severity, then area) and shown on the map. Severity: PHASE2_SPEC §4.2 table; heat stress on the UTCI scale.</p>
   ${hotspots.length ? "" : `<p>No design hotspots. ${esc(notes.join(" ") || mod.reason || "")}</p>`}
   <div class="hs-list">${hotspots.map((h) => { const su = sm(h, "utci_sun_peak_c"), sh = sm(h, "utci_shade_peak_c");
     return `<div class="panel hs" id="ev-${esc(h.id)}"><h3><span class="hs-badge">${h.number}</span> <span class="badge sev-${esc(h.severity)}">${esc(h.severity.toUpperCase())}${h.severity_capped ? " (PROVISIONAL)" : ""}</span> ${esc(h.type_labels.join(" + "))}</h3>
      <p>${fmt(h.area_m2, 0)} m² (${fmt(h.site_share_pct, 1)} % of the site${h.site_wide ? ", site-wide" : ""}) · size ${esc(h.size_class)} · intensity ${esc(h.intensity)}</p>
      ${su && sh ? `<p>UTCI in sun up to <strong>${fmt(su.value, 1)} °C</strong>, in shade ${fmt(sh.value, 1)} °C</p>` : ""}
      ${h.likely_causes.length ? `<ul>${h.likely_causes.map((c) => `<li><span class="mono">${esc(c.code)}</span> ${esc(c.text)}</li>`).join("")}</ul>` : `<p class="muted">None of the listed cause rules matched.</p>`}
      <details><summary>Severity basis and measurements</summary><p class="muted">${esc(h.severity_basis)}</p>
       <table>${h.supporting_metrics.map((s) => `<tr><td>${esc(s.label)}</td><td>${typeof s.value === "object" && s.value !== null ? esc(Object.entries(s.value).map(([k, v]) => `${k} ${v} %`).join(", ")) : s.value === null ? esc(s.note || "—") : `${fmt(s.value, 2)} ${esc(s.unit || "")}`}</td></tr>`).join("")}</table>
       <p class="muted">Confidence: ${esc(h.confidence)} — ${esc(h.confidence_reason)}</p></details>
      ${optionsTable(recs.filter((r) => r.hotspot_id === h.id && (r.options || []).length))}</div>`; }).join("")}</div>
   ${(intv?.details?.not_optimised || []).map((n) => `<p class="muted">${esc(n)}</p>`).join("")}
   ${intv?.details?.package ? `<div class="alert info"><strong>Package within budget (${fmt(intv.details.package.budget.amount, 0)} ${esc(intv.details.package.budget.currency)}):</strong> ${esc(intv.details.package.note)}
     ${intv.details.package.cost ? ` Typical cost ${fmt(intv.details.package.cost.typical, 0)} ${esc(intv.details.package.cost.currency)}; ${intv.details.package.option_ids.length} option(s).` : ""}
     ${canEdit && intv.details.package.status === "draft" ? ` <button class="small primary" id="pkg-draft" data-rid="${runId}">Save as draft alternative</button>` : ""}</div>` : ""}
   ${context.map((c) => `<div class="alert info"><strong>Site context (today's land):</strong> ${esc(c.statement)}<div class="muted">${esc(c.note)}</div></div>`).join("")}
   ${profile.length ? `<details><summary>Hot-day heat stress, hour by hour (UTCI in sun vs shade)</summary><table><thead><tr><th>Solar time</th><th>Air °C</th><th>UTCI sun °C</th><th>UTCI shade °C</th><th>Category in sun</th></tr></thead><tbody>
     ${profile.map((r) => `<tr><td>${String(r.hour).padStart(2, "0")}:00</td><td>${fmt(r.ta_c, 1)}</td><td>${fmt(r.utci_sun_c, 1)}</td><td>${fmt(r.utci_shade_c, 1)}</td><td>${esc(r.category_sun || "outside UTCI range")}</td></tr>`).join("")}</tbody></table></details>` : ""}
  </section>`;
}

async function tabResults(el, data, runId) {
  const p = data.project, pid = p.id;
  const [allRuns, scenList] = await Promise.all([api(`/projects/${pid}/analysis-runs`), api(`/projects/${pid}/scenarios`)]);
  const runs = allRuns.filter((r) => !["queued", "running"].includes(r.status));
  if (!runs.length) { el.innerHTML = `<section class="panel"><p>No completed analysis runs yet. Go to <a href="#/p/${pid}/analysis">Run analysis</a>.</p></section>`; return; }
  const names = Object.fromEntries(scenList.map((s) => [s.id, s.name]));
  // Open on the latest baseline run unless a run was chosen (the comparison reference comes first).
  const baseIds = new Set(scenList.filter((s) => s.kind === "baseline").map((s) => s.id));
  const rid = runId || (runs.find((r) => baseIds.has(r.scenario_id)) || runs[0]).id;
  const b = await api(`/analysis-runs/${rid}`);
  const run = b.run, ms = run.module_status || {};
  const scen = run.scenario_id ? await api(`/scenarios/${run.scenario_id}`) : null;
  const stale = Object.keys(run.stale_modules);
  const incomplete = Object.entries(ms).filter(([, s]) => !["completed", "skipped"].includes(s.status));
  const skipped = Object.values(ms).filter((s) => s.status === "skipped");
  const byMod = {};
  b.metrics.forEach((m) => (byMod[m.module] = byMod[m.module] || []).push(m));
  const recsByFinding = {};
  b.recommendations.forEach((r) => (recsByFinding[r.finding_id] = recsByFinding[r.finding_id] || []).push(r));
  const concerns = b.findings.filter((f) => f.classification !== "data_quality_issue");
  const dq = b.findings.filter((f) => f.classification === "data_quality_issue");
  const climate = ms.climate_baseline?.details?.summary;
  const hotspots = b.hotspots || [], context = b.site_context || [];
  const profile = ms.heat_stress?.details?.profile || [];
  el.innerHTML = `
  <section class="panel"><div class="row"><div><label for="rs-run">Analysis run</label><select id="rs-run">${runs.map((r) => `<option value="${r.id}" ${r.id === rid ? "selected" : ""}>${esc(names[r.scenario_id] || "site only")} — ${esc(r.completed_at)} (${esc(RUN_STATUS[r.status][0])})</option>`).join("")}</select></div></div>
   <div class="alert ${run.status === "succeeded" && !stale.length ? "ok" : "warn"}"><strong>${run.status === "succeeded" && !stale.length ? "Complete result" : "Partial or preliminary result"}</strong> for
    <strong>${esc(scen ? scen.name : "site context only")}</strong>. ${incomplete.length ? `${incomplete.length} module(s) did not complete fully: ${incomplete.map(([k, s]) => `${esc(s.label)} (${esc(MOD_STATUS[s.status][0].toLowerCase())})`).join("; ")}.` : ""}
    ${stale.length ? ` Inputs changed after this run — out-of-date modules: ${stale.map(esc).join(", ")}. Re-run to refresh.` : ""}
    ${skipped.length ? ` Not selected for this run: ${skipped.map((s) => esc(s.label)).join("; ")}.` : ""}</div>
   <h3>Your questions</h3><ul>${b.objectives.map((o) => `<li>${esc(o.question)} — ${!o.answerable ? badge(["cannot be answered with available data", "b-bad"]) : o.not_available?.length ? badge(["partly answered — some evidence unavailable", "b-warn"]) : badge(["answered with available evidence", "b-ok"])}
     ${o.not_available?.length ? `<div class="muted">${o.not_available.map(esc).join("<br>")}</div>` : ""}</li>`).join("")}</ul></section>
  <section class="panel" id="insights-panel"><h2>Insights</h2><div id="ins-body" aria-live="polite"><p class="muted">Loading…</p></div></section>
  <section class="panel"><h2>Map</h2><div class="grid"><div id="rs-map" class="map" style="height:520px"></div><div><div id="rs-layers" class="layerctl"></div><div id="rs-legend"></div>
   <p class="hint">Colours of value kinds: ${Object.keys(KIND).map(kindBadge).join(" ")}</p></div></div></section>
  ${hotspotsPanel(hotspots, context, profile, ms.hotspots, b.recommendations, ms.interventions, rid, p.role !== "viewer")}
  <section class="panel"><h2>Findings (${concerns.length})</h2>
   ${concerns.length ? "" : `<p>No rule-based findings were triggered by the available evidence. This is not proof that the site has no issues — check the analyses that could not run.</p>`}
   ${concerns.map((f) => { const prov = f.threshold?.status === "provisional_not_approved"; return `<div class="panel" id="ev-${esc(f.id)}"><h3><span class="badge sev-${esc(f.severity)}">${esc(f.severity.toUpperCase())}${prov ? " (PROVISIONAL)" : ""}</span> ${prov ? badge([f.threshold.severity_capped ? `Provisional threshold — not approved; capped from ${f.threshold.band_level}` : "Provisional threshold — not approved", "b-warn"]) : ""} ${esc(f.title)}</h3>
     ${f.affected_area ? `<p class="muted">Affected area: <strong>${esc(f.affected_area)}</strong></p>` : ""}<p>${esc(f.summary)}</p><p class="muted">${esc(f.classification.replace(/_/g, " "))} · confidence: ${esc(f.confidence)} · ${esc(f.severity_rule)}</p>
     <details><summary>Evidence, sources and limitations</summary><ul>${(f.evidence || []).map((e) => `<li>${esc(e.label || e.metric || "")}: ${e.value !== undefined ? `${fmt(e.value, 3)} ${esc(e.unit || "")}` : esc(e.reason || "")} ${e.output_kind ? kindBadge(e.output_kind) : ""} <span class="muted">${esc(e.method || e.rule_text || "")}</span></li>`).join("")}</ul>
       <p><strong>Threshold:</strong> ${esc(f.threshold?.justification || JSON.stringify(f.threshold || "") )}</p><ul>${(f.limitations || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
       <p><strong>Sources:</strong> ${(f.sources || []).map((s) => esc(s.text || s.dataset_id || (s.dates || []).join(", ") || s.document || JSON.stringify(s.value || ""))).join("; ") || "—"}</p></details>
     <p><button class="small explain" data-fid="${f.id}">Explain this finding</button></p><div class="expl" id="ex-${f.id}" aria-live="polite"></div>
     ${(recsByFinding[f.id] || []).map((r) => `<div class="alert info"><strong>Recommendation: ${esc(r.title)}</strong> ${badge([r.review_status, r.review_status === "accepted" ? "b-ok" : r.review_status === "proposed" ? "b-info" : "b-mute"])}
       <p>${esc(r.intervention)}</p><p><em>Expected direction (not quantified):</em> ${esc(r.expected_direction)}</p>
       <details><summary>Inputs, trade-offs, how to evaluate</summary><p><strong>Requires:</strong> ${esc(r.required_inputs.join("; ") || "—")}</p><p><strong>Trade-offs:</strong> ${esc(r.tradeoffs.join("; ") || "—")}</p><p><strong>Evaluate:</strong> ${esc(r.evaluation_method)}</p></details>
       ${p.role !== "viewer" ? `<p><button class="small rv" data-rec="${r.id}" data-s="accepted">Acceptable for project</button> <button class="small rv" data-rec="${r.id}" data-s="rejected">Not acceptable</button> <button class="small rv" data-rec="${r.id}" data-s="flagged_incorrect">Flag as incorrect</button></p>` : ""}</div>`).join("")}
   </div>`; }).join("")}</section>
  ${climate ? `<section class="panel"><h2>Climate baseline (${esc(ms.climate_baseline.details.period.start_year)}–${esc(ms.climate_baseline.details.period.end_year)})</h2>
    <p class="hint">${esc(ms.climate_baseline.details.provider_name)} — grid point ${fmt(ms.climate_baseline.details.grid_point.latitude, 3)}, ${fmt(ms.climate_baseline.details.grid_point.longitude, 3)}. ${esc(ms.climate_baseline.details.attribution)}</p>
    ${climateChart(climate)}</section>` : ""}
  <section class="panel"><h2>Metrics by analysis</h2><div class="cards">
   ${Object.entries(ms).map(([mod, s]) => `<div class="panel"><h3>${esc(s.label)} ${condBadge(mod)} ${badge(MOD_STATUS[s.status])} ${run.stale_modules[mod] ? badge(["out of date", "b-warn"]) : ""}</h3>
     ${s.reason ? `<p class="state">${esc(s.reason)}</p>` : ""}${(s.warnings || []).map((w) => `<div class="alert warn">${esc(w)}</div>`).join("")}
     ${(s.provider_failures || []).length ? `<p class="muted">Provider failures: ${s.provider_failures.map((x) => esc(`${x.provider}: ${x.message}`)).join("; ")}</p>` : ""}
     ${(byMod[mod] || []).length ? `<table>${byMod[mod].map((m) => `<tr id="ev-${esc(m.id)}"><td>${esc(m.label)}${modCondition(mod) === "design" && metricCondition(m) === "existing" ? ` <span class="muted">(same for every design)</span>` : ""}<details><summary>method</summary><span class="muted">${esc(m.method)}</span></details></td><td>${metricValue(m)}</td><td>${kindBadge(m.output_kind)}</td></tr>`).join("")}</table>` : ""}
     ${(s.limitations || []).length ? `<details><summary>Limitations</summary><ul>${s.limitations.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
     ${s.details?.accepted_scenes ? `<details><summary>Satellite scenes used (${s.details.accepted_scenes.length}) / excluded (${s.details.excluded_scenes.length})</summary><ul>${s.details.accepted_scenes.map((x) => `<li class="mono">✓ ${esc(x.scene_id)} ${esc(x.datetime.slice(0, 10))} cloud ${fmt(x.cloud_cover_pct, 1)}%, site valid ${fmt(x.valid_site_fraction * 100, 0)}%</li>`).join("")}${s.details.excluded_scenes.map((x) => `<li class="mono muted">✗ ${esc(x.scene_id)} ${esc(x.datetime.slice(0, 10))}: ${esc(x.reason)}</li>`).join("")}</ul></details>` : ""}
   </div>`).join("")}</div></section>
  ${dq.length ? `<section class="panel"><h2>Data-quality issues</h2><ul>${dq.map((f) => `<li><strong>${esc(f.title)}</strong> — ${esc(f.summary)}</li>`).join("")}</ul></section>` : ""}
  <section class="panel"><h2>Ask about this result</h2><form id="f-ask" class="row"><div style="flex:1"><label for="ask-q">Question</label><input id="ask-q" type="text" placeholder="e.g. Where is vegetation limited? What is LST?"></div><div><button type="submit">Ask</button></div></form>
   <div id="ask-out" aria-live="polite"></div><p class="hint">Answers are assembled only from this run's stored results — no generative AI is used, and nothing is invented.</p></section>
  <section class="panel"><h2>Datasets and provenance</h2><table><thead><tr><th>Dataset</th><th>Source</th><th>Nature</th><th>Coverage</th><th>Licence / attribution</th><th>Source status</th></tr></thead><tbody>
   ${b.datasets.filter(Boolean).map((d) => `<tr><td class="mono">${esc(d.id)}<div>${esc(d.dataset_type)}</div></td><td>${esc(d.source_id)}<div class="muted">${esc(d.spatial_resolution || "")}</div></td><td>${esc(d.data_nature)}</td>
     <td class="mono">${esc(d.time_coverage?.start ? `${d.time_coverage.start} – ${d.time_coverage.end}` : (d.time_coverage?.scenes || []).map((x) => x.slice(0, 10)).join(", ") || d.time_coverage?.osm_snapshot || "")}</td><td>${esc(d.license || "")}<div class="muted">${esc(d.attribution || "")}</div></td><td>${badge([d.approval_label || "unknown", (["approved", "fr_recommended"].includes(d.approval_status) && d.terms_confirmed) || d.approval_status === "fr_required" ? "b-ok" : "b-warn"])}<div class="muted">usage: ${esc(d.usage_context || "")}</div></td></tr>`).join("") || `<tr><td colspan="6">No external datasets in this run.</td></tr>`}</tbody></table>
   <details><summary>Run configuration and fingerprint</summary><pre class="mono">${esc(JSON.stringify({ configuration: run.configuration, fingerprint: run.fingerprint, analysis_version: run.analysis_version, software_version: run.software_version }, null, 1))}</pre></details></section>`;
  document.getElementById("rs-run").onchange = (e) => (location.hash = `#/p/${pid}/results/${e.target.value}`);
  renderInsights(rid, p);
  const pkgBtn = document.getElementById("pkg-draft");
  if (pkgBtn) pkgBtn.onclick = () => guarded(async () => { const s = await api(`/analysis-runs/${pkgBtn.dataset.rid}/package/draft`, { method: "POST" }); location.hash = `#/p/${pid}/scenarios/${s.id}`; }, pkgBtn);
  // map with toggleable layers
  const map = makeMap("rs-map", p.latitude, p.longitude, 16);
  const overlays = [];
  if (p.boundary) overlays.push(["Site boundary (user-provided)", L.geoJSON(p.boundary, { style: { color: "#c62828", weight: 3, fillOpacity: 0 } }), true, null]);
  if (scen) overlays.push([`Design: ${scen.name} (user-provided)`, L.geoJSON(scen.features, { style: featureStyle, pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 4 }), onEachFeature: (f, l) => l.bindPopup(popupFor(f.properties)) }), true, typeLegend()]);
  if (hotspots.some((h) => h.geometry)) {
    const hsLayer = L.geoJSON({ type: "FeatureCollection", features: hotspots.filter((h) => h.geometry).map((h) => ({ type: "Feature", geometry: h.geometry, properties: h })) }, {
      style: (f) => ({ color: "#5a0000", weight: 1.5, fillColor: SEV_COLORS[f.properties.severity] || "#999", fillOpacity: 0.45 }),
      onEachFeature: (f, l) => { const h = f.properties;
        l.bindTooltip(String(h.number), { permanent: true, direction: "center", className: "hs-num" });
        l.bindPopup(`<strong>Hotspot ${h.number}: ${esc(h.type_labels.join(" + "))}</strong><br>${fmt(h.area_m2, 0)} m² · ${esc(h.size_class)} · severity ${esc(h.severity)}<br>${h.likely_causes.map((c) => esc(c.text)).join("<br>")}`); } });
    overlays.push(["Design hotspots (numbered, calculated)", hsLayer, true, `<strong>Design hotspots</strong>${Object.entries(SEV_COLORS).map(([k, c]) => `<div><i style="background:${c}"></i>${esc(k)} severity</div>`).join("")}`]);
  }
  for (const lay of b.layers) {
    if (lay.module === "hotspots") continue;  // drawn above from the hotspot records, with numbers
    if (lay.layer_kind === "raster") {
      const url = URL.createObjectURL(await (await api(`/layers/${lay.id}/data`, { raw: true })).blob());
      const bb = lay.bounds;
      const leg = lay.legend?.type === "categorical" ? lay.legend.classes.map((c) => `<div><i style="background:${c.color}"></i>${esc(c.label)}</div>`).join("") + `<div class="muted">Breaks (°C): P25 ${fmt(lay.legend.breaks_c.p25)}, P75 ${fmt(lay.legend.breaks_c.p75)}, P90 ${fmt(lay.legend.breaks_c.p90)}</div>`
        : `<div>${esc(lay.legend.min)} ${esc(lay.legend.unit)} → ${esc(lay.legend.max)} ${esc(lay.legend.unit)} (${esc(lay.legend.cmap)})</div>`;
      overlays.push([`${lay.name} (${KIND[lay.output_kind]})`, L.imageOverlay(url, [[bb.south, bb.west], [bb.north, bb.east]], { opacity: 0.75 }), false, `<strong>${esc(lay.name)}</strong>${leg}`]);
    } else {
      const fc = await (await api(`/layers/${lay.id}/data`, { raw: true })).json();
      const isShadow = lay.module === "solar_shade", isCluster = lay.module === "surface_heat";
      overlays.push([`${lay.name} (${KIND[lay.output_kind]})`, L.geoJSON(fc, {
        style: (f) => isShadow ? { color: "#333", weight: 0.5, fillColor: "#000", fillOpacity: f.properties.solar_time === "15:00" ? 0.35 : 0.15 }
          : isCluster ? { color: "#b2182b", weight: 2, dashArray: "6 4", fillColor: "#b2182b", fillOpacity: 0.08 } : { color: FCOLORS[f.properties.feature_type] || "#666", weight: 1, fillOpacity: 0.25 },
        onEachFeature: (f, l) => l.bindPopup(popupFor(f.properties)) }), false,
        isShadow ? "<strong>Shade</strong><div>Darker = 15:00, lighter = 09:00/12:00 (solar time)</div>" : `<strong>${esc(lay.name)}</strong><div class="muted">${esc(lay.meta?.attribution || "")}</div>`]);
    }
  }
  const hot = b.findings.find((f) => f.rule_id === "HEAT-LST-02" && f.geometry);
  if (hot) overlays.push(["Finding: hottest-decile surfaces (classified)", L.geoJSON(hot.geometry, { style: { color: "#d7191c", weight: 2, fillOpacity: 0.2, dashArray: "4 3" } }), true, null]);
  const ctl = document.getElementById("rs-layers");
  const legendBox = document.getElementById("rs-legend");
  const refreshLegend = () => { legendBox.innerHTML = overlays.filter((o) => map.hasLayer(o[1]) && o[3]).map((o) => `<div class="legend panel">${o[3]}</div>`).join(""); };
  overlays.forEach(([name, layer, on], i) => {
    if (on) layer.addTo(map);
    const id = `lay-${i}`;
    const opacity = layer.setOpacity ? `<input type="range" min="0" max="1" step="0.05" value="0.75" aria-label="Opacity of ${esc(name)}" data-i="${i}">` : "";
    ctl.insertAdjacentHTML("beforeend", `<label for="${id}"><input type="checkbox" id="${id}" data-i="${i}" ${on ? "checked" : ""}> ${esc(name)} ${opacity}</label>`);
  });
  ctl.querySelectorAll("input[type=checkbox]").forEach((cb) => (cb.onchange = () => { const l = overlays[cb.dataset.i][1]; cb.checked ? l.addTo(map) : map.removeLayer(l); refreshLegend(); }));
  ctl.querySelectorAll("input[type=range]").forEach((r) => (r.oninput = () => overlays[r.dataset.i][1].setOpacity(+r.value)));
  if (p.boundary) map.fitBounds(L.geoJSON(p.boundary).getBounds(), { padding: [40, 40] });
  refreshLegend();
  el.querySelectorAll(".explain").forEach((btn) => (btn.onclick = () => guarded(async () => {
    const r = await api(`/findings/${btn.dataset.fid}/explanation`);
    document.getElementById(`ex-${btn.dataset.fid}`).innerHTML = `<div class="alert info">${esc(r.text)}<div class="muted">Generated by: ${esc(r.generator)}. Based on: ${r.based_on.map(short).join(", ")}</div></div>`;
  }, btn)));
  el.querySelectorAll(".rv").forEach((btn) => (btn.onclick = () => guarded(async () => {
    const comment = btn.dataset.s === "flagged_incorrect" ? prompt("What is incorrect? (optional)") || "" : "";
    await api(`/recommendations/${btn.dataset.rec}/review`, { body: { status: btn.dataset.s, comment } }); notify("Feedback recorded. It does not change results.", "ok"); route();
  }, btn)));
  document.getElementById("f-ask").onsubmit = (e) => { e.preventDefault(); guarded(async () => {
    const r = await api(`/analysis-runs/${rid}/ask`, { body: { question: document.getElementById("ask-q").value } });
    document.getElementById("ask-out").innerHTML = `<div class="alert ${r.sufficient_evidence ? "info" : "warn"}">${esc(r.answer)}</div>`;
  }); };
}

function climateChart(s) {
  const tx = s.monthly_climatology.tmax_c || [], tn = s.monthly_climatology.tmin_c || [];
  const vals = [...tx, ...tn].filter((v) => v !== null);
  if (!vals.length) return "";
  const lo = Math.floor(Math.min(...vals) / 5) * 5, hi = Math.ceil(Math.max(...vals) / 5) * 5;
  const W = 640, H = 220, pl = 40, pb = 24, pt = 10;
  const x = (i) => pl + (i + 0.5) * ((W - pl - 10) / 12), y = (v) => pt + (H - pt - pb) * (1 - (v - lo) / (hi - lo || 1));
  const line = (arr, col) => `<polyline fill="none" stroke="${col}" stroke-width="2" points="${arr.map((v, i) => (v === null ? "" : `${x(i)},${y(v)}`)).join(" ")}"/>` + arr.map((v, i) => (v === null ? "" : `<circle cx="${x(i)}" cy="${y(v)}" r="3" fill="${col}"><title>${v} °C</title></circle>`)).join("");
  const months = "JFMAMJJASOND".split("");
  const grid = []; for (let v = lo; v <= hi; v += 5) grid.push(`<line x1="${pl}" x2="${W - 10}" y1="${y(v)}" y2="${y(v)}" stroke="#e3e7ec"/><text x="${pl - 6}" y="${y(v) + 4}" font-size="11" text-anchor="end">${v}</text>`);
  const hot = new Set(s.hottest_months);
  const shade = months.map((m, i) => (hot.has(i + 1) ? `<rect x="${x(i) - 22}" y="${pt}" width="44" height="${H - pt - pb}" fill="#fde8e8"/>` : "")).join("");
  const idx = s.indices.map((ix) => `<tr><td>${esc(ix.label)}</td><td>${ix.mean_per_year === null ? '<span class="state">unknown</span>' : `${fmt(ix.mean_per_year, 1)} days/yr`}</td><td>${badge([ix.status === "standard" ? "standard index" : "provisional — not approved", ix.status === "standard" ? "b-ok" : "b-warn"])}</td></tr>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Monthly mean daily maximum and minimum air temperature" style="width:100%;max-width:${W}px">${shade}${grid.join("")}
    ${months.map((m, i) => `<text x="${x(i)}" y="${H - 6}" font-size="11" text-anchor="middle">${m}</text>`).join("")}${line(tx, "#d7191c")}${line(tn, "#2c7bb6")}
    <text x="${pl}" y="${pt + 10}" font-size="11">°C — red: mean daily max, blue: mean daily min; shaded: hottest 3 months</text></svg>
    <table><tr><th>Indicator</th><th>Mean</th><th>Rule</th></tr>${idx}<tr><td>P90 / P95 daily max</td><td>${fmt(s.tmax_p90_c)} / ${fmt(s.tmax_p95_c)} °C</td><td></td></tr></table>`;
}

async function tabCompare(el, data) {
  const p = data.project, pid = p.id, canEdit = p.role !== "viewer";
  const scen = (await api(`/projects/${pid}/scenarios`)).filter((s) => s.status === "active");
  const base = scen.find((s) => s.kind === "baseline");
  const alts = scen.filter((s) => s.kind === "alternative");
  const crit = ["green_space_pct", "hard_surface_pct", "solar_open_space_exposed_pct_1500", "solar_open_space_shade_hours", "open_space_pct"];
  el.innerHTML = `<section class="panel"><h2>Compare the original design with alternatives</h2>
   ${!base || !alts.length ? `<div class="alert warn">You need the baseline and at least one alternative (Scenarios tab), each analysed with the same settings ("Run all active scenarios").</div>` : ""}
   ${base && alts.length && canEdit ? `<form id="f-cmp"><p>Reference: <strong>${esc(base.name)}</strong> ${base.latest_run ? badge(RUN_STATUS[base.latest_run.status]) : badge(["not analysed", "b-bad"])}</p>
     <fieldset><legend>Alternatives</legend>${alts.map((a) => `<label class="check"><input type="checkbox" name="alt" value="${a.id}" checked> ${esc(a.name)} ${a.latest_run ? badge(RUN_STATUS[a.latest_run.status]) : badge(["not analysed", "b-bad"])}</label>`).join("")}</fieldset>
     <details><summary>Optional: weighted scoring (transparent multi-criteria)</summary><p class="hint">Leave all weights at 0 for a plain comparison. Scores are relative to the compared scenarios and depend on your weights.</p>
     ${crit.map((c) => `<div class="row"><label for="w-${c}" style="flex:1;font-weight:400">${esc(c)}</label><input id="w-${c}" data-c="${c}" type="number" min="0" step="0.5" value="0" style="width:90px"></div>`).join("")}</details>
     <p><button class="primary" type="submit">Compare</button></p></form>` : ""}
   <div id="cmp-out" aria-live="polite"></div></section>`;
  const f = document.getElementById("f-cmp");
  if (f) f.onsubmit = (e) => { e.preventDefault(); guarded(async () => {
    const criteria = [...document.querySelectorAll("input[data-c]")].filter((i) => +i.value > 0).map((i) => ({ metric: i.dataset.c, weight: +i.value }));
    const c = await api(`/projects/${pid}/scenario-comparisons`, { body: { baseline_id: base.id, alternative_ids: [...document.querySelectorAll("input[name=alt]:checked")].map((x) => x.value), criteria: criteria.length ? criteria : null } });
    renderComparison(document.getElementById("cmp-out"), c, pid);
  }); };
}

function renderComparison(box, c, pid) {
  const r = c.result, names = c.scenario_names;
  if (!r.comparable) { box.innerHTML = `<div class="alert bad"><strong>These results are not comparable.</strong><ul>${r.issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul></div>`; return; }
  const OUT = { improvement: ["Improvement", "b-ok"], regression: ["Regression", "b-bad"], unchanged: ["Unchanged", "b-mute"], changed: ["Changed (no preferred direction)", "b-info"], not_comparable: ["Not comparable", "b-warn"] };
  const v = (x) => (x.state === "available" ? fmt(x.value, 3) : `<span class="state">${esc(x.state)}</span>`);
  box.innerHTML = `<div class="alert info">${esc(r.statement)} ${esc(r.direction_note)}</div>
   <table><thead><tr><th>Alternative</th><th>Indicator</th><th>Kind</th><th>Baseline</th><th>Alternative</th><th>Difference</th><th>Outcome</th></tr></thead><tbody>
   ${r.rows.filter((x) => x.outcome !== "unchanged").map((x) => `<tr><td>${esc(names[x.alternative_id])}</td><td>${esc(x.label)} <span class="muted">${esc(x.unit || "")}</span></td><td>${kindBadge(x.output_kind)}</td><td>${v(x.baseline)}</td><td>${v(x.alternative)}</td>
     <td>${x.difference !== undefined ? `${x.difference > 0 ? "+" : ""}${fmt(x.difference, 3)}${x.difference_pct !== null && x.difference_pct !== undefined ? ` (${x.difference_pct > 0 ? "+" : ""}${fmt(x.difference_pct, 1)}%)` : ""}` : "—"}</td>
     <td>${badge(OUT[x.outcome])}${x.reason ? `<div class="muted">${esc(x.reason)}</div>` : ""}</td></tr>`).join("")}</tbody></table>
   <details><summary>Unchanged indicators (${r.rows.filter((x) => x.outcome === "unchanged").length})</summary><ul>${r.rows.filter((x) => x.outcome === "unchanged").map((x) => `<li>${esc(x.label)}: ${v(x.baseline)} ${esc(x.unit || "")}</li>`).join("")}</ul></details>
   <h3>Planning checks</h3>${r.planning.length ? `<table><tr><th>Alternative</th><th>Rule</th><th>Baseline</th><th>Alternative</th></tr>${r.planning.map((x) => `<tr><td>${esc(names[x.alternative_id])}</td><td>${esc(x.rule)} (limit ${esc(x.limit)})</td><td>${esc(x.baseline)} ${x.baseline_measured !== undefined && x.baseline_measured !== null ? `(${fmt(x.baseline_measured)})` : ""}</td><td>${esc(x.alternative)} ${x.alternative_measured !== undefined && x.alternative_measured !== null ? `(${fmt(x.alternative_measured)})` : ""}</td></tr>`).join("")}</table>` : "<p>Not assessed (no verified rules or planning module not run).</p>"}
   <h3>Findings by severity</h3><table><tr><th>Scenario</th><th>High</th><th>Moderate</th><th>Low</th></tr>${Object.entries(r.findings_by_severity).map(([sid, s]) => `<tr><td>${esc(names[sid])}</td><td>${s.high}</td><td>${s.moderate}</td><td>${s.low}</td></tr>`).join("")}</table>
   <h3>Indicative costs</h3>${Object.entries(r.costs).map(([sid, cst]) => `<p><strong>${esc(names[sid])}</strong> — ${esc(cst.label)} Status: ${badge([cst.status.replace(/_/g, " "), cst.status === "complete" ? "b-ok" : "b-warn"])}</p>
     <table><tr><th>Item</th><th>Quantity</th><th>Rate</th><th>Subtotal</th><th>Source</th></tr>${cst.lines.map((l) => `<tr><td>${esc(l.label)}</td><td>${fmt(l.quantity, 1)} ${esc(l.unit)}</td><td>${l.rate !== undefined ? `${fmt(l.rate)} ${esc(l.currency)}` : '<span class="state">price required</span>'}</td><td>${l.subtotal !== undefined ? `${fmt(l.subtotal)} ${esc(l.currency)}` : "—"}</td><td>${esc(l.rate_source || l.note)}</td></tr>`).join("") || `<tr><td colspan="5">No quantity differences.</td></tr>`}</table>
     <p>Totals: ${Object.entries(cst.totals_by_currency).map(([k, v2]) => `${fmt(v2)} ${esc(k)}`).join(", ") || "none"}. ${cst.warnings.map(esc).join(" ")}</p><p class="muted">Excluded: ${cst.exclusions.map(esc).join("; ")}</p>`).join("")}
   <h3>Site conditions (same for all scenarios)</h3><p class="muted">${esc(r.site_conditions_note)}</p>
   ${r.ranking ? `<h3>Weighted scoring</h3><p class="hint">${esc(r.ranking.method)} ${esc(r.ranking.caveat)}</p>${r.ranking.small_set_warning ? `<div class="alert warn">${esc(r.ranking.small_set_warning)}</div>` : ""}<table><tr><th>Scenario</th><th>Score</th><th>Weight coverage</th>${r.ranking.criteria.map((cr) => `<th>${esc(cr.metric)} (w=${cr.weight})</th>`).join("")}</tr>
     ${Object.entries(r.ranking.aggregate).map(([sid, a]) => `<tr><td>${esc(a.name)}</td><td>${a.score === null ? "—" : fmt(a.score, 3)}</td><td>${fmt(a.weight_coverage * 100, 0)}%</td>${r.ranking.criteria.map((cr) => `<td>${cr.scores[sid] === undefined ? '<span class="state">missing</span>' : `${fmt(cr.scores[sid], 2)}${cr.values ? `<div class="muted">value ${fmt(cr.values[sid], 2)} ${esc(cr.unit || "")}${cr.difference_vs_baseline?.[sid] ? `; ${cr.difference_vs_baseline[sid] > 0 ? "+" : ""}${fmt(cr.difference_vs_baseline[sid], 2)} vs baseline` : ""}</div>` : ""}`}</td>`).join("")}</tr>`).join("")}</table>${r.ranking.notes.map((n) => `<div class="alert warn">${esc(n)}</div>`).join("")}` : ""}
   <p class="hint">Comparison saved. Include it in a report on the Report tab (choose the baseline or alternative run used here).</p>`;
}

async function tabReport(el, data) {
  const p = data.project, pid = p.id, canEdit = p.role !== "viewer";
  const [runs, reps, scen, cmps] = await Promise.all([api(`/projects/${pid}/analysis-runs`), api(`/projects/${pid}/reports`), api(`/projects/${pid}/scenarios`), api(`/projects/${pid}/scenario-comparisons`)]);
  const done = runs.filter((r) => !["queued", "running"].includes(r.status));
  const names = Object.fromEntries(scen.map((s) => [s.id, s.name]));
  el.innerHTML = `${canEdit ? `<section class="panel"><h2>Generate a report</h2>
   ${done.length ? `<form id="f-rep" class="row"><div style="flex:1"><label for="rp-run">Analysis run</label><select id="rp-run">${done.map((r) => `<option value="${r.id}">${esc(names[r.scenario_id] || "site only")} — ${esc(r.completed_at)} (${esc(RUN_STATUS[r.status][0])})</option>`).join("")}</select></div>
     <div style="flex:1"><label for="rp-cmp">Include a scenario comparison <span class="hint">optional; only comparisons made from the selected run are accepted</span></label><select id="rp-cmp"><option value="">None</option>${cmps.filter((c) => c.comparable).map((c) => `<option value="${c.id}" data-runs="${esc(Object.values(c.run_ids).join(" "))}">${esc(c.label)} — ${esc(c.created_at)}</option>`).join("")}</select></div><div><button class="primary" type="submit">Generate PDF + HTML report</button></div></form>
     <p class="hint">A report is labelled <em>partial</em> whenever any analysis was limited, blocked, unavailable or out of date.</p>` : "<p>Run an analysis first.</p>"}</section>` : ""}
   <section class="panel"><h2>Reports</h2><table><thead><tr><th>Report</th><th>Run</th><th>Status</th><th>Created</th><th>Download</th></tr></thead><tbody>
   ${reps.map((r) => `<tr><td class="mono">${esc(r.id)}</td><td class="mono">${esc(r.run_id)}</td><td>${badge([r.status, r.status === "complete" ? "b-ok" : "b-warn"])}<div class="muted">${esc(r.summary?.label || "")}</div></td><td>${esc(r.created_at)}</td>
     <td><button class="small dl" data-id="${r.id}" data-f="pdf">PDF</button> <button class="small dl" data-id="${r.id}" data-f="html">HTML</button> <button class="small ex" data-run="${r.run_id}">Data export (ZIP)</button></td></tr>`).join("") || `<tr><td colspan="5">No reports yet.</td></tr>`}
   </tbody></table><p class="hint">The data export contains metrics (CSV, JSON), design and findings (GeoJSON, EPSG:4326), satellite composites (GeoTIFF) and full provenance.</p></section>`;
  const f = document.getElementById("f-rep");
  if (f) f.onsubmit = (e) => { e.preventDefault(); guarded(async () => {
    const cmp = document.getElementById("rp-cmp").value;
    const r = await api(`/analysis-runs/${document.getElementById("rp-run").value}/reports`, { body: cmp ? { comparison_id: cmp } : {} });
    notify(`Report generated (${r.status}).`, r.status === "complete" ? "ok" : "warn"); route();
  }, f.querySelector("button")); };
  el.querySelectorAll(".dl").forEach((b) => (b.onclick = () => guarded(() => download(`/reports/${b.dataset.id}/download?format=${b.dataset.f}`, `urbanova_${b.dataset.id}.${b.dataset.f}`), b)));
  el.querySelectorAll(".ex").forEach((b) => (b.onclick = () => guarded(() => download(`/analysis-runs/${b.dataset.run}/export`, `urbanova_${b.dataset.run}.zip`), b)));
}

// ------------------------------------------------------------------ router
let loadingTimer = null;

async function route() {
  const parts = (location.hash || "#/projects").slice(2).split("/");
  try {
    if (!state.meta) state.meta = await api("/meta");
    if (!state.token) return viewLogin();
    if (!state.user) { state.user = await api("/auth/me"); renderSession(); }
    clearMaps();
    // Show "Loading…" only if the view takes a moment and has not rendered yet, so quick refreshes don't flash.
    const before = $main.firstElementChild;
    loadingTimer = setTimeout(() => { if ($main.firstElementChild === before) $main.innerHTML = LOADING; }, 250);
    if (parts[0] === "p" && parts[1]) return await viewProject(parts[1], parts[2] || "overview", parts[3]);
    if (parts[0] === "login") return viewLogin();
    return await viewProjects();
  } catch (e) {
    $main.innerHTML = `<div class="alert bad" role="alert">${esc(e.message)}</div><p><a href="#/projects">Back to projects</a></p>`;
  } finally { clearTimeout(loadingTimer); }
}
window.addEventListener("hashchange", route);
route();
