"""The playground behind its sign-in: pages send a stranger to /login, the API refuses
them, and a session cookie opens everything."""

import http.client
import json
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pirtis.llm.fake import Fake
from pirtis.ops.store import FileStore
from pirtis.playground.auth import Auth, entry, parse_users
from pirtis.playground.server import make_server

ACCOUNTS = parse_users(
    entry("ana", "a long test password") + "\n" + entry("ben", "another test password")
)


class Http:
    def __init__(self, port: int) -> None:
        self.port = port
        self.cookie = ""

    def req(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], Any]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"Content-Type": "application/json", **(headers or {})}
        if self.cookie:
            h["Cookie"] = self.cookie
        conn.request(method, path, None if body is None else json.dumps(body), h)
        r = conn.getresponse()
        raw = r.read().decode(errors="replace")  # a font is bytes
        got = {k.lower(): v for k, v in r.getheaders()}
        conn.close()
        return r.status, got, json.loads(raw) if "json" in got.get("content-type", "") else raw

    def sign_in(
        self, password: str = "a long test password", name: str = "ana"
    ) -> tuple[int, dict[str, str], Any]:
        status, h, body = self.req("POST", "/api/login", {"name": name, "password": password})
        if status == 200:
            self.cookie = h["set-cookie"].split(";")[0]
        return status, h, body


def serve(
    tmp_path: Path, auth: Auth | None, rate: int = 100, per_day: int = 1000
) -> Iterator[Http]:
    srv = make_server(
        FileStore(tmp_path),
        Fake(),
        host="127.0.0.1",
        port=0,
        auth=auth,
        rate_per_min=rate,
        spend_per_day=per_day,
    )
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield Http(srv.server_address[1])
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def site(tmp_path: Path) -> Iterator[Http]:
    yield from serve(tmp_path, Auth(ACCOUNTS, b"k" * 32))


def test_a_stranger_is_sent_to_sign_in_and_the_api_refuses_them(site: Http) -> None:
    assert site.req("GET", "/")[:2][0] == 302
    assert site.req("GET", "/")[1]["location"] == "/login?next=/"
    assert site.req("GET", "/admin")[1]["location"] == "/login?next=/admin"
    for method, path, body in [
        ("GET", "/api/turns", None),
        ("GET", "/api/gate-runs", None),
        ("POST", "/api/session", {"phone": "+37062223344"}),
        ("GET", "/static/js/admin.js", None),
    ]:
        status, _, out = site.req(method, path, body)
        assert status in (302, 401), path
        if path.startswith("/api/"):
            assert out == {"error": "Sign in first."}


def test_the_sign_in_page_its_assets_and_health_stay_open(site: Http) -> None:
    for path in ["/login", "/static/app.css", "/static/js/login.js", "/api/health"]:
        assert site.req("GET", path)[0] == 200, path
    font = next((Path(__file__).parent / "static" / "fonts").glob("*.woff2")).name
    assert site.req("GET", f"/static/fonts/{font}")[0] == 200
    assert site.req("GET", "/api/me")[2] == {"user": None, "auth": True}


def test_signing_in_sets_a_strict_cookie_that_opens_everything(site: Http) -> None:
    status, h, body = site.sign_in()
    assert (status, body) == (200, {"user": "ana"})
    cookie = h["set-cookie"]
    for part in ("pirtis_session=", "HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=43200"):
        assert part in cookie
    assert "Secure" not in cookie  # plain http on 127.0.0.1
    assert site.req("GET", "/api/me")[2] == {"user": "ana", "auth": True}
    assert site.req("GET", "/")[0] == 200 and site.req("GET", "/api/turns")[0] == 200


def test_behind_https_the_cookie_is_secure(site: Http) -> None:
    _, h, _ = site.req(
        "POST",
        "/api/login",
        {"name": "ana", "password": "a long test password"},
        {"X-Forwarded-Proto": "https"},
    )
    assert "Secure" in h["set-cookie"]
    assert h["strict-transport-security"] == "max-age=31536000"
    assert "strict-transport-security" not in site.req("GET", "/login")[1]  # plain http


def test_a_wrong_password_and_an_unknown_name_get_the_same_answer(site: Http) -> None:
    wrong = site.sign_in("not it")
    unknown = site.req("POST", "/api/login", {"name": "zed", "password": "a long test password"})
    assert wrong[0] == unknown[0] == 401 and wrong[2] == unknown[2]
    assert "set-cookie" not in wrong[1]


def test_a_cross_site_post_is_refused_even_signed_in(site: Http) -> None:
    site.sign_in()
    status, _, _ = site.req(
        "POST", "/api/session", {"phone": "+37062223344"}, {"Origin": "https://evil.example"}
    )
    assert status == 403


def test_signing_out_clears_the_cookie(site: Http) -> None:
    site.sign_in()
    _, h, _ = site.req("POST", "/api/logout", {})
    assert "Max-Age=0" in h["set-cookie"]
    site.cookie = ""
    assert site.req("GET", "/api/turns")[0] == 401


def test_without_accounts_the_playground_stays_open(tmp_path: Path) -> None:
    for open_site in serve(tmp_path, None):
        assert open_site.req("GET", "/")[0] == 200
        assert open_site.req("GET", "/api/me")[2] == {"user": None, "auth": False}


def test_an_encoded_path_cannot_reach_a_closed_file(site: Http) -> None:
    """The fonts folder is open; a path that climbs out of it must not be."""
    for path in [
        "/static/fonts/..%2fadmin.html",
        "/static/fonts/..%2fjs/admin.js",
        "/static/fonts/%2e%2e/js/admin.js",
        "/static/fonts/..%5cadmin.html",
    ]:
        assert site.req("GET", path)[0] != 200, path


def test_a_bad_content_length_is_refused_at_once(site: Http) -> None:
    for length in ("-1", "abc"):
        conn = http.client.HTTPConnection("127.0.0.1", site.port, timeout=5)
        conn.putrequest("POST", "/api/login")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", length)
        conn.endheaders()
        assert conn.getresponse().status in (400, 401), length  # answered, not left hanging
        conn.close()


def spend(c: Http) -> int:
    sid = c.req("POST", "/api/session", {"phone": "+37062223344"})[2]["session_id"]
    return c.req("POST", "/api/silence", {"session_id": sid})[0]


def test_each_account_has_its_own_minute(tmp_path: Path) -> None:
    for ana in serve(tmp_path, Auth(ACCOUNTS, b"k" * 32), rate=2):
        ben = Http(ana.port)
        ana.sign_in()
        ben.sign_in("another test password", "ben")
        assert [spend(ana) for _ in range(3)] == [200, 200, 429]
        assert spend(ben) == 200, "one tester can't use up another's minute"


def test_a_daily_budget_stops_everyone(tmp_path: Path) -> None:
    for ana in serve(tmp_path, Auth(ACCOUNTS, b"k" * 32), per_day=3):
        ben = Http(ana.port)
        ana.sign_in()
        ben.sign_in("another test password", "ben")
        assert [spend(ana), spend(ana), spend(ben)] == [200, 200, 200]
        sid = ben.req("POST", "/api/session", {"phone": "+37062223344"})[2]["session_id"]
        status, _, out = ben.req("POST", "/api/silence", {"session_id": sid})
        assert status == 429 and "today" in out["error"].lower()


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_after_sign_in_the_browser_only_goes_to_this_site(tmp_path: Path) -> None:
    """A browser drops tabs and newlines from an address, so "/<tab>/evil" is "//evil"."""
    module = Path(__file__).parent / "static" / "js" / "next.js"
    copy = tmp_path / "next.mjs"
    copy.write_text(module.read_text())
    cases = {
        "/admin#reliability": "/admin#reliability",
        "/?card=first-booking": "/?card=first-booking",
        "": "/",
        "/\t/evil.example": "/",
        "//evil.example": "/",
        "/\\evil.example": "/",
        "https://evil.example/": "/",
        "javascript:alert(1)": "/",
    }
    script = (
        f"import {{ safeNext }} from {json.dumps(str(copy))};"
        f"const cases = {json.dumps(list(cases))};"
        "console.log(JSON.stringify(cases.map((c) => safeNext(c, 'https://pirtis.example'))));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True
    )
    assert json.loads(out.stdout) == list(cases.values())
