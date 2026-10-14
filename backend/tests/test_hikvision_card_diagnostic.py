"""Card-only diagnostic gates and durable sequencing; no live controller."""
from copy import deepcopy
from datetime import datetime, timedelta
import pytest
from sqlalchemy import select

from app.common import now, iso, get_setting, set_setting
from app.models import Card, Operation
from app.hikvision_card_diagnostic import CARDS, KEY, validate_action, verify_baseline, run_action
from app.hikvision_writes import WriteBlocked


def setup_scope(env):
    settings, database, accounts, _ = env
    snapshot = {"device": {"serial": "fake-controller"}, "resources": {"TEMPLATE:3": {"hex": "00"}},
                "protected": {"existing": "hash"}, "cards": []}
    scope = {"enabled": True, "actorId": accounts["SUPER_ADMIN"][0], "epoch": "test-epoch",
             "cards": CARDS, "baseline": deepcopy(snapshot), "lastRecords": {n: None for n in CARDS.values()},
             "revoked": {}, "expiresAt": iso(now() + timedelta(hours=1))}
    settings.controller_mode = "HIKVISION_READ_ONLY"
    with database.session() as db:
        set_setting(db, KEY, scope); db.commit()
    return settings, database, scope, snapshot


def test_existing_template_reference_does_not_require_capacity_or_weekday_order(env):
    _, database, scope, snapshot = setup_scope(env)
    with database.session() as db:
        assert validate_action(db, scope, snapshot, "A", True).role == "SUPER_ADMIN"


@pytest.mark.parametrize("change,error", [("schedule", "EXISTING_SCHEDULE_CHANGED"),
    ("protected", "PROTECTED_CARD_CHANGED"), ("device", "DEVICE_IDENTITY_CHANGED")])
def test_foreign_baseline_changes_block_writes(env, change, error):
    _, _, scope, snapshot = setup_scope(env)
    snapshot[{"schedule": "resources", "protected": "protected", "device": "device"}[change]] = {}
    with pytest.raises(WriteBlocked, match=error):
        verify_baseline(snapshot, scope["baseline"])


def test_replacement_requires_old_readback_and_revocation(env):
    _, database, scope, snapshot = setup_scope(env)
    with database.session() as db:
        with pytest.raises(WriteBlocked, match="OLD_DISABLE_UNCONFIRMED"):
            validate_action(db, scope, snapshot, "B", True)
        scope["revoked"][CARDS["A"]] = iso(now())
        assert validate_action(db, scope, snapshot, "B", True)
        snapshot["cards"] = [{"card": CARDS["A"], "type": 1, "doors": [1] + [0] * 255}]
        with pytest.raises(WriteBlocked, match="OLD_DISABLE_UNCONFIRMED"):
            validate_action(db, scope, snapshot, "B", True)


def test_revoke_is_permanent_and_visitor_cannot_skip_approval(env):
    _, database, scope, snapshot = setup_scope(env)
    with database.session() as db:
        scope["revoked"][CARDS["A"]] = iso(now())
        with pytest.raises(WriteBlocked, match="CANNOT_REACTIVATE"):
            validate_action(db, scope, snapshot, "A", True)
        with pytest.raises(WriteBlocked, match="VISITOR_REQUIRES_NORMAL"):
            validate_action(db, scope, snapshot, "V", True)


def test_scope_expiry_blocks_enable_but_allows_cleanup(env):
    _, database, scope, snapshot = setup_scope(env)
    scope["expiresAt"] = iso(now() - timedelta(seconds=1))
    with database.session() as db:
        with pytest.raises(WriteBlocked, match="SCOPE_EXPIRED"):
            validate_action(db, scope, snapshot, "A", True)
        assert validate_action(db, scope, snapshot, "A", False)


@pytest.mark.parametrize("write_code,flags,expected,probe", [
    ("OK", 1, True, False), ("SDK_ERROR", 1, False, False),
    ("OK", 0, False, False), ("OK", 1, True, True)])
def test_real_mode_never_marks_paid_assignment_synced_or_drains_backlog(env, monkeypatch, write_code, flags, expected, probe):
    settings, database, scope, snapshot = setup_scope(env)
    class FakeBridge:
        actual = deepcopy(snapshot)
        def __init__(self, _): self.rows = []
        def start(self): return deepcopy(self.actual)
        def close(self): pass
        def send(self, intent):
            if probe:
                from app.hikvision import LAGOS
                begin = datetime(*intent["start"], tzinfo=LAGOS)
                end = datetime(*intent["expiry"], tzinfo=LAGOS)
                assert 110 <= (begin - now()).total_seconds() <= 120
                assert (end - begin).total_seconds() == 60
            # Intent and pre-send marker must be committed before transport.
            with database.session() as db:
                op = db.scalar(select(Operation).where(Operation.target_type == "DIAGNOSTIC"))
                assert op.status == "UNCERTAIN" and op.attempts == 1
                assert op.data["purpose"] == "TEMPORARY_DIAGNOSTIC_AUTHORIZATION"
            card = {"card": intent["card"], "valid": 1, "type": 1, "leader": 0,
                    "doors": [1] + [0] * 255, "plans": [[3, 0, 0, 0]] + [[0] * 4 for _ in range(255)],
                    "validityEnabled": 1, "beginFlag": flags, "endFlag": flags, "timeType": 0,
                    "start": intent["start"], "expiry": intent["expiry"]}
            self.actual["cards"] = [card]
            self.rows = [{"kind": "permissionEnd", "request": 3, "code": write_code},
                         {"kind": "permissionRow", "request": 4, "data": card},
                         {"kind": "permissionEnd", "request": 4, "code": "OK"}] + [
                {"stage": stage, "ok": True} for stage in ["initialize", "login", "logout", "cleanup"]]
    monkeypatch.setattr("app.hikvision_card_diagnostic.Bridge", FakeBridge)
    monkeypatch.setattr("app.hikvision_card_diagnostic.save", lambda *_: "synthetic-test-artifact")
    if expected:
        run_action(settings, database, "A", True, probe)
    else:
        with pytest.raises(WriteBlocked, match="DIAGNOSTIC_CARD_READBACK_FAILED"):
            run_action(settings, database, "A", True, probe)
    with database.session() as db:
        op = db.scalar(select(Operation).where(Operation.target_type == "DIAGNOSTIC"))
        assert op.status == ("CONFIRMED" if expected else "FAILED") and op.data["simulated"] is False
        assert op.data["readbackVerified"] == expected
        assert db.scalar(select(Card).where(Card.number == CARDS["A"])) is None
        observed = get_setting(db, KEY)
        assert observed["lastRecords"][CARDS["A"]]["beginFlag"] == flags
        # A failed write/readback is reconciled to actual state, allowing safe cleanup.
        validate_action(db, observed, FakeBridge.actual, "A", False)


def test_native_bridge_has_no_schedule_dispatch_or_visitor_enable():
    from app.config import ROOT
    source = (ROOT / "backend/native/hikvision_card_diagnostic.cpp").read_text()
    assert 'permission_command(user,"CARD_WRITE",input,3000)' in source
    assert 'NET_DVR_SetDVRConfig(' not in source
    assert 'card=="1052815031" && enabled' in source
