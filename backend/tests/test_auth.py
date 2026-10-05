import json
import pytest
from sqlalchemy import select
from app.models import Account, AuthSession, Audit
from app.security import COOKIE, password_matches
from app.main import create_app
from app.cli import reset_password, bootstrap
from conftest import login, write, cmd, session_client, PASSWORD


@pytest.mark.parametrize(
    "email",
    [
        "a@example.org",
        "a@sub.futo.edu.ng",
        "a@futo.edu.ng.fake",
        "a@futo.edu.ngx",
        "a@futο.edu.ng",
    ],
)
def test_exact_domain(env, email):
    c = env[3]
    payload = {
        "type": "STUDENT",
        "reference": "test",
        "firstName": "Test",
        "lastName": "Person",
        "email": email,
        "phone": "0800",
        "department": "IFT",
        "password": PASSWORD,
    }
    assert (
        write(
            c, "/api/auth/register", data={"payload": json.dumps(payload)}
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "kind,email",
    [
        ("STUDENT", "valid@futo.edu.ng"),
        ("STAFF", "valid@FUTO.EDU.NG"),
        ("VISITOR", "valid@example.org"),
    ],
)
def test_registration_is_user(env, png, kind, email):
    c = env[3]
    payload = {
        "type": kind,
        "reference": "test",
        "firstName": "Test",
        "lastName": "Person",
        "email": email,
        "phone": "0800",
        "department": "IFT",
        "password": PASSWORD,
    }
    assert (
        write(
            c,
            "/api/auth/register",
            data={"payload": json.dumps({**payload, "role": "SUPER_ADMIN"})},
        ).status_code
        == 422
    )
    r = write(
        c,
        "/api/auth/register",
        data={"payload": json.dumps(payload)},
        files={"photo": ("../../photo.php", png, "text/html")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "USER"
    assert c.get("/api/workspace").json()["assignments"] == []
    with env[1].session() as db:
        a = db.get(Account, r.json()["id"])
        assert password_matches(a.password_hash, PASSWORD)
        assert a.password_hash.startswith("$argon2id$")
    assert COOKIE in c.cookies
    assert "httponly" in r.headers["set-cookie"].lower()


def test_auth_csrf_rotation_logout_restart(env):
    settings, database, accounts, c = env
    assert c.get("/api/workspace").status_code == 401
    old = c.get("/api/auth/csrf").json()["csrfToken"]
    old_cookie = c.cookies[COOKIE]
    assert (
        c.post(
            "/api/auth/login", json={"email": accounts["USER"][2], "password": PASSWORD}
        ).status_code
        == 403
    )
    assert login(c, accounts["USER"][2], "wrong").status_code == 401
    assert login(c, accounts["USER"][2]).status_code == 200
    assert c.cookies[COOKIE] != old_cookie
    assert (
        c.post(
            "/api/commands",
            json={"type": "application", "kind": "FIRST"},
            headers={"X-CSRF-Token": old, "Idempotency-Key": "x"},
        ).status_code
        == 403
    )
    new = c.get("/api/auth/csrf").json()["csrfToken"]
    assert (
        c.post(
            "/api/commands",
            json={"type": "application", "kind": "FIRST"},
            headers={
                "X-CSRF-Token": new,
                "Origin": "https://evil.example",
                "Idempotency-Key": "x",
            },
        ).status_code
        == 403
    )
    from fastapi.testclient import TestClient

    with TestClient(create_app(settings, database)) as restarted:
        restarted.cookies.update(c.cookies)
        assert restarted.get("/api/auth/me").json()["id"] == accounts["USER"][0]
    assert write(c, "/api/auth/logout", {}).status_code == 200
    assert c.get("/api/auth/me").json() is None
    assert c.get("/api/workspace").status_code == 401


def test_ownership_and_profile(env, png):
    c = session_client(env, "USER")
    other = session_client(env, "MANAGEMENT")
    p = c.get("/api/workspace").json()["people"][0]
    assert len(c.get("/api/workspace").json()["people"]) == 1
    assert (
        write(
            c,
            "/api/me/profile",
            data={
                "phone": "0900",
                "role": "SUPER_ADMIN",
                "email": "hacked@example.org",
            },
            files={"photo": ("x.png", png, "image/png")},
        ).status_code
        == 200
    )
    snap = c.get("/api/workspace").json()
    assert snap["identity"]["role"] == "USER"
    assert snap["people"][0]["email"] == p["email"]
    url = snap["people"][0]["photo"]
    assert c.get(url).status_code == 200
    assert other.get(url).status_code == 403
    cmd(
        c,
        {
            "type": "revoke",
            "assignmentId": "nonexistent",
            "reason": "MISSING",
            "own": False,
        },
        403,
    )
    assert c.get("/api/records/events").status_code == 403
    assert c.get("/api/recovery").status_code == 403


# Every administrative capability is checked in services before any target mutation.
@pytest.mark.parametrize(
    "role,commands",
    [
        (
            "MANAGER",
            [
                "issue",
                "person",
                "dates",
                "retry-sync",
                "visitor-approve",
                "visitor-assign",
                "visitor-return",
                "schedule",
                "unlock",
                "settings",
                "role",
                "device",
                "pool-card",
                "designate",
                "restore",
            ],
        ),
        (
            "MANAGEMENT",
            [
                "review",
                "issue",
                "person",
                "dates",
                "retry-sync",
                "visitor-approve",
                "visitor-assign",
                "visitor-return",
                "schedule",
                "unlock",
                "settings",
                "role",
                "device",
                "pool-card",
                "designate",
                "restore",
            ],
        ),
        (
            "USER",
            [
                "review",
                "issue",
                "person",
                "dates",
                "retry-sync",
                "visitor-approve",
                "visitor-assign",
                "visitor-return",
                "schedule",
                "unlock",
                "settings",
                "role",
                "device",
                "pool-card",
                "designate",
                "restore",
            ],
        ),
        ("ADMIN", ["settings", "role", "device", "pool-card", "designate", "restore"]),
    ],
)
def test_all_restricted_commands(env, role, commands):
    from app.service import AccessService
    from app.security import DomainError

    with env[1].session() as db:
        actor = db.get(Account, env[2][role][0])
        for command in commands:
            with pytest.raises(DomainError) as exc:
                AccessService(db, actor, env[0]).execute({"type": command})
            assert exc.value.status == 403
    c = session_client(env, role)
    if role in ["MANAGER", "USER"]:
        assert write(c, "/api/exports/events/csv", {}).status_code == 403
    if role in ["MANAGER", "MANAGEMENT"]:
        assert c.get("/api/records/events").status_code == 200
        assert c.get("/api/records/audits").status_code == 200
    if role != "SUPER_ADMIN":
        assert c.get("/api/recovery").status_code == 403


def test_role_changes_live_and_no_automatic_privilege(env):
    s = session_client(env, "SUPER_ADMIN")
    u = session_client(env, "USER")
    cmd(s, {"type": "role", "personId": env[2]["USER"][1], "role": "MANAGER"})
    assert u.get("/api/auth/me").json()["role"] == "MANAGER"
    assert u.get("/api/workspace").json()["settings"].get("privilegedCardId") is None
    cmd(s, {"type": "role", "personId": env[2]["SUPER_ADMIN"][1], "role": "USER"}, 409)


def test_reset_and_bootstrap_audited(env):
    c = session_client(env, "USER")
    settings, database, accounts, _ = env
    with database.session() as db:
        reset_password(
            db,
            settings,
            accounts["USER"][2],
            accounts["SUPER_ADMIN"][2],
            PASSWORD,
            "Changed-test-password!",
            "Test reset",
        )
        assert db.scalar(select(Audit).where(Audit.action == "LOCAL_PASSWORD_RESET"))
        assert not list(
            db.scalars(
                select(AuthSession).where(AuthSession.account_id == accounts["USER"][0])
            )
        )
        from app.security import DomainError

        with pytest.raises(DomainError):
            bootstrap(db, settings, {})
    assert c.get("/api/auth/me").json() is None
    assert login(c, accounts["USER"][2], "Changed-test-password!").status_code == 200


def test_explicit_first_super_admin_bootstrap(env):
    settings, database, accounts, _ = env
    with database.session() as db:
        db.delete(db.get(Account, accounts["SUPER_ADMIN"][0]))
        db.commit()
        data = {
            "type": "STAFF",
            "reference": "NEW-SUPER",
            "firstName": "Explicit",
            "lastName": "Owner",
            "email": "explicit@futo.edu.ng",
            "phone": "0800",
            "department": "IFT",
            "password": "  Untrimmed-test-password!  ",
        }
        account = bootstrap(db, settings, data)
        assert account.role == "SUPER_ADMIN" and password_matches(
            account.password_hash, data["password"]
        )
        assert db.scalar(select(Audit).where(Audit.action == "BOOTSTRAP_SUPER_ADMIN"))
    c = env[3]
    assert login(c, "explicit@futo.edu.ng", data["password"]).status_code == 200
    assert not c.get("/api/workspace").json()["cards"]


def test_insecure_cookies_rejected_outside_development():
    from app.config import Settings
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(environment="production", allow_insecure_dev_cookies=True)


def test_validation_errors_never_echo_passwords():
    from app.schemas import Registration
    from pydantic import ValidationError

    value = {
        "type": "STAFF",
        "reference": "SAFE",
        "firstName": "Test",
        "lastName": "Person",
        "email": "bad@example.org",
        "phone": "0800",
        "department": "IFT",
        "password": "Do-not-print-this-password!",
    }
    with pytest.raises(ValidationError) as error:
        Registration.model_validate(value)
    assert value["password"] not in str(error.value)
