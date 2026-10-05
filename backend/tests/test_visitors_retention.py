from datetime import timedelta, datetime, timezone
import json
from sqlalchemy import select, func
from app.models import (
    Person,
    Account,
    Card,
    Assignment,
    Visit,
    AccessEvent,
    Audit,
    Upload,
    Notification,
)
from app.common import now, audit, notify
from app.events import store_event, backfill, schedule_allows
from app.retention import run_retention, months
from app.operations import process_operations
from app.service import AccessService
from app.uploads import save_upload
from conftest import cmd, write, session_client, PASSWORD, issued
from test_workflows import controls


def visitor(env, name="First"):
    from fastapi.testclient import TestClient
    from app.main import create_app

    c = TestClient(create_app(env[0], env[1]))
    payload = {
        "type": "VISITOR",
        "reference": "",
        "firstName": name,
        "lastName": "Visitor",
        "email": name.lower() + "@example.org",
        "phone": "0800",
        "department": "External",
        "password": PASSWORD,
    }
    r = write(c, "/api/auth/register", data={"payload": json.dumps(payload)})
    assert r.status_code == 200, r.text
    cmd(
        c,
        {
            "type": "visit",
            "purpose": "Research",
            "host": "IFT",
            "start": (now() - timedelta(hours=1)).isoformat(),
            "expiry": (now() + timedelta(hours=3)).isoformat(),
        },
    )
    return c, c.get("/api/workspace").json()["visits"][0]


def assigned_visitor(env):
    v, visit = visitor(env)
    admin = session_client(env, "ADMIN")
    sa = session_client(env, "SUPER_ADMIN")
    cmd(sa, {"type": "pool-card", "number": "00004000"})
    card = next(
        c for c in admin.get("/api/workspace").json()["cards"] if c["kind"] == "VISITOR"
    )
    assert card["disabled"] and card["poolState"] == "AVAILABLE_DISABLED"
    cmd(admin, {"type": "visitor-assign", "id": visit["id"], "cardId": card["id"]}, 409)
    cmd(
        admin,
        {
            "type": "visitor-approve",
            "id": visit["id"],
            "note": "External departmental approval confirmed by telephone",
        },
    )
    cmd(admin, {"type": "visitor-assign", "id": visit["id"], "cardId": card["id"]})
    return v, admin, visit["id"], card["id"]


def test_visitor_checkout_reuse_and_immutable_history(env):
    v, a, vid, cid = assigned_visitor(env)
    cmd(v, {"type": "application", "kind": "FIRST"}, 403)
    with env[1].session() as db:
        event_time = now()
        event_id = store_event(db, "visitor-exit", event_time, "00004000", "EXIT")
        db.commit()
        old_assignment = db.get(AccessEvent, event_id).assignment_id
    assert v.get("/api/workspace").json()["visits"][0]["state"] == "ACTIVE"
    cmd(
        a,
        {"type": "visitor-return", "id": vid, "returned": False, "condition": "GOOD"},
        400,
    )
    controls(env, failDisable=True)
    cmd(a, {"type": "visitor-return", "id": vid, "returned": True, "condition": "GOOD"})
    snap = v.get("/api/workspace").json()
    assert snap["assignments"][0]["state"] == "CLOSED"
    pool = next(c for c in a.get("/api/workspace").json()["cards"] if c["id"] == cid)
    assert pool["poolState"] == "RETURN_PENDING_DISABLE" and not pool["disabled"]
    second, sv = visitor(env, "Second")
    cmd(
        a,
        {
            "type": "visitor-approve",
            "id": sv["id"],
            "note": "External approval confirmed",
        },
    )
    cmd(a, {"type": "visitor-assign", "id": sv["id"], "cardId": cid}, 409)
    controls(env, failDisable=False)
    cmd(a, {"type": "retry-sync", "assignmentId": old_assignment})
    assert next(c for c in a.get("/api/workspace").json()["cards"] if c["id"] == cid)[
        "disabled"
    ]
    cmd(a, {"type": "visitor-assign", "id": sv["id"], "cardId": cid})
    with env[1].session() as db:
        previous = db.get(AccessEvent, event_id)
        assert (
            previous.person == "First Visitor"
            and previous.assignment_id == old_assignment
        )
        late_id = store_event(
            db, "late-backfill", event_time, "00004000", "EXIT", received_at=now()
        )
        db.commit()
        assert db.get(AccessEvent, late_id).assignment_id == old_assignment
        assert db.get(AccessEvent, late_id).person == "First Visitor"
    cmd(
        a,
        {
            "type": "visitor-return",
            "id": sv["id"],
            "returned": True,
            "condition": "GOOD",
        },
    )
    assert (
        next(c for c in a.get("/api/workspace").json()["cards"] if c["id"] == cid)[
            "poolState"
        ]
        == "AVAILABLE_DISABLED"
    )


def test_expired_unreturned_pool_quarantined(env):
    v, a, vid, cid = assigned_visitor(env)
    with env[1].session() as db:
        visit = db.get(Visit, vid)
        deadline = visit.expiry
        run_retention(db, env[0], deadline)
        db.commit()
        process_operations(db, env[0], deadline)
        db.refresh(visit)
        assert visit.state == "ENDED" and not visit.returned
        card = db.get(Card, cid)
        assert card.disabled and card.pool_state == "QUARANTINED"
        assignment = db.get(Assignment, visit.assignment_id)
        assert assignment.state == "EXPIRED" and not assignment.returned_at
        assert db.get(Person, visit.person_id).access_ended_at == deadline
    cmd(a, {"type": "visitor-return", "id": vid, "returned": True, "condition": "GOOD"})
    assert (
        next(c for c in a.get("/api/workspace").json()["cards"] if c["id"] == cid)[
            "poolState"
        ]
        == "AVAILABLE_DISABLED"
    )


def test_missing_visitor_never_available(env):
    v, a, vid, cid = assigned_visitor(env)
    assignment = v.get("/api/workspace").json()["assignments"][0]
    cmd(
        v,
        {
            "type": "revoke",
            "assignmentId": assignment["id"],
            "reason": "MISSING",
            "own": True,
        },
    )
    snap = v.get("/api/workspace").json()
    assert snap["visits"][0]["state"] == "ENDED"
    assert snap["people"][0]["accessEndedAt"]
    assert (
        next(c for c in a.get("/api/workspace").json()["cards"] if c["id"] == cid)[
            "poolState"
        ]
        == "MISSING"
    )
    # Recording a previously missing asset as missing at checkout cannot free it.
    cmd(
        a,
        {
            "type": "visitor-return",
            "id": vid,
            "returned": False,
            "condition": "MISSING",
        },
    )
    assert (
        next(c for c in a.get("/api/workspace").json()["cards"] if c["id"] == cid)[
            "poolState"
        ]
        == "MISSING"
    )


def test_visitor_privacy_restore_and_original_deadline(env, png):
    v, a, vid, cid = assigned_visitor(env)
    p = v.get("/api/workspace").json()["people"][0]
    pid = p["id"]
    with env[1].session() as db:
        event = store_event(db, "visitor-record", now(), "00004000")
        save_upload(db, env[0], pid, "PHOTO", "x.png", png, 10000)
        audit(
            db,
            db.scalar(select(Account).where(Account.person_id == pid)),
            "TEST_COPY",
            "First Visitor",
            person_id=pid,
        )
        notify(db, "Visitor notice", "First Visitor", person_id=pid)
        notify(db, "Copied", "First Visitor requested something")
        db.commit()
    cmd(a, {"type": "visitor-return", "id": vid, "returned": True, "condition": "GOOD"})
    with env[1].session() as db:
        person = db.get(Person, pid)
        ended = person.access_ended_at
        deadline = ended + timedelta(days=45)
        run_retention(db, env[0], ended + timedelta(days=15))
        db.commit()
        assert person.deleted_at and person.purge_at == deadline
        sa = db.get(Account, env[2]["SUPER_ADMIN"][0])
        AccessService(db, sa, env[0], ended + timedelta(days=16)).execute(
            {"type": "restore", "family": "people", "id": pid}
        )
        db.commit()
        run_retention(db, env[0], ended + timedelta(days=17))
        db.commit()
        assert person.deleted_at is None and person.purge_at == deadline
        assert db.get(Visit, vid).state == "ENDED" and db.get(Card, cid).disabled
        run_retention(db, env[0], deadline)
        db.commit()
        assert person.purged and person.email is None and person.phone == ""
        row = db.get(AccessEvent, event)
        assert (
            row.person == "Anonymous visitor"
            and row.reference == ""
            and row.raw == {"source": "SIMULATED", "anonymized": True}
            and row.assignment_id
        )
        assert not db.scalar(select(Upload).where(Upload.person_id == pid))
        assert not db.scalar(select(Account).where(Account.person_id == pid))
        assert not db.scalar(select(Notification).where(Notification.person_id == pid))
        assert all(
            "First Visitor" not in r.actor + r.target for r in db.scalars(select(Audit))
        )
        assert all(
            "First Visitor" not in n.detail for n in db.scalars(select(Notification))
        )
        assert db.get(Visit, vid).purpose == "Purged"
    assert not list(env[0].upload_dir.iterdir())


def test_access_audit_retention_restore_deadlines(env):
    with env[1].session() as db:
        at = datetime(2026, 8, 31, 10, tzinfo=timezone.utc)
        eid = store_event(db, "historic", at, "00999999")
        audit(db, None, "HISTORIC", "Retention", at=at)
        db.commit()
        aid = db.scalar(select(Audit).where(Audit.action == "HISTORIC")).id
        run_retention(db, env[0], at + timedelta(days=90))
        db.commit()
        e = db.get(AccessEvent, eid)
        assert e.deleted_at == at + timedelta(days=90)
        deadline = e.purge_at
        AccessService(
            db,
            db.get(Account, env[2]["SUPER_ADMIN"][0]),
            env[0],
            at + timedelta(days=91),
        ).execute({"type": "restore", "family": "events", "id": eid})
        db.commit()
        run_retention(db, env[0], at + timedelta(days=92))
        db.commit()
        assert (
            db.get(AccessEvent, eid).deleted_at is None
            and db.get(AccessEvent, eid).purge_at == deadline
        )
        run_retention(db, env[0], at + timedelta(days=120))
        db.commit()
        assert not db.get(AccessEvent, eid)
        soft = months(at, 6)
        assert soft == datetime(2027, 2, 28, 10, tzinfo=timezone.utc)
        run_retention(db, env[0], soft)
        db.commit()
        assert db.get(Audit, aid).deleted_at == soft
        AccessService(
            db,
            db.get(Account, env[2]["SUPER_ADMIN"][0]),
            env[0],
            soft + timedelta(days=1),
        ).execute({"type": "restore", "family": "audits", "id": aid})
        db.commit()
        run_retention(db, env[0], soft + timedelta(days=2))
        db.commit()
        assert db.get(Audit, aid).deleted_at is None
        run_retention(db, env[0], soft + timedelta(days=30))
        db.commit()
        assert not db.get(Audit, aid)


def test_filtered_exports_dedup_and_audit(env):
    with env[1].session() as db:
        id = store_event(db, "source-1", now(), "00009999")
        assert store_event(db, "source-1", now(), "00009999") is None
        row = db.get(AccessEvent, id)
        row.person = " =SUM(A1)"
        store_event(db, "source-2", now(), "00008888")
        backfill(db)
        backfill(db)
        db.commit()
        assert db.scalar(select(func.count()).select_from(AccessEvent)) == 3
    c = session_client(env, "MANAGEMENT")
    r = c.get("/api/records/events", params={"card": "00009999", "result": "DENIED"})
    assert r.status_code == 200 and r.json()["total"] == 1
    export = write(
        c, "/api/exports/events/csv", {"card": "00009999", "result": "DENIED"}
    )
    assert export.status_code == 200
    assert (
        "00009999" in export.text
        and "00008888" not in export.text
        and "' =SUM(A1)" in export.text
    )
    pdf = write(c, "/api/exports/events/pdf", {"card": "00009999"})
    assert pdf.content.startswith(b"%PDF-")
    assert (
        c.get("/api/records/audits", params={"action": "EXPORT_"}).json()["total"] == 2
    )


def test_schedules_privileged_card_and_failed_designation(env, png):
    u, _, a, _, assignment = issued(env, png)
    sa = session_client(env, "SUPER_ADMIN")
    card = u.get("/api/workspace").json()["cards"][0]
    monday = datetime(2026, 10, 5, 11, tzinfo=timezone.utc)
    schedule = {
        "weekdays": [1, 2, 3, 4, 5],
        "opens": "08:00",
        "closes": "18:00",
        "exceptions": [{"date": "2026-10-05", "closed": True, "label": "Holiday"}],
    }
    cmd(a, {"type": "schedule", "schedule": schedule})
    assert a.get("/api/workspace").json()["schedule"]["sync"] == "PENDING"
    cmd(a, {"type": "retry-sync", "schedule": True})
    assert a.get("/api/workspace").json()["schedule"]["sync"] == "SYNCHRONIZED"
    with env[1].session() as db:
        assert not schedule_allows(db, monday)
    cmd(sa, {"type": "designate", "cardId": card["id"]}, 409)
    cmd(sa, {"type": "role", "personId": env[2]["USER"][1], "role": "SUPER_ADMIN"})
    assert not u.get("/api/workspace").json()["cards"][0]["privileged"]
    controls(env, offline=True)
    cmd(sa, {"type": "designate", "cardId": card["id"]})
    assert not u.get("/api/workspace").json()["cards"][0]["privileged"]
    controls(env, offline=False)
    cmd(sa, {"type": "designate", "cardId": card["id"]})
    assert u.get("/api/workspace").json()["cards"][0]["privileged"]
    with env[1].session() as db:
        # Explicitly designated credential alone can pass a closed holiday.
        eid = store_event(db, "holiday", monday, card["number"])
        db.commit()
        assert db.get(AccessEvent, eid).result == "GRANTED"
    cmd(sa, {"type": "role", "personId": env[2]["USER"][1], "role": "USER"})
    assert not u.get("/api/workspace").json()["cards"][0]["privileged"]
