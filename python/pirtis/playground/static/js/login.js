// The sign-in page: one form, then on to where the visitor was going. It loads before
// sign-in, so it carries its own two lines of theme rather than the shared helpers.

import { safeNext } from "./next.js";

try {
  const saved = localStorage.getItem("pirtis-theme");
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
} catch { /* storage blocked: follow the system */ }

const form = document.getElementById("signin");
const name = document.getElementById("name");
const password = document.getElementById("password");
const error = document.getElementById("error");
const status = document.getElementById("status");
const button = form.querySelector("button");
const label = button.textContent;

// The app scales to zero: the first request after a quiet spell waits 40-50 s while it
// starts. Say so instead of leaving a greyed-out button, and give up after 90 s.
const WAKING_AFTER_MS = 3000;
const GIVE_UP_AFTER_MS = 90000;

const next = safeNext(new URLSearchParams(location.search).get("next"), location.origin);

function say(text) {
  error.textContent = text;
  error.hidden = false;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  error.hidden = true;
  if (!name.value.trim() || !password.value) return say("Enter your name and password.");
  button.disabled = true;
  button.textContent = "Signing in…";
  const waking = setTimeout(() => {
    status.textContent = "Waking the app up. After a quiet spell this takes up to a minute.";
    status.hidden = false;
  }, WAKING_AFTER_MS);
  const abort = new AbortController();
  const giveUp = setTimeout(() => abort.abort(), GIVE_UP_AFTER_MS);
  try {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.value.trim(), password: password.value }),
      signal: abort.signal,
    });
    if (res.ok) return location.assign(next);
    const data = await res.json().catch(() => ({}));
    say(data.error || `The server answered ${res.status}.`);
  } catch {
    say(
      abort.signal.aborted
        ? "The app didn't answer within a minute and a half. Try again; if it keeps happening, it may not be running."
        : "The playground can't be reached. Check your connection and try again.",
    );
  } finally {
    clearTimeout(waking);
    clearTimeout(giveUp);
    status.hidden = true;
    button.textContent = label;
  }
  button.disabled = false;
  password.select();
});

name.focus();
