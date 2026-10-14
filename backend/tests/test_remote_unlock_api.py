"""No real door command: prove permissions, duration gates and one-shot persistence."""

from io import BytesIO
import pytest
from sqlalchemy import select, func
from app import hikvision_unlock
from app.hikvision_existing import DEVICE
from app.hikvision_writes import WriteBlocked
from app.models import Audit, Operation, Idempotency
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
        == 409
    )
    retry = write(
        c, "/api/controller/unlock", {"confirmed": True}, key="lost-door-response"
    )
    assert retry.json()["status"] == "UNCERTAIN" and sent == ["UNLOCK5"]


def test_uncertain_outcome_blocks_new_unlock_attempt_and_audits_rejection(env, monkeypatch):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    sent = fake_bridge(monkeypatch)
    c = session_client(env, "ADMIN")
    first = write(c, "/api/controller/unlock", {"confirmed": True}, key="first-attempt")
    assert first.status_code == 200
    blocked = write(c, "/api/controller/unlock", {"confirmed": True}, key="second-attempt")
    assert blocked.status_code == 409
    assert "uncertain outcome" in blocked.json()["message"]
    assert sent == ["UNLOCK5"]
    with env[1].session() as db:
        rejected = db.scalar(
            select(Audit).where(Audit.action == "TIMED_UNLOCK_REJECTED").limit(1)
        )
        assert rejected and rejected.result == "DUPLICATE_BLOCKED"
