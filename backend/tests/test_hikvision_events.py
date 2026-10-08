"""Synthetic transport fault/event fixtures, never hardware acceptance."""

from collections import deque
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import io
import json
import queue
import sys
import time

import pytest
from sqlalchemy import func, select

from app.common import get_setting, now, set_setting
from app.controllers import ControllerReadUnavailable
from app.events import store_real_event
from app.hikvision import NativeSession, parse_event
from app.jobs import collect_real, close_real
from app.hikvision_mapping import verify_mapping
from app.models import AccessEvent, Assignment
from test_hikvision import config
from conftest import issued, session_client


def frame(**changes):
    return {
        "major": 5,
        "minor": 1,
        "serial": 42,
        "door": 1,
        "reader": 1,
        "time": [2026, 1, 2, 12, 0, 0],
        "timeType": 0,
        "cardHex": (b"00001234" + bytes(24)).hex(),
        "outcome": "GRANTED",
        **changes,
    }


def event(**changes):
    return parse_event(frame(**changes), "synthetic-device", clock_verified=True)


def test_live_history_utc_local_identity_and_counter_reset():
    local = event()
    utc = event(time=[2026, 1, 2, 11, 0, 0], timeType=1)
    assert local["source_key"] == utc["source_key"]
    assert local["card"] == "00001234"
    assert local["at"].isoformat() == "2026-01-02T11:00:00+00:00"
    assert event(time=[2026, 1, 3, 12, 0, 0])["source_key"] != local["source_key"]


def test_unknown_fields_and_invalid_time_are_retained(env):
    e = event(reader=900, door=0, time=[0] * 6, outcome="UNKNOWN")
    with env[1].session() as db:
        id = store_real_event(db, e, "controller-1")
        assert store_real_event(db, e, "controller-1") is None
        db.commit()
        stored = db.get(AccessEvent, id)
        assert stored.at is None and stored.received_at
        assert stored.reader_id is None and stored.door is None
        assert stored.direction == stored.result == "UNKNOWN"
        assert not stored.simulated
        assert stored.raw["deviceReader"] == "900"
        assert "INVALID_OR_AMBIGUOUS_TIME" in stored.raw["flags"]


def test_owner_confirmed_reader_map_corrects_history_without_assuming_numbers(env):
    with env[1].session() as db:
        exit_id = store_real_event(db, event(reader=1), "controller-1")
        entry_id = store_real_event(db, event(reader=2, serial=43), "controller-1")
        assert db.get(AccessEvent, entry_id).direction == "UNKNOWN"
        mapping = verify_mapping(db, env[0], entry_id, exit_id)
        assert mapping["readers"] == {"2": "ENTRY", "1": "EXIT"}
        assert db.get(AccessEvent, entry_id).direction == "ENTRY"
        assert db.get(AccessEvent, exit_id).direction == "EXIT"
        assert db.get(AccessEvent, entry_id).raw["readerMappingVerified"]
        later = store_real_event(db, event(reader=1, serial=44), "controller-1")
        assert db.get(AccessEvent, later).direction == "EXIT"
        wrong = store_real_event(
            db,
            event(reader=2, serial=45, cardHex=(b"different" + bytes(23)).hex()),
            "controller-1",
        )
        with pytest.raises(ValueError):
            verify_mapping(db, env[0], wrong, exit_id)
        db.rollback()


def test_real_outcome_and_historical_owner_do_not_use_current_permissions(env, png):
    _, _, _, _, a = issued(env, png)
    at = now().replace(microsecond=0) + timedelta(seconds=1)
    e = event(time=list(at.timetuple()[:6]), timeType=1)
    with env[1].session() as db:
        old = db.get(Assignment, a["id"])
        old.ended_at = at + timedelta(seconds=1)
        old.state = "REVOKED"
        # Reuse after the historical event: matching the latest owner is wrong.
        newer = Assignment(
            person_id=env[2]["ADMIN"][1],
            card_id=old.card_id,
            start=at + timedelta(seconds=2),
            expiry=old.expiry,
            created_at=at + timedelta(seconds=2),
            state="ACTIVE",
            sync="PENDING",
        )
        db.add(newer)
        db.flush()
        stored = db.get(AccessEvent, store_real_event(db, e, "controller-1"))
        assert stored.assignment_id == old.id
        assert stored.person_id == old.person_id and stored.result == "GRANTED"
        uncertain = parse_event(
            frame(time=list(at.timetuple()[:6]), serial=43), "synthetic-device"
        )
        uncertain["at"] = at
        assert (
            db.get(
                AccessEvent, store_real_event(db, uncertain, "controller-1")
            ).assignment_id
            is None
        )
        db.commit()


class FakeSession:
    def __init__(self, rows=(), complete=True, live=(), failure=None):
        self.live = deque(live)
        self.rows, self.complete, self.failure = list(rows), complete, failure
        self.metadata = {"state": "CONNECTED", "code": "OK"}
        self.reconnected = True
        self.backoff = 5
        self.calls = []

    def poll_live(self):
        if self.failure:
            raise ControllerReadUnavailable(self.failure)

    def read_history(self, start, end, limit):
        self.calls.append((start, end, limit))
        return self.rows, self.complete

    def acknowledge_live(self, count):
        for _ in range(count):
            self.live.popleft()

    def close(self):
        pass


@pytest.mark.parametrize("complete", [False, True])
def test_history_checkpoint_dedup_and_reconnect(env, complete):
    settings, database, _, _ = env
    settings.controller_mode = "HIKVISION_READ_ONLY"
    at = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    checkpoint = at - timedelta(minutes=5)
    key = "backfill:HIKVISION_READ_ONLY:controller-1"
    with database.session() as db:
        set_setting(db, key, {"at": checkpoint.isoformat()})
        db.commit()
    e = event()
    session = FakeSession([e], complete, [e])
    adapter = SimpleNamespace(session=session)
    collect_real(database, settings, adapter, at=at)
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(AccessEvent)) == 1
        assert datetime.fromisoformat(get_setting(db, key)["at"]) == (
            at if complete else checkpoint
        )
    assert not session.live
    assert session.calls[0][0] == checkpoint - timedelta(minutes=2)
    # Reconnect requests history even without the periodic due flag.
    session.reconnected = True
    collect_real(database, settings, adapter, at=at)
    assert len(session.calls) == 2
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(AccessEvent)) == 1


def test_failed_commit_preserves_unacknowledged_live_and_checkpoint(env, monkeypatch):
    settings, database, _, _ = env
    settings.controller_mode = "HIKVISION_READ_ONLY"
    session = FakeSession([event()], live=[event()])
    # Fail before committing the events/watermark transaction.
    original = database.session
    calls = 0

    def transaction():
        nonlocal calls
        calls += 1
        db = original()
        if calls == 2:
            monkeypatch.setattr(
                db,
                "commit",
                lambda: (_ for _ in ()).throw(
                    RuntimeError("synthetic DB interruption")
                ),
            )
        return db

    monkeypatch.setattr(database, "session", transaction)
    with pytest.raises(RuntimeError):
        collect_real(database, settings, SimpleNamespace(session=session), at=now())
    assert len(session.live) == 1
    with original() as db:
        assert not get_setting(db, "backfill:HIKVISION_READ_ONLY:controller-1")
        assert db.scalar(select(func.count()).select_from(AccessEvent)) == 0


def test_gap_preserves_checkpoint_and_queued_events_for_recovery(env):
    settings, database, _, _ = env
    settings.controller_mode = "HIKVISION_READ_ONLY"
    session = FakeSession(live=[event()], failure="CALLBACK_GAP")
    with database.session() as db:
        set_setting(
            db,
            "backfill:HIKVISION_READ_ONLY:controller-1",
            {"at": "2026-01-01T00:00:00+00:00"},
        )
        db.commit()
    collect_real(database, settings, SimpleNamespace(session=session))
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(AccessEvent)) == 1
        assert get_setting(db, "backfill:HIKVISION_READ_ONLY:controller-1")[
            "at"
        ].startswith("2026-01-01")
        assert get_setting(db, "hikvision:controller-1")["code"] == "CALLBACK_GAP"
    assert not session.live and session.next_attempt > time.monotonic()


def test_cleanup_commits_last_unacknowledged_callbacks(env):
    session = FakeSession(live=[event()])

    def final_callback():
        session.live.append(event(serial=43))
        session.metadata.update(state="BLOCKED", code="SESSION_CLOSED")

    session.close = final_callback
    close_real(env[1], env[0], SimpleNamespace(session=session))
    assert not session.live
    with env[1].session() as db:
        assert db.scalar(select(func.count()).select_from(AccessEvent)) == 2
        assert not get_setting(db, "backfill:HIKVISION_READ_ONLY:controller-1")
        assert get_setting(db, "hikvision:controller-1")["code"] == "SESSION_CLOSED"


def test_expired_sdk_communication_never_projects_connected_or_syncs(env):
    env[0].controller_mode = "HIKVISION_READ_ONLY"
    with env[1].session() as db:
        set_setting(
            db,
            "hikvision:controller-1",
            {
                "state": "CONNECTED",
                "code": "OK",
                "lastCommunication": (now() - timedelta(minutes=4)).isoformat(),
            },
        )
        db.commit()
    snapshot = session_client(env, "ADMIN").get("/api/workspace").json()
    assert snapshot["controllerReadStatus"] == "FAILED"
    assert snapshot["controllerReadMessage"] == "STALE_DEVICE_COMMUNICATION"
    assert not any(device["online"] for device in snapshot["devices"])
    assert not snapshot["controllerWritesAvailable"]


def fake_bridge(tmp_path, output, stay=False):
    sdk = tmp_path / "sdk/lib"
    sdk.mkdir(parents=True)
    (sdk / "libhcnetsdk.so").touch()
    bridge = tmp_path / "bridge"
    bridge.write_text(
        f"#!{sys.executable}\nimport sys,time\nfor _ in range(6): sys.stdin.readline()\n"
        + "\n".join(
            "print(" + repr("IFT " + json.dumps(row)) + ",flush=True)" for row in output
        )
        + (
            "\nfor line in sys.stdin:\n if line.strip()=='QUIT': break\n"
            if stay
            else "\n"
        )
    )
    bridge.chmod(0o700)
    return NativeSession(
        config(
            tmp_path,
            hikvision_bridge_path=bridge,
            hikvision_username="synthetic-user",
            hikvision_password="synthetic-password",
        )
    )


def test_native_auth_failure_stops_automatic_login(tmp_path):
    session = fake_bridge(
        tmp_path, [{"kind": "status", "stage": "login", "status": "FAILED", "error": 1}]
    )
    session.settings.hikvision_sdk_dir = tmp_path / "sdk"
    with pytest.raises(ControllerReadUnavailable, match="AUTHENTICATION_FAILED"):
        session.start()
    assert session.process is None and session.auth_failed
    with pytest.raises(ControllerReadUnavailable, match="AUTHENTICATION_STOPPED"):
        session.start()
    assert "synthetic-" not in json.dumps(session.metadata)


def test_native_close_drains_frames_emitted_during_quit(tmp_path):
    session = fake_bridge(
        tmp_path,
        [{"kind": "identity", "serial": "synthetic-device"}, {"kind": "ready"}],
        stay=True,
    )
    bridge = session.settings.hikvision_bridge_path
    final_event = frame(kind="event", feed="LIVE", timeType=1)
    cleanup = {"kind": "status", "stage": "cleanup", "status": "VERIFIED", "error": 0}
    bridge.write_text(
        bridge.read_text().replace(
            " if line.strip()=='QUIT': break",
            " if line.strip()=='QUIT':\n  print("
            + repr("IFT " + json.dumps(final_event))
            + ",flush=True)\n  print("
            + repr("IFT " + json.dumps(cleanup))
            + ",flush=True)\n  break",
        )
    )
    session.settings.hikvision_sdk_dir = tmp_path / "sdk"
    session.start()
    session.close()
    assert len(session.live) == 1
    assert session.metadata["liveReceived"] == 1
    assert session.metadata["stages"]["cleanup"]["status"] == "VERIFIED"
    assert session.metadata["code"] == "SESSION_CLOSED"


def test_native_heartbeat_does_not_claim_device_communication(tmp_path):
    session = NativeSession(config(tmp_path))
    session._handle({"kind": "ready"})
    previous = session.metadata["lastCommunication"]
    session._handle({"kind": "heartbeat"})
    assert session.metadata["lastCommunication"] == previous
    session._handle({"kind": "fault", "code": "CALLBACK_GAP"})
    with pytest.raises(ControllerReadUnavailable, match="CALLBACK_GAP"):
        session.pump()


def test_actual_device_firmware_encoding_and_capacity_error(tmp_path):
    from app.hikvision import error_code

    session = NativeSession(config(tmp_path))
    session._handle(
        {
            "kind": "device",
            "model": "DS-K2802",
            "firmwareRaw": 33554440,
            "buildDateRaw": 1639961,
        }
    )
    assert session.metadata["firmwarePacked"] == "V2.0.8"
    assert session.metadata["firmwareBuild"] == "250625"
    assert error_code(1924) == "SUBSCRIPTION_CAPACITY_EXHAUSTED"


def test_native_queue_overflow_is_reported(tmp_path):
    session = NativeSession(config(tmp_path))
    process = SimpleNamespace(
        stdout=io.BytesIO(b'IFT {"kind":"heartbeat"}\n'), terminate=lambda: None
    )
    session.process = process
    session.messages = queue.Queue(maxsize=1)
    session.messages.put({"kind": "heartbeat"})
    session._reader(process, session.messages)
    with pytest.raises(
        ControllerReadUnavailable, match="NATIVE_QUEUE_OR_PROTOCOL_FAILURE"
    ):
        session.pump()


def test_native_deadline_closes_hung_child(tmp_path):
    session = fake_bridge(tmp_path, [], stay=True)
    session.settings.hikvision_sdk_dir = tmp_path / "sdk"
    session.settings.hikvision_process_timeout = 0.15
    started = time.monotonic()
    with pytest.raises(ControllerReadUnavailable, match="NATIVE_TIMEOUT"):
        session.start()
    assert session.process is None and time.monotonic() - started < 5
