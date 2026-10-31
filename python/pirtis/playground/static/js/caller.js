// The caller simulator: dial in as a seed customer or an unknown number, talk (or type),
// and watch the booking card condense as the receptionist learns each detail.

import { api, clear, currentTheme, el, fmt, initAccount, initTheme, reducedMotion } from "./common.js";

const $ = (id) => document.getElementById(id);
const S = { call: null, personas: [], spoken: 0, rated: new Map(), busy: false, cards: [], card: null, scored: false };

initTheme($("theme"));
initAccount($("account"));

// ---- smoke: soft plumes rising from the stove; more of them while someone speaks ----
const smoke = (() => {
  const canvas = $("smoke");
  const ctx = canvas.getContext("2d");
  let w = 0, h = 0, parts = [], level = 0.5, target = 0.5, raf = 0;
  const size = () => {
    const dpr = Math.min(2, devicePixelRatio || 1);
    w = innerWidth; h = innerHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };
  const source = () => {
    const stove = $("stove");
    if (stove && !$("call").hidden) {
      const r = stove.getBoundingClientRect();
      return { x: r.left + r.width / 2, spread: r.width, y: r.top };
    }
    return { x: w / 2, spread: w * 0.7, y: h + 40 };
  };
  const spawn = (age) => {
    const s = source();
    return { x: s.x + (Math.random() - 0.5) * s.spread, y: s.y + Math.random() * 30, r: 30 + Math.random() * 80,
      vy: 0.22 + Math.random() * 0.4, sway: Math.random() * 6.3, life: age, max: 700 + Math.random() * 600 };
  };
  const frame = () => {
    level += (target - level) * 0.03;
    ctx.clearRect(0, 0, w, h);
    const dark = currentTheme() === "dark";
    const rgb = dark ? "231,236,238" : "60,52,56";
    const want = Math.round(16 + 34 * level);
    while (parts.length < want) parts.push(spawn(parts.length ? 0 : Math.random() * 500));
    for (const p of parts) {
      p.life += 1;
      p.y -= p.vy * (0.6 + level);
      p.x += Math.sin(p.life / 90 + p.sway) * 0.35;
      p.r += 0.07;
      const a = Math.sin(Math.PI * Math.min(1, p.life / p.max)) * (dark ? 0.05 : 0.035) * (0.6 + level);
      const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.r);
      g.addColorStop(0, `rgba(${rgb},${a.toFixed(4)})`);
      g.addColorStop(1, `rgba(${rgb},0)`);
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
    }
    parts = parts.filter((p) => p.life < p.max && p.y + p.r > -40).slice(0, want);
    raf = requestAnimationFrame(frame);
  };
  const start = () => {
    cancelAnimationFrame(raf);
    if (reducedMotion()) { ctx.clearRect(0, 0, w, h); return; }
    size();
    raf = requestAnimationFrame(frame);
  };
  addEventListener("resize", size);
  matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", start);
  start();
  return { set: (v) => { target = v; } };
})();

// ---- voice out: every receptionist line is spoken at the speed the agent asked for ----
function pickVoice() {
  const voices = speechSynthesis.getVoices();
  return voices.find((v) => v.lang === "en-GB") || voices.find((v) => v.lang.startsWith("en")) || null;
}

function speak(text, speed) {
  if (!$("speak").checked || !("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-GB";
  u.rate = speed || 1;
  const v = pickVoice();
  if (v) u.voice = v;
  u.onstart = () => { $("stove").classList.add("speaking"); smoke.set(1); };
  u.onend = u.onerror = () => {
    if (!speechSynthesis.speaking) { $("stove").classList.remove("speaking"); smoke.set(0.5); }
  };
  speechSynthesis.speak(u);
}

function hush() {
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  $("stove").classList.remove("speaking");
}

// ---- voice in: hold to talk, where the browser can listen ----
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null;
let heard = "";
let cutIn = false;

function listen() {
  if (rec || S.busy || !S.call || S.call.status !== "open") return;
  cutIn = "speechSynthesis" in window && speechSynthesis.speaking;
  hush();
  heard = "";
  rec = new Recognition();
  rec.lang = "en-GB";
  rec.interimResults = true;
  rec.continuous = true;
  rec.onresult = (e) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) heard += r[0].transcript + " ";
      else interim += r[0].transcript;
    }
    $("interim").textContent = (heard + interim).trim();
  };
  rec.onerror = (e) => {
    if (e.error !== "no-speech" && e.error !== "aborted") showError(`The microphone stopped: ${e.error}.`);
  };
  rec.onend = () => {
    rec = null;
    setListening(false);
    const text = heard.trim();
    if (text) send(text, cutIn);
    else $("interim").textContent = "";
  };
  rec.start();
  setListening(true);
}

function stopListening() {
  if (rec) rec.stop();
}

function setListening(on) {
  $("mic").setAttribute("aria-pressed", String(on));
  $("mic").textContent = on ? "Listening… let go to send" : "Hold to talk";
  $("stove").classList.toggle("listening", on);
  smoke.set(on ? 0.9 : 0.5);
}

if (!Recognition) {
  $("mic").disabled = true;
  $("mic").textContent = "No microphone here";
  $("mic").title = "This browser can't listen. Type what you'd say instead.";
} else {
  const mic = $("mic");
  mic.addEventListener("pointerdown", (e) => { e.preventDefault(); listen(); });
  mic.addEventListener("pointerup", stopListening);
  mic.addEventListener("pointerleave", stopListening);
  mic.addEventListener("keydown", (e) => {
    if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); listen(); }
  });
  mic.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") stopListening(); });
}

// ---- the call ----
const STATUS = {
  open: ["On the line", "cold"],
  ended: ["Call ended", ""],
  transferred: ["Passed to a colleague", "hot"],
};
const CARD_STATUS = {
  gathering: ["Gathering details", ""],
  offered: ["Offered, waiting for your yes", "hot"],
  booked: ["Booked", "cold"],
  moved: ["Moved", "cold"],
  cancelled: ["Cancelled", ""],
  transferred: ["Passed to a colleague", "hot"],
  ended: ["Call ended", ""],
};
const NEXT = { name: "your name", dates: "the dates", people: "how many", tow: "what the car tows", power: "about mains power" };

function chip(node, [text, kind]) {
  node.className = `chip ${kind}`;
  node.textContent = text;
}

function showError(msg) {
  const box = S.call ? $("call-error") : $("dial-error");
  box.textContent = msg;
  box.hidden = !msg;
}

function personaFor(phone) {
  return S.personas.find((p) => p.phone === phone);
}

async function startCall(p) {
  try {
    const view = await api("/api/session", { phone: p.phone });
    history.replaceState(null, "", `#call=${view.session_id}${S.card ? `&card=${S.card.id}` : ""}`);
    S.spoken = 0;
    S.rated.clear();
    render(view);
    $("text").focus();
  } catch (e) {
    showError(e.message);
  }
}

async function send(text, barge = false) {
  if (S.busy || !S.call) return;
  const bargeIn = barge || $("barge").checked || ("speechSynthesis" in window && speechSynthesis.speaking);
  hush();
  S.busy = true;
  setBusy(true);
  $("interim").textContent = `You: ${text}`;
  try {
    const view = await api("/api/turn", {
      session_id: S.call.session_id,
      text,
      anger: Number($("anger").value),
      barge_in: bargeIn,
    });
    $("barge").checked = false;
    $("text").value = "";
    $("interim").textContent = "";
    showError("");
    render(view);
  } catch (e) {
    $("interim").textContent = "";
    showError(e.message);
  } finally {
    S.busy = false;
    setBusy(false);
  }
}

async function post(path, extra = {}) {
  if (S.busy || !S.call) return;
  hush();
  S.busy = true;
  setBusy(true);
  try {
    render(await api(path, { session_id: S.call.session_id, ...extra }));
    showError("");
  } catch (e) {
    showError(e.message);
  } finally {
    S.busy = false;
    setBusy(false);
  }
}

function setBusy(on) {
  for (const id of ["send", "quiet", "hangup", "text"]) $(id).disabled = on;
  $("stove").classList.toggle("thinking", on);
}

function render(view) {
  S.call = view;
  $("dial").hidden = true;
  $("call").hidden = false;
  $("drawers").hidden = false;

  const p = personaFor(view.phone);
  $("who").textContent = p && p.kind === "customer" ? `Calling as ${p.label}` : "Calling from an unknown number";
  chip($("status"), STATUS[view.status]);
  $("call").classList.toggle("closed", view.status !== "open");

  const ours = view.items.filter((i) => i.role === "assistant");
  const last = ours[ours.length - 1];
  clear($("line"),
    el("span", { class: "speaker", text: "Receptionist" }),
    last ? last.content + (last.interrupted ? " —" : "") : "");

  // speak whatever is new since last time, at the speed the agent chose
  const speeds = (view.last?.actions || []).filter((a) => a.type === "say").map((a) => a.generation_config?.speed);
  const fresh = ours.slice(S.spoken);
  fresh.forEach((line, i) => speak(line.content, speeds[i] || 1));
  S.spoken = ours.length;

  renderCard(view.card);
  renderTranscript(view);
  renderLastTurn(view);
  renderEnd(view);
  renderScenario(view);
}

function renderEnd(view) {
  let again = $("again");
  if (view.status === "open") { if (again) again.remove(); return; }
  if (!again) {
    again = el("button", { class: "btn primary", id: "again", type: "button", text: "Make another call",
      onclick: () => { history.replaceState(null, "", location.pathname); location.reload(); } });
    $("call-error").before(again);
  }
}

const FACTS = [
  { key: "who", label: "Caller", next: "name",
    value: (c) => (c.identified ? c.name || "On the books" : null),
    note: (c) => (c.identified ? "Found on the books" : null) },
  { key: "dates", label: "Dates", next: "dates",
    value: (c) => {
      const b = c.booking || c.offer;
      return (b ? fmt.range(b.start, b.end) : fmt.range(c.start, c.end)) || null;
    } },
  { key: "people", label: "People", next: "people",
    value: (c) => c.booking?.people ?? c.offer?.people ?? c.people ?? null },
  { key: "tow", label: "Car tows", next: "tow",
    value: (c) => (c.tow_kg ? fmt.kg(c.tow_kg) : null), evidence: (c) => c.evidence.tow },
  { key: "power", label: "Mains power", next: "power",
    value: (c) => (c.power === true ? "Yes" : c.power === false ? "No" : null) },
  { key: "sauna", label: "Sauna",
    value: (c) => c.booking?.unit ?? c.offer?.unit ?? c.unit ?? null, evidence: (c) => c.evidence.unit },
  { key: "total", label: "Total",
    value: (c) => fmt.euros(c.booking?.total ?? c.offer?.total) || null },
];

function renderCard(card) {
  chip($("card-status"), CARD_STATUS[card.status] || [card.status, ""]);
  const facts = $("facts");
  for (const f of FACTS) {
    let row = facts.querySelector(`[data-key="${f.key}"]`);
    if (!row) {
      row = el("div", { class: "fact fog", dataset: { key: f.key } },
        el("dt", { text: f.label }),
        el("dd", {}, el("button", { class: "ev-btn", type: "button", tabindex: "-1" },
          el("span", { class: "value" })),
        el("span", { class: "evidence" }), el("span", { class: "sub muted" })));
      row.querySelector(".ev-btn").addEventListener("click", () => row.classList.toggle("open"));
      facts.append(row);
    }
    const v = f.value(card);
    const ev = f.evidence ? f.evidence(card) : null;
    row.classList.toggle("fog", v === null || v === "");
    row.querySelector(".value").textContent = v === null || v === "" ? "▆▆▆▆▆▆" : String(v); // blurs into a smear of steam
    row.classList.toggle("has-evidence", Boolean(ev) && v !== null);
    const btn = row.querySelector(".ev-btn");
    btn.tabIndex = ev && v !== null ? 0 : -1;
    btn.setAttribute("aria-label", ev && v !== null ? `${f.label}: ${v}. Show what you said` : `${f.label}: ${v ?? "not known yet"}`);
    row.querySelector(".evidence").textContent = ev ? `You said: “${ev}”` : "";
    const dd = row.querySelector("dd");
    dd.querySelector(".next")?.remove();
    if (card.next && card.next === f.next) dd.append(el("span", { class: "next", text: `Asking ${NEXT[f.next]} next` }));
    row.querySelector(".sub").textContent = f.note ? f.note(card) || "" : "";
  }
  $("sms").hidden = !card.sms;
  $("sms-text").textContent = card.sms;
}

const THUMB = (up) => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "2");
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("d", "M7 11v9H4v-9h3Zm0 0 4-8c1.7 0 2.5 1.2 2.2 2.8L12.6 9H18a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 16.8 20H7");
  svg.append(path);
  if (!up) svg.style.transform = "rotate(180deg)";
  return svg;
};

function stepFor(item, steps) {
  let found = null;
  for (const s of steps) if (s.at <= item.start) found = s;
  return found;
}

function renderTranscript(view) {
  const list = view.items.map((it) => {
    const step = it.role === "assistant" ? stepFor(it, view.steps) : null;
    return el("li", { class: `turn ${it.role}` },
      el("span", { class: "who-l", text: it.role === "assistant" ? "Receptionist" : "You" }),
      el("span", {}, it.content, it.interrupted ? el("span", { class: "cut", text: " (you cut in)" }) : ""),
      step ? rating(step.turn_id) : el("span"));
  });
  clear($("transcript"), list);
}

function rating(turnId) {
  const done = S.rated.get(turnId);
  const box = el("div", { class: "rate" });
  if (done) {
    box.append(el("span", { class: "muted", text: done === "up" ? "Thanks, marked good" : "Thanks, flagged for review" }));
    return box;
  }
  const up = el("button", { class: "btn quiet", type: "button", "aria-label": "Good reply", title: "Good reply" }, THUMB(true));
  const down = el("button", { class: "btn quiet", type: "button", "aria-label": "Bad reply", title: "Bad reply" }, THUMB(false));
  up.addEventListener("click", () => rate(turnId, "up", ""));
  down.addEventListener("click", () => {
    const note = el("input", { class: "text", maxlength: "500", placeholder: "What went wrong? (optional)", "aria-label": "What went wrong" });
    const form = el("form", { class: "rate-note" }, note, el("button", { class: "btn", type: "submit", text: "Flag it" }));
    form.addEventListener("submit", (e) => { e.preventDefault(); rate(turnId, "down", note.value.trim()); });
    clear(box, form);
    note.focus();
  });
  box.append(up, down);
  return box;
}

async function rate(turnId, verdict, note) {
  try {
    await api("/api/feedback", { session_id: S.call.session_id, turn_id: turnId, verdict, note });
    S.rated.set(turnId, verdict);
    renderTranscript(S.call);
  } catch (e) {
    showError(e.message);
  }
}

function renderLastTurn(view) {
  const s = view.last;
  if (!s) { clear($("last-turn")); return; }
  const rows = [
    ["Decision", s.decision || `gate: ${s.gate}`],
    ["Intent", s.intent || "none (the model wasn't asked)"],
    ["Model calls", `${s.llm.calls} · ${fmt.num(s.llm.input_tokens)} in / ${fmt.num(s.llm.output_tokens)} out`],
    ["Time", fmt.secs(s.latency_ms)],
    ["API calls", s.api_calls.map((c) => `${c.method} ${c.status}`).join(", ") || "none"],
  ];
  clear($("last-turn"),
    el("p", { class: "muted", text: "The last turn" }),
    el("dl", { class: "kv" }, rows.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })])),
    el("p", {}, el("a", { href: `/admin#traces/${view.session_id}/${s.turn_id}`, text: "Open its trace in the admin console" })));
}

// ---- test controls ----
$("anger").addEventListener("input", (e) => { $("anger-value").textContent = Number(e.target.value).toFixed(1); });

async function loadFixtures() {
  const { fixtures } = await api("/api/fixtures");
  const groups = { given: el("optgroup", { label: "Given by the kit" }), ours: el("optgroup", { label: "Ours" }) };
  for (const f of fixtures) groups[f.suite].append(el("option", { value: f.id, text: f.id }));
  clear($("fixture"), groups.given, groups.ours);
}

$("replay").addEventListener("click", async () => {
  const id = $("fixture").value;
  const out = $("replay-out");
  $("replay").disabled = true;
  clear(out, el("p", { class: "muted", text: `Replaying ${id}…` }));
  try {
    const r = await api("/api/replay", { fixture: id });
    const said = r.effects.filter((e) => e.type === "speech").map((e) => e.text).join(" ");
    clear(out,
      el("p", { class: "verdict" }, el("span", { class: `chip ${r.passed ? "cold" : "hot"}`, text: r.passed ? "Passed" : "Failed" })),
      said ? el("p", {}, el("span", { class: "muted", text: "Said: " }), said) : null,
      r.failures.length ? el("ul", {}, r.failures.map((f) => el("li", { class: "error-line", text: f }))) : null,
      el("p", { class: "muted mono", text: `${r.llm.calls} model call${r.llm.calls === 1 ? "" : "s"} · ${fmt.secs(r.wall_ms)}` }));
  } catch (e) {
    clear(out, el("p", { class: "error-line", text: e.message }));
  } finally {
    $("replay").disabled = false;
  }
});

// ---- scenario cards and the rating form (the human evaluation) ----
const RUBRIC = [
  ["natural", "Sounded like a capable person on the phone"],
  ["numbers_clear", "Prices, dates and figures were easy to follow"],
  ["one_question", "Asked one thing at a time"],
  ["turn_taking", "Waited, checked in and coped with interruptions"],
];
const RATER_KEY = "pirtis-rater";

function remembered(value) {
  try {
    if (value === undefined) return localStorage.getItem(RATER_KEY) || "";
    localStorage.setItem(RATER_KEY, value);
  } catch {
    return ""; // storage blocked: the form still works, the initials just aren't kept
  }
  return "";
}

function cardBlock(card) {
  return [
    el("p", { class: "t", text: card.title }),
    el("p", {}, el("strong", { text: "Goal: " }), card.goal),
    card.facts.length ? el("ul", {}, card.facts.map((f) => el("li", { text: f }))) : null,
    el("p", {}, el("strong", { text: "Twist: " }), card.twist),
  ];
}

async function loadCards() {
  const { cards } = await api("/api/scenarios");
  S.cards = cards;
  const select = $("card-select");
  clear(select, cards.map((c) => el("option", { value: c.id, text: `${c.title} (${c.persona === "unknown" ? "unknown number" : c.persona})` })));
  const preview = () => {
    const card = cards.find((c) => c.id === select.value);
    clear($("card-preview"), card ? cardBlock(card) : []);
    $("card-call").textContent = card ? `Call as ${card.persona === "unknown" ? "an unknown number" : card.persona}` : "Call";
  };
  select.addEventListener("change", preview);
  preview();
  $("card-call").addEventListener("click", () => {
    const card = cards.find((c) => c.id === select.value);
    if (!card) return;
    S.card = card;
    S.scored = false;
    startCall({ phone: card.phone, label: card.persona, kind: card.persona === "unknown" ? "unknown" : "customer" });
  });
}

function renderScenario(view) {
  const box = $("scenario");
  box.hidden = !S.card;
  $("rate-open").hidden = !S.card || S.scored;
  if (!S.card) return;
  clear(box, cardBlock(S.card));
  if (view.status !== "open" && !S.scored && $("rate").hidden) showRating();
}

function scale(name, label) {
  return el("fieldset", {},
    el("legend", { text: label }),
    el("div", { class: "scale" },
      el("span", { class: "ends", text: "poor" }),
      [1, 2, 3, 4, 5].map((n) => el("label", {},
        el("input", { type: "radio", name, value: String(n), required: true }),
        el("span", { text: String(n) }))),
      el("span", { class: "ends", text: "like a person" })));
}

function showRating() {
  const panel = $("rate");
  const rater = el("input", { type: "text", name: "rater", required: true, pattern: "[A-Za-z]{2,4}", maxlength: "4",
    value: remembered(), "aria-describedby": "rater-hint", autocomplete: "off" });
  const note = el("textarea", { name: "note", rows: "3", maxlength: "500", placeholder: "What stood out? (optional)" });
  const out = el("p", { role: "status" });
  const form = el("form", {},
    el("label", { class: "field" }, "Your rater code", rater,
      el("span", { class: "muted", id: "rater-hint", text: "Two to four letters, your initials. No names." })),
    el("fieldset", {},
      el("legend", { text: `Did the call reach the card's outcome? (${S.card.success})` }),
      el("div", { class: "scale" },
        [["true", "Yes"], ["false", "No"]].map(([v, t]) => el("label", {},
          el("input", { type: "radio", name: "goal", value: v, required: true }), el("span", { text: t }))))),
    RUBRIC.map(([k, label]) => scale(k, label)),
    el("label", { class: "field" }, "Note", note),
    el("div", { class: "controls" },
      el("button", { class: "btn primary", type: "submit", text: "Save rating" }),
      el("button", { class: "btn ghost", type: "button", text: "Not now", onclick: () => { panel.hidden = true; } })),
    out);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const body = {
      session_id: S.call.session_id, scenario_id: S.card.id, rater: String(f.get("rater")).trim(),
      goal_achieved: f.get("goal") === "true", note: String(f.get("note") || "").trim(),
      ...Object.fromEntries(RUBRIC.map(([k]) => [k, Number(f.get(k))])),
    };
    try {
      await api("/api/rating", body);
      remembered(body.rater);
      S.scored = true;
      clear(panel, el("h2", { id: "rate-title", text: "Thanks, your rating is saved" }),
        el("p", { class: "muted", text: `${S.card.title}, rated by ${body.rater.toUpperCase()}. Pick another card from the first screen.` }),
        el("button", { class: "btn primary", type: "button", text: "Next card", onclick: () => { history.replaceState(null, "", location.pathname); location.reload(); } }));
      $("rate-open").hidden = true;
    } catch (err) {
      out.className = "error-line";
      out.textContent = err.message;
    }
  });
  clear(panel, el("h2", { id: "rate-title", text: "Rate this call" }), form);
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

$("rate-open").addEventListener("click", showRating);

// ---- wiring ----
$("say").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("text").value.trim();
  if (text) send(text);
});
$("quiet").addEventListener("click", () => post("/api/silence", { seconds: 8 }));
$("hangup").addEventListener("click", () => post("/api/hangup"));

async function boot() {
  try {
    const { personas } = await api("/api/personas");
    S.personas = personas;
    clear($("personas"), personas.map((p) => el("li", {},
      el("button", { class: `persona ${p.kind}`, type: "button", onclick: () => { S.card = null; startCall(p); } },
        el("span", { class: "name", text: p.kind === "unknown" ? "Unknown number" : p.label }),
        el("span", { class: "phone", text: p.kind === "unknown" ? "A number the rental has never seen" : `${p.phone} · on the books` })))));
    loadFixtures().catch(() => {});
    await loadCards().catch(() => {});
    const m = location.hash.match(/^#call=([\w-]+)(?:&card=([\w-]+))?$/);
    if (m && m[2]) S.card = S.cards.find((c) => c.id === m[2]) || null;
    if (m) {
      try {
        const view = await api(`/api/session/${m[1]}`);
        S.spoken = view.items.filter((i) => i.role === "assistant").length; // don't replay old lines aloud
        render(view);
      } catch {
        history.replaceState(null, "", location.pathname);
        showError("That call has ended its 30 days or never existed. Start a new one.");
      }
    }
  } catch (e) {
    showError(e.message);
  }
}
boot();
