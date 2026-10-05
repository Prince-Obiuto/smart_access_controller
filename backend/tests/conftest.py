import io
from uuid import uuid4
import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.engine import make_url
from fastapi.testclient import TestClient
from app.config import Settings
from app.db import Database
from app.common import initialize, now
from app.models import Person, Account, Base
from app.security import PASSWORDS
from app.main import create_app

PASSWORD = "Synthetic-test-password-2026!"


@pytest.fixture(scope="session")
def password_hash():
    return PASSWORDS.hash(PASSWORD)


@pytest.fixture
def env(tmp_path, password_hash):
    settings = Settings(
        jobs_enabled=False,
        simulation_controls_enabled=True,
        upload_dir=tmp_path / "uploads",
    )
    assert make_url(settings.database_url).database.startswith("ift_test"), (
        "Refuse to modify a non-test database"
    )
    database = Database(settings)
    with database.engine.begin() as c:
        c.execute(
            text(
                "TRUNCATE "
                + ",".join('"' + t.name + '"' for t in Base.metadata.sorted_tables)
                + " CASCADE"
            )
        )
    accounts = {}
    with database.session() as db:
        initialize(db, settings)
        for role in ["SUPER_ADMIN", "ADMIN", "MANAGER", "MANAGEMENT", "USER"]:
            p = Person(
                kind="STAFF" if role != "USER" else "STUDENT",
                reference=role,
                email=role.lower() + "@futo.edu.ng",
                first_name=role,
                last_name="Tester",
                phone="0800",
                department="IFT",
                status="ACTIVE",
                sync="PENDING",
            )
            db.add(p)
            db.flush()
            a = Account(person_id=p.id, role=role, password_hash=password_hash)
            db.add(a)
            db.flush()
            accounts[role] = (a.id, p.id, p.email)
        db.commit()
    app = create_app(settings, database)
    with TestClient(app) as client:
        yield settings, database, accounts, client
    database.engine.dispose()


@pytest.fixture
def png():
    out = io.BytesIO()
    Image.new("RGB", (12, 12), "green").save(out, format="PNG")
    return out.getvalue()


def login(client, email, password=PASSWORD):
    csrf = client.get("/api/auth/csrf").json()["csrfToken"]
    return client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )


def write(client, path, body=None, key=None, **kwargs):
    csrf = client.get("/api/auth/csrf").json()["csrfToken"]
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": key or str(uuid4())}
    if body is not None:
        kwargs["json"] = body
    return client.post(path, headers=headers, **kwargs)


def cmd(client, body, expected=200, key=None):
    response = write(client, "/api/commands", body, key)
    assert response.status_code == expected, response.text
    return response.json()


def session_client(env, role):
    settings, database, accounts, _ = env
    client = TestClient(create_app(settings, database))
    assert login(client, accounts[role][2]).status_code == 200
    return client


def approved(env, png, kind="FIRST"):
    _, _, _, client = env
    user = session_client(env, "USER")
    manager = session_client(env, "MANAGER")
    admin = session_client(env, "ADMIN")
    cmd(user, {"type": "application", "kind": kind})
    application = next(
        a
        for a in user.get("/api/workspace").json()["applications"]
        if a["state"] == "DRAFT"
    )
    assert (
        write(
            user,
            f"/api/applications/{application['id']}/evidence",
            files={"file": ("receipt.png", png, "image/png")},
        ).status_code
        == 200
    )
    cmd(manager, {"type": "review", "id": application["id"], "approve": True})
    snapshot = user.get("/api/workspace").json()
    p = snapshot["people"][0]
    cmd(
        admin,
        {
            "type": "dates",
            "personId": p["id"],
            "start": (now() - __import__("datetime").timedelta(days=1)).isoformat(),
            "expiry": (now() + __import__("datetime").timedelta(days=30)).isoformat(),
        },
    )
    return user, manager, admin, application["id"]


def issued(env, png, number="00001234"):
    user, manager, admin, aid = approved(env, png)
    cmd(admin, {"type": "issue", "id": aid, "number": number})
    a = user.get("/api/workspace").json()["assignments"][0]
    assert a["state"] == "ACTIVE"
    return user, manager, admin, aid, a
