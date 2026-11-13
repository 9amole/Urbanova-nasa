/* Trend Detective (NASA Space Apps 2026): pick a question and a place, get suggested NASA variables, test their trend,
   and map where it is changing. Uses app.js globals: api(), esc(), fmt(), state, makeMap(), clearMaps(), notify(), guarded().
   Public: no account needed. Shareable links: #/detective/<variable>/<lat>/<lon>. */
"use strict";

const det = { cat: null, interest: "heat", place: "city", lat: 30.03, lon: 31.47, where: "New Cairo, Egypt", results: [], map: null, marker: null,
  demo: false, previews: {}, trendMap: null, cases: null, caseMap: null };
const LEVEL = { significant: ["Significant", "b-ok", "🟢"], borderline: ["Likely — the tests disagree", "b-warn", "🟡"], not_significant: ["Not significant yet", "b-mute", "⚪"] };
const sig2 = (x) => { // two significant figures, signed, without trailing noise
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  const a = Math.abs(x); const s = x > 0 ? "+" : x < 0 ? "−" : "";
  const v = a === 0 ? "0" : a >= 100 ? a.toFixed(0) : a >= 10 ? a.toFixed(1) : a >= 1 ? a.toFixed(2) : a.toPrecision(2);
  return s + (v.includes(".") ? v.replace(/\.?0+$/, "") : v);
};

async function viewDetective(parts = []) {
  if (!det.cat) det.cat = await api("/detective/catalog");
  if (!det.cases) det.cases = await fetch("casefiles/index.json").then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
  const C = det.cat;
  clearMaps();
  det.marker = null;
  $main.innerHTML = `
  <section class="panel det-hero"><h1>Trend Detective <span class="det-badge">NASA Space Apps 2026</span></h1>
    <p class="lead">Is the Earth changing where you care about? Pick a question and a place, and test ${C.last_year - 1981 + 1} years of NASA data for a <strong>trend</strong> — and whether it is <strong>significant</strong>.</p>
    <p class="hint">Data: ${esc(C.source.name)}, ${esc(C.source.period)}. One value covers about 55 km: it describes a region, not a single street.</p></section>
  <section class="panel"><h2>Watch a demo</h2><p class="hint">Each card is a real question answered with NASA data. Click one to watch the detective work step by step.</p>
    <div class="det-demos">${C.regions.map((r) => `<button class="det-demo" data-r="${esc(r.id)}"><strong>${esc(r.name)}</strong><span>${esc(r.story)}</span>
      <em class="det-preview" id="pv-${esc(r.id)}">checking the data…</em></button>`).join("")}</div></section>
  ${Object.keys(det.cases).length ? `<section class="panel"><h2>Street-level case files <span class="hint">regional trend → what it looks like on the ground</span></h2>
    <p class="hint">For some demo places, URBANOVA analysed one 1.2 km plot with satellites: Landsat surface temperature (NASA/USGS), Sentinel-2 vegetation, ESA land cover, and how hot it feels in sun and shade.</p>
    <div class="det-chips">${Object.entries(det.cases).map(([id, c]) => `<button class="chip-btn det-case" data-c="${esc(id)}">🔎 ${esc(c.name)}</button>`).join("")}</div></section>` : ""}
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
  <section class="panel" id="det-case" hidden></section>
  <section class="panel" id="det-mapsec" hidden></section>
  <section class="panel"><h2>How the detective decides</h2>
    <details><summary>What is a trend, and what does "significant" mean?</summary>
      <p>A <strong>trend</strong> is a steady change in one direction over many years. Temperatures and rain jump around from month to month, so we first remove the normal <strong>seasonal cycle</strong> (summer is always warmer than winter), then average each year. The trend is tested on those <strong>yearly averages</strong>: neighbouring months are too alike to count as separate evidence.</p>
      <p>A trend is <strong>significant</strong> when it is very unlikely to be just chance. The <strong>p-value</strong> is the chance of seeing a trend at least this strong if nothing were really changing. Below 0.05 (5 %), scientists call it significant.</p>
      <p>We use two tests: the <strong>t-test</strong> on a straight-line fit (least squares), and the <strong>Mann-Kendall test</strong>, which only asks whether later years tend to be higher (or lower) than earlier ones, so one extreme year can't fool it. The rate shown is <strong>Sen's slope</strong>: the median of the slopes between every pair of years.</p>
      <p>The <strong>95 % range</strong> (confidence interval) is where the true rate probably lies. If it doesn't include zero, the trend is significant.</p>
      <p>When the tests <strong>disagree</strong>, the trend is "likely" but not proven. <strong>Not significant yet</strong> doesn't mean "no change": a longer record often makes a real trend significant.</p></details>
    <details><summary>Limits of this data</summary><p>${esc(C.source.resolution)} Values come from NASA models that combine satellite and ground observations (MERRA-2 reanalysis, CERES, GPCP/GPM rainfall): excellent for regional trends, not for a single street or field. Only straight-line trends are tested; sudden jumps and multi-year cycles (El Niño) are not modelled.</p></details>
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
  document.querySelectorAll(".det-case").forEach((b) => (b.onclick = () => guarded(() => openCase(b.dataset.c), b)));
  renderResults();
  loadPreviews();
  if (parts[1] === "case" && parts[2]) return guarded(() => openCase(parts[2]));
  // Shareable link: #/detective/<variable>/<lat>/<lon>
  const [, v, la, lo] = parts;
  if (v && la !== undefined && lo !== undefined && C.variables.some((x) => x.id === v)) {
    setPoint(+la, +lo);
    const near = (x) => x && Math.abs(x.lat - det.lat) < 0.05 && Math.abs(x.lon - det.lon) < 0.05;
    const known = C.regions.find((x) => near(x)) || C.regions.map((x) => x.pair).find((x) => near(x));
    det.where = known ? known.name.replace(/^Same planet, opposite rain$/, "Sahel, Niger") : "";
    document.getElementById("det-name").value = det.where;
    det.map.setView([det.lat, det.lon], 5);
    await guarded(() => testTrend(v));
  }
}

function loadPreviews() {
  det.cat.regions.forEach(async (r) => {
    const el = () => document.getElementById(`pv-${r.id}`);
    try {
      if (!det.previews[r.id]) {
        const a = await api("/detective/trend", { body: { lat: r.lat, lon: r.lon, variable: r.variable } });
        const b = r.pair ? await api("/detective/trend", { body: { lat: r.pair.lat, lon: r.pair.lon, variable: r.variable } }) : null;
        det.previews[r.id] = [a, b].filter(Boolean).map((x) => (x.result.ok ? `${LEVEL[x.result.verdict.level][2]} ${x.result.verdict.rate}` : "")).join("  vs  ");
      }
      if (el()) el().textContent = det.previews[r.id];
    } catch (e) { if (el()) el().textContent = ""; }
  });
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
  if (!opts.noScroll) document.getElementById(`res-${r.key}`)?.scrollIntoView({ behavior: det.demo ? "smooth" : "auto", block: "start" });
  return r;
}

function renderResults() {
  const box = document.getElementById("det-results");
  if (!box) return;
  box.innerHTML = det.results.length ? `<h2 class="det-h">3 · Results ${det.results.length > 1 ? `<span class="hint">(${det.results.length} side by side — test another variable or place to compare)</span>` : ""}</h2>
    <div class="det-results ${det.results.length > 1 ? "multi" : ""}">${det.results.map(resultHtml).join("")}</div>` : "";
  det.results.forEach((r) => {
    const el = document.getElementById(`res-${r.key}`);
    const again = (o) => guarded(() => { setPointQuiet(r); return testTrend(r.variable.id, { start: r.request.start_year, end: r.request.end_year, deseason: r.request.deseasonalised, replace: r.key, ...o }); });
    el.querySelector(".det-raw").onchange = (e) => again({ deseason: !e.target.checked });
    el.querySelector(".det-period").onsubmit = (e) => { e.preventDefault(); again({ start: +e.target.start.value, end: +e.target.end.value }); };
    el.querySelector(".det-close").onclick = () => { det.results = det.results.filter((x) => x.key !== r.key); renderResults(); };
    el.querySelector(".det-csv").onclick = () => downloadCsv(r);
    el.querySelector(".det-link").onclick = (e) => copyLink(r, e.target);
    el.querySelector(".det-where-btn").onclick = (e) => guarded(() => showTrendMap(r), e.target);
    const zoom = el.querySelector(".det-zoom"); if (zoom) zoom.onclick = () => guarded(() => zoomToPlot(r), zoom);
    const cf = el.querySelector(".det-street"); if (cf) cf.onclick = () => guarded(() => openCase(cf.dataset.c, r), cf);
  });
}

function setPointQuiet(r) { det.lat = r.point.lat; det.lon = r.point.lon; det.where = r.where; }

function resultHtml(r) {
  const v = r.variable, R = r.result;
  if (!R.ok) return `<article class="panel det-result" id="res-${r.key}"><div class="row det-head"><h3>${esc(v.name)} — ${esc(r.where)}</h3><button class="small det-close" aria-label="Remove this result">✕</button></div><p>${esc(R.reason)}</p>${controls(r)}</article>`;
  const L_ = LEVEL[R.verdict.level], V = R.verdict;
  const sen = R.sen, ols = R.ols, mk = R.mann_kendall;
  const p = (x) => (x < 0.001 ? "< 0.001" : fmt(x, 3));
  return `<article class="panel det-result" id="res-${r.key}">
    <div class="row det-head"><h3>${esc(v.name)} — ${esc(r.where)}</h3><button class="small det-close" aria-label="Remove this result">✕</button></div>
    <div class="det-verdict ${esc(V.level)}">
      <div class="det-big"><span class="det-big-num">${esc(V.total)}</span><span class="hint">since ${R.period.first_year}</span></div>
      <div><span class="badge ${L_[1]}">${L_[2]} ${esc(L_[0])}</span>
        <p class="det-headline">${esc(V.headline)}</p>
        <p><strong>${esc(V.rate)}</strong> <span class="hint">(${esc(V.range)})</span></p>
        <p>${esc(V.why)} <span class="hint">${esc(V.tests)}</span></p></div></div>
    ${chartSvg(R, v)}
    <p class="det-legend"><span class="lg-month"></span> each month ${R.deseasonalised ? "(seasonal cycle removed)" : ""} <span class="lg-year"></span> yearly average
      <span class="lg-trend ${esc(V.level)}"></span> trend <span class="lg-band"></span> 95 % range</p>
    ${seasonsHtml(R, v)}
    ${R.notes.length ? `<ul class="det-notes">${R.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    <details><summary>What is ${esc(v.name.toLowerCase())}?</summary><p>${esc(v.definition)}</p><p class="hint">${esc(v.why)}</p></details>
    <details><summary>For scientists: the full statistics</summary>
      <table class="det-stats"><thead><tr><th>Test (on ${R.period.years} yearly averages)</th><th>Rate per decade</th><th>95 % range</th><th>p</th><th>Significant</th></tr></thead><tbody>
        <tr><td>Mann-Kendall + Sen's slope</td><td>${sig2(sen.per_decade.slope)}</td><td>${sig2(sen.per_decade.ci_low)} to ${sig2(sen.per_decade.ci_high)}</td><td>${p(mk.p_value)}</td><td>${mk.significant ? "Yes" : "No"}</td></tr>
        <tr><td>Least squares + t-test</td><td>${sig2(ols.per_decade.slope)}</td><td>${sig2(ols.per_decade.ci_low)} to ${sig2(ols.per_decade.ci_high)}</td><td>${p(ols.p_value)}</td><td>${ols.significant ? "Yes" : "No"}</td></tr>
      </tbody></table>
      <p class="hint">Unit: ${esc(v.unit)} per decade. Mann-Kendall S = ${mk.S}, z = ${fmt(mk.z, 2)}, Kendall's τ = ${fmt(mk.tau, 3)} · R² = ${fmt(ols.r2, 3)} · lag-1 autocorrelation of yearly residuals ${fmt(ols.lag1_autocorrelation, 2)}${R.rate.method === "ols" ? " · headline rate from least squares" : ""}.
      For comparison, the same tests on ${R.monthly_tests.n} monthly anomalies give p = ${p(R.monthly_tests.ols_p)} (t-test) and ${p(R.monthly_tests.mk_p)} (Mann-Kendall), with a month-to-month autocorrelation of ${fmt(R.monthly_tests.lag1_autocorrelation, 2)} — which is why the verdict uses yearly averages.</p></details>
    ${controls(r)}
    <p class="hint">Data: ${esc(r.source.name)} — parameter ${esc(r.source.parameter)} from ${esc(r.source.model)}, ${esc(R.period.start)} to ${esc(R.period.end)}. ${esc(r.source.attribution)}.</p></article>`;
}

function seasonsHtml(R, v) {
  const S = R.seasons || [];
  if (S.length < 12) return "";
  const pal = PALETTE[v.interest] || PALETTE.other;
  const max = Math.max(...S.map((s) => Math.abs(s.per_decade)), 1e-9);
  const W = 360, H = 120, mid = 60, bw = W / 12;
  const bars = S.map((s, i) => { const h = (Math.abs(s.per_decade) / max) * (mid - 14); const y = s.per_decade >= 0 ? mid - h : mid;
    return `<rect x="${i * bw + 3}" y="${y.toFixed(1)}" width="${bw - 6}" height="${Math.max(h, 1).toFixed(1)}" class="sbar" fill="rgba(${s.per_decade >= 0 ? pal.up : pal.down},${s.significant ? 1 : 0.35})"><title>${esc(s.name)}: ${sig2(s.per_decade)} ${esc(v.unit)} per decade${s.significant ? " (significant)" : ""}</title></rect>
      <text x="${i * bw + bw / 2}" y="${H - 2}" class="ax" text-anchor="middle">${esc(s.name[0])}</text>`; }).join("");
  const top = [...S].sort((a, b) => Math.abs(b.per_decade) - Math.abs(a.per_decade))[0];
  return `<details class="det-seasons"><summary>Which months are changing most?</summary>
    <svg viewBox="0 0 ${W} ${H}" class="det-season-chart" role="img" aria-label="Trend for each calendar month"><line x1="0" x2="${W}" y1="${mid}" y2="${mid}" class="grid"/>${bars}</svg>
    <p class="hint">Each bar is the trend of one calendar month across the years (darker = significant on its own). The biggest change is in <strong>${esc(top.name)}</strong> (${sig2(top.per_decade)} ${esc(v.unit)} per decade).</p></details>`;
}

function controls(r) {
  return `<div class="det-controls"><label class="check"><input type="checkbox" class="det-raw" ${r.request.deseasonalised ? "" : "checked"}> Show months with the seasonal cycle</label>
    <form class="det-period row"><label>From <input name="start" type="number" min="1981" max="${det.cat.last_year - 10}" value="${r.request.start_year}"></label>
      <label>to <input name="end" type="number" min="1991" max="${det.cat.last_year}" value="${r.request.end_year}"></label><button class="small" type="submit">Re-test</button></form>
    ${nearCase(r) ? `<button class="small primary det-street" data-c="${esc(nearCase(r))}">🔎 See it at street level</button>` : ""}
    <button class="small det-where-btn">🗺 Where is it changing?</button><button class="small det-csv">Download CSV</button><button class="small det-link">Copy link</button>
    ${state.token ? `<button class="small det-zoom">Zoom into a plot here with URBANOVA →</button>` : ""}</div>`;
}

function chartSvg(R, v) {
  const box = document.getElementById("det-results");
  const W = Math.round(Math.max(320, Math.min(760, (box && box.clientWidth ? box.clientWidth / (det.results.length > 1 ? Math.min(det.results.length, 2) : 1) : 680) - 48)));
  const H = Math.round(W < 480 ? 220 : 260), l = 46, r = 10, top = 14, b = 24;
  const pts = R.series.map((s) => ({ t: +s.date.slice(0, 4) + (+s.date.slice(5, 7) - 0.5) / 12, y: s.value }));
  const off = R.trend_line.offset || 0;  // raw view: the yearly trend is drawn around the series mean
  const ann = (R.deseasonalised ? R.annual : R.annual_raw).map((a) => ({ t: a.year + 0.5, y: a.value }));
  const t0 = Math.min(pts[0].t, ann[0].t), t1 = Math.max(pts[pts.length - 1].t, ann[ann.length - 1].t);
  const tl = R.trend_line, slope = (tl.y1 - tl.y0) / (tl.t1 - tl.t0);
  const band = (t, s) => off + tl.y_mean + s * (t - tl.t_mean);
  // Scale to the yearly averages and the trend band (the story), not to single extreme months, which are clipped.
  const ys = ann.map((p) => p.y).concat([band(tl.t0, tl.ci_low_slope), band(tl.t0, tl.ci_high_slope), band(tl.t1, tl.ci_low_slope), band(tl.t1, tl.ci_high_slope)]);
  let y0 = Math.min(...ys), y1 = Math.max(...ys); if (y0 === y1) { y0 -= 1; y1 += 1; }
  const monthly = pts.map((p) => p.y).sort((a, b) => a - b);
  const q = (f) => monthly[Math.min(monthly.length - 1, Math.max(0, Math.round(f * (monthly.length - 1))))];
  const pad = (y1 - y0) * 0.6;
  y0 = Math.max(y0 - pad, Math.min(y0, q(0.05))); y1 = Math.min(y1 + pad, Math.max(y1, q(0.95)));
  const clipId = `clip-${Math.random().toString(36).slice(2, 8)}`;
  const X = (t) => l + ((t - t0) / (t1 - t0)) * (W - l - r), Y = (y) => top + (1 - (y - y0) / (y1 - y0)) * (H - top - b);
  const path = (arr) => arr.map((p, i) => `${i ? "L" : "M"}${X(p.t).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
  const bandPath = `M${X(tl.t0)},${Y(Math.max(band(tl.t0, tl.ci_low_slope), band(tl.t0, tl.ci_high_slope)))} L${X(tl.t1)},${Y(Math.max(band(tl.t1, tl.ci_low_slope), band(tl.t1, tl.ci_high_slope)))} `
    + `L${X(tl.t1)},${Y(Math.min(band(tl.t1, tl.ci_low_slope), band(tl.t1, tl.ci_high_slope)))} L${X(tl.t0)},${Y(Math.min(band(tl.t0, tl.ci_low_slope), band(tl.t0, tl.ci_high_slope)))} Z`;
  const step = niceStep((y1 - y0) / 4);
  const yTicks = []; for (let y = Math.ceil(y0 / step) * step; y <= y1 + 1e-9; y += step) yTicks.push(+y.toPrecision(10));
  const xTicks = []; const xs = W < 480 ? 10 : 5; for (let y = Math.ceil(t0 / xs) * xs; y <= t1; y += xs) xTicks.push(y);
  const zero = R.deseasonalised && y0 < 0 && y1 > 0 ? `<line x1="${l}" x2="${W - r}" y1="${Y(0)}" y2="${Y(0)}" class="zero"/>` : "";
  return `<svg class="det-chart" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(v.name)} over time with its trend line">
    ${yTicks.map((y) => `<line x1="${l}" x2="${W - r}" y1="${Y(y)}" y2="${Y(y)}" class="grid"/><text x="${l - 6}" y="${Y(y) + 4}" class="ax" text-anchor="end">${esc(sig2(y).replace("+", ""))}</text>`).join("")}
    ${xTicks.map((t) => `<text x="${X(t)}" y="${H - 6}" class="ax" text-anchor="middle">${t}</text>`).join("")}
    <clipPath id="${clipId}"><rect x="${l}" y="${top}" width="${W - l - r}" height="${H - top - b}"/></clipPath>
    ${zero}<path d="${bandPath}" class="band"/><path d="${path(pts)}" class="series" clip-path="url(#${clipId})"/>
    <path d="${path(ann)}" class="annual"/>${ann.map((a) => `<circle cx="${X(a.t).toFixed(1)}" cy="${Y(a.y).toFixed(1)}" r="2.8" class="ann"/>`).join("")}
    <line x1="${X(tl.t0)}" y1="${Y(off + tl.y0)}" x2="${X(tl.t1)}" y2="${Y(off + tl.y0 + slope * (tl.t1 - tl.t0))}" class="trend ${esc(R.verdict.level)}"/>
    <text x="${l + 4}" y="${top + 10}" class="ax unit">${esc(v.unit)}${R.deseasonalised ? " (difference from normal)" : ""}</text></svg>`;
}

function niceStep(raw) {
  const p = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}

// ------------------------------------------------------------------ where is it changing? (5° × 5° trend map)
// Colours by topic: warmer = red, wetter = blue, drier = brown (as on climate maps).
const PALETTE = { heat: { up: "200,40,40", down: "30,90,200" }, water: { up: "30,90,200", down: "150,90,30" }, land: { up: "30,90,200", down: "150,90,30" },
  other: { up: "120,60,170", down: "220,130,20" } };
async function showTrendMap(r) {
  const sec = document.getElementById("det-mapsec");
  sec.hidden = false;
  sec.innerHTML = `<h2>Where is ${esc(r.variable.name.toLowerCase())} changing?</h2><p class="hint" role="status">Testing every NASA cell around ${esc(r.where)}…</p>`;
  sec.scrollIntoView({ behavior: "smooth", block: "start" });
  const m = await api("/detective/map", { body: { lat: r.point.lat, lon: r.point.lon, variable: r.variable.id, start_year: r.request.start_year, end_year: r.request.end_year } });
  const max = Math.max(...m.cells.map((c) => Math.abs(c.per_decade)), 1e-9);
  const pal = PALETTE[m.variable.interest] || PALETTE.other;
  sec.innerHTML = `<h2>Where is ${esc(m.variable.name.toLowerCase())} changing? <span class="hint">${m.request.start_year}–${m.request.end_year}</span></h2>
    <p class="det-headline">${esc(m.summary)}</p>
    <div id="det-tmap" class="map det-tmap" aria-label="Map of trends around the point"></div>
    <div class="det-maplegend"><span class="sw" style="background:rgb(${pal.up})"></span> towards ${esc(m.variable.up)} <span class="sw" style="background:rgb(${pal.down})"></span> towards ${esc(m.variable.down)}
      <span class="hint">· stronger colour = faster change (up to ${sig2(max).replace("+", "")} ${esc(m.variable.unit)} per decade) · solid = significant, faded = not significant · click a square to test it</span></div>
    <p class="hint">Each square is one NASA POWER cell (${m.cell_size.lat}° × ${m.cell_size.lon}°), tested exactly like the result above. ${esc(m.source.attribution)}.</p>`;
  if (det.trendMap) { det.trendMap.remove(); state.maps = state.maps.filter((x) => x !== det.trendMap); }
  const tm = makeMap("det-tmap", m.center.lat, m.center.lon, 6);
  det.trendMap = tm;
  const hl = m.cell_size.lat / 2, hw = m.cell_size.lon / 2;
  const group = L.featureGroup().addTo(tm);
  m.cells.forEach((c) => {
    const k = Math.min(1, Math.abs(c.per_decade) / max);
    const col = `rgba(${c.per_decade >= 0 ? pal.up : pal.down},${0.15 + 0.7 * k})`;
    const sig = c.level === "significant";
    L.rectangle([[c.lat - hl, c.lon - hw], [c.lat + hl, c.lon + hw]], { color: sig ? "#222" : "#999", weight: sig ? 0.8 : 0.3, fillColor: col, fillOpacity: sig ? 0.85 : 0.3 })
      .bindTooltip(`${sig2(c.per_decade)} ${esc(m.variable.unit)} per decade · ${LEVEL[c.level][0]}`)
      .on("click", () => guarded(async () => { setPoint(c.lat, c.lon); det.where = `cell ${c.lat}, ${c.lon}`; document.getElementById("det-name").value = det.where; await testTrend(m.variable.id, { start: m.request.start_year, end: m.request.end_year }); }))
      .addTo(group);
  });
  L.marker([m.center.lat, m.center.lon]).addTo(tm);
  tm.fitBounds(group.getBounds(), { animate: false });
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

function copyLink(r, btn) {
  const url = `${location.origin}${location.pathname}#/detective/${r.variable.id}/${r.point.lat}/${r.point.lon}`;
  const done = () => { btn.textContent = "Link copied"; setTimeout(() => (btn.textContent = "Copy link"), 2000); };
  if (navigator.clipboard) navigator.clipboard.writeText(url).then(done, () => prompt("Copy this link:", url)); else prompt("Copy this link:", url);
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
    const where = r.pair ? "Sahel, Niger" : r.name;
    caption(`Step 2 — where? We put the pin on ${where}. NASA POWER gives one value for about 55 km around it.`);
    det.map.flyTo([r.lat, r.lon], 5, { duration: 1.2 });
    setPoint(r.lat, r.lon); document.getElementById("det-name").value = where;
    await wait(1800);
    caption("Step 3 — the detective suggests variables that could show a trend here.");
    await findVariables();
    await wait(1800);
    caption(`Step 4 — we test "${v.name}": ${det.cat.last_year - 1981 + 1} years of NASA data, seasonal cycle removed, yearly averages, two significance tests.`);
    const res = await testTrend(r.variable);
    if (r.pair) {
      await wait(2600);
      caption(`Step 5 — now the same variable at ${r.pair.name}.`);
      det.map.flyTo([r.pair.lat, r.pair.lon], 5, { duration: 1.2 });
      setPoint(r.pair.lat, r.pair.lon); det.where = r.pair.name; document.getElementById("det-name").value = r.pair.name;
      await wait(1500);
      const res2 = await testTrend(r.variable);
      await wait(600);
      const a = res.result.verdict, b = res2.result.verdict;
      caption(`Same planet, opposite trends: ${where} — ${a.headline.replace(/\.$/, "")} (${a.rate}); ${r.pair.name} — ${b.headline.replace(/\.$/, "")} (${b.rate}). The same climate changes can move rain belts, making one region wetter and another drier.`, true);
      return;
    }
    await wait(500);
    const R = res.result;
    caption(R.ok ? `Result: ${R.verdict.headline.replace(/\.$/, ":")} ${R.verdict.rate} (${R.verdict.range}) — ${R.verdict.total_text}. ${R.verdict.why}` : R.reason, true);
  } finally { det.demo = false; }
}

function caption(text, final = false) {
  let c = document.getElementById("det-caption");
  if (!c) { c = document.createElement("div"); c.id = "det-caption"; c.setAttribute("role", "status"); c.setAttribute("aria-live", "polite"); document.body.appendChild(c); }
  c.innerHTML = `<p>${esc(text)}</p><button class="small" aria-label="Close the demo caption">✕</button>`;
  c.querySelector("button").onclick = () => c.remove();
  c.classList.toggle("final", final);
}


// ------------------------------------------------------------------ street-level case files (URBANOVA site analysis, pre-run)
function nearCase(r) {
  const cases = det.cases || {};
  let best = null, bestD = 1.3;  // within about one NASA region (°)
  Object.entries(cases).forEach(([id, c]) => { const d = Math.hypot(c.lat - r.point.lat, c.lon - r.point.lon); if (d < bestD) { best = id; bestD = d; } });
  return best;
}

async function openCase(id, fromResult) {
  const s = await fetch(`casefiles/${encodeURIComponent(id)}/summary.json`).then((x) => { if (!x.ok) throw new Error("This case file is not available."); return x.json(); });
  const sec = document.getElementById("det-case");
  sec.hidden = false;
  const M = s.metrics || {};
  const val = (k, d = 1) => (M[k] && M[k].state === "available" ? `<strong>${esc(fmt(M[k].value, d))}</strong> ${esc(M[k].unit || "")}` : `<span class="muted">not available${M[k] && M[k].reason ? ": " + esc(M[k].reason) : ""}</span>`);
  const T = (title, body, foot) => `<div class="tile"><h3>${esc(title)}</h3><div class="tile-v">${body}</div>${foot ? `<p class="hint">${foot}</p>` : ""}</div>`;
  const trend = fromResult && fromResult.result.ok ? fromResult.result.verdict : null;
  const lst = M.lst_site_median_c, diff = M.lst_site_minus_surroundings_k, sun = M.utci_sun_peak_c, shade = M.utci_shade_peak_c;
  const story = [
    trend ? `The region around ${esc(fromResult.where)}: <strong>${esc(fromResult.variable.name.toLowerCase())} ${esc(trend.rate)}</strong> (${esc(LEVEL[trend.level][0].toLowerCase())}).` : "",
    lst && lst.state === "available" ? `On this plot today, the ground reaches <strong>${esc(fmt(lst.value, 1))} °C</strong> in the hot season${diff && diff.state === "available" ? `, ${esc(fmt(Math.abs(diff.value), 1))} °C ${diff.value >= 0 ? "hotter" : "cooler"} than its surroundings` : ""}.` : "",
    sun && sun.state === "available" && shade && shade.state === "available" ? `On a hot day it feels like <strong>${esc(fmt(sun.value, 1))} °C in the sun</strong> but ${esc(fmt(shade.value, 1))} °C in the shade: shade alone takes off ${esc(fmt(sun.value - shade.value, 1))} °C.` : "",
  ].filter(Boolean).join(" ");
  sec.innerHTML = `<div class="row det-head"><h2>🔎 Case file: ${esc(s.name)}</h2><button class="small" id="det-case-close" aria-label="Close the case file">✕</button></div>
    <p class="hint">Street level, today: a 1.2 km plot analysed by URBANOVA (site analysis) with satellite data. ${esc(s.generated_at.slice(0, 10))}.</p>
    ${story ? `<p class="det-story">${story}</p>` : ""}
    <div class="det-case-grid"><div><div id="det-case-map" class="map det-case-map" aria-label="Map of the plot with satellite layers"></div><div id="det-case-layers" class="det-chips"></div></div>
    <div class="tiles det-case-tiles">
      ${T("Ground temperature (Landsat)", val("lst_site_median_c"), "Satellite land surface temperature, hot season median")}
      ${T("Compared with surroundings", val("lst_site_minus_surroundings_k"), "Positive = the plot is hotter")}
      ${T("Share in an observed hot cluster", val("lst_site_hot_cluster_share_pct", 0), "Hot-spot analysis of the surrounding area")}
      ${T("Vegetation (Sentinel-2 NDVI)", val("ndvi_site_mean", 2), "0 = bare, above 0.3 = green")}
      ${T("Feels like in full sun", val("utci_sun_peak_c"), "UTCI on a typical hot day")}
      ${T("Feels like in shade", val("utci_shade_peak_c"), "UTCI on a typical hot day")}
      ${T("Hot days above 35 °C", val("climate_mean_annual_TX35_days", 0), "Per year, regional climate (ERA5)")}
      ${T("Tropical nights", val("climate_mean_annual_TR_days", 0), "Nights above 20 °C, per year")}
    </div></div>
    ${s.land_cover.length ? `<h3>Land cover on the plot (ESA WorldCover)</h3>${barsSvg(s.land_cover.slice(0, 6).map((x) => ({ label: x.label.replace(/^Site: /, "").replace(/ \(\d{4} land cover\)$/, ""), value: x.value })), "%", { labelW: 220 })}` : ""}
    ${(s.site_context || []).map((c) => `<p>${esc(c.statement)}</p>`).join("")}
    <details><summary>Analyses and their status</summary><ul>${Object.values(s.modules).map((m) => `<li>${badge(MOD_STATUS[m.status] || [m.status, "b-mute"])} ${esc(m.label)}${m.reason ? ` — <span class="muted">${esc(m.reason)}</span>` : ""}</li>`).join("")}</ul></details>
    <details><summary>Limitations</summary><ul>${(s.limitations || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>
    <p class="hint">Data: ${(s.datasets || []).map((d) => esc(d.attribution || d.name)).filter(Boolean).join(" · ")}</p>
    <p class="hint">Want this for your own plot, with a design? Sign in and use URBANOVA's site analysis and design check (Projects).</p>`;
  document.getElementById("det-case-close").onclick = () => { sec.hidden = true; };
  if (det.caseMap) { det.caseMap.remove(); state.maps = state.maps.filter((x) => x !== det.caseMap); }
  const cm = makeMap("det-case-map", s.lat, s.lon, 14);
  det.caseMap = cm;
  const outline = L.geoJSON(s.boundary, { style: { color: "#c62828", weight: 3, fillOpacity: 0 } }).addTo(cm);
  const box = document.getElementById("det-case-layers");
  // Open on the satellite surface temperature (the street-level heat story), else the first layer.
  const first = Math.max(0, s.layers.findIndex((lay) => lay.module === "surface_heat"));
  const overlays = s.layers.map((lay, i) => {
    const o = L.imageOverlay(`casefiles/${encodeURIComponent(s.id)}/${encodeURIComponent(lay.file)}`, [[lay.bounds.south, lay.bounds.west], [lay.bounds.north, lay.bounds.east]], { opacity: 0.7 });
    if (i === first) o.addTo(cm);
    return o;
  });
  box.innerHTML = s.layers.map((lay, i) => `<button class="chip-btn ${i === first ? "on" : ""}" data-i="${i}">${esc(lay.name)}</button>`).join("");
  box.querySelectorAll("button").forEach((b) => (b.onclick = () => {
    const o = overlays[+b.dataset.i];
    if (cm.hasLayer(o)) { cm.removeLayer(o); b.classList.remove("on"); } else { o.addTo(cm); b.classList.add("on"); }
  }));
  cm.fitBounds(outline.getBounds().pad(1.5), { animate: false });
  sec.scrollIntoView({ behavior: "smooth", block: "start" });
}
