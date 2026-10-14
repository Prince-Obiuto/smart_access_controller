"""Audited card-only acceptance against existing TEMPLATE 3; no schedule writes.

Collector/outbox configuration is unchanged. This authenticated, short-lived
diagnostic scope is separate from paid issuance and from general write scopes.
"""
import argparse
import hashlib
import json
import os
import select as io_select
import subprocess
import sys
import time
from datetime import datetime, timedelta
from getpass import getpass
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select, text

from .common import audit, get_setting, iso, notify, now, set_setting
from .config import ROOT, Settings
from .db import Database, domain_lock
from .hikvision import LAGOS, error_code
from .hikvision_writes import WriteBlocked, card_matches, digest
from .models import Account, Assignment, Card, Operation, Person
from .security import login_account

CARDS = {"A": "1051305927", "B": "1052625607", "V": "1052815031"}
KEY = "hikvision:card-diagnostic:controller-1"
ROOT_EVIDENCE = ROOT / "backend/var/acceptance/card-diagnostic"


def save(label, value):
    ROOT_EVIDENCE.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = ROOT_EVIDENCE / (now().strftime("%Y%m%d-%H%M%S%f") + "-" + label + ".json")
    with path.open("x") as f:
        json.dump(value, f, indent=2, default=str)
    path.chmod(0o600)
    return str(path)


def brief_card(card):
    if card is None:
        return None
    return {**{k: v for k, v in card.items() if k not in {"doors", "plans"}},
            "doorRights": {str(i + 1): n for i, n in enumerate(card["doors"]) if n},
            "plansByDoor": {str(i + 1): p for i, p in enumerate(card["plans"]) if any(p)}}


class Bridge:
    source_name = "hikvision_card_diagnostic.cpp"
    def __init__(self, settings):
        self.settings = settings
        self.process = None
        self.rows = []
        self.binary = None
        self.reuse_binary = None
        self.keep_binary = False

    def start(self):
        s = self.settings
        binary = self.reuse_binary or ROOT_EVIDENCE / ("card-only-bridge-" + str(uuid4()))
        self.binary = binary
        header = s.hikvision_sdk_dir / "incEn/HCNetSDK.h"
        source = ROOT / "backend/native" / self.source_name
        ROOT_EVIDENCE.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.reuse_binary:
            if not binary.is_file():
                raise WriteBlocked("NATIVE_BRIDGE_MISSING")
        else:
            try:
                subprocess.run(["g++", "-std=c++17", "-O2", "-pthread", "-Wall", "-Wextra",
                        "-I", str(header.parent), str(source), "-L", str(s.hikvision_sdk_dir / "lib"),
                        "-Wl,-rpath-link," + str(s.hikvision_sdk_dir / "lib"), "-lhcnetsdk",
                        "-o", str(binary)], check=True, timeout=30, capture_output=True)
            except subprocess.TimeoutExpired:
                binary.unlink(missing_ok=True)
                raise WriteBlocked("NATIVE_BRIDGE_BUILD_TIMEOUT") from None
            except subprocess.CalledProcessError:
                binary.unlink(missing_ok=True)
                raise WriteBlocked("NATIVE_BRIDGE_BUILD_FAILED") from None
        binary.chmod(0o700)
        values = [s.hikvision_host, str(s.hikvision_port), s.hikvision_username.get_secret_value(),
                  s.hikvision_password.get_secret_value(), str(s.hikvision_sdk_dir / "lib")]
        if hasattr(self, "scope_cards"):
            values.append(str(len(self.scope_cards)) + " " + " ".join(self.scope_cards))
        if not values[2] or not values[3]:
            raise WriteBlocked("RUN_CONFIGURE_HIKVISION_PRIVATELY")
        if any(any(c in v for c in "\r\n\0") for v in values):
            raise WriteBlocked("INVALID_PRIVATE_CONFIGURATION")
        self.process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=False, bufsize=0, env={"PATH": os.defpath, "LANG": "C",
            "LD_LIBRARY_PATH": str(s.hikvision_sdk_dir / "lib") + ":" + str(s.hikvision_sdk_dir / "lib/HCNetSDKCom")})
        self.process.stdin.write(("\n".join(values) + "\n").encode())
        self.process.stdin.flush()
        self.read(until_ready=True)
        return snapshot(self.rows)

    def read(self, until_ready=False):
        deadline = time.monotonic() + 45
        pending = b""
        while time.monotonic() < deadline:
            if not io_select.select([self.process.stdout], [], [], 0.2)[0]:
                continue
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                if until_ready:
                    failures = [r for r in self.rows if r.get("error") and (r.get("ok") is False or r.get("code") == "SDK_ERROR")]
                    if failures:
                        raise WriteBlocked(error_code(failures[0]["error"]))
                    raise WriteBlocked("NATIVE_PREFLIGHT_FAILED")
                return
            pending += chunk
            if len(pending) > 1_000_000:
                raise WriteBlocked("NATIVE_FRAME_TOO_LARGE")
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                if line.startswith(b"IFT "):
                    row = json.loads(line[4:]); self.rows.append(row)
                    if until_ready and row.get("kind") == "ready":
                        if pending:
                            raise WriteBlocked("UNEXPECTED_PREFLIGHT_FRAME")
                        return
        raise WriteBlocked("NATIVE_TIMEOUT")

    def send(self, intent):
        tokens = ["CARD", intent["card"], str(int(intent["enabled"]))]
        if intent["enabled"]:
            tokens.extend(str(x) for x in intent["start"] + intent["expiry"])
        self.process.stdin.write((" ".join(tokens) + "\n").encode()); self.process.stdin.flush()
        self.read()

    def close(self):
        if self.process:
            if self.process.poll() is None:
                try:
                    self.process.stdin.write(b"QUIT\n"); self.process.stdin.flush()
                    self.read()
                except (OSError, WriteBlocked):
                    self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait()
        if self.binary and not self.keep_binary and (self.process is None or self.process.poll() is not None):
            self.binary.unlink(missing_ok=True)
            self.binary = None


def snapshot(rows):
    def one(kind):
        found = [r for r in rows if r.get("kind") == kind]
        if len(found) != 1:
            raise WriteBlocked("NATIVE_PREFLIGHT_INCOMPLETE_" + kind)
        return found[0]
    device = one("device")
    clock = datetime(*one("clock")["time"], tzinfo=LAGOS)
    if device["model"] != "DS-K2802" or abs((clock - now()).total_seconds()) > 120:
        raise WriteBlocked("DEVICE_IDENTITY_OR_CLOCK_UNVERIFIED")
    if device["firmware"] != 33554440 or device["build"] != 1639961:
        raise WriteBlocked("DEVICE_FIRMWARE_CHANGED")
    resources = {r["resource"]: r for r in rows if r.get("kind") == "resource"}
    if not resources or not all(r["ok"] and r["returned"] == r["size"] for r in resources.values()):
        raise WriteBlocked("LINKED_SCHEDULE_READ_INCOMPLETE")
    end = [r for r in rows if r.get("kind") == "permissionEnd" and r.get("request") == 1]
    if len(end) != 1 or end[0].get("code") != "OK":
        raise WriteBlocked("CARD_INVENTORY_INCOMPLETE")
    cards = [r["data"] for r in rows if r.get("kind") == "permissionRow" and r.get("request") == 1]
    if len({c["card"] for c in cards}) != len(cards):
        raise WriteBlocked("DUPLICATE_NATIVE_CARD")
    template, week = one("templateFields"), one("weekFields")
    if not template["enabled"] or not week["enabled"] or "TEMPLATE:3" not in resources:
        raise WriteBlocked("EXISTING_TEMPLATE_NOT_ENABLED")
    return {"at": iso(now()), "device": device, "clock": iso(clock), "resources": resources,
            "template": template, "week": week, "cards": cards,
            "protected": {c["card"]: digest(c) for c in cards if c["card"] not in CARDS.values()}}


def verify_baseline(actual, baseline):
    if actual["device"] != baseline["device"]:
        raise WriteBlocked("DEVICE_IDENTITY_CHANGED")
    if actual["resources"] != baseline["resources"]:
        raise WriteBlocked("EXISTING_SCHEDULE_CHANGED")
    if actual["protected"] != baseline["protected"]:
        raise WriteBlocked("PROTECTED_CARD_CHANGED")


def actor_for(db, scope):
    actor = db.get(Account, scope.get("actorId"))
    person = db.get(Person, actor.person_id) if actor else None
    if not actor or actor.role != "SUPER_ADMIN" or not person or person.status != "ACTIVE" or person.purged or person.deleted_at:
        raise WriteBlocked("ACTIVE_AUTHENTICATED_SUPER_ADMIN_REQUIRED")
    return actor


def validate_action(db, scope, actual, label, enabled):
    if not scope.get("enabled") or scope.get("cards") != CARDS:
        raise WriteBlocked("DIAGNOSTIC_SCOPE_REQUIRED")
    verify_baseline(actual, scope["baseline"])
    actor_for(db, scope)
    if enabled and now() >= datetime.fromisoformat(scope["expiresAt"]):
        raise WriteBlocked("DIAGNOSTIC_SCOPE_EXPIRED")
    if label == "V":
        raise WriteBlocked("VISITOR_REQUIRES_NORMAL_APPROVAL_AND_ASSIGNMENT")
    number = CARDS[label]
    card = db.scalar(select(Card).where(Card.number == number))
    if enabled and (scope.get("revoked", {}).get(number) or card and card.revoked):
        raise WriteBlocked("REVOKED_PERSONAL_CARD_CANNOT_REACTIVATE")
    if card and db.scalar(select(Assignment).where(Assignment.card_id == card.id, Assignment.state.in_(["ACTIVE", "PENDING"]))):
        raise WriteBlocked("NORMAL_ASSIGNMENT_CONFLICT")
    if label == "B" and enabled:
        if not scope.get("revoked", {}).get(CARDS["A"]):
            raise WriteBlocked("OLD_DISABLE_UNCONFIRMED")
        a = next((c for c in actual["cards"] if c["card"] == CARDS["A"]), None)
        if not card_matches(a, {"enabled": False, "card": CARDS["A"]}, None):
            raise WriteBlocked("OLD_DISABLE_UNCONFIRMED")
    actual_card = next((c for c in actual["cards"] if c["card"] == number), None)
    expected = scope.get("lastRecords", {}).get(number)
    if digest(actual_card) != digest(expected):
        raise WriteBlocked("TEST_CARD_CHANGED_RECONCILE_FIRST")
    return actor_for(db, scope)


def run_action(settings, database, label, enabled, validity_probe=False):
    bridge = Bridge(settings)
    operation_id = None
    try:
        actual = bridge.start()
        with database.session() as db:
            domain_lock(db)
            scope = get_setting(db, KEY)
            actor = validate_action(db, scope, actual, label, enabled)
            start = now().astimezone(LAGOS).replace(microsecond=0)
            if validity_probe:
                start += timedelta(minutes=2)
            expiry = min(start + (timedelta(minutes=1) if validity_probe else timedelta(minutes=30)),
                         datetime.fromisoformat(scope["expiresAt"]).astimezone(LAGOS))
            intent = {"kind": "CARD", "card": CARDS[label], "enabled": enabled,
                      "start": list(start.timetuple()[:6]), "expiry": list(expiry.timetuple()[:6])}
            if enabled and expiry <= start:
                raise WriteBlocked("VALIDITY_RANGE_EXPIRED")
            op = Operation(controller_id=settings.hikvision_controller_id, target_type="DIAGNOSTIC",
                target_id=str(uuid4()), desired="ENABLED" if enabled else "DISABLED", created_at=now(),
                actor_id=actor.id, status="PENDING", attempts=0,
                data={"purpose": "TEMPORARY_DIAGNOSTIC_AUTHORIZATION", "validityProbe": validity_probe, "simulated": False,
                      "diagnosticEpoch": scope["epoch"], "intent": intent, "existingTemplate": 3,
                      "baselineHash": digest(actual), "readbackVerified": False})
            db.add(op); db.flush(); operation_id = op.id
            audit(db, actor, "HIKVISION_DIAGNOSTIC_INTENT", operation_id, "PENDING")
            db.commit()
            # Durable pre-send marker. Only this exact operation is executed.
            op.status = "UNCERTAIN"; op.attempts = 1
            op.data = {**op.data, "sentAt": iso(now())}; db.commit()
        bridge.send(intent)
        bridge.close()
        result_rows = [r["data"] for r in bridge.rows if r.get("kind") == "permissionRow" and r.get("request") == 4]
        end = [r for r in bridge.rows if r.get("kind") == "permissionEnd" and r.get("request") == 4]
        final = result_rows[0] if len(result_rows) == 1 and result_rows[0]["card"] == intent["card"] else None
        read_ok = len(end) == 1 and (end[0].get("code") == "OK" or
            not result_rows and end[0].get("error") == 1903 and end[0].get("code") == "SDK_ERROR")
        lifecycle = [r for r in bridge.rows if r.get("stage") in {"initialize", "login", "logout", "cleanup"}]
        lifecycle_ok = len(lifecycle) == 4 and all(r.get("ok") for r in lifecycle)
        write_end = [r for r in bridge.rows if r.get("kind") == "permissionEnd" and r.get("request") == 3]
        write_ok = len(write_end) == 1 and write_end[0].get("code") == "OK" and write_end[0].get("error", 0) == 0
        verified = write_ok and read_ok and lifecycle_ok and card_matches(final, intent, 3)
        artifact = save(label + ("-enable" if enabled else "-disable"), {"operationId": operation_id,
            "intent": intent, "native": bridge.rows, "final": final, "readbackVerified": verified})
        # Independent post-operation snapshot protects every linked schedule and
        # foreign card. A mismatch leaves this operation unresolved, not success.
        post = Bridge(settings)
        try:
            after = post.start(); verify_baseline(after, actual)
        finally:
            post.close()
        with database.session() as db:
            domain_lock(db); op = db.get(Operation, operation_id); scope = get_setting(db, KEY)
            op.status = "CONFIRMED" if verified else "FAILED"
            op.completed_at = now() if verified else None
            op.data = {**op.data, "readbackVerified": verified, "nativeResults": bridge.rows,
                       "actualRecord": final, "artifact": artifact, "protectedReadbackVerified": True}
            if read_ok:
                scope["lastRecords"] = {**scope.get("lastRecords", {}), intent["card"]: final}
                set_setting(db, KEY, scope)
            if verified:
                if not enabled:
                    scope["revoked"] = {**scope.get("revoked", {}), intent["card"]: iso(now())}
                    card = db.scalar(select(Card).where(Card.number == intent["card"]))
                    if not card:
                        card = Card(number=intent["card"], kind="PERSONAL", disabled=True, revoked=True)
                        db.add(card)
                    else:
                        card.disabled = True; card.revoked = True
                set_setting(db, KEY, scope)
            audit(db, actor_for(db, scope), "HIKVISION_DIAGNOSTIC_RESULT", op.id, op.status)
            notify(db, "Temporary controller diagnostic", label + " " + op.desired + ": " + op.status + ". Paid issuance remains unchanged.")
            db.commit()
        print(json.dumps({"operationId": operation_id, "label": label, "card": intent["card"],
                          "intent": intent, "readbackVerified": verified, "record": brief_card(final),
                          "writeResult": [r for r in bridge.rows if r.get("kind") == "permissionEnd" and r.get("request") == 3],
                          "artifact": artifact}))
        if not verified:
            raise WriteBlocked("DIAGNOSTIC_CARD_READBACK_FAILED")
    except Exception as exc:
        save("failure", {"operationId": operation_id, "errorType": type(exc).__name__,
                         "error": str(exc) if isinstance(exc, WriteBlocked) else "LOCAL_OR_NATIVE_FAILURE",
                         "native": bridge.rows})
        raise
    finally:
        bridge.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect")
    sub.add_parser("activate").add_argument("--actor-email")
    for command in ["enable", "disable", "probe-validity"]:
        sub.add_parser(command).add_argument("label", choices=["A", "B"])
    sub.add_parser("close")
    args = parser.parse_args(); settings = Settings()
    if settings.controller_mode != "HIKVISION_READ_ONLY":
        raise WriteBlocked("KEEP_GENERAL_COLLECTOR_AND_OUTBOX_READ_ONLY")
    if args.command in {"inspect", "activate"}:
        bridge = Bridge(settings)
        try:
            observed = bridge.start()
        finally:
            bridge.close()
        artifact = save("baseline", {"snapshot": observed, "native": bridge.rows})
        if args.command == "inspect":
            print(json.dumps({"device": observed["device"], "clock": observed["clock"],
                "template": observed["template"], "week": observed["week"], "cards": [brief_card(c) for c in observed["cards"]],
                "artifact": artifact, "controllerWrites": 0}, indent=2)); return
        if not sys.stdin.isatty():
            raise WriteBlocked("PRIVATE_INTERACTIVE_ADMIN_LOGIN_REQUIRED")
        database = Database(settings)
        try:
            with database.session() as db:
                emails = list(db.scalars(select(Person.email).join(Account, Account.person_id == Person.id)
                                         .where(Account.role == "SUPER_ADMIN", Person.status == "ACTIVE")))
                email = args.actor_email or (emails[0] if len(emails) == 1 else input("Existing Super Admin email: "))
                actor = login_account(db, email, getpass("Existing Super Admin password: "),
                    SimpleNamespace(client=SimpleNamespace(host="local-card-diagnostic")))
                db.commit(); domain_lock(db)
                if get_setting(db, KEY).get("enabled"):
                    raise WriteBlocked("EXISTING_DIAGNOSTIC_SCOPE_REQUIRES_REVIEW")
                scope = {"enabled": True, "epoch": str(uuid4()), "actorId": actor.id,
                    "cards": CARDS, "baseline": observed, "expiresAt": iso(now() + timedelta(hours=2)),
                    "revoked": get_setting(db, KEY).get("revoked", {}),
                    "lastRecords": {n: next((c for c in observed["cards"] if c["card"] == n), None) for n in CARDS.values()}}
                actor_for(db, scope)
                for label in ["A", "B"]:
                    if scope["lastRecords"][CARDS[label]] is not None:
                        raise WriteBlocked("EXISTING_TEST_CARD_RECORD_REQUIRES_REVIEW")
                set_setting(db, KEY, scope); audit(db, actor, "HIKVISION_DIAGNOSTIC_SCOPE", "TEMPLATE:3")
                db.commit()
            print("Card-only diagnostic scope activated for A/B; visitor enable requires normal approval. No SDK write sent.")
        finally:
            database.engine.dispose()
        return
    database = Database(settings)
    try:
        if args.command == "close":
            bridge = Bridge(settings)
            try:
                actual = bridge.start()
            finally:
                bridge.close()
            with database.session() as db:
                domain_lock(db); scope = get_setting(db, KEY); actor_for(db, scope)
                verify_baseline(actual, scope["baseline"])
                for label in ["A", "B"]:
                    c = next((c for c in actual["cards"] if c["card"] == CARDS[label]), None)
                    if not card_matches(c, {"enabled": False, "card": CARDS[label]}):
                        raise WriteBlocked("CLEANUP_NOT_CONFIRMED")
                set_setting(db, KEY, {**scope, "enabled": False});
                audit(db, actor_for(db, scope), "HIKVISION_DIAGNOSTIC_CLOSED", scope["epoch"]); db.commit()
            print("Diagnostic scope closed; A/B physically inactive; revoked history retained.")
        else:
            lock = int.from_bytes(hashlib.sha256(("ift-write:" + settings.hikvision_controller_id).encode()).digest()[:8], "big", signed=True)
            with database.engine.connect() as connection:
                if not connection.execute(text("SELECT pg_try_advisory_lock(:lock)"), {"lock": lock}).scalar():
                    raise WriteBlocked("ANOTHER_CONTROLLER_WRITE_IS_RUNNING")
                try:
                    run_action(settings, database, args.label, args.command != "disable", args.command == "probe-validity")
                finally:
                    connection.execute(text("SELECT pg_advisory_unlock(:lock)"), {"lock": lock})
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
