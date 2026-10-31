// Where to go after signing in: a page on this site, never another one. Decided on the
// parsed address, as the browser will see it: a browser drops tabs and newlines from an
// address, so "/<tab>/evil.example" is "//evil.example", another site.
export function safeNext(wanted, origin) {
  try {
    const url = new URL(wanted || "/", origin);
    return url.origin === origin ? url.pathname + url.search + url.hash : "/";
  } catch {
    return "/";
  }
}
