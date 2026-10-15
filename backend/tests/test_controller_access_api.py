"""Application controller authorization uses isolated data and a fake native transport."""

import hashlib
import json
import pytest
from sqlalchemy import select
from app import controller_access, controller_management, hikvision_existing
from app.common import get_setting, now
from app.hikvision_binary import prebuilt
from app.hikvision_writes import WriteBlocked
from app.models import Application, Assignment, Operation
from conftest import approved, cmd, session_client, write


@pytest.mark.parametrize(
    "role,allowed",
    [
        ("SUPER_ADMIN", True),
        ("ADMIN", True),
        ("MANAGER", False),
        ("MANAGEMENT", False),
        ("USER", False),
    ],
)
def test_real_controller_access_role_boundary(env, role, allowed):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    response = session_client(env, role).get("/api/controller/access")
    assert response.status_code == (200 if allowed else 403)


def setup(env, png, monkeypatch):
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    _, _, admin, application_id = approved(env, png)
    result = cmd(admin, {"type": "issue", "id": application_id, "number": "00009999"})
    with env[1].session() as db:
        a = db.get(Application, application_id)
        operation = db.scalar(
            select(Operation).where(Operation.target_id == a.assignment_id)
        )
        operation_id, assignment_id = operation.id, a.assignment_id
        unrelated = Operation(
            controller_id="controller-1",
            target_type="SCHEDULE",
            target_id="normal",
            desired="ENABLED",
            version=100,
            status="PENDING",
            attempts=0,
            created_at=now(),
            data={},
        )
        db.add(unrelated)
        db.flush()
        unrelated_id = unrelated.id
        db.commit()
    monkeypatch.setattr(
        controller_access,
        "observe",
        lambda *args: ({}, {"cards": [], "resources": {}}, []),
    )
    monkeypatch.setattr(controller_access, "validity_profile", lambda *args: {})
    body = {
        "cards": [
            {"number": "00009999", "kind": "PERSONAL", "personIds": [env[2]["USER"][1]]}
        ],
        "confirmed": True,
    }
    return (
        admin,
        session_client(env, "SUPER_ADMIN"),
        body,
        operation_id,
        assignment_id,
        unrelated_id,
    )


def test_only_super_admin_authorizes_and_confirmation_is_required(
    env, png, monkeypatch
):
    admin, super_admin, body, *_ = setup(env, png, monkeypatch)
    assert write(admin, "/api/controller/access/authorize", body).status_code == 403
    assert (
        write(
            super_admin,
            "/api/controller/access/authorize",
            {**body, "confirmed": False},
        ).status_code
        == 400
    )
    body["cards"][0]["number"] = "1051305927"
    assert (
        write(super_admin, "/api/controller/access/authorize", body).status_code == 409
    )
    with env[1].session() as db:
        assert not get_setting(db, "hikvision:write-scope:controller-1").get("enabled")


def test_application_executes_only_selected_current_card_with_readback(
    env, png, monkeypatch
):
    admin, super_admin, body, op_id, assignment_id, unrelated_id = setup(
        env, png, monkeypatch
    )
    assert (
        write(super_admin, "/api/controller/access/authorize", body).status_code == 200
    )
    records, writes = {}, []

    class Transport:
        def __init__(self, *args):
            self.last = None

        def start(self):
            pass

        def verify_ownership(self):
            pass

        def read_card(self, number):
            self.last = records.get(number)
            return self.last

        def write_card(self, intent, plan):
            writes.append((intent["card"], plan))
            records[intent["card"]] = {
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
            }

        def evidence(self):
            from app.hikvision_writes import digest

            return {
                "actualRecord": self.last,
                "protectedReadbackVerified": True,
                "existingTemplateBaselineHash": digest({}),
            }

        def close(self):
            pass

    monkeypatch.setattr(hikvision_existing, "ExistingTemplateTransport", Transport)
    monkeypatch.setattr(controller_access, "ExistingTemplateTransport", Transport)
    assert (
        write(
            admin,
            f"/api/controller/operations/{unrelated_id}/execute",
            {"confirmed": True},
        ).status_code
        == 409
    )
    assert (
        write(
            admin, f"/api/controller/operations/{op_id}/execute", {"confirmed": False}
        ).status_code
        == 400
    )
    response = write(
        admin, f"/api/controller/operations/{op_id}/execute", {"confirmed": True}
    )
    assert response.status_code == 200, response.text
    assert (
        response.json()["status"] == "CONFIRMED" and response.json()["readbackVerified"]
    )
    assert (
        write(
            admin, f"/api/controller/operations/{op_id}/execute", {"confirmed": True}
        ).json()["status"]
        == "CONFIRMED"
    )
    assert writes == [("00009999", 3)]
    class ReadTransport:
        def __init__(self, *args): pass
        def start(self): pass
        def card(self, number): return records.get(number)
        def close(self): pass
    monkeypatch.setattr(controller_management, "Transport", ReadTransport)
    assert admin.get("/api/controller/cards/00009999").json()["record"]["doors"] == [1] + [0] * 255
    with env[1].session() as db:
        assert db.get(Assignment, assignment_id).sync == "SYNCHRONIZED"
        assert db.get(Operation, unrelated_id).status == "PENDING"
        assert db.get(Operation, unrelated_id).attempts == 0
        assert "writeEpoch" not in db.get(Operation, unrelated_id).data
    assert write(admin, "/api/controller/access/close", {}).status_code == 403
    assert (
        write(super_admin, "/api/controller/access/close", {}).json()["enabled"]
        is False
    )
    assert (
        records["00009999"]["doors"][0] == 1
    )  # Locking execution does not revoke paid access.


def test_closed_authorization_cannot_review_or_execute(env, png, monkeypatch):
    admin, _, _, op_id, _, _ = setup(env, png, monkeypatch)
    assert (
        write(
            admin, f"/api/controller/operations/{op_id}/execute", {"confirmed": True}
        ).status_code
        == 409
    )
    with env[1].session() as db:
        op = db.get(Operation, op_id)
        assert (
            op.status == "PENDING" and op.attempts == 0 and "writeEpoch" not in op.data
        )


def test_prebuilt_wrapper_rejects_tampering(env, tmp_path, monkeypatch):
    settings = env[0]
    settings.hikvision_sdk_dir = tmp_path / "sdk"
    settings.hikvision_bridge_path = tmp_path / "bin/ift-hikvision"
    native = tmp_path / "backend/native"
    native.mkdir(parents=True)
    for p in [
        settings.hikvision_sdk_dir / "incEn/HCNetSDK.h",
        settings.hikvision_sdk_dir / "lib/libhcnetsdk.so",
        native / "wrapper.cpp",
    ]:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fixture")
    import app.hikvision_binary as binaries

    monkeypatch.setattr(binaries, "ROOT", tmp_path)
    binary = settings.hikvision_bridge_path.parent / "wrapper.bin"
    binary.parent.mkdir()
    binary.write_bytes(b"compiled-fixture")
    h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    evidence = {
        "headerSha256": h(settings.hikvision_sdk_dir / "incEn/HCNetSDK.h"),
        "librarySha256": h(settings.hikvision_sdk_dir / "lib/libhcnetsdk.so"),
        "sources": {"wrapper.cpp": h(native / "wrapper.cpp")},
        "binarySha256": h(binary),
    }
    binary.with_suffix(".json").write_text(json.dumps(evidence))
    assert prebuilt(settings, "wrapper.cpp") == binary
    binary.write_bytes(b"tampered")
    with pytest.raises(WriteBlocked, match="NATIVE_BUILD_EVIDENCE_MISMATCH"):
        prebuilt(settings, "wrapper.cpp")
