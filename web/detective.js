/* Trend Detective (NASA Space Apps 2026): pick a question and a place, get suggested NASA variables, test their trend.
   Uses app.js globals: api(), esc(), fmt(), state, makeMap(), clearMaps(), notify(), guarded(). Public: no account needed. */
"use strict";

const det = { cat: null, interest: "heat", place: "city", lat: 30.03, lon: 31.47, where: "New Cairo, Egypt", results: [], map: null, marker: null, demo: false };
const LEVEL = { significant: ["Significant trend", "b-ok", "🟢"], borderline: ["Likely trend — tests disagree", "b-warn", "🟡"], not_significant: ["Not significant (yet)", "b-mute", "⚪"] };

async function viewDetective() {
  if (!det.cat) det.cat = await api("/detective/catalog");
  const C = det.cat;
  clearMaps();
  $main.innerHTML = `
  <section class="panel det-hero"><h1>Trend Detective</h1>
    <p class="lead">Is the Earth changing where you care about? Pick a question and a place, and test NASA data for a <strong>trend</strong> — and whether it is <strong>significant</strong>.</p>
    <p class="hint">Data: ${esc(C.source.name)}, ${esc(C.source.period)}. ${esc(C.source.resolution)}</p></section>
  <section class="panel"><h2>Watch a demo</h2><p class="hint">Each demo answers a real question with ${C.last_year - 1981 + 1} years of NASA data. Click one to watch the detective work step by step.</p>
    <div class="det-demos">${C.regions.map((r) => `<button class="det-demo" data-r="${esc(r.id)}"><strong>${esc(r.name)}</strong><span>${esc(r.story)}</span></button>`).join("")}</div></section>
  <section class="panel" id="det-q"><h2>1 · Your question</h2>
    <div class="det-steps">
      <div><h3>What are you curious about?</h3><div class="det-chips" id="det-int">${Object.entries(C.interests).map(([k, v]) => `<button data-v="${esc(k)}" class="chip-btn ${k === det.interest ? "on" : ""}">${esc(v)}</button>`).join("")}</div></div>
      <div><h3>What kind of place?</h3><div class="det-chips" id="det-place">${Object.entries(C.place_types).map(([k, v]) => `<button data-v="${esc(k)}" class="chip-btn ${k === det.place ? "on" : ""}">${esc(v)}</button>`).join("")}</div></div>
      <div><h3>Where? <span class="hint">click the map</span></h3><div id="det-map" class="map det-map" aria-label="Map: click to choose the place"></div>
        <p id="det-where" class="hint"></p>
        <label for="det-name">Name of the place (optional)</label><input id="det-name" type="text" maxlength="120" value="${esc(det.where)}"></div>
    </div>
    <p><button class="primary" id="det-find">Find variables to investigate</button></p></section>
  <section class="panel" id="det-vars" hidden><h2>2 · Variables to investigate</h2><p class="hint" id="det-by"></p><div class="det-cards" id="det-cards"></div></section>
  <section id="det-results"></section>
  <section class="panel"><h2>How the detective decides</h2>
    <details><summary>What is a trend, and what does "significant" mean?</summary>
      <p>A <strong>trend</strong> is a steady change in one direction over many years. Temperatures and rain jump around from month to month, so we first remove the normal <strong>seasonal cycle</strong> (summer is always warmer than winter) and look at what is left: the <em>anomalies</em>.</p>
      <p>A trend is <strong>significant</strong> when it is very unlikely to be just chance. The <strong>p-value</strong> is the chance of seeing a trend at least this strong if nothing were really changing. Below 0.05 (5 %), scientists call it significant.</p>
      <p>We use two tests: the <strong>t-test</strong> on a straight-line fit (least squares), and the <strong>Mann-Kendall test</strong>, which only asks whether later values tend to be higher (or lower) than earlier ones, so a few extreme months can't fool it. The rate shown is <strong>Sen's slope</strong>: the median of the slopes between every pair of months.</p>
      <p>The <strong>95 % confidence interval</strong> is the range the true rate probably lies in. If it doesn't include zero, the trend is significant.</p>
      <p>When the tests <strong>disagree</strong>, the trend is "likely" but not proven. <strong>Not significant yet</strong> doesn't mean "no change": a longer record often makes a real trend significant.</p></details>
    <details><summary>Limits of this data</summary><p>${esc(C.source.resolution)} Values come from NASA models that combine satellite and ground observations (MERRA-2 reanalysis, CERES, GPCP/GPM rainfall): excellent for regional trends, not for a single street or field.</p></details>
    <p class="hint">${esc(C.source.attribution)}.</p></section>`;
  det.map = makeMap("det-map", det.lat, det.lon, 4);
  setPoint(det.lat, det.lon);
  det.map.on("click", (e) => { setPoint(e.latlng.lat, e.latlng.lng); document.getElementById("det-name").value = ""; });
  const pick = (id, key) => document.getElementById(id).querySelectorAll("button").forEach((b) => (b.onclick = () => {
    det[key] = b.dataset.v;
    document.getElementById(id).querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
  }));
  pick("det-int", "interest"); pick("det-place", "place");
  document.getElementById("det-find").onclick = (e) => guarded(() => findVariables(), e.target);
  document.querySelectorAll(".det-demo").forEach((b) => (b.onclick = () => guarded(() => runDemo(b.dataset.r), b)));
  renderResults();
}

function setPoint(lat, lon) {
  det.lat = +(+lat).toFixed(2); det.lon = +(+lon).toFixed(2);
  if (det.marker) det.marker.setLatLng([det.lat, det.lon]); else det.marker = L.marker([det.lat, det.lon]).addTo(det.map);
  const el = document.getElementById("det-where");
  if (el) el.textContent = `Point ${det.lat}, ${det.lon} — one NASA POWER cell of about 55 km around it.`;
}

async function findVariables() {
  det.where = document.getElementById("det-name").value.trim();
  const s = await api("/detective/suggest", { body: { place: det.place, interest: det.interest, where: det.where } });
  const byId = Object.fromEntries(det.cat.variables.map((v) => [v.id, v]));
  document.getElementById("det-vars").hidden = false;
  document.getElementById("det-by").textContent = s.label + (s.by === "ai" ? " — a starting point; the test below decides." : ".");
  document.getElementById("det-cards").innerHTML = s.suggestions.map((x) => { const v = byId[x.id];
    return `<article class="det-card"><h3>${esc(v.name)} <span class="hint">${esc(v.unit)}</span></h3><p>${esc(v.definition)}</p>
      <p class="hint"><strong>Why it might change here:</strong> ${esc(x.reason)}</p><p class="hint">Source: ${esc(v.model)}</p>
      <button class="primary det-test" data-v="${esc(v.id)}">Test for a trend</button></article>`; }).join("")
    + `<details class="det-more"><summary>Other variables</summary><div class="det-chips">${det.cat.variables.filter((v) => !s.suggestions.some((x) => x.id === v.id))
        .map((v) => `<button class="chip-btn det-test" data-v="${esc(v.id)}">${esc(v.name)}</button>`).join("")}</div></details>`;
  document.querySelectorAll(".det-test").forEach((b) => (b.onclick = () => guarded(() => testTrend(b.dataset.v), b)));
  document.getElementById("det-vars").scrollIntoView({ behavior: det.demo ? "smooth" : "auto", block: "start" });
}

async function testTrend(varId, opts = {}) {
  const body = { lat: det.lat, lon: det.lon, variable: varId, start_year: opts.start || 1981, end_year: opts.end || det.cat.last_year, deseasonalise: opts.deseason !== false };
  const r = await api("/detective/trend", { body });
  r.where = det.where || `${det.lat}, ${det.lon}`;
  r.key = Math.random().toString(36).slice(2, 8);
  if (opts.replace) det.results = det.results.map((x) => (x.key === opts.replace ? r : x)); else det.results = [r, ...det.results].slice(0, 3);
  renderResults();
  document.getElementById(`res-${r.key}`)?.scrollIntoView({ behavior: det.demo ? "smooth" : "auto", block: "start" });
  return r;
}

function renderResults() {
  const box = document.getElementById("det-results");
  if (!box) return;
  box.innerHTML = det.results.length ? `<h2 class="det-h">3 · Results ${det.results.length > 1 ? `<span class="hint">(${det.results.length} side by side — test another variable or place to compare)</span>` : ""}</h2>
    <div class="det-results ${det.results.length > 1 ? "multi" : ""}">${det.results.map(resultHtml).join("")}</div>` : "";
  det.results.forEach((r) => {
    const el = document.getElementById(`res-${r.key}`);
    el.querySelector(".det-raw").onchange = (e) => guarded(() => { setPointQuiet(r); return testTrend(r.variable.id, { deseason: !e.target.checked, start: r.request.start_year, end: r.request.end_year, replace: r.key }); });
    el.querySelector(".det-period").onsubmit = (e) => { e.preventDefault(); const f = e.target; guarded(() => { setPointQuiet(r);
      return testTrend(r.variable.id, { start: +f.start.value, end: +f.end.value, deseason: r.request.deseasonalised, replace: r.key }); }); };
    el.querySelector(".det-close").onclick = () => { det.results = det.results.filter((x) => x.key !== r.key); renderResults(); };
    el.querySelector(".det-csv").onclick = () => downloadCsv(r);
    const zoom = el.querySelector(".det-zoom"); if (zoom) zoom.onclick = () => guarded(() => zoomToPlot(r), zoom);
  });
}

function setPointQuiet(r) { det.lat = r.point.lat; det.lon = r.point.lon; det.where = r.where; }

function resultHtml(r) {
  const v = r.variable, R = r.result;
  if (!R.ok) return `<article class="panel det-result" id="res-${r.key}"><h3>${esc(v.name)} — ${esc(r.where)}</h3><p>${esc(R.reason)}</p>${controls(r)}</article>`;
  const L_ = LEVEL[R.verdict.level];
  const rate = R.rate.per_decade, sen = R.sen, ols = R.ols, mk = R.mann_kendall;
  const p = (x) => (x < 0.001 ? "< 0.001" : fmt(x, 3));
  return `<article class="panel det-result" id="res-${r.key}">
    <div class="row det-head"><h3>${esc(v.name)} — ${esc(r.where)}</h3><button class="small det-close" aria-label="Remove this result">✕</button></div>
    <div class="det-verdict ${esc(R.verdict.level)}"><span class="badge ${L_[1]}">${L_[2]} ${esc(L_[0])}</span>
      <p class="det-headline">${esc(R.verdict.headline)}</p><p>${esc(R.verdict.why)}</p><p>${esc(R.verdict.total)}</p></div>
    ${chartSvg(R, v)}
    <p class="hint">${R.deseasonalised ? "Monthly anomalies (the usual value for each calendar month removed)" : "Monthly values (seasonal cycle kept)"} · dots: yearly means · line: trend with its 95 % confidence band.</p>
    <table class="det-stats"><thead><tr><th>Test</th><th>Rate per decade</th><th>95 % interval</th><th>p-value</th><th>Significant?</th></tr></thead><tbody>
      <tr><td>Mann-Kendall + Sen's slope</td><td>${fmt(sen.per_decade.slope, 4)} ${esc(v.unit)}</td><td>${fmt(sen.per_decade.ci_low, 4)} to ${fmt(sen.per_decade.ci_high, 4)}</td><td>${p(mk.p_value)}</td><td>${mk.significant ? "Yes" : "No"}</td></tr>
      <tr><td>Least squares + t-test</td><td>${fmt(ols.per_decade.slope, 4)} ${esc(v.unit)}</td><td>${fmt(ols.per_decade.ci_low, 4)} to ${fmt(ols.per_decade.ci_high, 4)}</td><td>${p(ols.p_value)}</td><td>${ols.significant ? "Yes" : "No"}</td></tr>
    </tbody></table>
    <p class="hint">Mann-Kendall S = ${mk.S}, z = ${fmt(mk.z, 2)}, Kendall's τ = ${fmt(mk.tau, 3)} · R² = ${fmt(ols.r2, 3)} · ${R.period.months} months, ${esc(R.period.start)} to ${esc(R.period.end)}${R.rate.method === "ols" ? " · headline rate from least squares" : ""}.</p>
    ${R.notes.length ? `<ul class="det-notes">${R.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    <details><summary>What is ${esc(v.name.toLowerCase())}?</summary><p>${esc(v.definition)}</p><p class="hint">${esc(v.why)}</p></details>
    ${controls(r)}
    <p class="hint">Data: ${esc(r.source.name)} — parameter ${esc(r.source.parameter)} from ${esc(r.source.model)}. ${esc(r.source.attribution)}.</p></article>`;
}

function controls(r) {
  return `<div class="det-controls"><label class="check"><input type="checkbox" class="det-raw" ${r.request.deseasonalised ? "" : "checked"}> Keep the seasonal cycle</label>
    <form class="det-period row"><label>From <input name="start" type="number" min="1981" max="${det.cat.last_year - 10}" value="${r.request.start_year}"></label>
      <label>to <input name="end" type="number" min="1991" max="${det.cat.last_year}" value="${r.request.end_year}"></label><button class="small" type="submit">Re-test</button></form>
    <button class="small det-csv">Download CSV</button>${state.token ? `<button class="small det-zoom">Zoom into a plot here with URBANOVA →</button>` : ""}</div>`;
}

function chartSvg(R, v) {
  const pts = R.series.map((s) => ({ t: +s.date.slice(0, 4) + (+s.date.slice(5, 7) - 0.5) / 12, y: s.value }));
  const ann = R.deseasonalised ? annualOf(pts) : R.annual.map((a) => ({ t: a.year + 0.5, y: a.value }));
  const W = 640, H = 240, l = 52, r = 12, top = 12, b = 26;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  const tl = R.trend_line, slope = (tl.y1 - tl.y0) / (tl.t1 - tl.t0);
  const band = (t, s) => tl.y_mean + s * (t - tl.t_mean);
  const ys = pts.map((p) => p.y).concat([band(t0, tl.ci_low_slope), band(t0, tl.ci_high_slope), band(t1, tl.ci_low_slope), band(t1, tl.ci_high_slope)]);
  let y0 = Math.min(...ys), y1 = Math.max(...ys); if (y0 === y1) { y0 -= 1; y1 += 1; }
  const X = (t) => l + ((t - t0) / (t1 - t0)) * (W - l - r), Y = (y) => top + (1 - (y - y0) / (y1 - y0)) * (H - top - b);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${X(p.t).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
  const bandPath = `M${X(t0)},${Y(Math.max(band(t0, tl.ci_low_slope), band(t0, tl.ci_high_slope)))} L${X(t1)},${Y(Math.max(band(t1, tl.ci_low_slope), band(t1, tl.ci_high_slope)))} `
    + `L${X(t1)},${Y(Math.min(band(t1, tl.ci_low_slope), band(t1, tl.ci_high_slope)))} L${X(t0)},${Y(Math.min(band(t0, tl.ci_low_slope), band(t0, tl.ci_high_slope)))} Z`;
  const step = niceStep((y1 - y0) / 4);
  const yTicks = []; for (let y = Math.ceil(y0 / step) * step; y <= y1 + 1e-9; y += step) yTicks.push(+y.toPrecision(10));
  const xTicks = []; for (let y = Math.ceil(t0 / 5) * 5; y <= t1; y += 5) xTicks.push(y);
  const zero = R.deseasonalised && y0 < 0 && y1 > 0 ? `<line x1="${l}" x2="${W - r}" y1="${Y(0)}" y2="${Y(0)}" class="zero"/>` : "";
  return `<svg class="det-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(v.name)} over time with its trend line">
    ${yTicks.map((y) => `<line x1="${l}" x2="${W - r}" y1="${Y(y)}" y2="${Y(y)}" class="grid"/><text x="${l - 6}" y="${Y(y) + 4}" class="ax" text-anchor="end">${esc(fmt(y, step < 0.01 ? 4 : step < 1 ? 2 : 0))}</text>`).join("")}
    ${xTicks.map((t) => `<text x="${X(t)}" y="${H - 8}" class="ax" text-anchor="middle">${t}</text>`).join("")}
    ${zero}<path d="${bandPath}" class="band"/><path d="${line}" class="series"/>
    ${ann.map((a) => `<circle cx="${X(a.t).toFixed(1)}" cy="${Y(a.y).toFixed(1)}" r="2.6" class="ann"/>`).join("")}
    <line x1="${X(tl.t0)}" y1="${Y(tl.y0)}" x2="${X(tl.t1)}" y2="${Y(tl.y0 + slope * (tl.t1 - tl.t0))}" class="trend ${esc(R.verdict.level)}"/>
    <text x="${l + 4}" y="${top + 12}" class="ax">${esc(v.unit)}</text></svg>`;
}

function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}

function annualOf(pts) {
  const by = {}; pts.forEach((p) => { const y = Math.floor(p.t); (by[y] = by[y] || []).push(p.y); });
  return Object.entries(by).filter(([, v]) => v.length >= 10).map(([y, v]) => ({ t: +y + 0.5, y: v.reduce((a, b) => a + b, 0) / v.length }));
}

function downloadCsv(r) {
  const rows = [["month", r.result.deseasonalised ? `${r.variable.id}_anomaly` : r.variable.id, `${r.variable.id}_value`]];
  const raw = Object.fromEntries((r.result.raw || []).map((x) => [x.date, x.value]));
  (r.result.series || []).forEach((s) => rows.push([s.date, s.value, raw[s.date]]));
  const head = `# ${r.variable.name} (${r.variable.unit}) at ${r.point.lat}, ${r.point.lon}; source ${r.source.name}, parameter ${r.source.parameter}\n`;
  const blob = new Blob([head + rows.map((x) => x.join(",")).join("\n")], { type: "text/csv" });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `trend_${r.variable.id}_${r.point.lat}_${r.point.lon}.csv` });
  document.body.appendChild(a); a.click(); a.remove();
}

async function zoomToPlot(r) {
  const res = await api("/projects", { body: { name: `Plot near ${r.where}`.slice(0, 160), latitude: r.point.lat, longitude: r.point.lon, objectives: ["main_concerns"] } });
  notify("Project created. Confirm the location, then click 'Analyse this site now'.", "ok");
  location.hash = `#/p/${res.project.id}/overview`;
}

// ------------------------------------------------------------------ guided demo: fills in the questions step by step
async function runDemo(id) {
  const r = det.cat.regions.find((x) => x.id === id);
  const v = det.cat.variables.find((x) => x.id === r.variable);
  const wait = (ms) => new Promise((res) => setTimeout(res, ms));
  det.demo = true;
  try {
    caption(`Demo: ${r.name}. ${r.story}`);
    document.getElementById("det-q").scrollIntoView({ behavior: "smooth", block: "start" });
    await wait(1600);
    caption(`Step 1 — what are we curious about? "${det.cat.interests[r.interest]}". What kind of place? "${det.cat.place_types[r.place]}".`);
    document.querySelector(`#det-int button[data-v="${r.interest}"]`).click();
    document.querySelector(`#det-place button[data-v="${r.place}"]`).click();
    await wait(1600);
    caption(`Step 2 — where? We put the pin on ${r.name}. NASA POWER gives one value for about 55 km around it.`);
    det.map.flyTo([r.lat, r.lon], 5, { duration: 1.2 });
    setPoint(r.lat, r.lon); document.getElementById("det-name").value = r.name;
    await wait(1800);
    caption("Step 3 — the detective suggests variables that could show a trend here.");
    await findVariables();
    await wait(1800);
    caption(`Step 4 — we test "${v.name}": ${det.cat.last_year - 1981 + 1} years of monthly data, seasonal cycle removed, two significance tests.`);
    const res = await testTrend(r.variable);
    await wait(500);
    const R = res.result;
    caption(R.ok ? `Result: ${R.verdict.headline} ${R.verdict.why}` : R.reason, true);
  } finally { det.demo = false; }
}

function caption(text, final = false) {
  let c = document.getElementById("det-caption");
  if (!c) { c = document.createElement("div"); c.id = "det-caption"; c.setAttribute("role", "status"); c.setAttribute("aria-live", "polite"); document.body.appendChild(c); }
  c.innerHTML = `<p>${esc(text)}</p><button class="small" aria-label="Close the demo caption">✕</button>`;
  c.querySelector("button").onclick = () => c.remove();
  c.classList.toggle("final", final);
}
