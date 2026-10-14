"""Offline diagnostic and fail-closed tests. These are not SDK/device acceptance."""

import json
import socket
import struct
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select, func

from app.config import ControllerSettings
from app.controllers import (
    ControllerReadUnavailable,
    HikvisionAdapter,
    SimulatedAdapter,
    adapter_for,
)
from app.hikvision_diagnostic import diagnose, inspect_sdk, main, tcp_probe
from app.events import backfill
from app.common import get_setting, set_setting
from app.jobs import tick
from app.models import AccessEvent, Assignment, Confirmation, Controller, Operation
from app.operations import process_operations
from conftest import approved, cmd, issued, session_client, write


def config(tmp_path, **kwargs):
    return ControllerSettings(
        _env_file=None, hikvision_sdk_dir=tmp_path / "absent", **kwargs
    )


@pytest.mark.parametrize(
    "failure,code",
    [
        (TimeoutError("sensitive native detail"), "TCP_TIMEOUT"),
        (PermissionError("private detail"), "TCP_PERMISSION_DENIED"),
        (ConnectionRefusedError("credential detail"), "TCP_CONNECTION_FAILED"),
    ],
)
def test_tcp_failure_is_bounded_and_sanitized(tmp_path, monkeypatch, failure, code):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.connect.side_effect = failure
    monkeypatch.setattr(socket, "socket", lambda *args: connection)
    report = tcp_probe(config(tmp_path))
    assert report == {"status": "FAILED", "code": code}
    connection.settimeout.assert_called_once_with(3)
    connection.__exit__.assert_called_once()


def test_tcp_success_is_not_login_and_never_exposes_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.hikvision_diagnostic.tcp_probe",
        lambda settings: {"status": "VERIFIED", "code": "TCP_CONNECTED"},
    )
    report = diagnose(
        config(
            tmp_path,
            hikvision_username="private-user",
            hikvision_password="private-password",
        )
    )
    assert report["checks"]["login"]["status"] == "BLOCKED"
    assert report["checks"]["login"]["attempts"] == 0
    assert report["checks"]["sdk"]["code"] == "SDK_PACKAGE_MISSING"
    assert report["checks"]["historicalEvents"]["status"] == "BLOCKED"
    assert not report["integrationComplete"]
    assert not report["deviceWritesAllowed"]
    assert "private-" not in json.dumps(report)


def test_invalid_host_does_not_resolve_dns(tmp_path, monkeypatch):
    socket_factory = MagicMock(side_effect=AssertionError("Must not connect"))
    monkeypatch.setattr(socket, "socket", socket_factory)
    assert (
        tcp_probe(config(tmp_path, hikvision_host="not-a-device-host"))["code"]
        == "HOST_MUST_BE_IPV4"
    )
    socket_factory.assert_not_called()


def test_invalid_configuration_cli_is_sanitized(monkeypatch, capsys):
    monkeypatch.setenv("HIKVISION_PORT", "private-invalid-value")
    assert main([]) == 2
    output = capsys.readouterr().out
    assert "INVALID_LOCAL_CONFIGURATION" in output
    assert "private-invalid-value" not in output


@pytest.mark.parametrize("limit", ["0", "101"])
def test_diagnostic_history_bound(limit):
    with pytest.raises(SystemExit):
        main(["--sample-limit", limit])


def test_sdk_architecture_checked_without_loading_library(tmp_path, monkeypatch):
    (tmp_path / "HCNetSDK.h").write_text("Synthetic test header, not vendor SDK")
    # Deliberately incompatible 32-bit ARM ELF header.
    elf = b"\x7fELF\x01\x01" + bytes(12) + struct.pack("<H", 40)
    (tmp_path / "libhcnetsdk.so").write_bytes(elf)
    monkeypatch.setattr("app.hikvision_diagnostic.platform.machine", lambda: "x86_64")
    assert inspect_sdk(tmp_path)["code"] == "SDK_ARCHITECTURE_MISMATCH"


def test_sdk_file_presence_never_claims_support(tmp_path, monkeypatch):
    (tmp_path / "HCNetSDK.h").write_text("Synthetic test header")
    (tmp_path / "libhcnetsdk.so").write_bytes(
        b"\x7fELF\x02\x01" + bytes(12) + struct.pack("<H", 62)
    )
    monkeypatch.setattr("app.hikvision_diagnostic.platform.machine", lambda: "x86_64")
    monkeypatch.setattr(
        "app.hikvision_diagnostic.subprocess.run",
        lambda *args, **kwargs: MagicMock(
            returncode=0, stdout=b"(NEEDED) Shared library: [libstdc++.so.6]"
        ),
    )
    report = inspect_sdk(tmp_path)
    assert report["status"] == "VERIFIED"
    assert report["runtimeDependencies"] == "UNKNOWN"
    assert report["declaredDependencies"] == ["libstdc++.so.6"]
    assert report["licensing"] == report["sdkVersion"] == "UNKNOWN"


def test_unknown_mode_does_not_fall_back(tmp_path):
    with pytest.raises(ValueError):
        config(tmp_path, controller_mode="REAL")
    settings = config(tmp_path, controller_mode="HIKVISION_CONTROLLED")
    adapter = adapter_for(settings)
    assert isinstance(adapter, HikvisionAdapter)
    assert adapter.execute(None, None, {}).status == "PENDING"
    with pytest.raises(ControllerReadUnavailable):
        adapter.collect_events(SimpleNamespace(id="controller-1"), None, None)


def test_blocked_read_keeps_checkpoints_and_never_inserts_mock_events(env):
    settings, database, _, _ = env
    settings.controller_mode = "HIKVISION_CONTROLLED"
    with database.session() as db:
        set_setting(db, "backfill:controller-1", {"at": "2026-01-01T00:00:00+00:00"})
        set_setting(
            db,
            "backfill:HIKVISION_CONTROLLED:controller-1",
            {"at": "2026-01-02T00:00:00+00:00"},
        )
        db.commit()
    # Repeated ticks/restarts cannot fall back or advance the failed read.
    tick(database, settings, collect=True)
    tick(database, settings, collect=True)
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(AccessEvent)) == 0
        assert get_setting(db, "backfill:controller-1")["at"].startswith("2026-01-01")
        assert get_setting(db, "backfill:HIKVISION_CONTROLLED:controller-1")[
            "at"
        ].startswith("2026-01-02")
        with pytest.raises(ControllerReadUnavailable):
            backfill(db, adapter=HikvisionAdapter())


def test_pending_outbox_untouched_even_if_mock_adapter_injected(env, png):
    _, _, admin, aid = approved(env, png)
    settings, database, _, _ = env
    settings.controller_mode = "HIKVISION_CONTROLLED"
    response = cmd(admin, {"type": "issue", "id": aid, "number": "00000042"})
    assert response["result"] == "PENDING"
    assert "confirmation pending" in response["message"]
    assert all(not c["confirmed"] for c in response["confirmations"])
    with database.session() as db:
        before = [
            (o.id, o.status, o.attempts, o.data, o.completed_at)
            for o in db.scalars(select(Operation))
        ]
        process_operations(db, settings, adapter=SimulatedAdapter())
        db.commit()
        after = [
            (o.id, o.status, o.attempts, o.data, o.completed_at)
            for o in db.scalars(select(Operation))
        ]
        assert before == after
        a = db.scalar(select(Assignment))
        assert a.state == "PENDING" and a.sync == "PENDING"
        assert all(c.confirmed_at is None for c in db.scalars(select(Confirmation)))


def test_prior_simulated_confirmations_not_real_evidence_or_idempotent_success(
    env, png
):
    user, _, admin, aid, _ = issued(env, png)
    # Persist a distinct idempotent response while still explicitly simulated.
    body = {"type": "retry-sync", "schedule": True}
    cmd(admin, body, key="mode-replay")
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    snap = admin.get("/api/workspace").json()
    assert snap["controllerReadStatus"] == "BLOCKED"
    assert not snap["controllerWritesAvailable"]
    assert not snap["simulationControlsEnabled"]
    assert all(
        not d["online"] and "lastCommunication" not in d for d in snap["devices"]
    )
    assert all(a["sync"] == "OUT_OF_SYNC" for a in snap["assignments"])
    assert all(
        not c["confirmed"] and c["simulated"]
        for a in snap["assignments"]
        for c in a["confirmations"]
    )
    assert user.get("/api/workspace").json()["assignments"][0]["sync"] == "OUT_OF_SYNC"
    replay = cmd(admin, body, key="mode-replay")
    assert replay["result"] == "PENDING"
    assert all(not c["confirmed"] for c in replay["confirmations"])
    # The original simulation history is retained, not rewritten as device data.
    with env[1].session() as db:
        assert db.scalar(select(Assignment)).sync == "SYNCHRONIZED"
        assert db.get(Controller, "controller-1").last_communication is not None


def test_read_only_rejects_unlock_scanning_and_simulation(env):
    admin = session_client(env, "ADMIN")
    sa = session_client(env, "SUPER_ADMIN")
    scan = write(admin, "/api/enrolment-sessions").json()
    detected = admin.get("/api/enrolment-sessions/" + scan["id"]).json()
    assert detected["state"] == "DETECTED"
    env[0].controller_mode = "HIKVISION_CONTROLLED"
    cmd(admin, {"type": "unlock", "doorId": "door-1", "confirmed": True}, 409)
    assert write(admin, "/api/enrolment-sessions").status_code == 409
    assert admin.get("/api/enrolment-sessions/" + scan["id"]).status_code == 409
    assert (
        write(admin, f"/api/enrolment-sessions/{scan['id']}/confirm").status_code == 409
    )
    assert (
        write(admin, f"/api/enrolment-sessions/{scan['id']}/cancel").status_code == 200
    )
    cmd(sa, {"type": "demo-event", "event": "UNKNOWN"}, 403)
    assert admin.get("/api/health").json()["controllerMode"] == "HIKVISION_CONTROLLED"
    with env[1].session() as db:
        assert db.scalar(select(func.count()).select_from(Operation)) == 0
