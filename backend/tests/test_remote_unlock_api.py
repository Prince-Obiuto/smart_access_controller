"""No real door command: prove permissions, duration gates and one-shot persistence."""

from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
import pytest
from sqlalchemy import select, func
from app import hikvision_unlock
from app.hikvision_existing import DEVICE
from app.hikvision_writes import WriteBlocked
from app.models import Audit, Operation, Idempotency, Controller
from app.common import now
from conftest import session_client, write


def fake_bridge(monkeypatch, duration=5, configure_verified=True, failure=False):
    sent = []

    class Bridge:
        def __init__(self, settings):
            self.rows = [{"kind": "doorFields", "door": 1, "openDuration": duration}]
            self.process = type("Process", (), {"stdin": BytesIO()})()

        def start(self):
            return {"device": DEVICE}

        def read(self):
            command = self.process.stdin.getvalue().decode().strip()
            sent.append(command)
            if failure:
                raise WriteBlocked("NATIVE_TIMEOUT")
            config = command == "DURATION5"
            self.rows.append(
                {
                    "kind": "durationResult" if config else "unlockResult",
                    "sent": True,
                    "ok": True,
                    "readbackVerified": configure_verified if config else False,
                    "beforeHex": "private-door-bytes",
                }
            )

        def close(self):
            pass

    monkeypatch.setattr(hikvision_unlock, "UnlockBridge", Bridge)
    monkeypatch.setattr(hikvision_unlock, "save", lambda *args: "private-evidence")
    return sent


@pytest.mark.parametrize("role", ["MANAGER", "MANAGEMENT", "USER"])
def test_unprivileged_roles_cannot_read_or_unlock(env, monkeypatch, role):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    sent = fake_bridge(monkeypatch)
    c = session_client(env, role)
    assert c.get("/api/controller/unlock").status_code == 403
    assert write(c, "/api/controller/unlock", {"confirmed": True}).status_code == 403
    assert sent == []


def test_duration_mismatch_blocks_before_intent_or_dispatch(env, monkeypatch):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    sent = fake_bridge(monkeypatch, duration=2)
    c = session_client(env, "ADMIN")
    status = c.get("/api/controller/unlock").json()
    assert (
        status["configuredSeconds"] == 2
        and not status["ready"]
        and status["controllerStatus"] == "CONNECTED"
    )
    assert write(c, "/api/controller/unlock", {"confirmed": True}).status_code == 409
    assert sent == []
    with env[1].session() as db:
        assert db.scalar(select(func.count()).select_from(Operation)) == 0


def test_unlock_acknowledgement_is_not_physical_success_and_replay_never_sends(
    env, monkeypatch
):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    sent = fake_bridge(monkeypatch)
    c = session_client(env, "ADMIN")
    assert write(c, "/api/controller/unlock", {"confirmed": False}).status_code == 400
    response = write(
        c, "/api/controller/unlock", {"confirmed": True}, key="one-door-command"
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert (
        result["status"] == "UNCERTAIN"
        and result["physicalState"] == "UNKNOWN"
        and not result["readbackVerified"]
    )
    assert "private-door-bytes" not in response.text
    assert (
        write(
            c, "/api/controller/unlock", {"confirmed": True}, key="one-door-command"
        ).json()
        == result
    )
    assert sent == ["UNLOCK5"]
    with env[1].session() as db:
        op = db.get(Operation, result["operationId"])
        assert (
            op.status == "UNCERTAIN"
            and op.attempts == 1
            and "writeEpoch" not in op.data
        )
        assert "private-door-bytes" not in str(op.data)
        assert (
            db.get(Idempotency, (env[2]["ADMIN"][0], "one-door-command")).result
            == result
        )


@pytest.mark.parametrize("verified", [True, False])
def test_duration_configuration_requires_super_admin_and_exact_readback(
    env, monkeypatch, verified
):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    sent = fake_bridge(monkeypatch, duration=2, configure_verified=verified)
    assert (
        write(
            session_client(env, "ADMIN"),
            "/api/controller/unlock-duration",
            {"confirmed": True},
        ).status_code
        == 403
    )
    c = session_client(env, "SUPER_ADMIN")
    result = write(
        c,
        "/api/controller/unlock-duration",
        {"confirmed": True},
        key="one-duration-change",
    ).json()
    assert result["status"] == ("CONFIRMED" if verified else "UNCERTAIN")
    assert result["readbackVerified"] is verified
    assert (
        write(
            c,
            "/api/controller/unlock-duration",
            {"confirmed": True},
            key="one-duration-change",
        ).json()
        == result
    )
    assert sent == ["DURATION5"]


def test_timeout_after_durable_intent_is_not_replayed(env, monkeypatch):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    sent = fake_bridge(monkeypatch, failure=True)
    c = session_client(env, "ADMIN")
    assert (
        write(
            c, "/api/controller/unlock", {"confirmed": True}, key="lost-door-response"
        ).status_code
        == 200
    )
    retry = write(
        c, "/api/controller/unlock", {"confirmed": True}, key="lost-door-response"
    )
    assert retry.json()["status"] == "UNCERTAIN" and sent == ["UNLOCK5"]


def test_uncertain_outcome_blocks_new_unlock_attempt_and_audits_rejection(
    env, monkeypatch
):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    sent = fake_bridge(monkeypatch)
    c = session_client(env, "ADMIN")
    first = write(c, "/api/controller/unlock", {"confirmed": True}, key="first-attempt")
    assert first.status_code == 200
    blocked = write(
        c, "/api/controller/unlock", {"confirmed": True}, key="second-attempt"
    )
    assert blocked.status_code == 409
    assert "uncertain outcome" in blocked.json()["message"]
    assert sent == ["UNLOCK5"]
    with env[1].session() as db:
        rejected = db.scalar(
            select(Audit).where(Audit.action == "TIMED_UNLOCK_REJECTED").limit(1)
        )
        assert rejected and rejected.result == "DUPLICATE_BLOCKED"


def ledger(
    env,
    desired="UNLOCK5",
    status="UNCERTAIN",
    controller="controller-1",
    door="door-1",
    data=None,
):
    with env[1].session() as db:
        if not db.get(Controller, controller):
            db.add(Controller(id=controller, name="Other controller"))
            db.flush()
        version = (
            db.scalar(
                select(func.max(Operation.version)).where(
                    Operation.controller_id == controller,
                    Operation.target_type == "DOOR",
                    Operation.target_id == door,
                )
            )
            or 0
        ) + 1
        op = Operation(
            controller_id=controller,
            target_type="DOOR",
            target_id=door,
            desired=desired,
            status=status,
            version=version,
            created_at=now() - timedelta(minutes=10 - version),
            data=data or {"message": "Original evidence retained"},
        )
        db.add(op)
        db.flush()
        identifier = op.id
        db.commit()
        return identifier


@pytest.mark.parametrize(
    "newer",
    [("DURATION5", "CONFIRMED"), ("UNLOCK5", "FAILED"), ("UNLOCK5", "CONFIRMED")],
)
def test_older_uncertain_unlock_cannot_be_hidden_by_newer_record(
    env, monkeypatch, newer
):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    sent = fake_bridge(monkeypatch)
    old = ledger(env, desired="UNLOCK")
    ledger(env, desired=newer[0], status=newer[1])
    c = session_client(env, "ADMIN")
    status = c.get("/api/controller/unlock").json()
    assert not status["ready"] and [
        o["operationId"] for o in status["unresolvedOperations"]
    ] == [old]
    assert write(c, "/api/controller/unlock", {"confirmed": True}).status_code == 409
    assert sent == []


def test_multiple_unresolved_records_need_individual_operation_confirmation(
    env, monkeypatch
):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    sent = fake_bridge(monkeypatch)
    first = ledger(
        env,
        desired="UNLOCK",
        data={
            "sdkAcknowledged": False,
            "message": "Original timeout",
            "nativeResult": [],
        },
    )
    second = ledger(env)
    pending = ledger(env, status="PENDING")
    c = session_client(env, "ADMIN")
    assert len(c.get("/api/controller/unlock").json()["unresolvedOperations"]) == 3
    body = {"operationId": first, "confirmed": True}
    result = write(c, "/api/controller/unlock/confirm-physical", body)
    assert result.status_code == 200 and result.json()["operationId"] == first
    assert (
        result.json()["sdkStatus"] == "NOT_ACKNOWLEDGED"
        and result.json()["physicalState"] == "RELEASED_AND_RELOCKED_OBSERVED"
    )
    assert write(c, "/api/controller/unlock/confirm-physical", body).status_code == 409
    assert (
        write(
            c,
            "/api/controller/unlock/confirm-physical",
            {"operationId": pending, "confirmed": True},
        ).status_code
        == 409
    )
    assert write(c, "/api/controller/unlock", {"confirmed": True}).status_code == 409
    with env[1].session() as db:
        assert (
            db.get(Operation, second).status == "UNCERTAIN"
            and db.get(Operation, pending).status == "PENDING"
        )
        original = db.get(Operation, first).data
        assert (
            original["message"] == "Original timeout"
            and original["sdkAcknowledged"] is False
            and original["nativeResult"] == []
        )
    assert sent == []


@pytest.mark.parametrize(
    "wrong", ["missing", "controller", "door", "command", "completed"]
)
def test_confirmation_cannot_target_wrong_or_stale_operation(env, monkeypatch, wrong):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    fake_bridge(monkeypatch)
    current = ledger(env)
    identifier = {
        "missing": lambda: "absent-id",
        "controller": lambda: ledger(env, controller="other-controller"),
        "door": lambda: ledger(env, door="door-2"),
        "command": lambda: ledger(env, desired="DURATION5"),
        "completed": lambda: ledger(env, status="CONFIRMED"),
    }[wrong]()
    c = session_client(env, "ADMIN")
    assert (
        write(
            c,
            "/api/controller/unlock/confirm-physical",
            {"operationId": identifier, "confirmed": True},
        ).status_code
        == 409
    )
    with env[1].session() as db:
        assert db.get(Operation, current).status == "UNCERTAIN"
    assert (
        write(
            c, "/api/controller/unlock/confirm-physical", {"confirmed": True}
        ).status_code
        == 422
    )


def test_reconciliation_preserves_unknown_release_and_never_replays(env, monkeypatch):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    sent = fake_bridge(monkeypatch)
    identifier = ledger(env, desired="UNLOCK", status="PENDING")
    c = session_client(env, "ADMIN")
    result = write(
        c,
        "/api/controller/unlock/reconcile",
        {
            "operationId": identifier,
            "confirmed": True,
            "reason": "Door is secured now; prior release unobserved.",
        },
    )
    assert result.status_code == 200, result.text
    assert (
        result.json()["status"] == "RECONCILED"
        and result.json()["physicalState"] == "SECURED_OBSERVED_RELEASE_UNKNOWN"
    )
    with env[1].session() as db:
        op = db.get(Operation, identifier)
        assert (
            not op.data["physicalObservationConfirmed"]
            and op.data["message"] == "Original evidence retained"
        )
    assert (
        sent == []
        and c.get("/api/controller/unlock").json()["unresolvedOperations"] == []
    )


def test_offline_status_still_exposes_unresolved_reconciliation(env, monkeypatch):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    fake_bridge(monkeypatch)
    identifier = ledger(env)

    def offline(self):
        raise WriteBlocked("TCP_CONNECT_TIMEOUT")

    monkeypatch.setattr(hikvision_unlock.UnlockBridge, "start", offline)
    status = session_client(env, "ADMIN").get("/api/controller/unlock").json()
    assert not status["ready"] and status["controllerStatus"] == "FAILED"
    assert status["unresolvedOperations"][0]["operationId"] == identifier


def test_missing_acknowledgement_and_timeout_have_explicit_uncertainty(
    env, monkeypatch
):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    fake_bridge(monkeypatch)
    monkeypatch.setattr(hikvision_unlock.UnlockBridge, "read", lambda self: None)
    result = write(
        session_client(env, "ADMIN"), "/api/controller/unlock", {"confirmed": True}
    ).json()
    assert (
        result["status"] == "UNCERTAIN"
        and result["sdkAcknowledged"] is None
        and result["sdkStatus"] == "UNKNOWN"
    )
    assert (
        result["transportStatus"] == "UNCERTAIN"
        and "SDK acknowledged the command" not in result["message"]
    )


def test_concurrent_submissions_send_only_one_command(env, monkeypatch):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    sent = fake_bridge(monkeypatch)
    entered, release = Event(), Event()
    original = hikvision_unlock.UnlockBridge.read

    def slow(self):
        entered.set()
        assert release.wait(5)
        original(self)

    monkeypatch.setattr(hikvision_unlock.UnlockBridge, "read", slow)
    first_client, second_client = (
        session_client(env, "ADMIN"),
        session_client(env, "SUPER_ADMIN"),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(
            write,
            first_client,
            "/api/controller/unlock",
            {"confirmed": True},
            key="concurrent-first",
        )
        try:
            assert entered.wait(5)
            blocked = write(
                second_client,
                "/api/controller/unlock",
                {"confirmed": True},
                key="concurrent-second",
            )
            assert blocked.status_code == 409
            same = write(
                first_client,
                "/api/controller/unlock",
                {"confirmed": True},
                key="concurrent-first",
            )
            assert same.status_code == 409
        finally:
            release.set()
        assert future.result(timeout=5).status_code == 200
    assert sent == ["UNLOCK5"]
    with env[1].session() as db:
        assert db.scalar(select(func.count()).select_from(Operation)) == 1
