// Shared by both pages: the JSON API, small DOM helpers, the theme switch, formats.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function api(path, body) {
  const init = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, init);
  if (res.status === 401) toSignIn(); // the session ended: sign in, then come back here
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.error || `The server answered ${res.status}.`);
  return data;
}

function toSignIn() {
  location.assign(`/login?next=${encodeURIComponent(location.pathname + location.hash)}`);
}

// "Signed in as ana · Sign out", when the playground has sign-in on; nothing when it's open
export async function initAccount(slot) {
  const { user } = await api("/api/me").catch(() => ({}));
  if (!user) return;
  const out = el("button", { class: "btn quiet", type: "button", text: "Sign out" });
  out.addEventListener("click", async () => {
    await api("/api/logout", {}).catch(() => null);
    location.assign("/login");
  });
  clear(slot, el("span", { class: "muted who", text: `Signed in as ${user}` }), out);
}

// el("p", {class: "x", text: "hi", onclick: fn}, child, ...)
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "text") node.textContent = v;
    else if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === undefined || c === null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(node, ...children) {
  node.replaceChildren(...children.flat().filter(Boolean));
  return node;
}

// The theme follows the system until the viewer picks one; the pick wins both ways.
const THEME_KEY = "pirtis-theme";

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch {
    return null; // private windows and blocked storage: the page still works
  }
  return null;
}

export function currentTheme() {
  const set = document.documentElement.dataset.theme;
  if (set) return set;
  return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function initTheme(button) {
  const saved = store(THEME_KEY);
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  const label = () => {
    const next = currentTheme() === "dark" ? "Steam" : "Soot";
    button.textContent = currentTheme() === "dark" ? "☀" : "☾";
    button.setAttribute("aria-label", `Switch to the ${next} theme`);
    button.title = `Switch to the ${next} theme`;
  };
  button.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store(THEME_KEY, next);
    label();
    document.dispatchEvent(new CustomEvent("themechange"));
  });
  label();
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

const DAY = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
const NUM = new Intl.NumberFormat("en-GB");

export const fmt = {
  date: (iso) => (iso ? DAY.format(new Date(iso + "T00:00:00Z")) : ""),
  range: (a, b) => (!a ? "" : !b || a === b ? fmt.date(a) : `${fmt.date(a)} – ${fmt.date(b)}`),
  euros: (n) => (n === null || n === undefined ? "" : `€${NUM.format(n)}`),
  kg: (n) => (n === null || n === undefined ? "" : `${NUM.format(n)} kg`),
  num: (n) => NUM.format(n),
  secs: (ms) => `${(ms / 1000).toFixed(2)} s`,
  pct: (x) => `${Math.round(x * 100)}%`,
  time: (iso) => new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }),
  usd: (x) => `$${x.toFixed(4)}`,
};

export function reducedMotion() {
  return matchMedia("(prefers-reduced-motion: reduce)").matches;
}
