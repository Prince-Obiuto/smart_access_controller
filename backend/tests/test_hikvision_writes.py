"""Controlled outbox and readback regressions on isolated PostgreSQL; no device."""

from copy import deepcopy

import pytest
from sqlalchemy import select

from app.common import set_setting
from app.models import (
    Application,
    Assignment,
    Card,
    Confirmation,
    Notification,
    Operation,
)
from app.operations import process_operations
from app.hikvision_writes import (
    digest,
    scope_key,
    device_card_expiry,
    compile_schedule,
    card_matches,
    reconcile,
    WriteBlocked,
    process_controlled,
)
from datetime import datetime, timezone
from app.hikvision_control import activate
from app.controllers import ControllerReadUnavailable
from conftest import approved, cmd, issued


def scope():
    empty_week = {
        "enabled": 0,
        "days": [[[0, 0, 0, 0, 0] for _ in range(8)] for _ in range(7)],
    }
    empty_template = {"enabled": 0, "name": "", "week": 0, "groups": [0] * 16}
    return {
        "enabled": True,
        "epoch": "test-epoch",
        "deviceIdentity": "synthetic",
        "cards": {},
        "weekdayOrder": "MONDAY_FIRST",
        "normal": {"week": 3, "template": 3},
        "resources": {
            "WEEK:3": digest(empty_week),
            "TEMPLATE:3": digest(empty_template),
        },
    }, {("WEEK", 3): empty_week, ("TEMPLATE", 3): empty_template}


def test_device_card_expiry_uses_last_inclusive_second():
    expiry = datetime(2026, 12, 30, 23, 0, tzinfo=timezone.utc)

    assert device_card_expiry(expiry) == [2026, 12, 30, 23, 59, 59]


def test_device_card_expiry_intent_is_list_and_range_check_safe():
    expiry = datetime(2026, 12, 30, 23, 0, tzinfo=timezone.utc)
    converted = device_card_expiry(expiry)

    assert isinstance(converted, list)
    assert converted[-1] == 59


class FakeTransport:
    plans = {}
    cards = {}
    calls = []
    fail = None
    mismatch = False

    def __init__(self, settings, scope):
        pass

    def start(self):
        self.calls.append("start")

    def close(self):
        pass

    def verify_ownership(self):
        pass

    def read_card(self, number):
        self.calls.append("read-card")
        return deepcopy(self.cards.get(number))

    def read_plan(self, kind, number):
        return deepcopy(self.plans[(kind, number)])

    def write_plan(self, kind, number, expected):
        self.calls.append("write-plan")
        self.plans[(kind, number)] = deepcopy(expected)

    def write_card(self, intent, plan):
        self.calls.append("write-card")
        if not self.mismatch:
            self.cards[intent["card"]] = {
                "card": intent["card"],
                "valid": 1,
                "type": 1 if intent["enabled"] else 3,
                "leader": 0,
                "doors": [1] + [0] * 255 if intent["enabled"] else [0] * 256,
                "plans": [[plan, 0, 0, 0]] + [[0] * 4 for _ in range(255)],
                "validityEnabled": 1,
                "beginFlag": 1,
                "endFlag": 1,
                "timeType": 0,
                "start": intent.get("start"),
                "expiry": intent.get("expiry"),
            }
        if self.fail:
            raise self.fail


def prepare(env, png, monkeypatch):
    _, _, admin, application = approved(env, png)
    settings, database, accounts, _ = env
    settings.controller_mode = "HIKVISION_CONTROLLED"
    # Normal workflow creates the explicit pending test credential; no scope yet.
    result = cmd(admin, {"type": "issue", "id": application, "number": "00000042"})
    assert result["result"] == "PENDING"
    data, plans = scope()
    FakeTransport.cards, FakeTransport.plans, FakeTransport.calls = {}, plans, []
    FakeTransport.fail, FakeTransport.mismatch = None, False
    monkeypatch.setattr("app.hikvision_writes.PermissionTransport", FakeTransport)
    with database.session() as db:
        assignment = db.scalar(select(Assignment))
        data["cards"] = {
            "00000042": {"kind": "PERSONAL", "personIds": [assignment.person_id]}
        }
        set_setting(db, scope_key("controller-1"), data)
        db.commit()
    return settings, database, admin, result["operationIds"][0], data


def review_and_run(settings, database, op_id):
    with database.session() as db:
        op = db.get(Operation, op_id)
        op.data = {**op.data, "writeEpoch": "test-epoch"}
        db.commit()
        process_controlled(db, settings)


def test_activation_does_not_drain_backlog_and_readback_confirms(env, png, monkeypatch):
    settings, database, admin, op_id, _ = prepare(env, png, monkeypatch)
    with database.session() as db:
        process_operations(db, settings)
        assert db.get(Operation, op_id).attempts == 0
        assert FakeTransport.calls == []
    review_and_run(settings, database, op_id)
    with database.session() as db:
        op = db.get(Operation, op_id)
        assert (
            op.status == "CONFIRMED"
            and op.data["readbackVerified"]
            and op.data["simulated"] is False
        )
        assert db.scalar(select(Assignment)).state == "ACTIVE"
    snap = admin.get("/api/workspace").json()
    assert snap["assignments"][0]["confirmations"][0]["simulated"] is False
    assert snap["assignments"][0]["confirmations"][0]["confirmed"]
    assert snap["controllerTestWritesEnabled"] and not snap["controllerWritesAvailable"]
    cmd(admin, {"type": "unlock", "doorId": "door-1", "confirmed": True}, 409)
    # Read-only controls must not erase independently stored real write proof.
    previous_calls = list(FakeTransport.calls)
    settings.controller_mode = "HIKVISION_CONTROLLED"
    snap = admin.get("/api/workspace").json()
    assert snap["assignments"][0]["confirmations"][0]["confirmed"]
    assert snap["assignments"][0]["confirmations"][0]["simulated"] is False
    with database.session() as db:
        process_operations(db, settings)
    assert FakeTransport.calls == previous_calls


def test_new_assignment_in_existing_template_scope_is_released_to_writer(
    env, png, monkeypatch
):
    monkeypatch.setattr("app.main.process_operations", lambda *a, **k: None)
    _, _, admin, application_id = approved(env, png)
    settings, database, _, _ = env
    settings.controller_mode = "HIKVISION_CONTROLLED"
    with database.session() as db:
        set_setting(
            db,
            scope_key("controller-1"),
            {
                "enabled": True,
                "epoch": "existing-template-epoch",
                "kind": "EXISTING_TEMPLATE",
                "existingTemplate": 3,
                "cards": {
                    "00000043": {"kind": "PERSONAL", "personIds": [env[2]["USER"][1]]}
                },
            },
        )
        db.commit()

    result = cmd(admin, {"type": "issue", "id": application_id, "number": "00000043"})
    with database.session() as db:
        application = db.get(Application, application_id)
        operation = db.scalar(
            select(Operation).where(Operation.target_id == application.assignment_id)
        )
        assert operation
        assert operation.data["writeEpoch"] == "existing-template-epoch"
        assert application.state == "ISSUANCE_PENDING"
        assert result["result"] == "PENDING"


def test_ambiguous_send_reconciles_without_replaying(env, png, monkeypatch):
    settings, database, _, op_id, _ = prepare(env, png, monkeypatch)
    FakeTransport.fail = ControllerReadUnavailable("PERMISSION_UNCERTAIN")
    review_and_run(settings, database, op_id)
    with database.session() as db:
        op = db.get(Operation, op_id)
        assert op.status == "UNCERTAIN" and not op.data["readbackVerified"]
        assert db.scalar(select(Confirmation)).state != "SYNCHRONIZED"
        op.data = {**op.data, "retryAt": None}
        db.commit()
        FakeTransport.fail = None
        process_controlled(db, settings)
        assert db.get(Operation, op_id).status == "CONFIRMED"
    assert FakeTransport.calls.count("write-card") == 1


def test_device_ignoring_expiry_flag_cannot_synchronize(env, png, monkeypatch):
    settings, database, _, op_id, _ = prepare(env, png, monkeypatch)
    read = FakeTransport.read_card

    def missing_limit(self, number):
        record = read(self, number)
        return {**record, "endFlag": 0} if record else record

    monkeypatch.setattr(FakeTransport, "read_card", missing_limit)
    review_and_run(settings, database, op_id)
    with database.session() as db:
        op = db.get(Operation, op_id)
        assert op.status == "FAILED" and op.data["message"] == "CARD_READBACK_MISMATCH"
        assert db.scalar(select(Confirmation)).state != "SYNCHRONIZED"


def test_failed_readback_never_confirms_then_revoke_is_prioritized(
    env, png, monkeypatch
):
    settings, database, admin, op_id, _ = prepare(env, png, monkeypatch)
    FakeTransport.mismatch = True
    review_and_run(settings, database, op_id)
    with database.session() as db:
        assert db.get(Operation, op_id).status == "FAILED"
        assert db.scalar(select(Assignment)).state == "PENDING"
        assignment_id = db.scalar(select(Assignment.id))
    FakeTransport.mismatch = False
    cmd(
        admin,
        {"type": "revoke", "assignmentId": assignment_id, "reason": "NOT_PERMITTED"},
    )
    with database.session() as db:
        assert db.get(Card, db.get(Assignment, assignment_id).card_id).revoked
        # Old enable cannot run even if manually marked pending again.
        old = db.get(Operation, op_id)
        old.status = "PENDING"
        old.data = {**old.data, "retryAt": None}
        db.commit()
        process_controlled(db, settings)
        assert db.get(Operation, op_id).status == "SUPERSEDED"
    assert (
        FakeTransport.calls.count("write-card") == 1
    )  # absent card is already disabled


def test_readonly_blocks_permission_protocol_even_with_scoped_settings(tmp_path):
    from app.config import ControllerSettings
    from app.hikvision import NativeSession

    session = NativeSession(
        ControllerSettings(_env_file=None, controller_mode="SIMULATED"),
        permissions=True,
    )
    for command in ["AUTHORIZE", "CARD_WRITE", "PLAN_WRITE"]:
        with pytest.raises(ControllerReadUnavailable, match="READ_ONLY"):
            session.permission_request(command, ["00000042"])


def test_policy_compilation_does_not_guess_weekdays_or_omit_exceptions():
    data, _ = scope()
    intent = {
        "kind": "SCHEDULE",
        "schedule": {"weekdays": [1, 2, 3, 4, 5], "opens": "08:00", "closes": "18:00"},
        "exceptions": [],
    }
    resources, _ = compile_schedule(intent, data)
    assert [d[0][0] for d in resources[0][2]["days"]] == [1, 1, 1, 1, 1, 0, 0]
    assert all(s[3:] == [0, 0] for day in resources[0][2]["days"] for s in day)
    data.pop("weekdayOrder")
    with pytest.raises(WriteBlocked, match="WEEKDAY_ORDER_UNVERIFIED"):
        compile_schedule(intent, data)
    data["weekdayOrder"] = "MONDAY_FIRST"
    intent["exceptions"] = [{"date": "2026-12-25", "closed": True}]
    with pytest.raises(WriteBlocked, match="HOLIDAY_CAPACITY"):
        compile_schedule(intent, data)
    data["normal"].update(group=3, holidays=[3])
    resources, _ = compile_schedule(intent, data)
    holiday = next(r[2] for r in resources if r[0] == "HOLIDAY")
    assert holiday["start"] == [2026, 12, 25] and not any(
        s[0] for s in holiday["segments"]
    )


def test_disabled_readback_requires_blacklist_and_no_rights():
    intent = {"card": "00000042", "enabled": False}
    assert card_matches(None, intent)
    assert not card_matches({"card": "00000042", "type": 6, "doors": [0] * 256}, intent)
    assert card_matches({"card": "00000042", "type": 3, "doors": [0] * 256}, intent)


def test_unmanaged_schedule_change_blocks_before_write():
    data, plans = scope()
    FakeTransport.plans = plans
    FakeTransport.calls = []
    FakeTransport.plans[("WEEK", 3)]["enabled"] = 1
    intent = {
        "kind": "SCHEDULE",
        "schedule": {"weekdays": [1], "opens": "08:00", "closes": "09:00"},
        "exceptions": [],
    }
    with pytest.raises(WriteBlocked, match="UNMANAGED_SCHEDULE_CHANGE"):
        reconcile(
            FakeTransport(None, data), intent, data, lambda *x: None, lambda *x: None
        )
    assert "write-plan" not in FakeTransport.calls


def test_old_simulated_confirmation_cannot_enable_replacement(env, png):
    _, _, _, _, a = issued(env, png)
    settings, database, _, _ = env
    settings.controller_mode = "HIKVISION_CONTROLLED"
    with database.session() as db:
        assignment = db.get(Assignment, a["id"])
        assignment.state = "REVOKED"
        c = db.scalar(select(Confirmation))
        c.desired = c.last_confirmed = "DISABLED"
        c.state = "SYNCHRONIZED"
        from app.service import AccessService

        assert not AccessService(db, None, settings).old_removed(assignment.person_id)


def test_activation_protects_existing_enabled_plans(env):
    settings, database, accounts, _ = env
    from app.models import Account

    with database.session() as db:
        card = Card(
            number="00000042",
            kind="PERSONAL",
            disabled=True,
            revoked=False,
            privileged=False,
            ever_privileged=False,
        )
        db.add(card)
        db.flush()
        manifest = {
            "controllerId": "controller-1",
            "cards": {
                "00000042": {"kind": "PERSONAL", "personIds": [accounts["USER"][1]]}
            },
            "normal": {"week": 3, "template": 3},
        }
        observed = {
            "complete": True,
            "model": "DS-K2802",
            "plans": {"WEEK:3": {"enabled": 1}},
            "cards": [],
            "deviceIdentity": "synthetic",
        }
        with pytest.raises(WriteBlocked, match="EXISTING_RESOURCE_PROTECTED"):
            activate(
                db,
                settings,
                db.get(Account, accounts["SUPER_ADMIN"][0]),
                manifest,
                observed,
            )


def test_activation_requires_complete_inventory(env):
    settings, database, accounts, _ = env
    from app.models import Account

    with database.session() as db:
        with pytest.raises(WriteBlocked, match="INVENTORY_REQUIRED"):
            activate(
                db,
                settings,
                db.get(Account, accounts["SUPER_ADMIN"][0]),
                {"controllerId": "controller-1"},
                {"complete": False, "model": "DS-K2802"},
            )


def test_partial_application_plan_can_be_reconciled_to_new_desired_state():
    data, plans = scope()
    old_intent = {
        "kind": "SCHEDULE",
        "schedule": {"weekdays": [1], "opens": "08:00", "closes": "09:00"},
        "exceptions": [],
    }
    old_resources, _ = compile_schedule(old_intent, data)
    old_week = old_resources[0][2]
    # The durable pre-send ledger knows the exact possibly applied payload.
    plans[("WEEK", 3)] = deepcopy(old_week)
    data["preparedResources"] = {"WEEK:3": digest(old_week)}
    FakeTransport.plans, FakeTransport.calls = plans, []
    new_intent = deepcopy(old_intent)
    new_intent["schedule"]["closes"] = "08:30"
    reconcile(
        FakeTransport(None, data), new_intent, data, lambda *a: None, lambda *a: None
    )
    assert FakeTransport.plans[("WEEK", 3)]["days"][0][0][2] == 8 * 3600 + 30 * 60


def test_schedule_change_invalidates_prior_card_confirmation(env, png, monkeypatch):
    settings, database, admin, op_id, _ = prepare(env, png, monkeypatch)
    review_and_run(settings, database, op_id)
    assert admin.get("/api/workspace").json()["assignments"][0]["confirmations"][0][
        "confirmed"
    ]
    cmd(
        admin,
        {
            "type": "schedule",
            "schedule": {
                "weekdays": [1, 2, 3, 4, 5],
                "opens": "08:00",
                "closes": "17:00",
                "exceptions": [],
            },
        },
    )
    assert not admin.get("/api/workspace").json()["assignments"][0]["confirmations"][0][
        "confirmed"
    ]


def test_failed_disable_is_critical_and_bounded(env, png, monkeypatch):
    settings, database, admin, op_id, _ = prepare(env, png, monkeypatch)
    review_and_run(settings, database, op_id)
    FakeTransport.mismatch = True
    with database.session() as db:
        assignment_id = db.scalar(select(Assignment.id))
    cmd(admin, {"type": "revoke", "assignmentId": assignment_id, "reason": "MISSING"})
    with database.session() as db:
        op = db.scalar(select(Operation).where(Operation.desired == "DISABLED"))
        assert op.status == "FAILED"
        assert db.scalar(
            select(Notification).where(Notification.severity == "CRITICAL")
        )
        for _ in range(8):
            op.data = {**op.data, "retryAt": None}
            db.commit()
            process_controlled(db, settings)
        assert db.get(Operation, op.id).attempts == 5


def test_controlled_visitor_return_requires_readback_before_reuse(env, monkeypatch):
    from test_visitors_retention import visitor
    from conftest import session_client
    from app.models import Person

    settings, database, _, _ = env
    first, visit = visitor(env)
    second, second_visit = visitor(env, "Second")
    admin = session_client(env, "ADMIN")
    sa = session_client(env, "SUPER_ADMIN")
    settings.controller_mode = "HIKVISION_CONTROLLED"
    cmd(sa, {"type": "pool-card", "number": "00004000"})
    data, plans = scope()
    FakeTransport.cards, FakeTransport.plans, FakeTransport.calls = {}, plans, []
    FakeTransport.fail, FakeTransport.mismatch = None, False
    monkeypatch.setattr("app.hikvision_writes.PermissionTransport", FakeTransport)
    with database.session() as db:
        people = list(db.scalars(select(Person.id).where(Person.kind == "VISITOR")))
        data["cards"] = {"00004000": {"kind": "VISITOR", "personIds": people}}
        set_setting(db, scope_key("controller-1"), data)
        pool_op = db.scalar(select(Operation).where(Operation.target_type == "POOL"))
        card_id, op_id = pool_op.target_id, pool_op.id
        db.commit()
    review_and_run(settings, database, op_id)
    for v in [visit, second_visit]:
        cmd(
            admin,
            {
                "type": "visitor-approve",
                "id": v["id"],
                "note": "Synthetic approved test visit",
            },
        )
    cmd(admin, {"type": "visitor-assign", "id": visit["id"], "cardId": card_id})
    assert FakeTransport.cards["00004000"]["doors"][0] == 1
    FakeTransport.mismatch = True
    cmd(
        admin,
        {
            "type": "visitor-return",
            "id": visit["id"],
            "returned": True,
            "condition": "GOOD",
        },
    )
    cmd(
        admin,
        {"type": "visitor-assign", "id": second_visit["id"], "cardId": card_id},
        409,
    )
    FakeTransport.mismatch = False
    assignment_id = first.get("/api/workspace").json()["assignments"][0]["id"]
    cmd(admin, {"type": "retry-sync", "assignmentId": assignment_id})
    assert FakeTransport.cards["00004000"]["doors"] == [0] * 256
    assert FakeTransport.cards["00004000"]["type"] == 3
    cmd(admin, {"type": "visitor-assign", "id": second_visit["id"], "cardId": card_id})
    assert FakeTransport.cards["00004000"]["doors"][0] == 1
