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
const button = form.querySelector("button");

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
  try {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.value.trim(), password: password.value }),
    });
    if (res.ok) return location.assign(next);
    const data = await res.json().catch(() => ({}));
    say(data.error || `The server answered ${res.status}.`);
  } catch {
    say("The playground can't be reached. Check your connection and try again.");
  }
  button.disabled = false;
  password.select();
});

name.focus();
