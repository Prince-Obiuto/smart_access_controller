"""Web management acceptance using an isolated DB and explicit synthetic SDK."""

import copy

import pytest
from sqlalchemy import select

from app import controller_management as management
from app.common import now, set_setting
from app.hikvision_writes import WriteBlocked, digest
from app.models import Application, Assignment, Card, Operation
from conftest import approved, cmd, session_client, write


def wrapped(record):
    return {"record": record, "nativeHex": digest(record)}


class Device:
    def __init__(self):
        week = {
            "enabled": 1,
            "days": [
                [[1, 0, 86400, 0, 0]] + [[0] * 5 for _ in range(7)] for _ in range(7)
            ],
        }
        self.resources = {
            "TEMPLATE:3": wrapped(
                {
                    "enabled": 1,
                    "name": "Existing owner template",
                    "week": 3,
                    "groups": [0] * 16,
                }
            ),
            "WEEK:3": wrapped(week),
        }
        self.cards, self.prepared, self.writes = [], {}, []
        self.fail = None
        self.corrupt = False
        self.closed = 0

    def start(self):
        pass

    def close(self):
        self.closed += 1

    def capability(self, card=False):
        pass

    def plan_capability(self, kind, slot, proposed):
        pass

    def graph(self, extra=()):
        return copy.deepcopy(
            {
                "cards": self.cards,
                "resources": self.resources,
                "doors": {"1": {"nativeHex": "door1"}, "2": {"nativeHex": "door2"}},
            }
        )

    def card(self, value):
        if value == "ALL":
            return copy.deepcopy(self.cards)
        return copy.deepcopy(next((c for c in self.cards if c["card"] == value), None))

    def plan(self, kind, slot):
        return copy.deepcopy(self.resources[f"{kind}:{slot}"])

    def request(self, name, fields):
        if name == "AUTHORIZE":
            return []
        action, kind = fields[:2]
        if action == "PREPARE" and kind == "CARD":
            _, _, number, _, enabled, template, *dates = fields
            record = self.card(number) or {
                "card": number,
                "doors": [0] * 256,
                "plans": [[0] * 4 for _ in range(256)],
                "leader": 0,
                "valid": 0,
                "validityEnabled": 0,
                "beginFlag": 0,
                "endFlag": 0,
                "timeType": 0,
                "start": [0] * 6,
                "expiry": [0] * 6,
            }
            record["type"] = 1 if enabled else 3
            record["doors"] = [int(enabled)] + [0] * 255
            if enabled:
                record.update(
                    valid=1,
                    leader=0,
                    validityEnabled=1,
                    beginFlag=1,
                    endFlag=1,
                    timeType=0,
                    start=dates[:6],
                    expiry=dates[6:],
                )
                record["plans"][0] = [template, 0, 0, 0]
            record["nativeHex"] = digest(management.public(record))
            self.prepared[record["nativeHex"]] = record
            return [copy.deepcopy(record)]
        if action == "PREPARE":
            _, _, slot, _, enabled, *values = fields
            record = copy.deepcopy(self.resources[f"{kind}:{slot}"]["record"])
            record["enabled"] = enabled
            if kind == "WEEK":
                record["days"] = [
                    [
                        [values[(d * 8 + s) * 3 + i] for i in range(3)]
                        + record["days"][d][s][3:]
                        for s in range(8)
                    ]
                    for d in range(7)
                ]
            if kind == "TEMPLATE":
                record.update(week=values[0], groups=values[1:])
            result = wrapped(record)
            self.prepared[result["nativeHex"]] = result
            return [result]
        if action == "APPLY":
            self.writes.append(copy.deepcopy(fields))
            if kind == "CARD":
                number, _, raw = fields[2:]
                self.cards = [c for c in self.cards if c["card"] != number] + [
                    copy.deepcopy(self.prepared[raw])
                ]
                if self.corrupt:
                    self.cards[-1]["endFlag"] = 0
            else:
                self.resources[f"{kind}:{fields[2]}"] = copy.deepcopy(
                    self.prepared[fields[-1]]
                )
            if self.fail:
                raise WriteBlocked(self.fail)
            return []
        raise AssertionError(fields)


@pytest.fixture
def device(monkeypatch):
    value = Device()
    monkeypatch.setattr(management, "Transport", lambda settings: value)
    return value


def pending(env, png):
    _, _, admin, application_id = approved(env, png)
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    result = cmd(admin, {"type": "issue", "id": application_id, "number": "00009999"})
    return admin, application_id, result["operationIds"][0]


def preview(admin, op, reconcile=False):
    response = write(
        admin,
        "/api/controller/management/card-preview",
        {
            "sourceOperationId": op,
            "template": 3,
            "reconcile": reconcile,
            "reason": "Owner reviewed current device rights" if reconcile else "",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def apply(admin, p):
    return write(
        admin,
        f"/api/controller/management/{p['id']}/execute",
        {"confirmed": True, "affectedCards": [x["card"] for x in p["affected"]]},
    )


def test_paid_web_issuance_without_manifest_preserves_backlog_and_proof(
    env, png, device
):
    admin, app_id, source = pending(env, png)
    with env[1].session() as db:
        backlog = Operation(
            controller_id="controller-1",
            target_type="SCHEDULE",
            target_id="normal",
            desired="UPDATED",
            created_at=now(),
            data={},
        )
        db.add(backlog)
        db.commit()
        backlog_id = backlog.id
    p = preview(admin, source)
    assert not device.writes and p["proposed"]["plans"][0] == [3, 0, 0, 0]
    result = apply(admin, p)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "CONFIRMED" and result.json()["readbackVerified"]
    assert apply(admin, p).json()["status"] == "CONFIRMED"
    assert len(device.writes) == 1
    with env[1].session() as db:
        assert db.get(Application, app_id).state == "ISSUED"
        assert db.scalar(select(Assignment)).sync == "SYNCHRONIZED"
        assert db.get(Operation, backlog_id).status == "PENDING"
        assert "writeEpoch" not in db.get(Operation, source).data


def test_external_change_requires_new_comparison_and_preserves_failure(
    env, png, device
):
    admin, _, source = pending(env, png)
    p = preview(admin, source)
    device.resources["WEEK:3"]["nativeHex"] = "external-change"
    result = apply(admin, p).json()
    assert result["status"] == "REQUIRES_RECONCILIATION" and not device.writes
    with env[1].session() as db:
        assert db.get(Operation, source).status == "PENDING"


def test_unknown_transport_outcome_never_replays_same_preview(env, png, device):
    admin, _, source = pending(env, png)
    p = preview(admin, source)
    device.fail = "SDK_ERROR:17"
    result = apply(admin, p).json()
    assert (
        result["status"] == "REQUIRES_RECONCILIATION" and not result["readbackVerified"]
    )
    assert apply(admin, p).status_code == 409 and len(device.writes) == 1
    device.fail = None
    # A new explicit reconciliation GET finds the exact desired state. It is
    # confirmed without a second SET; the failed preview remains evidence.
    reconciled = preview(admin, source, True)
    assert apply(admin, reconciled).json()["status"] == "CONFIRMED"
    assert len(device.writes) == 1
    with env[1].session() as db:
        assert db.get(Operation, p["id"]).data["error"] == "SDK_ERROR:17"


def test_validity_mismatch_remains_unsynchronized(env, png, device):
    admin, app_id, source = pending(env, png)
    p = preview(admin, source)
    device.corrupt = True
    result = apply(admin, p).json()
    assert result["status"] == "REQUIRES_RECONCILIATION"
    with env[1].session() as db:
        assert db.get(Application, app_id).state == "ISSUANCE_PENDING"
        assert db.get(Operation, source).status == "PENDING"


def test_local_revoke_after_preview_blocks_activation(env, png, device):
    admin, _, source = pending(env, png)
    p = preview(admin, source)
    with env[1].session() as db:
        db.scalar(select(Card)).revoked = True
        db.commit()
    assert apply(admin, p).status_code == 409 and not device.writes


def test_role_guards_and_ordinary_super_admin_eligibility(env, device):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    for role in ("MANAGER", "MANAGEMENT", "USER"):
        assert (
            session_client(env, role).get("/api/controller/cards/00009999").status_code
            == 403
        )
    assert (
        session_client(env, "ADMIN").get("/api/controller/plans/WEEK/3").status_code
        == 403
    )
    snapshot = session_client(env, "SUPER_ADMIN").get("/api/workspace").json()
    person = next(p for p in snapshot["people"] if p["id"] == env[2]["SUPER_ADMIN"][1])
    assert person["controllerCardEligible"]
    assert not any(c["privileged"] for c in snapshot["cards"])


def test_schedule_preview_stale_revision_and_shared_impact(env, device):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    admin = session_client(env, "SUPER_ADMIN")
    read = admin.get("/api/controller/plans/TEMPLATE/3").json()
    desired = {"enabled": 0, "week": 3, "groups": [0] * 16}
    body = {
        "kind": "TEMPLATE",
        "slot": 3,
        "revision": read["revision"],
        "desired": desired,
    }
    key = "same-plan-preview"
    first = write(admin, "/api/controller/management/plan-preview", body, key=key)
    assert first.status_code == 200, first.text
    assert (
        write(admin, "/api/controller/management/plan-preview", body, key=key).json()[
            "id"
        ]
        == first.json()["id"]
    )
    assert not device.writes
    body["desired"] = {**desired, "enabled": 1}
    assert (
        write(
            admin, "/api/controller/management/plan-preview", body, key=key
        ).status_code
        == 409
    )
    body["revision"] = "0" * 64
    assert (
        write(admin, "/api/controller/management/plan-preview", body).status_code == 409
    )


def test_nonuniform_week_requires_independent_order_and_reserved_flags_preserved(
    env, device
):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    admin = session_client(env, "SUPER_ADMIN")
    week = device.resources["WEEK:3"]["record"]
    days = [[s[:3] for s in day] for day in week["days"]]
    days[0][0] = [1, 3600, 7200]
    with env[1].session() as db:
        with pytest.raises(WriteBlocked, match="WEEKDAY_ORDER_UNVERIFIED"):
            management.plan_payload(db, "WEEK", {"enabled": 1, "days": days})
        set_setting(
            db, "hikvision:weekday-evidence", {"indexToWeekday": [6, 0, 1, 2, 3, 4, 5]}
        )
        db.commit()
        compiled = management.plan_payload(db, "WEEK", {"enabled": 1, "days": days})
        assert compiled[24:27] == [1, 3600, 7200]
    assert (
        write(
            admin,
            "/api/controller/weekday-evidence",
            {
                "slot": 3,
                "sourceReference": "Retrieved iVMS owner view",
                "retrievedFromController": True,
                "labelledDays": [days[1]] * 7,
            },
        ).status_code
        == 409
    )


def test_plan_readback_and_exact_rollback_preview(env, device):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    admin = session_client(env, "SUPER_ADMIN")
    read = admin.get("/api/controller/plans/TEMPLATE/3").json()
    original = copy.deepcopy(device.resources["TEMPLATE:3"])
    p = write(
        admin,
        "/api/controller/management/plan-preview",
        {
            "kind": "TEMPLATE",
            "slot": 3,
            "revision": read["revision"],
            "desired": {"enabled": 0, "week": 3, "groups": [0] * 16},
        },
    ).json()
    assert apply(admin, p).json()["status"] == "CONFIRMED"
    current = admin.get("/api/controller/plans/TEMPLATE/3").json()
    rollback = write(
        admin,
        "/api/controller/management/plan-preview",
        {
            "kind": "TEMPLATE",
            "slot": 3,
            "revision": current["revision"],
            "desired": {},
            "rollbackOf": p["id"],
        },
    ).json()
    device.prepared[original["nativeHex"]] = original
    assert apply(admin, rollback).json()["status"] == "CONFIRMED"
    assert device.resources["TEMPLATE:3"] == original


def test_fresh_card_reads_do_not_require_scope_and_adoption_changes_no_permissions(
    env, device
):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    admin = session_client(env, "SUPER_ADMIN")
    assert admin.get("/api/controller/cards/1051305927").status_code == 200
    response = write(
        admin,
        "/api/controller/management/adopt-card-baseline",
        {
            "number": "00009999",
            "reason": "Owner inspected the current external record",
            "confirmed": True,
        },
    )
    assert response.status_code == 200
    assert not device.writes
    with env[1].session() as db:
        assert db.scalar(select(Assignment)) is None
        assert db.scalar(select(Card)) is None


def test_wrong_impact_and_stale_source_do_not_send(env, png, device):
    admin, _, source = pending(env, png)
    p = preview(admin, source)
    r = write(
        admin,
        f"/api/controller/management/{p['id']}/execute",
        {"confirmed": True, "affectedCards": ["wrong-card"]},
    )
    assert r.status_code == 409 and not device.writes
    with env[1].session() as db:
        db.scalar(select(Assignment)).version += 1
        db.commit()
    assert apply(admin, p).status_code == 409 and not device.writes


def test_concurrent_submissions_send_once(env, png, device, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    admin, _, source = pending(env, png)
    other = session_client(env, "ADMIN")
    p = preview(admin, source)
    entered, release = Event(), Event()
    original = device.request

    def paused(name, fields):
        if name == "MANAGE" and fields[:2] == ["APPLY", "CARD"]:
            entered.set()
            assert release.wait(10)
        return original(name, fields)

    monkeypatch.setattr(device, "request", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(apply, admin, p)
        try:
            assert entered.wait(10)
            assert apply(other, p).status_code == 409
        finally:
            release.set()
        assert first.result(timeout=10).json()["status"] == "CONFIRMED"
    assert len(device.writes) == 1


def test_plan_shared_by_door_two_cannot_be_changed(env, device):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    device.cards = [{"card": "external-card", "doors": [1, 1], "plans": [[3], [3]]}]
    admin = session_client(env, "SUPER_ADMIN")
    read = admin.get("/api/controller/plans/TEMPLATE/3").json()
    r = write(
        admin,
        "/api/controller/management/plan-preview",
        {
            "kind": "TEMPLATE",
            "slot": 3,
            "revision": read["revision"],
            "desired": {"enabled": 0, "week": 3, "groups": [0] * 16},
        },
    )
    assert r.status_code == 409 and not device.writes


def test_capabilities_distinguish_template_links_from_total_capacity():
    from types import SimpleNamespace

    t = object.__new__(management.Transport)
    t.session = SimpleNamespace(
        metadata={
            "permissionCapabilities": {
                "CardRightPlan": """<CardRightPlan><PlanTemplate><maxHolidayGroupNum>4</maxHolidayGroupNum></PlanTemplate><WeekPlan><weekPlanNo min="1" max="64"/><maxDaySegment>8</maxDaySegment></WeekPlan></CardRightPlan>"""
            }
        }
    )
    t.plan_capability("TEMPLATE", 3, wrapped({"groups": [1, 2, 3, 4] + [0] * 12}))
    with pytest.raises(WriteBlocked, match="CAPABILITY_UNVERIFIED"):
        t.plan_capability(
            "TEMPLATE", 3, wrapped({"groups": [1, 2, 3, 4, 5] + [0] * 11})
        )
    with pytest.raises(WriteBlocked, match="OUTSIDE_ADVERTISED"):
        t.plan_capability("WEEK", 65, wrapped({"days": [[[1, 0, 86400]]] * 7}))


def test_live_validity_range_accepts_last_day_2037_but_rejects_year_9999(env):
    from app.service import AccessService
    from app.security import DomainError

    env[0].controller_mode = "HIKVISION_CONTROLLED"
    service = AccessService(None, None, env[0])
    _, expiry = service.valid_dates(
        "2026-01-01T00:00:00+01:00", "2038-01-01T00:00:00+01:00"
    )
    assert expiry.year == 2037  # UTC conversion of exclusive Lagos midnight
    with pytest.raises(DomainError, match="2037"):
        service.valid_dates("2026-01-01T00:00:00+01:00", "9999-03-12T00:00:00+01:00")


def test_web_preview_does_not_mix_with_open_legacy_scope(env, png, device):
    admin, _, source = pending(env, png)
    with env[1].session() as db:
        set_setting(
            db,
            "hikvision:write-scope:controller-1",
            {"enabled": True, "epoch": "old", "kind": "EXISTING_TEMPLATE"},
        )
        db.commit()
    response = write(
        admin,
        "/api/controller/management/card-preview",
        {"sourceOperationId": source, "template": 3, "reconcile": False, "reason": ""},
    )
    assert response.status_code == 409 and not device.writes
    assert "close the legacy" in response.json()["message"]


def test_privilege_waits_for_every_controller_confirmation(env):
    from app.models import Controller
    from app.common import get_setting

    env[0].controller_mode = "HIKVISION_CONTROLLED"
    with env[1].session() as db:
        db.add(
            Controller(
                id="controller-2",
                name="Isolated second controller",
                model="TEST",
                online=False,
            )
        )
        c = Card(
            number="00007777",
            kind="PERSONAL",
            disabled=False,
            privileged=False,
            ever_privileged=False,
        )
        db.add(c)
        db.flush()
        set_setting(db, "pending_designation", {"cardId": c.id})
        ops = []
        for controller in ["controller-1", "controller-2"]:
            op = Operation(
                controller_id=controller,
                target_type="PRIVILEGE",
                target_id=c.id,
                desired="UPDATED",
                version=1,
                status="PENDING",
                created_at=now(),
                data={},
            )
            db.add(op)
            ops.append(op)
        db.flush()
        management.finalize(
            db, env[0], ops[0], {"card": c.number, "enabled": True}, None
        )
        assert not c.privileged and get_setting(db, "pending_designation")
        management.finalize(
            db, env[0], ops[1], {"card": c.number, "enabled": True}, None
        )
        assert (
            c.privileged
            and c.ever_privileged
            and not get_setting(db, "pending_designation")
        )


def validity_pair():
    from app.hikvision_existing import DEVICE, expected_validity

    intent = {
        "card": "00009999",
        "start": [2026, 10, 15, 7, 0, 0],
        "expiry": [2026, 12, 30, 23, 59, 59],
    }
    data = bytearray(2708)
    data[300:352] = expected_validity(intent)
    expected = {
        "card": intent["card"],
        "valid": 1,
        "type": 1,
        "leader": 0,
        "doors": [1] + [0] * 255,
        "plans": [[3, 0, 0, 0]] + [[0] * 4 for _ in range(255)],
        "validityEnabled": 1,
        "beginFlag": 1,
        "endFlag": 1,
        "timeType": 0,
        "start": intent["start"],
        "expiry": intent["expiry"],
        "nativeHex": data.hex(),
    }
    data[301:303] = bytes(2)
    actual = {
        **copy.deepcopy(expected),
        "beginFlag": 0,
        "endFlag": 0,
        "nativeHex": data.hex(),
    }
    profile = {
        "device": DEVICE,
        "ownerTimedEvidenceVerified": True,
        "abi": {"v50Size": 2708, "v50ValidityOffset": 300, "validitySize": 52},
        "offsets": {"beginFlag": 1, "endFlag": 2},
    }
    return actual, expected, profile


def test_observed_validity_profile_preserves_raw_mismatch_and_global_strictness():
    from app.hikvision_writes import card_matches

    actual, expected, profile = validity_pair()
    assert not management.matching_card_readback(actual, expected)
    assert management.matching_card_readback(actual, expected, profile)
    assert actual["beginFlag"] == actual["endFlag"] == 0
    assert not card_matches(
        actual,
        {
            "card": actual["card"],
            "enabled": True,
            "start": actual["start"],
            "expiry": actual["expiry"],
        },
        3,
    )


@pytest.mark.parametrize(
    "field",
    [
        "card",
        "doors",
        "plans",
        "type",
        "leader",
        "start",
        "expiry",
        "validityEnabled",
        "timeType",
    ],
)
def test_profile_cannot_hide_other_permission_differences(field):
    actual, expected, profile = validity_pair()
    actual[field] = {
        "card": "different",
        "doors": [1, 1] + [0] * 254,
        "plans": [[1, 0, 0, 0]] + [[0] * 4 for _ in range(255)],
        "type": 6,
        "leader": 1,
        "start": [2026, 10, 14, 7, 0, 0],
        "expiry": [2026, 12, 31, 23, 59, 59],
        "validityEnabled": 0,
        "timeType": 1,
    }[field]
    assert not management.matching_card_readback(actual, expected, profile)


def test_profile_rejects_other_firmware_template_partial_flags_and_raw_changes():
    actual, expected, profile = validity_pair()
    assert not management.matching_card_readback(
        actual, expected, {**profile, "device": {**profile["device"], "firmware": 1}}
    )
    assert not management.matching_card_readback(
        actual, expected, {**profile, "ownerTimedEvidenceVerified": False}
    )
    assert not management.matching_card_readback(
        {**actual, "beginFlag": 1}, expected, profile
    )
    damaged = bytearray.fromhex(actual["nativeHex"])
    damaged[351] = 1
    assert not management.matching_card_readback(
        {**actual, "nativeHex": damaged.hex()}, expected, profile
    )
    expected["plans"][0][0] = 1
    actual["plans"][0][0] = 1
    assert not management.matching_card_readback(actual, expected, profile)


def test_readonly_reconciliation_confirms_actual_state_without_replay_and_keeps_failure(
    env, png, device
):
    admin, _, source = pending(env, png)
    p = preview(admin, source)
    device.fail = "PERMISSION_UNCERTAIN"
    assert apply(admin, p).json()["status"] == "REQUIRES_RECONCILIATION"
    assert len(device.writes) == 1
    device.fail = None
    response = write(
        admin,
        f"/api/controller/management/{p['id']}/reconcile",
        {"confirmed": True, "affectedCards": ["00009999"]},
    )
    assert response.status_code == 200, response.text
    assert (
        response.json()["status"] == "CONFIRMED" and response.json()["readbackVerified"]
    )
    assert len(device.writes) == 1
    with env[1].session() as db:
        op = db.get(Operation, p["id"])
        assert op.attempts == 1 and op.data["readbackOnly"]
        assert op.data["reconciliationHistory"][0]["status"] == "UNCERTAIN"
        assert (
            op.data["reconciliationHistory"][0]["data"]["error"]
            == "PERMISSION_UNCERTAIN"
        )


def test_readonly_reconciliation_never_repairs_different_permissions(env, png, device):
    admin, _, source = pending(env, png)
    p = preview(admin, source)
    device.corrupt = True
    apply(admin, p)
    response = write(
        admin,
        f"/api/controller/management/{p['id']}/reconcile",
        {"confirmed": True, "affectedCards": ["00009999"]},
    )
    assert response.status_code == 409 and len(device.writes) == 1


def test_missing_card_error_cannot_erase_partial_or_full_inventory():
    from types import SimpleNamespace
    import time

    t = object.__new__(management.Transport)
    t.deadline = time.monotonic() + 10
    t.session = SimpleNamespace(
        permission_request=lambda *a: ([], {"code": "SDK_ERROR", "error": 1903})
    )
    assert t.request("MANAGE", ["READ", "CARD", "00009999"]) == []
    with pytest.raises(WriteBlocked, match="1903"):
        t.request("MANAGE", ["READ", "CARD", "ALL"])
    t.session.permission_request = lambda *a: (
        [{"card": "00009999"}],
        {"code": "SDK_ERROR", "error": 1903},
    )
    with pytest.raises(WriteBlocked, match="1903"):
        t.request("MANAGE", ["READ", "CARD", "00009999"])


def test_linked_reconciliation_record_cannot_replay_an_old_pending_preview(
    env, png, device
):
    admin, _, source = pending(env, png)
    p = preview(admin, source)
    with env[1].session() as db:
        o = db.get(Operation, p["id"])
        o.data = {
            **o.data,
            "reconciledBy": "already-verified-operation",
            "managementState": "RECONCILED",
        }
        db.commit()
    assert apply(admin, p).json()["status"] == "RECONCILED"
    assert not device.writes
