"""Paid entitlement preservation is distinct from temporary visitor cleanup."""

import hashlib
import subprocess
from datetime import timedelta

import pytest
from app.common import iso, now
from app.hikvision_card_diagnostic import Bridge
from app.hikvision_existing import (
    DEVICE,
    ExistingTemplateTransport,
    expected_validity,
    verify_retained_paid,
)
from app.hikvision_unlock import validate_duration
from app.hikvision_writes import WriteBlocked, current_intent
from app.models import Card, Operation
from conftest import issued
from sqlalchemy import select


@pytest.mark.parametrize("duration", [0, 2, 255, None])
def test_timed_unlock_rejects_nonfive_duration(duration):
    with pytest.raises(WriteBlocked):
        validate_duration([{"kind": "doorFields", "door": 1, "openDuration": duration}])


def test_timed_unlock_requires_exact_door_and_single_result():
    validate_duration([{"kind": "doorFields", "door": 1, "openDuration": 5}])
    with pytest.raises(WriteBlocked):
        validate_duration([{"kind": "doorFields", "door": 2, "openDuration": 5}])


def test_close_retains_only_verified_normal_paid_card(env, png):
    _, _, _, _, assignment = issued(env, png, number="00009999")
    with env[1].session() as db:
        op = db.scalar(
            select(Operation).where(
                Operation.target_id == assignment["id"], Operation.status == "CONFIRMED"
            )
        )
        op.data = {
            **op.data,
            "readbackVerified": True,
            "writeEpoch": "paid-test",
            "simulated": False,
        }
        scope = {
            "kind": "EXISTING_TEMPLATE",
            "existingTemplate": 3,
            "enabled": True,
            "epoch": "paid-test",
            "actorId": env[2]["SUPER_ADMIN"][0],
            "expiresAt": iso(now() + timedelta(hours=1)),
            "reviewedOperations": [op.id],
            "cards": {
                "00009999": {"kind": "PERSONAL", "personIds": [env[2]["USER"][1]]}
            },
            "baseline": {"resources": {}},
            "validityProfile": {
                "ownerTimedEvidenceVerified": True,
                "device": DEVICE,
                "abi": {
                    "validitySize": 52,
                    "v50Size": 2708,
                    "v50ValidityOffset": 300,
                    "timeExSize": 8,
                },
            },
        }
        intent = current_intent(db, op, scope, now())
        record = {
            "card": "00009999",
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
        value = bytearray(expected_validity(intent))
        raw = {
            "card": "00009999",
            "command": 2178,
            "direction": "READ",
            "nativeSize": 2708,
            "declaredSize": 2708,
            "callbackLength": 2708,
            "validityOffset": 300,
            "validitySize": 52,
            "hex": value.hex(),
        }
        assert (
            verify_retained_paid(db, "00009999", scope, record, raw)["assignmentId"]
            == assignment["id"]
        )
        with pytest.raises(WriteBlocked):
            verify_retained_paid(
                db, "00009999", scope, {**record, "beginFlag": 0, "endFlag": 0}, raw
            )
        with pytest.raises(WriteBlocked):
            verify_retained_paid(db, "00009999", scope, {**record, "type": 6}, raw)
        card = db.scalar(select(Card).where(Card.number == "00009999"))
        card.revoked = True
        with pytest.raises(WriteBlocked):
            verify_retained_paid(db, "00009999", scope, record, raw)
        with pytest.raises(WriteBlocked):
            verify_retained_paid(db, "1051305927", scope, record, raw)
        with pytest.raises(WriteBlocked):
            verify_retained_paid(db, "1052815031", scope, record, raw)


def test_compile_timeout_is_sanitized_and_partial_binary_removed(env, monkeypatch):
    bridge = Bridge(env[0])

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("g++", 30)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(WriteBlocked, match="NATIVE_BRIDGE_BUILD_TIMEOUT"):
        bridge.start()
    path = bridge.binary
    bridge.close()
    assert not path.exists()


def test_scoped_transport_reuses_one_compile_and_removes_it(env, tmp_path, monkeypatch):
    settings = env[0]
    settings.hikvision_sdk_dir = tmp_path
    (tmp_path / "incEn").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "incEn/HCNetSDK.h").write_bytes(b"fixture-header")
    (tmp_path / "lib/libhcnetsdk.so").write_bytes(b"fixture-library")
    baseline = {"device": DEVICE, "resources": {}, "protected": {}, "cards": []}
    compiled = []

    def start(bridge):
        if bridge.reuse_binary is None:
            bridge.binary = tmp_path / "fixture-binary"
            bridge.binary.write_bytes(b"fixture")
            compiled.append(bridge.binary)
        else:
            bridge.binary = bridge.reuse_binary
        return baseline

    monkeypatch.setattr(Bridge, "start", start)
    scope = {
        "kind": "EXISTING_TEMPLATE",
        "existingTemplate": 3,
        "cards": {"00009999": {}},
        "baseline": baseline,
        "validityProfile": {
            "headerSha256": hashlib.sha256(b"fixture-header").hexdigest(),
            "librarySha256": hashlib.sha256(b"fixture-library").hexdigest(),
        },
    }
    transport = ExistingTemplateTransport(settings, scope)
    try:
        for _ in range(3):
            b, _ = transport.preflight()
            b.close()
        assert len(compiled) == 1 and compiled[0].exists()
    finally:
        transport.close()
    assert not compiled[0].exists()
