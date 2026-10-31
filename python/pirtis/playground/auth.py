"""Sign-in for the playground: accounts we create, passwords kept only as hashes.

Accounts come from PIRTIS_USERS, one `name:salt:hash` per line (or comma), where hash is
scrypt(password, salt). `python -m pirtis.playground.auth add <name>` makes one: it
writes the account line to the accounts file and the password to a private file for you
to hand over, and never prints the password. In the cloud the accounts file is a
Container App secret.

A session is a cookie `name|expiry|signature`, signed with PIRTIS_SESSION_KEY
(HMAC-SHA256) over the name, the expiry and the account's password hash: HttpOnly,
SameSite=Strict, Secure over HTTPS, for 12 hours. Removing an account or giving it a new
password ends its sessions on the next request; changing the key ends everyone's.

Five wrong passwords in 15 minutes lock the address they came from, never the account:
a lock on the account would let anyone keep its owner out. A guess counts before it is
hashed, and at most two hashes (16 MiB each) run at once, so a flood of sign-ins can't
exhaust the container's memory.

Without PIRTIS_USERS the playground is open, as it is on 127.0.0.1 while developing. The
image sets PIRTIS_AUTH=required, so a container without accounts refuses to start.
"""

import argparse
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

COOKIE = "pirtis_session"
SESSION_S = 12 * 3600
MAX_FAILURES = 5
LOCK_S = 15 * 60
MAX_HASHING = 2
NAME = re.compile(r"^[a-z0-9_-]{2,32}$")
LINE = re.compile(r"^([a-z0-9_-]{2,32}):([0-9a-f]{32}):([0-9a-f]{64})$")
DEPLOY = Path(__file__).resolve().parents[3] / "deploy"


def _scrypt(password: str, salt: str) -> str:
    return hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32
    ).hex()


def entry(name: str, password: str, salt: str | None = None) -> str:
    """One account line; the password itself is not in it."""
    if not NAME.match(name):
        raise ValueError(f"an account name is 2-32 of a-z, 0-9, _ and -, not {name!r}")
    salt = salt or secrets.token_hex(16)
    return f"{name}:{salt}:{_scrypt(password, salt)}"


def parse_users(text: str) -> dict[str, tuple[str, str]]:
    """name -> (salt, hash), from account lines separated by newlines or commas."""
    out = {}
    for n, line in enumerate(re.split(r"[\n,]", text), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = LINE.match(line)
        if m is None:
            raise ValueError(f"account line {n} is not name:salt:hash")
        out[m[1]] = (m[2], m[3])
    return out


class Auth:
    def __init__(
        self,
        users: dict[str, tuple[str, str]],
        key: bytes,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.users = users
        self.key = key
        self.clock = clock
        self._failures: dict[str, list[float]] = {}  # address -> times of recent guesses
        self._lock = threading.Lock()
        self._hashing = threading.BoundedSemaphore(MAX_HASHING)

    def check(self, name: str, password: str, address: str) -> bool:
        """True when the password is right and the address isn't locked out. The guess
        is counted before the hash runs, so parallel guesses can't all pass the check."""
        with self._lock:
            now = self.clock()
            for k in list(self._failures):
                self._failures[k] = [t for t in self._failures[k] if now - t < LOCK_S]
                if not self._failures[k]:
                    del self._failures[k]
            if len(self._failures.get(address, [])) >= MAX_FAILURES:
                return False
            self._failures.setdefault(address, []).append(now)
        salt, want = self.users.get(name, ("00" * 16, "00" * 32))  # the same work either way
        if not self._hashing.acquire(timeout=10):
            return False  # too many sign-ins at once; the guess stays counted
        try:
            ok = hmac.compare_digest(_scrypt(password, salt), want) and name in self.users
        finally:
            self._hashing.release()
        if ok:
            with self._lock:
                self._failures.pop(address, None)
        return ok

    def _sign(self, name: str, expiry: int) -> str:
        # the password hash is signed in, so a new password ends the old sessions
        hashed = self.users[name][1]
        msg = f"{name}|{expiry}|{hashed}".encode()
        return hmac.new(self.key, msg, hashlib.sha256).hexdigest()

    def issue(self, name: str) -> str:
        expiry = int(self.clock()) + SESSION_S
        return f"{name}|{expiry}|{self._sign(name, expiry)}"

    def user_of(self, cookie: str) -> str | None:
        """The signed-in account, or None for a missing, forged or expired cookie, or one
        whose account was removed."""
        parts = cookie.split("|")
        if len(parts) != 3 or not parts[1].isdigit():
            return None
        name, expiry, sig = parts[0], int(parts[1]), parts[2]
        if name not in self.users or not hmac.compare_digest(self._sign(name, expiry), sig):
            return None
        return name if expiry > self.clock() else None


def auth_from_env(env: Mapping[str, str] = os.environ) -> Auth | None:
    users = env.get("PIRTIS_USERS", "").strip()
    if not users:
        if env.get("PIRTIS_AUTH") == "required":
            raise RuntimeError("sign-in is required here, but PIRTIS_USERS holds no accounts")
        return None
    key = env.get("PIRTIS_SESSION_KEY", "")
    if len(key) < 32:
        raise RuntimeError("PIRTIS_SESSION_KEY must be set, 32 characters or more")
    return Auth(parse_users(users), key.encode())


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


def add_user(name: str, accounts: Path, passwords: Path) -> Path:
    """Adds the account, or gives it a new password; returns the file holding the password."""
    password = secrets.token_urlsafe(18)
    line = entry(name, password)
    existing = accounts.read_text() if accounts.exists() else ""
    kept = [ln for ln in existing.splitlines() if ln.strip() and not ln.startswith(f"{name}:")]
    accounts.parent.mkdir(parents=True, exist_ok=True)
    _write_private(accounts, "\n".join([*kept, line]) + "\n")
    passwords.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = passwords / f"{name}.txt"
    _write_private(path, password + "\n")
    return path


def remove_user(name: str, accounts: Path) -> None:
    kept = [ln for ln in accounts.read_text().splitlines() if not ln.startswith(f"{name}:")]
    _write_private(accounts, "\n".join(kept) + "\n" if kept else "")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="accounts", description="playground sign-in accounts")
    p.add_argument("action", choices=["add", "remove"])
    p.add_argument("name")
    p.add_argument("--accounts", default=str(DEPLOY / ".users"))
    p.add_argument("--passwords", default=str(DEPLOY / ".passwords"))
    args = p.parse_args(argv)
    accounts = Path(args.accounts)
    if args.action == "add":
        path = add_user(args.name, accounts, Path(args.passwords))
        print(f"account {args.name} saved in {accounts}; its password is in {path}")
        print("hand the password over privately, then delete that file")
    else:
        remove_user(args.name, accounts)
        print(f"account {args.name} removed from {accounts}")


if __name__ == "__main__":
    main()
