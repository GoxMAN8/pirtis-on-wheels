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
    armSilence(); // the quiet is counted from when the receptionist stops talking
  };
  speechSynthesis.speak(u);
}

function hush() {
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  $("stove").classList.remove("speaking");
}

// ---- voice in: tap to talk, or hold to talk, where the browser can listen ----
// One button, two ways to use it, because people do both:
// - a tap starts listening and leaves it on; a second tap sends
// - a press held while speaking sends when it is let go
// A press let go before the microphone has even started counts as a tap: the first
// time, the browser asks for the microphone, and answering it means letting go.
// The browser's recogniser also ends a session by itself after a pause; while the
// caller still wants to talk it is started again, keeping what was heard.
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const TAP_MS = 350; // shorter than this is a tap, longer is a hold
const RESTARTS = 6; // sessions the browser may end by itself before we stop too
const FATAL = {
  "not-allowed": "The browser blocked the microphone. Allow it in the site settings (the icon at the left of the address bar), then tap again. Or type below.",
  "service-not-allowed": "The browser's speech service isn't allowed here (on a Mac, Safari needs Dictation turned on). Type below instead.",
  "audio-capture": "No microphone was found. Plug one in, or type below.",
  network: "The browser's speech service can't be reached. Type below instead.",
  "language-not-supported": "This browser can't recognise English speech. Type below instead.",
};
let rec = null;
let heard = "";
let cutIn = false;
// "Check speech before sending": the browser's recogniser mishears, Lithuanian names
// above all, and a tester shouldn't have to blame the receptionist for it. When on,
// what was heard goes into the text box to fix, and is sent with Say it; a line that
// was spoken over the receptionist still counts as cutting in.
const REVIEW_KEY = "pirtis-review-speech";
let pendingCutIn = false;
const mic = { want: false, live: false, holding: false, latched: false, cancelled: false,
  downAt: 0, startedAt: 0, restarts: 0, error: "", ignoreUp: false, timer: 0, lang: "en-GB" };
// Names in Lithuanian: an English recogniser hears "Eglė Butkutė" as English words, and a
// name that loses its letters can't be found. When the receptionist has just asked for the
// caller's full name, the browser listens in Lithuanian for that one turn, as a phone
// system would with a language hint; anything else stays English. A browser that can't
// recognise Lithuanian falls back to English for the rest of the visit.
const NAME_ASKED = /\byour full name\b/i;
const LT_KEY = "pirtis-lt-names";
let ltUnsupported = false;

function nameTurn() {
  const ours = (S.call?.items || []).filter((i) => i.role === "assistant");
  const last = ours[ours.length - 1];
  return Boolean(last && NAME_ASKED.test(last.content));
}

function listenLang() {
  return nameTurn() && $("lt-names").checked && !ltUnsupported ? "lt-LT" : "en-GB";
}

function listen() {
  if (mic.want || S.busy || !S.call || S.call.status !== "open") return false;
  clearTimeout(quietTimer);
  cutIn = "speechSynthesis" in window && speechSynthesis.speaking;
  hush();
  heard = "";
  showError("");
  Object.assign(mic, { want: true, live: false, latched: false, cancelled: false, startedAt: 0, restarts: 0, error: "" });
  mic.forName = nameTurn(); // a name is checked before it is sent, whatever the setting
  session();
  setMic("starting");
  return true;
}

function session() {
  rec = new Recognition();
  mic.lang = listenLang();
  rec.lang = mic.lang;
  rec.interimResults = true;
  rec.continuous = true;
  rec.onstart = () => {
    mic.live = true;
    if (!mic.startedAt) mic.startedAt = Date.now();
    setMic("listening");
  };
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
    if (mic.lang !== "en-GB" && (e.error === "language-not-supported" || e.error === "service-not-allowed")) {
      ltUnsupported = true; // no Lithuanian here: the restart below listens in English
      return;
    }
    mic.error = e.error;
    if (FATAL[e.error]) { mic.want = false; showError(FATAL[e.error]); }
  };
  rec.onend = () => {
    rec = null;
    if (mic.want && mic.restarts < RESTARTS) { mic.restarts += 1; session(); return; }
    finish();
  };
  try {
    rec.start();
  } catch {
    rec = null;
    finish();
  }
}

function finish() {
  const text = mic.cancelled ? "" : heard.trim();
  Object.assign(mic, { want: false, live: false, holding: false, latched: false });
  setMic("idle");
  if (!text) {
    $("interim").textContent = mic.cancelled || FATAL[mic.error] ? "" : "Didn't catch anything. Tap and speak again, or type below.";
  } else if ($("review").checked || mic.forName) {
    pendingCutIn = cutIn;
    $("text").value = text;
    $("interim").textContent = mic.forName
      ? "Check your name as it was heard, fix any letter, then press Say it."
      : "Check what was heard, fix any word, then press Say it.";
    $("text").focus();
    $("text").setSelectionRange(text.length, text.length);
  } else {
    send(text, cutIn);
  }
}

function stopListening() {
  if (!mic.want) return;
  mic.want = false;
  if (rec) rec.stop();
  else finish();
}

function cancelListening() {
  if (!mic.want && !rec) return;
  mic.cancelled = true;
  mic.want = false;
  if (rec) rec.abort();
  else finish();
}

function setMic(state) {
  const on = state !== "idle";
  const verb = $("review").checked ? "check" : "send";
  const label = {
    idle: "Tap to talk",
    starting: "Starting the microphone…",
    listening: `${mic.lang === "lt-LT" ? "Your name, in Lithuanian" : "Listening"} · ${mic.holding ? "let go" : "tap"} to ${verb}`,
  }[state];
  $("talk").dataset.state = state;
  $("mic").setAttribute("aria-pressed", String(on));
  $("mic-label").textContent = label;
  $("mic-cancel").hidden = !on;
  $("mic-hint").textContent = on
    ? (matchMedia("(pointer: coarse)").matches ? "Cancel throws it away." : "Esc or Cancel throws it away.")
    : "Tap, speak, then tap again to send. Or hold it while you speak.";
  $("stove").classList.toggle("listening", state === "listening");
  smoke.set(on ? 0.9 : 0.5);
  clearInterval(mic.timer);
  $("mic-time").hidden = state !== "listening";
  if (state === "listening") {
    const tick = () => {
      const s = Math.floor((Date.now() - mic.startedAt) / 1000);
      $("mic-time").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    };
    tick();
    mic.timer = setInterval(tick, 250);
  }
}

// the press and the release, whether from a pointer or the keyboard
function press() {
  if (mic.want) { stopListening(); mic.ignoreUp = true; return; } // a tap while listening sends
  if (!listen()) return;
  mic.holding = true;
  mic.downAt = Date.now();
}

function release() {
  if (mic.ignoreUp) { mic.ignoreUp = false; return; }
  if (!mic.holding) return;
  mic.holding = false;
  if (Date.now() - mic.downAt < TAP_MS || !mic.live) {
    mic.latched = true; // a tap, or let go before the microphone was on: keep listening
    if (mic.want) setMic(mic.live ? "listening" : "starting");
    return;
  }
  stopListening(); // a hold: letting go sends
}

if (!Recognition) {
  $("mic").disabled = true;
  $("mic-label").textContent = "No microphone in this browser";
  $("mic-hint").textContent = "This browser can't listen (Firefox can't). Type what you'd say below, or open the page in Chrome, Edge or Safari.";
  $("review-wrap").hidden = true;
  $("lt-wrap").hidden = true;
  $("send").classList.add("primary");
} else {
  try {
    $("review").checked = localStorage.getItem(REVIEW_KEY) !== "0";
  } catch { /* storage blocked: the option starts on */ }
  $("review").addEventListener("change", () => {
    try { localStorage.setItem(REVIEW_KEY, $("review").checked ? "1" : "0"); } catch { /* not kept */ }
  });
  try {
    $("lt-names").checked = localStorage.getItem(LT_KEY) !== "0";
  } catch { /* storage blocked: the option starts on */ }
  $("lt-names").addEventListener("change", () => {
    try { localStorage.setItem(LT_KEY, $("lt-names").checked ? "1" : "0"); } catch { /* not kept */ }
  });
  $("text").addEventListener("input", () => { if (!$("text").value.trim()) pendingCutIn = false; });
  const btn = $("mic");
  btn.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault(); // no focus steal, no text selection, no long-press menu
    try { btn.setPointerCapture(e.pointerId); } catch { /* the press still works */ }
    press();
  });
  btn.addEventListener("pointerup", release);
  // the browser took the press away (a scroll, a system prompt): keep listening
  btn.addEventListener("pointercancel", () => { mic.ignoreUp = false; if (mic.holding) { mic.holding = false; mic.latched = true; } });
  btn.addEventListener("contextmenu", (e) => e.preventDefault());
  btn.addEventListener("keydown", (e) => {
    if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); press(); }
  });
  btn.addEventListener("keyup", (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); release(); } });
  $("mic-cancel").addEventListener("click", cancelListening);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && mic.want) cancelListening(); });
}

// ---- a real silence: on a phone line, saying nothing for a while is a turn too ----
// Eight seconds of quiet after the receptionist finishes speaking is sent as a silence,
// as the "Stay quiet" control does: the receptionist checks in, then says goodbye.
// Typing, the microphone, or an open rating form keeps it from counting.
const QUIET_MS = 8000;
const QUIET_KEY = "pirtis-auto-quiet";
let quietTimer = 0;

function armSilence() {
  clearTimeout(quietTimer);
  if (!$("auto-quiet").checked || !S.call || S.call.status !== "open" || S.busy || mic.want) return;
  quietTimer = setTimeout(() => {
    if ("speechSynthesis" in window && speechSynthesis.speaking) { armSilence(); return; }
    if (S.busy || mic.want || $("text").value.trim() || !$("rate").hidden) return;
    post("/api/silence", { seconds: QUIET_MS / 1000 });
  }, QUIET_MS);
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
  showYou(text);
  $("interim").textContent = "The receptionist is replying…";
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
    return true;
  } catch (e) {
    $("interim").textContent = "";
    const said = S.call.items.filter((i) => i.role === "user"); // the line didn't arrive
    showYou(said.length ? said[said.length - 1].content : "");
    showError(e.message);
    return false;
  } finally {
    S.busy = false;
    setBusy(false);
    armSilence(); // the reply is in: from now on the quiet counts
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
    armSilence(); // the reply is in: from now on the quiet counts
  }
}

function setBusy(on) {
  for (const id of ["send", "quiet", "hangup", "text"]) $(id).disabled = on;
  if (Recognition) $("mic").disabled = on;
  $("stove").classList.toggle("thinking", on);
  $("line").classList.toggle("stale", on);
}

function showYou(text) {
  $("you-text").textContent = text || "";
  $("you").hidden = !text;
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
  const yours = view.items.filter((i) => i.role === "user");
  showYou(yours.length ? yours[yours.length - 1].content : "");
  clear($("line"),
    el("span", { class: "speaker", text: "Receptionist" }),
    last ? last.content + (last.interrupted ? " —" : "") : "");

  // speak whatever is new since last time, at the speed the agent chose
  const speeds = (view.last?.actions || []).filter((a) => a.type === "say").map((a) => a.generation_config?.speed);
  const fresh = ours.slice(S.spoken);
  fresh.forEach((line, i) => speak(line.content, speeds[i] || 1));
  S.spoken = ours.length;

  renderLineRate(view);
  renderNameChip(view);
  armSilence();
  renderCard(view.card);
  renderTranscript(view);
  renderLastTurn(view);
  renderEnd(view);
  renderScenario(view);
}

// who takes a transferred call, and the note they get with it: everything the caller said
const QUEUES = { new_customers: "New customers", front_desk: "Front desk", bookings_support: "Bookings support" };

function renderHandoff(view) {
  const box = $("handoff");
  const move = view.status === "transferred" ? (view.last?.actions || []).find((a) => a.type === "transfer") : null;
  box.hidden = !move;
  if (!move) return;
  const facts = String(move.summary || "").split("; ").filter(Boolean)
    .map((f) => (f === "not identified" ? "not identified as a customer" : f));
  clear(box,
    el("p", { class: "t", text: `Passed to ${QUEUES[move.to] || "a colleague"}, with this note` }),
    facts.length ? el("ul", {}, facts.map((f) => el("li", { text: f }))) : el("p", { class: "muted", text: "Nothing to pass on yet." }),
    el("p", { class: "why", text: `Why: ${move.reason}` }));
}

function renderEnd(view) {
  renderHandoff(view);
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

// when the receptionist asks for the caller's name, offer the persona's own, spelled right:
// the browser's recogniser is weakest exactly there
function renderNameChip(view) {
  const p = personaFor(view.phone);
  const box = $("name-chip");
  const show = view.status === "open" && p && p.kind === "customer" && nameTurn();
  box.hidden = !show;
  if (!show) return;
  clear(box, el("button", {
    class: "btn", type: "button",
    onclick: () => { $("text").value = `It's ${p.label}.`; $("text").focus(); armSilence(); },
  }, "Use my name: ", el("strong", { text: p.label })));
}

function renderLineRate(view) {
  // the latest reply's 👍/👎, where the caller is looking; the transcript has them all
  const ours = view.items.filter((i) => i.role === "assistant");
  const step = ours.length && view.status === "open" ? stepFor(ours[ours.length - 1], view.steps) : null;
  clear($("line-rate"), step ? [el("span", { text: "How was this reply?" }), rating(step.turn_id)] : []);
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
    renderLineRate(S.call);
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
  const { fixtures, groups } = await api("/api/fixtures");
  // the kit's own cases first, then ours by kind (ops/cases.py)
  const boxes = Object.fromEntries(groups.map((g) => [g.key, el("optgroup", { label: g.family === "given" ? g.label : `Ours · ${g.label}` })]));
  for (const f of fixtures) boxes[f.group].append(el("option", { value: f.id, text: f.id }));
  clear($("fixture"), ...groups.map((g) => boxes[g.key]).filter((b) => b.children.length));
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
  if (text) send(text, pendingCutIn).then((ok) => { if (ok) pendingCutIn = false; });
});
$("quiet").addEventListener("click", () => post("/api/silence", { seconds: 8 }));
$("text").addEventListener("input", armSilence);
try { $("auto-quiet").checked = localStorage.getItem(QUIET_KEY) !== "0"; } catch { /* starts on */ }
$("auto-quiet").addEventListener("change", () => {
  try { localStorage.setItem(QUIET_KEY, $("auto-quiet").checked ? "1" : "0"); } catch { /* not kept */ }
  armSilence();
});
$("hangup").addEventListener("click", () => post("/api/hangup"));

async function boot() {
  try {
    const { personas } = await api("/api/personas");
    S.personas = personas;
    clear($("personas"), personas.map((p) => el("li", {},
      el("button", { class: `persona ${p.kind}`, type: "button", onclick: () => { S.card = null; startCall(p); } },
        el("span", { class: "name", text: p.kind === "unknown" ? "Unknown number" : p.label }),
        el("span", { class: "phone", text: p.kind === "unknown" ? "A number the rental has never seen" : p.phone }),
        p.kind === "unknown" ? null : el("span", { class: "phone", text: "on the books" })))));
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
