// The admin console: reliability (the thermal view), conversations, traces, feedback and
// the fleet. Charts are Observable Plot (vendored); every chart has a table view, a
// hover layer, and colours read from the theme's validated tokens.

import { api, clear, cssVar, el, fmt, initAccount, initTheme } from "./common.js";

const $ = (id) => document.getElementById(id);
const view = $("view");
const tip = $("tip");
const Plot = window.Plot;

initTheme($("theme"));
initAccount($("account"));
api("/api/health").then((h) => { $("version").textContent = `agent ${h.agent_version}`; }).catch(() => {});

const SUITES = { fixtures: "Given by the kit", extra: "Ours", variants: "Variants" };
const STATE_WORDS = {
  pass: "passed",
  fail: "failed a deterministic check",
  judge: "failed on the judge only",
  none: "not in this run",
};
const STAGES = { started: "Called", identified: "Identified", offered: "Offered", booked: "Booked" };

const colors = () => ({
  pass: cssVar("--pass"), fail: cssVar("--fail"), ink: cssVar("--ink"), ink2: cssVar("--ink-2"),
  muted: cssVar("--muted"), line: cssVar("--line"),
});

// ---- routing ----
const VIEWS = { reliability, conversations, traces, feedback, fleet, human };
let painters = [];
let current = () => Promise.resolve();

function route() {
  const [name, ...rest] = (location.hash.slice(1) || "reliability").split("/");
  const fn = VIEWS[name] ? name : "reliability";
  for (const a of document.querySelectorAll(".side-nav a")) {
    if (a.dataset.view === fn) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  hideTip();
  current = () => VIEWS[fn](...rest.map(decodeURIComponent));
  return current().catch((e) => clear(view, el("p", { class: "error-line", text: e.message })));
}

function paint() {
  for (const p of painters) p();
}

addEventListener("hashchange", () => route().then(() => view.focus({ preventScroll: true })));
document.addEventListener("themechange", paint);
let resizeTimer = 0;
addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(paint, 150); });

// ---- building blocks ----
function head(title, lead, ...extra) {
  return [
    el("div", { class: "view-head" }, el("h1", { class: "stamp view-title", text: title }), ...extra),
    lead ? el("p", { class: "lead" }, lead) : null,
  ];
}

function empty(text, link) {
  return el("div", { class: "empty panel" }, el("p", { text }), link || null);
}

function table(rows, columns) {
  return el("table", { class: "data" },
    el("thead", {}, el("tr", {}, columns.map((c) => el("th", { class: c.num ? "num" : "", text: c.label })))),
    el("tbody", {}, rows.map((r) => el("tr", {}, columns.map((c) => {
      const v = c.fmt ? c.fmt(r[c.key], r) : r[c.key];
      return el("td", { class: c.num ? "num" : "", text: v ?? "" });
    })))));
}

// A chart with a title, an optional legend, and a table view of the same rows.
function figure({ title, sub, legend, draw, rows, columns, wide }) {
  const box = el("div", { class: "chart" });
  const tbl = el("div", { hidden: true }, rows && columns ? table(rows, columns) : null);
  const toggle = el("button", { class: "btn quiet", type: "button", "aria-pressed": "false", text: "Show as table" });
  toggle.addEventListener("click", () => {
    const on = toggle.getAttribute("aria-pressed") !== "true";
    toggle.setAttribute("aria-pressed", String(on));
    toggle.textContent = on ? "Show as chart" : "Show as table";
    box.hidden = on;
    tbl.hidden = !on;
  });
  const fig = el("figure", { class: "figure", style: wide ? "grid-column: 1 / -1" : null },
    el("div", { class: "figure-head" },
      el("div", {}, el("h2", { class: "figure-title", text: title }), sub ? el("p", { class: "figure-sub" }, sub) : null),
      rows && columns ? toggle : null),
    legend || null, box, tbl);
  painters.push(() => {
    const width = Math.max(260, box.clientWidth || 600);
    clear(box, draw(width, colors()));
  });
  return fig;
}

function legend(items) {
  return el("div", { class: "legend" }, items.map(([cls, text]) =>
    el("span", { class: "key" }, el("span", { class: `swatch ${cls}` }), text)));
}

function base(width, height, extra = {}) {
  return {
    width, height,
    style: { background: "transparent", color: cssVar("--muted"), fontFamily: "IBM Plex Mono, monospace", fontSize: "11px" },
    ...extra,
  };
}

function render(...nodes) {
  painters = [];
  return (...built) => {
    clear(view, ...nodes, ...built);
    paint();
  };
}

function showTip(target, lines) {
  clear(tip, lines.map((l, i) => el(i === 0 ? "strong" : "div", { text: l })));
  tip.hidden = false;
  const r = target.getBoundingClientRect();
  const x = Math.min(innerWidth - tip.offsetWidth - 8, r.left + r.width + 8);
  const y = Math.min(innerHeight - tip.offsetHeight - 8, r.top - 4);
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}

function hideTip() {
  tip.hidden = true;
}

// counts are whole: an axis never shows 0.2 of a call
const whole = (v) => (Number.isInteger(v) ? fmt.num(v) : "");

function chip(text, kind = "") {
  return el("span", { class: `chip ${kind}`, text });
}

// ---- Reliability: version tiles and the thermal view ----
async function reliability() {
  const [{ runs }, th] = await Promise.all([api("/api/gate-runs"), api("/api/thermal?limit=6")]);
  const done = render(...head("Reliability", [
    "Every saved gate run, case by case. The frozen gate is pass^3 = 1.0: every case passes all three runs, the judge included. ",
    el("code", { text: "make gate" }), " and the nightly gate job save each run here."]));
  if (!runs.length) return done(empty("No gate runs saved yet. Run make gate and each run appears here."));

  const tiles = el("div", { class: "versions" }, runs.slice(0, 4).map((r, i) =>
    el("article", { class: `version${i === 0 ? " latest" : ""}` },
      el("div", { class: "top-row" },
        el("span", { class: "mono", text: r.agent_version }),
        chip(r.passed ? "Gate holds" : "Gate fails", r.passed ? "cold" : "hot")),
      el("p", { class: "big" }, `${r.passed_cases}/${r.cases}`, el("small", { text: `cases passing all ${r.k} run${r.k === 1 ? "" : "s"}` })),
      el("p", { class: "meta", text: `deterministic ${r.deterministic_passed_cases}/${r.cases} · judge ${r.judge_passed_cases}/${r.cases}` }),
      r.extract_cases != null ? el("p", { class: "meta", text: `extractor ${r.extract_passed_cases}/${r.extract_cases} cases heard right in all runs` }) : null,
      el("p", { class: "meta mono", text: `${r.runs} runs · ${r.model_calls} model calls · p95 ${fmt.secs(r.p95_ms)} · ≈ ${fmt.usd(r.est_cost_usd)}` }),
      r.failed.length ? el("p", { class: "meta", text: `Failed: ${r.failed.join(", ")}` }) : null,
      el("p", { class: "meta muted", text: fmt.time(r.run_ts) }))));

  const detail = el("div", { class: "detail", hidden: true, "aria-live": "polite" });
  const grid = thermalGrid(th, detail);
  const thermalTable = el("div", { hidden: true }, table(
    th.rows.map((r) => ({ fixture: r.fixture, suite: SUITES[r.suite] || r.suite, ...Object.fromEntries(r.cells.map((c, i) => [`c${i}`, c])) })),
    [{ key: "fixture", label: "Case" }, { key: "suite", label: "Suite" },
      ...th.columns.map((c, i) => ({ key: `c${i}`, label: `${c.agent_version.slice(0, 8)} r${c.run}` }))]));
  const toggle = el("button", { class: "btn quiet", type: "button", "aria-pressed": "false", text: "Show as table" });
  toggle.addEventListener("click", () => {
    const on = toggle.getAttribute("aria-pressed") !== "true";
    toggle.setAttribute("aria-pressed", String(on));
    toggle.textContent = on ? "Show as grid" : "Show as table";
    grid.hidden = on;
    thermalTable.hidden = !on;
  });

  done(
    tiles,
    el("h2", { class: "section-title" }, "Thermal view", toggle),
    el("p", { class: "lead", text: "Rows are cases; columns are runs, grouped by agent version, oldest on the left. Hot spots are failures." }),
    legend([["pass", "Passed"], ["judge", "Failed on the judge only"], ["fail", "Failed a deterministic check"], ["none", "Not in that run"]]),
    grid, thermalTable, detail);
}

function thermalGrid(th, detail) {
  // columns grouped by run (version and time); an 8px spacer column between groups
  const groups = [];
  th.columns.forEach((c, i) => {
    const g = groups[groups.length - 1];
    if (g && g.agent_version === c.agent_version && g.run_ts === c.run_ts) g.cols.push(i);
    else groups.push({ agent_version: c.agent_version, run_ts: c.run_ts, cols: [i] });
  });
  const colOf = {};
  const tracks = ["max-content"];
  let col = 2;
  groups.forEach((g, gi) => {
    if (gi > 0) { tracks.push("8px"); col += 1; }
    g.start = col;
    for (const i of g.cols) { colOf[i] = col; tracks.push("22px"); col += 1; }
  });
  const gridEl = el("div", { class: "thermal", style: `grid-template-columns: ${tracks.join(" ")}`, role: "grid", "aria-label": "Thermal view of gate runs" });
  let row = 1;
  gridEl.append(el("span", { style: `grid-row: ${row}; grid-column: 1` }));
  for (const g of groups) {
    gridEl.append(el("span", {
      class: "vhead", title: `${g.agent_version} · ${fmt.time(g.run_ts)}`,
      style: `grid-row: ${row}; grid-column: ${g.start} / span ${g.cols.length}`,
      text: g.agent_version.length > 12 ? g.agent_version.slice(0, 12) : g.agent_version,
    }));
  }
  let suite = null;
  for (const r of th.rows) {
    if (r.suite !== suite) {
      suite = r.suite;
      row += 1;
      gridEl.append(el("span", { class: "suite", style: `grid-row: ${row}`, text: SUITES[suite] || suite }));
    }
    row += 1;
    gridEl.append(el("span", { class: "rhead", style: `grid-row: ${row}; grid-column: 1`, text: r.fixture }));
    r.cells.forEach((state, i) => {
      const c = th.columns[i];
      const label = `${r.fixture}, ${c.agent_version} run ${c.run}: ${STATE_WORDS[state]}`;
      const cell = el("button", {
        class: `cell ${state}`, type: "button", "aria-label": label,
        style: `grid-row: ${row}; grid-column: ${colOf[i]}`, disabled: state === "none",
      });
      const lines = [r.fixture, `${c.agent_version} · run ${c.run} · ${fmt.time(c.run_ts)}`, STATE_WORDS[state]];
      cell.addEventListener("mouseenter", () => showTip(cell, lines));
      cell.addEventListener("focus", () => showTip(cell, lines));
      cell.addEventListener("mouseleave", hideTip);
      cell.addEventListener("blur", hideTip);
      cell.addEventListener("click", () => {
        clear(detail,
          el("p", {}, el("strong", { text: r.fixture }), ` · ${c.agent_version} run ${c.run}: ${STATE_WORDS[state]}.`),
          el("p", { class: "muted" }, "Reproduce it with its full trace: ", el("code", { text: `make trace ID=${r.fixture}` })));
        detail.hidden = false;
      });
      gridEl.append(cell);
    });
  }
  return el("div", { class: "thermal-wrap" }, gridEl);
}

// ---- Conversations ----
const conv = { days: 7, source: "" };

function seg(options, value, onPick, label) {
  return el("div", { class: "seg", role: "group", "aria-label": label }, options.map(([v, text]) => {
    const b = el("button", { type: "button", "aria-pressed": String(v === value), text });
    b.addEventListener("click", () => onPick(v));
    return b;
  }));
}

async function conversations() {
  const d = await api(`/api/conversations?days=${conv.days}&source=${conv.source}`);
  const filters = el("div", { class: "filters" },
    seg([[7, "7 days"], [30, "30 days"]], conv.days, (v) => { conv.days = v; route(); }, "Window"),
    seg([["", "All"], ["playground", "Playground"], ["gate", "Gate"]], conv.source, (v) => { conv.source = v; route(); }, "Source"));
  const done = render(...head("Conversations",
    "What callers asked for and how calls went, from content-free turn records: no words, no names, the phone only hashed.", filters));
  if (!d.turns) {
    return done(empty(`No turns in the last ${d.days} days.`, el("a", { href: "/", text: "Make a call in the caller simulator" })));
  }
  const lat = d.latency;
  const tiles = el("div", { class: "tiles" },
    tile("Calls", fmt.num(d.calls), `${fmt.num(d.turns)} turns`),
    tile("Answered without the model", d.zero_call_share === null ? "–" : fmt.pct(d.zero_call_share), "waiting, check-ins, hand-offs"),
    tile("Latency, model turns", lat.n ? fmt.secs(lat.p50) : "–", lat.n ? `p50 · p95 ${fmt.secs(lat.p95)}` : "no model turns"),
    tile("Model cost", fmt.usd(d.est_cost_usd), "the agent's calls, estimated"));

  const window = conv.days === 7 ? ["this week", "the week before"] : [`the last ${conv.days} days`, `the ${conv.days} before`];
  done(tiles, el("div", { class: "grid2", style: "margin-top: 16px" },
    funnelFigure(d.funnel),
    mixFigure(d.mix, d.drift, window),
    latencyFigure(lat),
    tokensFigure(d.tokens),
    apiFigure(d.api)));
}

function tile(k, v, s) {
  return el("div", { class: "tile" }, el("span", { class: "k", text: k }), el("span", { class: "v", text: v }), el("span", { class: "s", text: s }));
}

function funnelFigure(f) {
  const started = f.stages[0].calls || 1;
  const rows = f.stages.map((s) => ({ ...s, label: STAGES[s.stage], share: s.calls / started }));
  const exits = el("p", { class: "exits" }, "Left before booking: ",
    el("span", {}, "passed to a colleague ", el("b", { text: f.exits.transferred })),
    el("span", {}, "hung up ", el("b", { text: f.exits.hung_up })),
    el("span", {}, "fell back after an error ", el("b", { text: f.exits.fallback })));
  return figure({
    title: "From call to booking",
    sub: "Calls reaching each step",
    rows, columns: [{ key: "label", label: "Step" }, { key: "calls", label: "Calls", num: true }, { key: "share", label: "Of calls", num: true, fmt: fmt.pct }],
    draw: (w, c) => el("div", {}, Plot.plot(base(w, 180, {
      marginLeft: 84, marginRight: 70,
      x: { label: "calls →", grid: true, nice: true, tickFormat: whole },
      y: { label: null, domain: rows.map((r) => r.label) },
      marks: [
        Plot.barX(rows, { x: "calls", y: "label", fill: c.pass, rx: 3, insetTop: 4, insetBottom: 4, tip: { format: { share: (v) => fmt.pct(v) } }, channels: { share: "share" } }),
        Plot.text(rows, { x: "calls", y: "label", text: (r) => `${r.calls} · ${fmt.pct(r.share)}`, dx: 6, textAnchor: "start", fill: c.ink }),
        Plot.ruleX([0], { stroke: c.line }),
      ],
    })), exits),
  });
}

function mixFigure(mix, drift, [now, before]) {
  const kind = { stable: "cold", warn: "hot", alert: "hot" }[drift.band] || "";
  const words = drift.band === "too few"
    ? `Too few turns to measure drift: ${drift.current_turns} in ${now}, ${drift.baseline_turns} in ${before}; 30 each needed`
    : `${{ stable: "Stable", warn: "Shifting", alert: "Shifted" }[drift.band]} · PSI ${drift.psi}`;
  const rows = mix.map((m) => ({ ...m, label: m.intent.replace("_", " ") }));
  return figure({
    title: "What callers wanted",
    sub: el("span", { class: "badge" }, chip(words, kind)),
    legend: el("div", { class: "legend" },
      el("span", { class: "key" }, el("span", { class: "swatch", style: `background: ${cssVar("--ink")}; border-radius: 50%` }), now),
      el("span", { class: "key" }, el("span", { class: "swatch", style: `border: 2px solid ${cssVar("--muted")}; border-radius: 50%` }), before)),
    rows, columns: [{ key: "label", label: "Intent" }, { key: "now", label: now, num: true, fmt: fmt.pct }, { key: "before", label: before, num: true, fmt: fmt.pct }],
    draw: (w, c) => Plot.plot(base(w, 40 + rows.length * 28, {
      marginLeft: 100,
      x: { label: "share of turns →", tickFormat: "%", grid: true, domain: [0, Math.max(0.1, ...rows.map((r) => Math.max(r.now, r.before)))], nice: true },
      y: { label: null, domain: rows.map((r) => r.label) },
      marks: [
        Plot.link(rows, { x1: "before", x2: "now", y1: "label", y2: "label", stroke: c.line, strokeWidth: 2 }),
        Plot.dot(rows, { x: "before", y: "label", r: 5, stroke: c.muted, strokeWidth: 2, fill: "none" }),
        Plot.dot(rows, { x: "now", y: "label", r: 5, fill: c.ink }),
        Plot.tip(rows, Plot.pointerY({ x: "now", y: "label", title: (r) => `${r.label}\n${now}: ${fmt.pct(r.now)} (${r.now_turns} turns)\n${before}: ${fmt.pct(r.before)} (${r.before_turns} turns)` })),
      ],
    })),
  });
}

function latencyFigure(lat) {
  if (!lat.n) return figure({ title: "How long a model turn takes", sub: "No turns called the model in this window.", draw: () => el("span") });
  return figure({
    title: "How long a model turn takes",
    sub: `Share of model turns finished within each time; ${lat.zero_call_turns} turns needed no model call and are left out`,
    rows: lat.points, columns: [{ key: "ms", label: "Time", num: true, fmt: fmt.secs }, { key: "share", label: "Turns this fast or faster", num: true, fmt: fmt.pct }],
    draw: (w, c) => Plot.plot(base(w, 220, {
      marginRight: 20,
      x: { label: "seconds →", transform: (v) => v / 1000, grid: true },
      y: { label: "↑ share of turns", tickFormat: "%", domain: [0, 1], grid: true },
      marks: [
        Plot.ruleY([0], { stroke: c.line }),
        Plot.ruleX([lat.p50, lat.p95], { stroke: c.ink2, strokeDasharray: "3,3" }),
        Plot.text([{ x: lat.p50, t: `p50 ${fmt.secs(lat.p50)}` }, { x: lat.p95, t: `p95 ${fmt.secs(lat.p95)}` }], { x: "x", y: 0.08, text: "t", dx: 4, textAnchor: "start", fill: c.ink2 }),
        Plot.line(lat.points, { x: "ms", y: "share", curve: "step-after", stroke: c.fail, strokeWidth: 2 }),
        Plot.tip(lat.points, Plot.pointerX({ x: "ms", y: "share", title: (p) => `${fmt.secs(p.ms)}\n${fmt.pct(p.share)} of model turns this fast or faster` })),
      ],
    })),
  });
}

function tokensFigure(tokens) {
  if (!tokens.length) return figure({ title: "Tokens per model turn", sub: "No turns called the model in this window.", draw: () => el("span") });
  return figure({
    title: "Tokens per model turn",
    sub: "Prompt and answer together; the prompt grows with the call",
    rows: tokens.map((t, i) => ({ turn: i + 1, tokens: t })), columns: [{ key: "turn", label: "Model turn", num: true }, { key: "tokens", label: "Tokens", num: true, fmt: fmt.num }],
    draw: (w, c) => Plot.plot(base(w, 200, {
      x: { label: "tokens →" },
      y: { label: "↑ turns", grid: true, tickFormat: whole },
      marks: [
        Plot.rectY(tokens, Plot.binX({ y: "count" }, { x: (t) => t, fill: c.muted, inset: 1, rx: 2, tip: true })),
        Plot.ruleY([0], { stroke: c.line }),
      ],
    })),
  });
}

function apiFigure(rows) {
  const data = rows.map((r) => ({ ...r, outcome: r.status === 200 ? "Answered" : `Error ${r.status}` , ok: r.status === 200 }));
  const methods = [...new Set(data.map((r) => r.method))];
  return figure({
    title: "Calls to the rental API",
    sub: "By method; errors stand out hot",
    wide: true,
    legend: legend([["pass", "Answered (200)"], ["fail", "Error (4xx or 5xx)"]]),
    rows: data, columns: [{ key: "method", label: "Method" }, { key: "outcome", label: "Outcome" }, { key: "calls", label: "Calls", num: true }],
    draw: (w, c) => Plot.plot(base(w, 40 + methods.length * 30, {
      marginLeft: 150,
      x: { label: "calls →", grid: true, tickFormat: whole },
      y: { label: null, domain: methods },
      marks: [
        Plot.barX(data, { x: "calls", y: "method", fill: (r) => (r.ok ? c.pass : c.fail), rx: 2, insetTop: 5, insetBottom: 5, insetRight: 1, tip: { format: { fill: false } }, channels: { outcome: "outcome" } }),
        Plot.ruleX([0], { stroke: c.line }),
      ],
    })),
  });
}

// ---- Traces ----
const FIELD_ORDER = ["intent", "full_name", "people", "start", "end", "shift_days", "change_target_start", "tow_quote", "tow_kg", "power", "unit_quote", "unit", "unit_is_must", "heater_pref", "heater_reason", "answer", "consent_quote", "accepted_offer", "go_ahead"];

async function traces(sid, tid) {
  const { turns } = await api("/api/turns?days=30&source=playground");
  const done = render(...head("Traces",
    "One turn from the caller simulator, left to right: what decide heard, what the model understood (and the caller's words behind it), what the policy decided, what reached the API, and what was said."));
  if (!turns.length) return done(empty("No calls yet. Every turn of a call in the caller simulator shows up here.", el("a", { href: "/", text: "Make a call" })));
  if (!tid) {  // a call without a turn: its latest turn (the list is newest first)
    const t = turns.find((x) => x.session_id === sid) || turns[0];
    sid = t.session_id;
    tid = t.turn_id;
  }

  const list = el("nav", { class: "turn-list panel", "aria-label": "Recent turns" }, turns.slice(0, 200).map((t) =>
    el("a", { href: `#traces/${t.session_id}/${t.turn_id}`, "aria-current": String(t.session_id === sid && t.turn_id === tid) },
      el("span", { class: "d", text: t.decision || `gate: ${t.gate}` }),
      el("span", { class: "t", text: `${t.llm_calls} model · ${fmt.secs(t.latency_ms)}` }),
      el("span", { class: "t", text: `${t.intent || "no model call"} · ${t.session_id}` }),
      el("span", { class: "t", text: new Date(t.ts).toLocaleTimeString("en-GB") }))));

  let trace;
  try {
    trace = await api(`/api/trace/${sid}/${tid}`);
  } catch (e) {
    return done(el("div", { class: "traces" }, list, empty(e.message)));
  }
  done(el("div", { class: "traces" }, list, el("div", {}, pipeline(trace), traceActions(trace))));
}

function highlight(text, quotes) {
  const marks = [];
  const lower = text.toLowerCase();
  for (const q of quotes) {
    if (!q) continue;
    const at = lower.indexOf(q.toLowerCase());
    if (at >= 0) marks.push([at, at + q.length]);
  }
  marks.sort((a, b) => a[0] - b[0]);
  const out = [];
  let i = 0;
  for (const [a, b] of marks) {
    if (a < i) continue;
    out.push(text.slice(i, a), el("mark", { text: text.slice(a, b) }));
    i = b;
  }
  out.push(text.slice(i));
  return out;
}

function pipeline(trace) {
  const s = trace.step;
  const st = s.state || {};
  // the agent drops a fact whose quote isn't the caller's words, but keeps the quote:
  // only a quote whose fact survived is evidence (D-54)
  const kept = {
    tow_quote: st.tow_kg !== null && st.tow_kg !== undefined,
    unit_quote: Boolean(st.unit),
    consent_quote: Boolean(st.accepted_offer || st.go_ahead),
  };
  const quotes = Object.keys(kept).filter((k) => kept[k]).map((k) => st[k]);
  const msgs = trace.transcript.slice(-10);
  let latestFrom = msgs.length;
  while (latestFrom > 0 && msgs[latestFrom - 1].role === "user") latestFrom -= 1;

  const heard = el("div", { class: "heard" }, msgs.map((m, i) =>
    el("div", { class: `m ${m.role}${i >= latestFrom ? " latest" : ""}` },
      el("div", { class: "r", text: m.role === "user" ? "caller" : m.role === "assistant" ? "receptionist" : "api" }),
      m.role === "user" ? highlight(m.content, quotes) : m.content,
      m.interrupted ? el("span", { class: "muted", text: " (cut off)" }) : null)));

  let understood;
  if (!s.state) {
    understood = el("p", { class: "muted", text: s.gate !== "none"
      ? `Not asked: the turn gate settled this turn (${s.gate}) without the model.`
      : "The model's answer didn't pass validation, so the receptionist asked again." });
  } else {
    const shown = FIELD_ORDER.filter((k) => st[k] !== null && st[k] !== false && st[k] !== "");
    understood = el("dl", { class: "fields" }, shown.flatMap((k) => [
      el("dt", { text: k }),
      el("dd", {}, !k.endsWith("_quote") ? String(st[k])
        : kept[k] ? el("mark", { text: st[k] })
        : [el("s", { class: "muted", text: st[k] }), el("span", { class: "muted", text: " not the caller's words, so ignored" })]),
    ]));
  }

  const decided = el("div", { class: "said" },
    el("p", { class: "stamp", style: "font-size: 2rem", text: s.decision || s.gate }),
    el("dl", { class: "fields" },
      el("dt", { text: "intent" }), el("dd", { text: s.intent || "–" }),
      el("dt", { text: "gate" }), el("dd", { text: s.gate }),
      el("dt", { text: "model" }), el("dd", { text: `${s.llm.calls} call · ${fmt.num(s.llm.input_tokens)}/${fmt.num(s.llm.output_tokens)} tokens` }),
      el("dt", { text: "time" }), el("dd", { text: fmt.secs(s.latency_ms) }),
      el("dt", { text: "version" }), el("dd", { class: "mono", text: s.agent_version })),
    s.fallback ? chip("Fell back after an error", "hot") : null);

  const called = s.api_calls.length
    ? el("div", { class: "calls-l" }, s.api_calls.map((c) => el("div", {},
        chip(`${c.status}`, c.status === 200 ? "cold" : "hot"), " ", c.method, c.code ? ` ${c.code}` : "")))
    : el("p", { class: "muted", text: "No API calls." });

  const said = el("div", { class: "said" }, s.effects.filter((e) => e.type !== "note").map((e) => {
    switch (e.type) {
      case "speech": return el("p", { text: e.text });
      case "sms": return el("p", {}, el("span", { class: "fx", text: "Text message " }), el("span", { class: "mono", text: e.text }));
      case "transfer": return el("p", {}, el("span", { class: "fx", text: "Transfer " }), `to ${e.to}: ${e.reason}`);
      case "hangup": return el("p", {}, el("span", { class: "fx", text: "Hung up " }), e.reason || "");
      case "silence": return el("p", { class: "muted", text: `Waited ${e.seconds} s` });
      default: return el("p", { class: "muted", text: e.type });
    }
  }));

  const col = (title, body) => el("section", { class: "stage-col" }, el("h3", { text: title }), body);
  return el("div", { class: "pipeline" },
    col("Heard", heard), col("Understood", understood), col("Decided", decided), col("Called", called), col("Said", said));
}

function traceActions(trace) {
  const flag = el("button", { class: "btn", type: "button", text: "Flag this turn" });
  flag.addEventListener("click", async () => {
    try {
      await api("/api/feedback", { session_id: trace.session_id, turn_id: trace.step.turn_id, verdict: "down", note: "flagged in the trace view" });
      flag.textContent = "Flagged for review";
      flag.disabled = true;
    } catch (e) {
      flag.textContent = e.message;
    }
  });
  return el("div", { class: "trace-actions" },
    el("a", { class: "btn", href: `/#call=${trace.session_id}`, text: "Open the call in the simulator" }), flag);
}

// ---- Feedback ----
const fbState = { verdict: "" };

async function feedback() {
  const [{ feedback: items }, grid] = await Promise.all([
    api(`/api/feedback${fbState.verdict ? `?verdict=${fbState.verdict}` : ""}`),
    api("/api/fleet?days=1"),
  ]);
  const units = grid.units.map((u) => [u.id, u.name]);
  const filters = seg([["", "All"], ["down", "Flagged"], ["up", "Good"]], fbState.verdict, (v) => { fbState.verdict = v; route(); }, "Show");
  const done = render(...head("Feedback",
    "Receptionist lines rated in the caller simulator, newest first. A person turns a line into a test case here: what should have happened, in a form. Nothing becomes a test case on its own.", filters));
  if (!items.length) return done(empty("Nothing rated yet. Rate any receptionist line with a thumb in the caller simulator's transcript.", el("a", { href: "/", text: "Open the caller simulator" })));
  done(el("div", { class: "fb" }, items.map((f) => {
    const slot = el("div");
    const save = el("button", { class: "btn", type: "button", text: "Save as test case" });
    save.addEventListener("click", () => { save.hidden = true; clear(slot, promoteForm(f, units, slot, save)); });
    return el("article", { class: "fb-item" },
      el("div", { class: "row" },
        chip(f.verdict === "down" ? "Flagged" : "Good", f.verdict === "down" ? "hot" : "cold"),
        el("span", { text: fmt.time(f.created) }),
        el("span", { class: "mono", text: f.agent_version }),
        f.decision ? el("span", { text: f.decision }) : null,
        f.promoted_fixture ? chip(`Test case ${f.promoted_fixture}`, "cold") : null),
      f.reply ? el("blockquote", { text: f.reply }) : el("p", { class: "muted", text: "The call has expired; only the note is left." }),
      f.note ? el("p", { class: "note", text: f.note }) : null,
      el("details", {}, el("summary", { text: "The call so far" }),
        el("div", { class: "heard", style: "margin-top: 8px" }, f.transcript.map((m) =>
          el("div", { class: `m ${m.role}` }, el("div", { class: "r", text: m.role === "user" ? "caller" : m.role === "assistant" ? "receptionist" : "api" }), m.content)))),
      el("div", { class: "trace-actions" },
        el("a", { href: `#traces/${f.session_id}/${f.turn_id}`, text: "Open its trace" }),
        f.reply && !f.promoted_fixture ? save : null),
      slot);
  })));
}

// What the agent did, as the harness names actions: the reviewer starts from it.
const ACTION_OF = { Handoff: "transfer", Goodbye: "end_call", Hold: "wait" };
const ACTIONS = [["say", "Answer: say something"], ["transfer", "Pass the call to a colleague"], ["end_call", "End the call"], ["wait", "Wait, saying nothing"]];

function promoteForm(f, units, slot, opener) {
  const field = (label, control, hint) => el("label", { class: "field" }, label, control, hint ? el("span", { class: "muted", text: hint }) : null);
  const title = el("input", { class: "text", name: "title", required: true, minlength: "3", maxlength: "60", placeholder: "What this case checks, e.g. Offers Didžioji when there's no power" });
  const action = el("select", { name: "action" }, ACTIONS.map(([v, t]) => el("option", { value: v, text: t, selected: v === (ACTION_OF[f.decision] || "say") })));
  const change = el("input", { type: "checkbox", name: "change" });
  const unit = el("select", { name: "unit" }, units.map(([id, name]) => el("option", { value: id, text: name })));
  const start = el("input", { type: "date", name: "start", value: "2026-10-06" });
  const end = el("input", { type: "date", name: "end", value: "2026-10-07" });
  const people = el("input", { type: "number", name: "people", min: "1", max: "20", placeholder: "any" });
  const status = el("select", { name: "status" }, [["booked", "booked"], ["cancelled", "cancelled"]].map(([v, t]) => el("option", { value: v, text: t })));
  const bookingId = el("input", { class: "text", name: "booking_id", placeholder: "bk_88, only for a change to an existing booking", maxlength: "8" });
  const booking = el("fieldset", { class: "booking-want", disabled: true },
    el("legend", { class: "visually-hidden", text: "The booking" }),
    el("div", { class: "row2" }, field("Sauna", unit), field("People", people)),
    el("div", { class: "row2" }, field("From", start), field("To", end)),
    el("div", { class: "row2" }, field("Status", status), field("Booking id", bookingId)));
  change.addEventListener("change", () => { booking.disabled = !change.checked; });
  const judge = el("textarea", { class: "text", name: "judge", rows: "4", required: true,
    placeholder: "One statement per line, each true of the right reply. For example:\nThe reply offers Didžioji for Saturday the tenth to Sunday the eleventh of October.\nThe reply doesn't offer Vidutinė." });
  const why = el("input", { class: "text", name: "why", maxlength: "300", value: f.note || "" });
  const confirm = el("input", { type: "checkbox", name: "confirm", required: true });
  const out = el("div", { "aria-live": "polite" });
  const form = el("form", { class: "promote panel" },
    el("h3", { class: "section-title", text: "Save as test case" }),
    el("p", { class: "muted", text: "The case is the call as the receptionist heard it at this line, and what should have happened. It goes into the gate, and stays red until the agent gets it right." }),
    field("Title", title),
    field("What should the receptionist have done?", action),
    el("label", { class: "toggle" }, change, "A booking should be made or changed on this turn"),
    booking,
    field("What must the reply say? One statement per line", judge, "A judge reads the reply and checks each statement."),
    field("Why this case (optional)", why),
    el("label", { class: "toggle" }, confirm, "Everyone in this call is a seed customer or made up: no real names, numbers or addresses."),
    el("div", { class: "trace-actions" },
      el("button", { class: "btn primary", type: "submit", text: "Save test case" }),
      el("button", { class: "btn ghost", type: "button", text: "Cancel", onclick: () => { clear(slot); opener.hidden = false; } })),
    out);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      session_id: f.session_id, turn_id: f.turn_id, title: title.value.trim(), action: action.value,
      judge: judge.value.split("\n").map((l) => l.trim()).filter(Boolean),
      why: why.value.trim(), confirm: confirm.checked,
      bookings: change.checked ? [{ unit: unit.value, start: start.value, end: end.value,
        people: people.value ? Number(people.value) : null, status: status.value, id: bookingId.value.trim() }] : [],
    };
    try {
      const r = await api("/api/promote", body);
      clear(slot, el("div", { class: "detail" },
        el("p", {}, el("strong", { text: r.written ? `Saved as fixtures/extra/${r.file}` : `${r.file} is ready` }),
          r.written ? "" : " (this server keeps no files: download it into fixtures/extra)."),
        r.written ? null : el("p", {}, el("a", { href: URL.createObjectURL(new Blob([r.yaml], { type: "text/yaml" })), download: r.file, text: `Download ${r.file}` })),
        el("p", { class: "muted" }, "Run it three times: ", el("code", { text: `cd python && ${r.run}` })),
        el("p", { class: "muted" }, "Then the whole gate: ", el("code", { text: "make gate" }), ". Red until fixed; green, and it guards from then on.")));
    } catch (err) {
      clear(out, el("p", { class: "error-line", role: "alert", text: err.message }));
    }
  });
  return form;
}

// ---- Fleet ----
const DAY_WORDS = { free: "free", booked: "booked", turnaround: "a turnaround day", blocked: "in for service" };
const ask = { people: 6, tow_kg: 1500, power: false, start: "2026-10-13", end: "2026-10-14" };

async function fleet() {
  const grid = await api("/api/fleet?days=42");
  const done = render(...head("Fleet",
    "The next six weeks as every simulated call starts from them. Booked days never say who. Ask the what-if below why a sauna would or wouldn't take a booking: the answers come from the same rule mirror the receptionist checks before every write."));
  const cal = fleetGrid(grid);
  const rows = grid.units.flatMap((u) => u.days.filter((d) => d.state !== "free").map((d) => ({ unit: u.name, date: d.date, state: DAY_WORDS[d.state] })));
  const calTable = el("div", { hidden: true }, table(rows, [{ key: "unit", label: "Sauna" }, { key: "date", label: "Day", fmt: (v) => fmt.date(v) }, { key: "state", label: "State" }]));
  const toggle = el("button", { class: "btn quiet", type: "button", "aria-pressed": "false", text: "Show as table" });
  toggle.addEventListener("click", () => {
    const on = toggle.getAttribute("aria-pressed") !== "true";
    toggle.setAttribute("aria-pressed", String(on));
    toggle.textContent = on ? "Show as calendar" : "Show as table";
    cal.hidden = on;
    calTable.hidden = !on;
  });
  const results = el("div", { "aria-live": "polite" });
  const form = whatIfForm(results, cal);
  done(
    el("h2", { class: "section-title" }, "Calendar", toggle),
    legend([["fail", "Booked"], ["turnaround", "Turnaround day"], ["blocked", "In for service"], ["free", "Free"]]),
    cal, calTable,
    el("h2", { class: "section-title", text: "What if?" }),
    el("div", { class: "whatif" }, form, results));
  form.requestSubmit();
}

function fleetGrid(grid) {
  const days = grid.units[0]?.days.map((d) => d.date) || [];
  const g = el("div", { class: "fleet", role: "img", "aria-label": `Calendar of ${grid.units.length} saunas over ${days.length} days; the table view lists every busy day`,
    style: `grid-template-columns: max-content repeat(${days.length}, 20px)` });
  // month labels, then day headers
  days.forEach((d, i) => {
    const dt = new Date(d + "T00:00:00Z");
    if (i === 0 || dt.getUTCDate() === 1) {
      // a month's label spans only its own days, so two never overlap
      const left = days.slice(i).findIndex((x, j) => j > 0 && x.endsWith("-01"));
      const span = left === -1 ? days.length - i : left;
      const name = dt.toLocaleString("en-GB", { month: span >= 6 ? "long" : "short", timeZone: "UTC" });
      g.append(el("span", { class: "mh", style: `grid-row: 1; grid-column: ${i + 2} / span ${span}`, text: name }));
    }
    const we = dt.getUTCDay() === 0 || dt.getUTCDay() === 6;
    g.append(el("span", { class: `dh${we ? " we" : ""}`, style: `grid-row: 2; grid-column: ${i + 2}` },
      "MTWTFSS"[(dt.getUTCDay() + 6) % 7], el("br"), String(dt.getUTCDate())));
  });
  grid.units.forEach((u, ui) => {
    const row = ui + 3;
    g.append(el("div", { class: "uh", style: `grid-row: ${row}; grid-column: 1` },
      el("span", { class: "stamp", text: u.name }),
      el("span", { class: "muted", text: `${u.capacity} seats · ${fmt.kg(u.weight_kg)} · ${u.heater}${u.needs_power ? " · needs mains" : ""} · ${fmt.euros(u.price_per_day)}/day` })));
    u.days.forEach((d, i) => {
      const dt = new Date(d.date + "T00:00:00Z");
      const we = dt.getUTCDay() === 0 || dt.getUTCDay() === 6;
      g.append(el("span", { class: `day ${d.state}${we ? " we" : ""}`, dataset: { date: d.date },
        title: `${u.name}, ${fmt.date(d.date)}: ${DAY_WORDS[d.state]}`, style: `grid-row: ${row}; grid-column: ${i + 2}` }));
    });
  });
  return el("div", { class: "fleet-wrap" }, g);
}

function whatIfForm(results, cal) {
  const input = (name, label, attrs) => el("label", { class: "field" }, label, el("input", { name, ...attrs }));
  const form = el("form", { class: "panel" },
    el("div", { class: "row2" },
      input("people", "People", { type: "number", min: "1", max: "20", value: ask.people, required: true }),
      input("tow_kg", "The car tows, kg", { type: "number", min: "0", max: "10000", step: "50", value: ask.tow_kg, required: true })),
    el("div", { class: "row2" },
      input("start", "From", { type: "date", value: ask.start, required: true }),
      input("end", "To", { type: "date", value: ask.end, required: true })),
    el("label", { class: "toggle" }, el("input", { type: "checkbox", name: "power", checked: ask.power }), "Mains power at the site"),
    el("button", { class: "btn primary", type: "submit", text: "Check every sauna" }));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    Object.assign(ask, { people: Number(f.get("people")), tow_kg: Number(f.get("tow_kg")), power: f.get("power") === "on", start: f.get("start"), end: f.get("end") });
    try {
      const out = await api("/api/what-if", ask);
      for (const d of cal.querySelectorAll(".day")) d.classList.toggle("asked", d.dataset.date >= ask.start && d.dataset.date <= ask.end);
      clear(results, out.units.map((u) => el("div", { class: "fit" },
        el("div", {}, el("span", { class: "name", text: u.name }), el("br"), chip(u.fits ? "Would take it" : "Wouldn't take it", u.fits ? "cold" : "hot")),
        u.fits
          ? el("p", { class: "muted", text: `${u.capacity} seats, ${fmt.kg(u.weight_kg)}, free on those days.` })
          : el("ul", {}, u.why.map((w) => el("li", { text: w }))))));
    } catch (err) {
      clear(results, el("p", { class: "error-line", text: err.message }));
    }
  });
  return form;
}

// ---- Human eval ----
const RUBRIC_LABELS = {
  natural: "Natural", numbers_clear: "Numbers and dates", one_question: "One question at a time", turn_taking: "Turn-taking",
};

async function human() {
  const d = await api("/api/human-eval");
  const done = render(...head("Human eval", [
    "Calls made and rated by people following the scenario cards (",
    el("code", { text: "tests/human/README.md" }),
    "). The same questions then go to the kit's judge, and the agreement says how far a judge verdict can be trusted.",
  ]));
  const tiles = el("div", { class: "tiles" },
    tile("Ratings", fmt.num(d.ratings), `${d.raters} rater${d.raters === 1 ? "" : "s"}`),
    tile("Cards rated", `${d.scenarios_rated}/${d.scenarios}`, "each card can be rated by more than one person"),
    tile("Goal reached", d.goal === null ? "–" : fmt.pct(d.goal), "as the raters judged it"));
  const calib = calibrationBlock(d.calibration);
  if (!d.ratings) {
    return done(tiles, empty("No ratings yet. On the caller simulator's first screen, pick a scenario card, make the call, and rate it when it ends.",
      el("a", { href: "/", text: "Open the caller simulator" })), calib);
  }
  const rows = d.cards.map((c) => ({ ...c, goalTxt: c.goal === null ? "–" : fmt.pct(c.goal),
    systemTxt: c.system_outcome === null ? "–" : fmt.pct(c.system_outcome) }));
  const cardsTable = el("table", { class: "data" },
    el("thead", {}, el("tr", {}, ["Card", "Expected", "Ratings", "Goal (people)", "Goal (call record)",
      ...Object.values(RUBRIC_LABELS), "Calls"].map((h, i) => el("th", { class: i >= 2 && i < 9 ? "num" : "", text: h })))),
    el("tbody", {}, rows.map((c) => el("tr", {},
      el("td", { text: c.title }), el("td", { text: c.outcome }),
      el("td", { class: "num", text: `${c.ratings} (${c.raters})` }),
      el("td", { class: "num", text: c.goalTxt }), el("td", { class: "num", text: c.systemTxt }),
      Object.keys(RUBRIC_LABELS).map((k) => el("td", { class: "num", text: c[k] === null ? "–" : c[k].toFixed(1) })),
      el("td", {}, c.sessions.map((sid) => el("span", {},
        el("a", { href: `/#call=${sid}&card=${c.id}`, text: "rate again" }), " · ",
        el("a", { href: `#traces/${sid}`, text: "trace" }), " ")))))));
  const spread = d.items.map((i) => ({ ...i, label: RUBRIC_LABELS[i.item] }));
  done(tiles,
    el("h2", { class: "section-title", text: "By card" }), el("div", { class: "panel", style: "overflow-x: auto" }, cardsTable),
    el("div", { class: "grid2", style: "margin-top: 16px" },
      figure({
        title: "The rubric", sub: "Mean score with the lowest and highest given, 1 to 5",
        rows: spread, columns: [{ key: "label", label: "Item" }, { key: "mean", label: "Mean", num: true }, { key: "min", label: "Lowest", num: true }, { key: "max", label: "Highest", num: true }],
        draw: (w, c) => Plot.plot(base(w, 40 + spread.length * 34, {
          marginLeft: 150, x: { domain: [1, 5], label: "score →", grid: true }, y: { label: null, domain: spread.map((r) => r.label) },
          marks: [
            Plot.ruleY(spread, { x1: "min", x2: "max", y: "label", stroke: c.muted, strokeWidth: 2 }),
            Plot.dot(spread, { x: "mean", y: "label", r: 6, fill: c.ink, tip: true }),
          ],
        })),
      }),
      calib),
    d.notes.length ? el("h2", { class: "section-title", text: "Notes" }) : null,
    d.notes.length ? el("div", { class: "fb" }, d.notes.map((n) => el("article", { class: "fb-item" },
      el("div", { class: "row" }, el("span", { class: "mono", text: n.rater }), el("span", { text: n.scenario }), el("span", { text: fmt.time(n.created) })),
      el("p", { class: "note", text: n.note })))) : null);
}

function calibrationBlock(cal) {
  if (!cal) {
    return el("div", { class: "figure" }, el("h2", { class: "figure-title", text: "Judge calibration" }),
      el("p", { class: "figure-sub" }, "Not run yet. Once calls are rated, ", el("code", { text: "make calibrate" }),
        " asks the kit's judge the same questions about the same calls and saves how often it agrees with people (Cohen's κ)."));
  }
  const k = (v) => (v === null || v === undefined ? "–" : Number(v).toFixed(2));
  return el("div", { class: "figure" },
    el("h2", { class: "figure-title", text: "Judge calibration" }),
    el("p", { class: "figure-sub", text: `${cal.ratings} rated calls, judge ${cal.judge_model}, ${fmt.time(cal.run_ts)}. κ above 0.6 is substantial agreement; near 0 is chance.` }),
    el("table", { class: "data" },
      el("thead", {}, el("tr", {}, ["Item", "Calls", "Agree", "κ"].map((h, i) => el("th", { class: i ? "num" : "", text: h })))),
      el("tbody", {},
        Object.entries(cal.items).map(([item, r]) => el("tr", {},
          el("td", { text: item === "goal" ? "Goal" : RUBRIC_LABELS[item] || item }),
          el("td", { class: "num", text: r.n }), el("td", { class: "num", text: r.agree === null ? "–" : fmt.pct(r.agree) }),
          el("td", { class: "num", text: k(r.kappa) }))),
        el("tr", {}, el("td", { text: "All items" }), el("td", { class: "num", text: "" }),
          el("td", { class: "num", text: cal.overall_agree === null ? "–" : fmt.pct(cal.overall_agree) }), el("td", { class: "num", text: k(cal.overall_kappa) })),
        el("tr", {}, el("td", { text: "Rater with rater" }), el("td", { class: "num", text: `${cal.rater_pairs} pairs` }),
          el("td", { class: "num", text: "" }), el("td", { class: "num", text: k(cal.rater_kappa) })))));
}

// start only once every view's state above exists: a direct link to any view must work
route();
