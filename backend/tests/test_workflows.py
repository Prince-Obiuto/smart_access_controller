from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
import io
import pytest
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from pypdf import PdfWriter
from app.models import (
    Account,
    Person,
    Assignment,
    Card,
    Confirmation,
    Operation,
    Audit,
    Upload,
    Controller,
)
from app.common import now, set_setting
from app.operations import process_operations
from app.service import AccessService
from conftest import cmd, write, approved, issued, session_client


def controls(env, **patch):
    c = session_client(env, "SUPER_ADMIN")
    old = c.get("/api/workspace").json()["controls"]
    cmd(c, {"type": "demo-controls", "controls": {**old, **patch}})


def test_approval_and_protected_evidence(env, png):
    u = session_client(env, "USER")
    m = session_client(env, "MANAGER")
    a = session_client(env, "ADMIN")
    outsider = session_client(env, "MANAGEMENT")
    cmd(u, {"type": "application", "kind": "FIRST"}, key="same")
    cmd(u, {"type": "application", "kind": "FIRST"}, key="same")
    cmd(u, {"type": "application", "kind": "REPLACEMENT"}, 409, key="same")
    app = u.get("/api/workspace").json()["applications"][0]
    cmd(a, {"type": "issue", "id": app["id"], "number": "00001234"}, 409)
    assert (
        write(
            u,
            f"/api/applications/{app['id']}/evidence",
            files={"file": ("fake.jpg", b"<script>unsafe</script>", "image/jpeg")},
        ).status_code
        == 400
    )
    r = write(
        u,
        f"/api/applications/{app['id']}/evidence",
        key="evidence",
        files={"file": ("../../receipt.php", png, "text/html")},
    )
    assert r.status_code == 200, r.text
    assert (
        write(
            u,
            f"/api/applications/{app['id']}/evidence",
            key="evidence",
            files={"file": ("same.png", png, "image/png")},
        ).status_code
        == 200
    )
    app = u.get("/api/workspace").json()["applications"][0]
    assert app["state"] == "SUBMITTED"
    assert outsider.get(app["evidence"]["url"]).status_code == 403
    assert m.get(app["evidence"]["url"]).status_code == 200
    assert env[3].get(app["evidence"]["url"]).status_code == 401
    cmd(m, {"type": "review", "id": app["id"], "approve": False}, 400)
    cmd(
        m,
        {
            "type": "review",
            "id": app["id"],
            "approve": False,
            "reason": "Receipt missing bank stamp",
        },
    )
    assert (
        u.get("/api/workspace").json()["applications"][0]["reason"]
        == "Receipt missing bank stamp"
    )
    cmd(m, {"type": "issue", "id": app["id"], "number": "00001234"}, 403)
    with env[1].session() as db:
        upload = db.scalar(select(Upload))
        assert upload.stored_name.endswith(".png") and ".." not in upload.stored_name
        assert upload.name == "receipt.php"


@pytest.mark.parametrize("kind", ["malformed", "active", "valid"])
def test_pdf_validation(env, kind):
    u = session_client(env, "USER")
    cmd(u, {"type": "application", "kind": "FIRST"})
    aid = u.get("/api/workspace").json()["applications"][0]["id"]
    if kind == "malformed":
        data = b"%PDF-1.7\nmalformed"
    else:
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        if kind == "active":
            writer.add_js('app.alert("bad")')
        out = io.BytesIO()
        writer.write(out)
        data = out.getvalue()
    r = write(
        u,
        f"/api/applications/{aid}/evidence",
        files={"file": ("receipt.pdf", data, "application/pdf")},
    )
    assert r.status_code == (200 if kind == "valid" else 400), r.text


def test_upload_limit_and_rollback_cleanup(env):
    u = session_client(env, "USER")
    cmd(u, {"type": "application", "kind": "FIRST"})
    aid = u.get("/api/workspace").json()["applications"][0]["id"]
    r = write(
        u,
        f"/api/applications/{aid}/evidence",
        files={"file": ("big.png", b"x" * (env[0].upload_limit + 1), "image/png")},
    )
    assert r.status_code == 400
    from app.uploads import save_upload
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (2, 2)).save(out, format="PNG")
    with env[1].session() as db:
        save_upload(
            db, env[0], env[2]["USER"][1], "PHOTO", "x.png", out.getvalue(), 10000
        )
        db.rollback()
    assert not list(env[0].upload_dir.iterdir())


def test_failed_replacement_every_controller(env, png):
    user, _, admin, _, old = issued(env, png)
    # A second target controller prevents a single-controller success from hiding failure.
    with env[1].session() as db:
        db.add(
            Controller(
                id="controller-2", name="Second simulated controller", online=True
            )
        )
        db.commit()
    user, manager, admin, aid = approved(env, png, "REPLACEMENT")
    controls(env, failDisable=True)
    r = cmd(admin, {"type": "issue", "id": aid, "number": "00009999"}, key="replace")
    assert r["result"] == "FAILED"
    snap = user.get("/api/workspace").json()
    assert (
        next(a for a in snap["assignments"] if a["id"] == old["id"])["state"]
        == "REVOKED"
    )
    assert not any(c["number"] == "00009999" for c in snap["cards"])
    assert any(
        n["severity"] == "CRITICAL"
        for n in admin.get("/api/workspace").json()["notifications"]
    )
    cmd(admin, {"type": "issue", "id": aid, "number": "00009999"}, key="replace")
    controls(env, failDisable=False)
    cmd(admin, {"type": "retry-sync", "assignmentId": old["id"]})
    app = next(
        a for a in user.get("/api/workspace").json()["applications"] if a["id"] == aid
    )
    assert app["state"] == "ISSUED"
    assert app["reviewer"] == "MANAGER Tester"
    assert app["issuer"] == "ADMIN Tester"
    snap = user.get("/api/workspace").json()
    new = next(a for a in snap["assignments"] if a["state"] == "ACTIVE")
    assert len(new["confirmations"]) == 2
    assert next(c for c in snap["cards"] if c["number"] == "00001234")["revoked"]
    cmd(admin, {"type": "retry-sync", "assignmentId": old["id"]})
    assert (
        next(
            a
            for a in user.get("/api/workspace").json()["assignments"]
            if a["id"] == old["id"]
        )["state"]
        == "REVOKED"
    )


def test_self_revoke_prompt_and_ownership(env, png):
    u, _, a, _, assignment = issued(env, png)
    controls(env, offline=True)
    r = cmd(
        u,
        {
            "type": "revoke",
            "assignmentId": assignment["id"],
            "reason": "MISSING",
            "own": True,
        },
        key="missing",
    )
    assert r["result"] == "FAILED"
    assert u.get("/api/workspace").json()["cards"][0]["revoked"]
    assert u.get("/api/workspace").json()["assignments"][0]["state"] == "REVOKED"
    assert (
        cmd(
            u,
            {
                "type": "revoke",
                "assignmentId": assignment["id"],
                "reason": "MISSING",
                "own": True,
            },
            key="missing",
        )
        == r
    )
    cmd(
        u,
        {
            "type": "revoke",
            "assignmentId": assignment["id"],
            "reason": "DAMAGED",
            "own": True,
        },
        403,
    )
    with env[1].session() as db:
        assert db.scalar(select(Audit).where(Audit.action == "revoke"))
        assert db.scalar(select(Confirmation)).last_confirmed == "ENABLED"


@pytest.mark.parametrize("conflict", ["person", "card"])
def test_database_concurrent_assignment_constraints(env, conflict):
    database = env[1]
    accounts = env[2]
    barrier = Barrier(2)
    with database.session() as db:
        cards = [
            Card(number="00000111", kind="PERSONAL"),
            Card(number="00000222", kind="PERSONAL"),
        ]
        db.add_all(cards)
        db.commit()
        ids = [c.id for c in cards]

    def writer(index):
        with database.session() as db:
            barrier.wait()
            db.add(
                Assignment(
                    person_id=accounts["USER"][1]
                    if conflict == "person"
                    else accounts[["USER", "ADMIN"][index]][1],
                    card_id=ids[index] if conflict == "person" else ids[0],
                    start=now() - timedelta(days=1),
                    expiry=now() + timedelta(days=1),
                    state="ACTIVE",
                    sync="SYNCHRONIZED",
                )
            )
            try:
                db.commit()
                return "committed"
            except IntegrityError:
                db.rollback()
                return "conflict"

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(writer, [0, 1]))
    assert sorted(results) == ["committed", "conflict"]
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Assignment)) == 1


def test_delayed_activation_persists_restart_and_expiry(env, png):
    u, _, a, aid = approved(env, png)
    controls(env, delayActivation=True)
    cmd(a, {"type": "issue", "id": aid, "number": "00007890"})
    snap = u.get("/api/workspace").json()
    assert snap["applications"][0]["state"] == "ISSUANCE_PENDING"
    from app.db import Database

    restarted = Database(env[0])
    with restarted.session() as db:
        set_setting(db, "simulation", {})
        db.commit()
        process_operations(db, env[0])
    assert u.get("/api/workspace").json()["applications"][0]["state"] == "ISSUED"
    restarted.engine.dispose()
    assert u.get("/api/workspace").json()["cards"][0]["number"] == "00007890"


def test_queued_activation_cannot_outlive_expiry(env, png):
    u, _, a, aid = approved(env, png)
    controls(env, delayActivation=True)
    cmd(a, {"type": "issue", "id": aid, "number": "00007890"})
    with env[1].session() as db:
        assignment = db.scalar(select(Assignment))
        assignment.expiry = now() - timedelta(hours=1)
        set_setting(db, "simulation", {})
        db.commit()
        process_operations(db, env[0])
        process_operations(db, env[0])
        db.refresh(assignment)
        assert assignment.state == "EXPIRED"
        assert db.get(Card, assignment.card_id).disabled
    cmd(a, {"type": "issue", "id": aid, "number": "00007890"}, 409)


def test_uncertain_unlock_never_replayed(env):
    controls(env, unlockTimeout=True)
    a = session_client(env, "ADMIN")
    cmd(a, {"type": "unlock", "doorId": "door-1", "confirmed": False}, 400)
    r = cmd(a, {"type": "unlock", "doorId": "door-1", "confirmed": True}, key="unlock")
    assert r["result"] == "UNCERTAIN"
    cmd(a, {"type": "unlock", "doorId": "door-1", "confirmed": True}, key="unlock")
    with env[1].session() as db:
        process_operations(db, env[0])
        process_operations(db, env[0])
        op = db.scalar(select(Operation).where(Operation.target_type == "DOOR"))
        assert op.attempts == 1 and op.status == "UNCERTAIN"
        assert db.scalar(select(func.count()).select_from(Operation)) == 1
    assert a.get("/api/workspace").json()["devices"][0]["sensor"] == "UNKNOWN"


def test_crash_before_unlock_result_never_replayed(env):
    class Crash:
        def execute(self, *args):
            raise RuntimeError("Simulated crash after uncertain send")

    with env[1].session() as db:
        actor = db.get(Account, env[2]["ADMIN"][0])
        AccessService(db, actor, env[0]).execute(
            {"type": "unlock", "doorId": "door-1", "confirmed": True}
        )
        db.commit()
        with pytest.raises(RuntimeError):
            process_operations(db, env[0], adapter=Crash())
    with env[1].session() as db:
        process_operations(db, env[0])
        op = db.scalar(select(Operation))
        assert op.status == "UNCERTAIN" and op.attempts == 1


def test_concurrent_http_issuance(env, png):
    user, _, admin, aid = approved(env, png)
    database = env[1]
    clients = [session_client(env, "ADMIN"), session_client(env, "ADMIN")]
    # Prepare CSRF once; independently authenticated writers enter together.
    tokens = [c.get("/api/auth/csrf").json()["csrfToken"] for c in clients]
    barrier = Barrier(2)

    def issue(index):
        barrier.wait()
        return (
            clients[index]
            .post(
                "/api/commands",
                json={"type": "issue", "id": aid, "number": "00006666"},
                headers={
                    "X-CSRF-Token": tokens[index],
                    "Idempotency-Key": "writer-" + str(index),
                },
            )
            .status_code
        )

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(issue, [0, 1]))
        assert 200 in results and all(status in (200, 409) for status in results)
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Assignment)) == 1


def test_previous_privilege_disable_required_on_every_attempt(env, png):
    u, _, admin, _, assignment = issued(env, png)
    sa = session_client(env, "SUPER_ADMIN")
    old_card = u.get("/api/workspace").json()["cards"][0]
    cmd(sa, {"type": "role", "personId": env[2]["USER"][1], "role": "SUPER_ADMIN"})
    cmd(sa, {"type": "designate", "cardId": old_card["id"]})
    with env[1].session() as db:
        p = db.get(Person, env[2]["SUPER_ADMIN"][1])
        p.start = now() - timedelta(days=1)
        p.expiry = now() + timedelta(days=30)
        card = Card(number="00005555", kind="PERSONAL")
        db.add(card)
        db.flush()
        AccessService(db, db.get(Account, env[2]["SUPER_ADMIN"][0]), env[0]).assign(
            p, card, p.start, p.expiry
        )
        db.commit()
        process_operations(db, env[0])
        new_id = card.id
    controls(env, failDisable=True)
    for _ in range(2):
        cmd(sa, {"type": "designate", "cardId": new_id})
        snap = sa.get("/api/workspace").json()
        assert not next(c for c in snap["cards"] if c["id"] == new_id)["privileged"]
    controls(env, failDisable=False)
    cmd(sa, {"type": "retry-sync", "assignmentId": assignment["id"]})
    snap = sa.get("/api/workspace").json()
    assert next(c for c in snap["cards"] if c["id"] == old_card["id"])["disabled"]
    assert sum(c["privileged"] for c in snap["cards"]) == 1
    assert next(c for c in snap["cards"] if c["id"] == new_id)["privileged"]


def test_successful_replacement_returns_activation_confirmation(env, png):
    user, _, admin, _, old = issued(env, png)
    user, _, admin, aid = approved(env, png, "REPLACEMENT")
    result = cmd(
        admin,
        {"type": "issue", "id": aid, "number": "00009999"},
        key="completed-replacement",
    )
    assert result["result"] == "CONFIRMED"
    assert len(result["confirmations"]) == 2
    assert {c["intendedState"] for c in result["confirmations"]} == {
        "DISABLED",
        "ENABLED",
    }
    assert (
        cmd(
            admin,
            {"type": "issue", "id": aid, "number": "00009999"},
            key="completed-replacement",
        )
        == result
    )
