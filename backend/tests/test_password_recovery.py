"""Isolated PostgreSQL recovery tests; never contacts the controller or real inboxes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from app import mail
from app.common import now
from app.models import (
    Account,
    Audit,
    Card,
    Operation,
    PasswordReset,
    Person,
)
from app.password_recovery import consume, issue
from app.security import DomainError, digest
from conftest import PASSWORD, login, session_client, write
from sqlalchemy import func, select

NEW = "New-test-password-private-2026!"


@pytest.fixture
def captured(env, monkeypatch):
    settings = env[0]
    settings.smtp_host, settings.smtp_from = "local-mail-fixture", "ift@example.test"
    settings.application_url = "http://localhost:5173"
    messages = []
    monkeypatch.setattr(
        mail,
        "send",
        lambda settings, recipient, subject, body: messages.append(
            (recipient, subject, body)
        ),
    )
    return messages


def token_from(messages):
    link = next(
        line for line in messages[-1][2].splitlines() if "/reset-password#" in line
    )
    return parse_qs(urlsplit(link).fragment)["token"][0]


def reset(client, token, password=NEW, confirmation=NEW):
    return write(
        client,
        "/api/auth/reset-password",
        {"token": token, "password": password, "confirmation": confirmation},
    )


def test_request_uniform_hash_only_and_registered_email(env, captured):
    c = env[3]
    known = write(c, "/api/auth/forgot-password", {"email": env[2]["USER"][2]})
    token = token_from(captured)
    unknown = write(c, "/api/auth/forgot-password", {"email": "absent@example.test"})
    assert known.json() == unknown.json() and known.status_code == 200
    assert len(captured) == 1 and captured[0][0] == env[2]["USER"][2]
    assert token not in known.text
    with env[1].session() as db:
        row = db.get(PasswordReset, digest(token))
        assert row.account_id == env[2]["USER"][0]
        assert timedelta(minutes=19) < row.expires_at - now() <= timedelta(minutes=20)
        assert row.consumed_at is None
        assert token not in str([a.__dict__ for a in db.scalars(select(Audit))])


def test_success_revokes_sessions_outstanding_tokens_and_preserves_access(
    env, captured
):
    c = env[3]
    other = session_client(env, "USER")
    with env[1].session() as db:
        person = db.get(Person, env[2]["USER"][1])
        person.start = now()
        person.expiry = now() + timedelta(days=30)
        db.add(Card(number="000001234", kind="PERSONAL", revoked=True, disabled=True))
        db.commit()
        before = (
            person.role
            if hasattr(person, "role")
            else db.get(Account, env[2]["USER"][0]).role,
            person.status,
            person.start,
            person.expiry,
        )
    write(c, "/api/auth/forgot-password", {"email": env[2]["USER"][2]})
    first = token_from(captured)
    write(c, "/api/auth/forgot-password", {"email": env[2]["USER"][2]})
    second = token_from(captured)
    # Opening HTML/GET is neither a validation nor consumption endpoint.
    assert c.get("/api/auth/reset-password").status_code == 405
    with env[1].session() as db:
        assert db.get(PasswordReset, digest(first)).consumed_at is None
    assert reset(c, first).status_code == 200
    assert other.get("/api/auth/me").json() is None
    assert reset(c, first).status_code == reset(c, second).status_code == 400
    assert login(c, env[2]["USER"][2], PASSWORD).status_code == 401
    assert login(c, env[2]["USER"][2], NEW).status_code == 200
    assert len(captured) == 3 and "password changed" in captured[-1][1].lower()
    with env[1].session() as db:
        p = db.get(Person, env[2]["USER"][1])
        a = db.get(Account, env[2]["USER"][0])
        assert (a.role, p.status, p.start, p.expiry) == before
        card = db.scalar(select(Card))
        assert card.revoked and card.disabled
        assert db.scalar(select(func.count()).select_from(Operation)) == 0


def test_invalid_expired_policy_and_reused_tokens(env, captured):
    c = env[3]
    write(c, "/api/auth/forgot-password", {"email": env[2]["USER"][2]})
    token = token_from(captured)
    assert reset(c, token, "short", "short").status_code == 422
    assert reset(c, token, NEW, NEW + "x").status_code == 400
    assert reset(c, "invalid").status_code == 400
    with env[1].session() as db:
        assert db.get(PasswordReset, digest(token)).consumed_at is None
        db.get(PasswordReset, digest(token)).expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert reset(c, token).status_code == 400


def test_concurrent_consumption_one_winner(env):
    with env[1].session() as db:
        token = issue(db, db.get(Account, env[2]["USER"][0]))
        db.commit()

    def attempt():
        with env[1].session() as db:
            try:
                consume(db, token, NEW, NEW)
                db.commit()
                return "OK"
            except DomainError:
                db.rollback()
                return "INVALID"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == ["INVALID", "OK"]


@pytest.mark.parametrize(
    "role,target,allowed",
    [
        ("ADMIN", "USER", True),
        ("ADMIN", "ADMIN", False),
        ("SUPER_ADMIN", "ADMIN", True),
        ("MANAGER", "USER", False),
        ("MANAGEMENT", "USER", False),
        ("USER", "USER", False),
    ],
)
def test_admin_authority_and_identity_confirmation(
    env, captured, role, target, allowed
):
    c = session_client(env, role)
    body = {"personId": env[2][target][1], "identityConfirmed": False}
    assert write(c, "/api/auth/admin-recovery", body).status_code == 403
    response = write(c, "/api/auth/admin-recovery", {**body, "identityConfirmed": True})
    assert response.status_code == (200 if allowed else 403)
    assert len(captured) == int(allowed)
    if allowed:
        token = token_from(captured)
        assert token not in response.text
        with env[1].session() as db:
            assert (
                db.scalar(
                    select(Audit).where(Audit.action == "ADMIN_RECOVERY_INITIATED")
                ).actor_id
                == env[2][role][0]
            )


def test_unavailable_and_delivery_failure_are_honest(env, monkeypatch):
    c = env[3]
    assert not c.get("/api/auth/recovery-options").json()["available"]
    a = write(c, "/api/auth/forgot-password", {"email": env[2]["USER"][2]})
    b = write(c, "/api/auth/forgot-password", {"email": "missing@example.test"})
    assert a.json() == b.json() and "unavailable" in a.text
    with env[1].session() as db:
        assert not db.scalar(select(PasswordReset))
    env[0].smtp_host, env[0].smtp_from = "fixture", "ift@example.test"

    def fail(*args):
        raise OSError("Private SMTP failure")

    monkeypatch.setattr(mail, "send", fail)
    write(c, "/api/auth/forgot-password", {"email": env[2]["USER"][2]})
    assert not c.get("/api/auth/recovery-options").json()["available"]
    with env[1].session() as db:
        assert db.scalar(select(PasswordReset)).consumed_at is not None


def test_rate_limits_do_not_lock_or_change_account(env, captured):
    c = env[3]
    for _ in range(5):
        assert (
            write(
                c, "/api/auth/forgot-password", {"email": env[2]["USER"][2]}
            ).status_code
            == 200
        )
    assert len(captured) == 3
    for _ in range(10):
        assert reset(c, "one-bad-token").status_code == 400
    assert reset(c, "one-bad-token").status_code == 429
    assert login(c, env[2]["USER"][2], PASSWORD).status_code == 200


def test_smtp_message_fixture(env, monkeypatch):
    """Exercise MIME envelope/body through the real SMTP adapter with a local transport fixture."""
    observed = {}

    class CaptureSMTP:
        def __init__(self, host, port, timeout):
            observed.update(host=host, port=port, timeout=timeout)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self, context):
            observed["tls"] = True

        def login(self, user, password):
            observed["authenticated"] = bool(user and password)

        def send_message(self, message):
            observed["message"] = message
            return {}

    monkeypatch.setattr(mail.smtplib, "SMTP", CaptureSMTP)
    env[0].smtp_host, env[0].smtp_from = "capture", "ift@example.test"
    mail.send(env[0], "visitor@example.test", "Recovery", "Single-use fixture link")
    assert observed["tls"] and observed["timeout"] == 8
    assert observed["message"]["To"] == "visitor@example.test"
    assert "Single-use fixture link" in observed["message"].get_content()
