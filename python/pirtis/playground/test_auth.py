"""Sign-in: accounts we create, passwords kept only as hashes, signed session cookies,
and a lockout after repeated wrong passwords."""

import os
import stat
import threading
import time
from pathlib import Path

import pytest

from pirtis.playground import auth
from pirtis.playground.auth import Auth, add_user, auth_from_env, parse_users, remove_user

KEY = b"k" * 32


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def users(**passwords: str) -> str:
    return "\n".join(auth.entry(name, pw, salt="ab" * 16) for name, pw in passwords.items())


def test_a_password_is_checked_against_its_hash_and_never_stored() -> None:
    text = users(ana="correct horse")
    assert "correct horse" not in text
    a = Auth(parse_users(text), KEY)
    assert a.check("ana", "correct horse", "1.2.3.4")
    assert not a.check("ana", "wrong", "1.2.3.4")
    assert not a.check("nobody", "correct horse", "1.2.3.4")


def test_a_bad_account_line_is_refused() -> None:
    with pytest.raises(ValueError, match="account"):
        parse_users("Ana Smith:salt:hash")


def test_a_session_cookie_names_its_user_until_it_expires() -> None:
    clock = Clock()
    a = Auth(parse_users(users(ana="pw")), KEY, clock=clock)
    cookie = a.issue("ana")
    assert a.user_of(cookie) == "ana"
    clock.now += auth.SESSION_S + 1
    assert a.user_of(cookie) is None


def test_a_forged_or_foreign_cookie_is_refused() -> None:
    a = Auth(parse_users(users(ana="pw", ben="pw")), KEY)
    name, expiry, sig = a.issue("ana").split("|")
    assert a.user_of(f"ben|{expiry}|{sig}") is None  # another name, the same signature
    assert a.user_of(f"ana|{int(expiry) + 999}|{sig}") is None  # a longer life
    assert Auth(parse_users(users(ana="pw")), b"x" * 32).user_of(a.issue("ana")) is None
    assert a.user_of("") is None and a.user_of("garbage") is None


def test_removing_an_account_ends_its_sessions() -> None:
    cookie = Auth(parse_users(users(ana="pw")), KEY).issue("ana")
    assert Auth(parse_users(users(ben="pw")), KEY).user_of(cookie) is None


def test_five_wrong_passwords_lock_that_address_never_the_account() -> None:
    """A lock on the account would let anyone keep its owner out with five bad guesses."""
    clock = Clock()
    a = Auth(parse_users(users(ana="pw", ben="pw2")), KEY, clock=clock)
    for _ in range(auth.MAX_FAILURES):
        assert not a.check("ana", "guess", "1.2.3.4")
    assert not a.check("ana", "pw", "1.2.3.4"), "that address is locked"
    assert not a.check("ben", "pw2", "1.2.3.4"), "for every account"
    assert a.check("ana", "pw", "5.6.7.8"), "the owner, from elsewhere, still gets in"
    clock.now += auth.LOCK_S + 1
    assert a.check("ana", "pw", "1.2.3.4")


def test_parallel_guesses_are_counted_before_any_hashing_and_hash_two_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each hash takes 16 MiB: unbounded parallel sign-ins would exhaust the container."""
    running, most, hashed = [0], [0], [0]
    lock = threading.Lock()
    real = auth._scrypt

    def slow(password: str, salt: str) -> str:
        with lock:
            running[0] += 1
            hashed[0] += 1
            most[0] = max(most[0], running[0])
        time.sleep(0.05)
        with lock:
            running[0] -= 1
        return real(password, salt)

    monkeypatch.setattr(auth, "_scrypt", slow)
    a = Auth(parse_users(users(ana="pw")), KEY)
    hashed[0] = 0
    threads = [
        threading.Thread(target=a.check, args=("ana", "guess", "1.2.3.4")) for _ in range(12)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert hashed[0] <= auth.MAX_FAILURES, "guesses past the lock never reach the hash"
    assert most[0] <= auth.MAX_HASHING


def test_a_new_password_ends_the_old_sessions() -> None:
    cookie = Auth(parse_users(users(ana="old")), KEY).issue("ana")
    assert Auth(parse_users(users(ana="new")), KEY).user_of(cookie) is None


def test_the_environment_turns_sign_in_on_and_a_container_without_accounts_fails() -> None:
    assert auth_from_env({}) is None  # development on 127.0.0.1 stays open
    with pytest.raises(RuntimeError, match="PIRTIS_USERS"):
        auth_from_env({"PIRTIS_AUTH": "required"})
    with pytest.raises(RuntimeError, match="PIRTIS_SESSION_KEY"):
        auth_from_env({"PIRTIS_USERS": users(ana="pw")})
    a = auth_from_env({"PIRTIS_USERS": users(ana="pw"), "PIRTIS_SESSION_KEY": "s" * 32})
    assert a is not None and a.check("ana", "pw", "1.2.3.4")


def test_adding_an_account_writes_the_password_to_a_private_file_and_never_prints_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    accounts, passwords = tmp_path / "new" / "users", tmp_path / "passwords"  # no folders yet
    first = add_user("ana", accounts, passwords)
    again = add_user("ana", accounts, passwords)  # a new password replaces the old one
    add_user("ben", accounts, passwords)
    pw = again.read_text().strip()
    assert first == again and len(pw) >= 20
    assert stat.S_IMODE(os.stat(again).st_mode) == 0o600
    assert pw not in capsys.readouterr().out
    a = Auth(parse_users(accounts.read_text()), KEY)
    assert a.check("ana", pw, "1.2.3.4") and set(a.users) == {"ana", "ben"}
    remove_user("ana", accounts)
    assert set(parse_users(accounts.read_text())) == {"ben"}


def test_an_account_name_is_short_and_plain(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="account"):
        add_user("Ana|admin", tmp_path / "u", tmp_path / "p")
