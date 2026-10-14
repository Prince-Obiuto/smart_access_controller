"""Individually reviewed normal-workflow card operations using unchanged TEMPLATE 3."""
import argparse
import hashlib
import json
import os
import struct
import sys
from datetime import datetime, timedelta
from getpass import getpass
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from .common import audit, get_setting, iso, now, set_setting
from .config import Settings
from .db import Database, domain_lock
from .hikvision_card_diagnostic import Bridge, actor_for, save, verify_baseline
from .hikvision_validity_probe import inspect
from .hikvision_writes import (
    WriteBlocked,
    card_matches,
    current_intent,
    digest,
    process_controlled,
    scope_key,
)
from .models import (
    AccessEvent,
    Account,
    Application,
    Assignment,
    Card,
    Operation,
    Person,
    Visit,
)
from .security import login_account

BANNED = {"1051305927", "1052625607", "1050853895"}
DEVICE = {"kind": "device", "model": "DS-K2802", "serial": "DS-K280220250625V020008ENGS6372431",
          "firmware": 33554440, "build": 1639961}


def validate_designation(db, cards, baseline):
    for number, bound in cards.items():
        if number in BANNED or bound.get("kind") not in {"PERSONAL", "VISITOR"} or not bound.get("personIds"):
            raise WriteBlocked("INVALID_DESIGNATED_SCOPE")
        card = db.scalar(select(Card).where(Card.number == number))
        if card and (card.revoked or card.kind != bound["kind"] or card.privileged or card.ever_privileged):
            raise WriteBlocked("TEST_CARD_LOCAL_CONFLICT")
        pending = db.scalar(select(Assignment).where(Assignment.card_id == card.id, Assignment.ended_at.is_(None))) if card else None
        if pending:
            application = db.scalar(select(Application).where(Application.assignment_id == pending.id))
            # Normal read-only issuance reserves the card locally before a scope
            # exists. It is safe to review that exact owned intent when fresh
            # device readback below proves the card still inactive.
            if not (card.kind == "PERSONAL" and pending.state == "PENDING" and pending.sync == "PENDING"
                    and pending.person_id in bound["personIds"] and application
                    and application.person_id == pending.person_id and application.state == "ISSUANCE_PENDING"
                    and application.evidence_id and application.reviewer_id):
                raise WriteBlocked("TEST_CARD_ALREADY_ASSIGNED")
        record = next((c for c in baseline["cards"] if c["card"] == number), None)
        if not card_matches(record, {"card": number, "enabled": False}):
            raise WriteBlocked("TEST_CARD_EXISTING_CONTROLLER_PERMISSION")
        for person_id in bound["personIds"]:
            person = db.get(Person, person_id)
            account = db.scalar(select(Account).where(Account.person_id == person_id))
            if not person or person.status != "ACTIVE" or person.purged or person.deleted_at or not account or account.role != "USER":
                raise WriteBlocked("ACTIVE_TEST_USER_REQUIRED")
            if (person.kind == "VISITOR") != (bound["kind"] == "VISITOR"):
                raise WriteBlocked("TEST_IDENTITY_KIND_CONFLICT")


def check_operation(db, op, scope, at):
    actor_for(db, scope)
    if op.id not in scope.get("reviewedOperations", []):
        raise WriteBlocked("INDIVIDUAL_OPERATION_REVIEW_REQUIRED")
    if op.target_type not in {"ASSIGNMENT", "POOL"}:
        raise WriteBlocked("EXISTING_TEMPLATE_CARD_ONLY")
    if op.desired != "DISABLED" and at >= datetime.fromisoformat(scope["expiresAt"]):
        raise WriteBlocked("TEST_SCOPE_EXPIRED")
    if op.target_type == "ASSIGNMENT" and op.desired != "DISABLED":
        a = db.get(Assignment, op.target_id)
        card = db.get(Card, a.card_id) if a else None
        if not a or not card or card.number in BANNED or card.revoked:
            raise WriteBlocked("REVOKED_OR_UNDESIGNATED_CARD")
        if card.kind == "VISITOR":
            visit = db.scalar(select(Visit).where(Visit.assignment_id == a.id))
            if not visit or visit.state not in {"PENDING", "ACTIVE"} or not visit.approval or visit.approval.get("action") != "EXTERNAL_APPROVAL_CONFIRMED":
                raise WriteBlocked("NORMAL_VISITOR_APPROVAL_REQUIRED")
            if visit.start != a.start or visit.expiry != a.expiry or visit.person_id != a.person_id:
                raise WriteBlocked("VISITOR_ASSIGNMENT_CHANGED")
        else:
            app = db.scalar(select(Application).where(Application.assignment_id == a.id))
            if not app or app.state not in {"ISSUANCE_PENDING", "ISSUED"} or not app.evidence_id or not app.reviewer_id:
                raise WriteBlocked("NORMAL_PAID_ISSUANCE_REQUIRED")


class ScopedBridge(Bridge):
    source_name = "hikvision_existing_template.cpp"
    def __init__(self, settings, cards):
        super().__init__(settings)
        if not cards or len(cards) > 4 or any(c in BANNED or not c.isascii() or not c.isdecimal() or not 4 <= len(c) <= 32 for c in cards):
            raise WriteBlocked("INVALID_DESIGNATED_SCOPE")
        self.scope_cards = list(cards)


def capture(settings, cards, binary=None):
    bridge = ScopedBridge(settings, cards)
    bridge.reuse_binary = binary
    bridge.keep_binary = binary is not None
    try:
        actual = bridge.start()
        actual["protected"] = {c["card"]: digest(c) for c in actual["cards"] if c["card"] not in cards}
        return bridge, actual
    except Exception as exc:
        bridge.close()
        if isinstance(exc, WriteBlocked):
            exc.artifact = save("normal-card-preflight-failed", {"code": str(exc), "native": bridge.rows, "writes": 0})
        raise


def expected_validity(intent):
    def t(parts): return struct.pack("<H6B", *parts, 0)
    return b"\x01\x01\x01\x00" + t(intent["start"]) + t(intent["expiry"]) + bytes(32)


def compatible_match(record, intent, plan, profile, raw):
    if card_matches(record, intent, plan):
        return True
    if not intent["enabled"] or not record or not profile.get("ownerTimedEvidenceVerified") or profile.get("device") != DEVICE:
        return False
    if profile.get("abi") != {"validitySize": 52, "v50Size": 2708, "v50ValidityOffset": 300, "timeExSize": 8}:
        return False
    if not raw or raw.get("card") != intent["card"] or raw.get("command") != 2178 or raw.get("direction") != "READ":
        return False
    if [raw.get(k) for k in ["nativeSize", "declaredSize", "callbackLength", "validityOffset", "validitySize"]] != [2708, 2708, 2708, 300, 52]:
        return False
    try:
        actual = bytearray.fromhex(raw["hex"])
        if actual[1:3] != b"\x00\x00": return False
        actual[1:3] = b"\x01\x01"
        if bytes(actual) != expected_validity(intent): return False
    except (KeyError, ValueError, IndexError, struct.error):
        return False
    return record.get("beginFlag") == record.get("endFlag") == 0 and card_matches({**record, "beginFlag": 1, "endFlag": 1}, intent, plan)


class ExistingTemplateTransport:
    def __init__(self, settings, scope):
        self.settings, self.scope = settings, scope
        self.rows = []; self.last_raw = None; self.last_card = None; self.used_compatibility = False
        self.binary = None
    def preflight(self):
        if self.scope.get("kind") != "EXISTING_TEMPLATE" or self.scope.get("existingTemplate") != 3:
            raise WriteBlocked("EXISTING_TEMPLATE_SCOPE_REQUIRED")
        header_hash = hashlib.sha256((self.settings.hikvision_sdk_dir / "incEn/HCNetSDK.h").read_bytes()).hexdigest()
        if self.scope["validityProfile"].get("headerSha256") != header_hash:
            raise WriteBlocked("MATCHING_HEADER_CHANGED")
        library_hash = hashlib.sha256((self.settings.hikvision_sdk_dir / "lib/libhcnetsdk.so").read_bytes()).hexdigest()
        if self.scope["validityProfile"].get("librarySha256") != library_hash:
            raise WriteBlocked("MATCHING_SDK_LIBRARY_CHANGED")
        try:
            bridge, actual = capture(self.settings, self.scope["cards"], self.binary)
        except WriteBlocked as exc:
            self.rows.append({"kind": "preflightFailure", "code": str(exc), "artifact": getattr(exc, "artifact", None)})
            raise
        try:
            verify_baseline(actual, self.scope["baseline"])
        except Exception:
            bridge.close(); raise
        if self.binary is None:
            self.binary = bridge.binary
            bridge.keep_binary = True
        return bridge, actual
    def start(self):
        b, _ = self.preflight(); b.close()
    def verify_ownership(self):
        self.start()  # No allocation/editing: complete referenced preimages + foreign-card protection.
    def read_card(self, number):
        b, _ = self.preflight()
        try:
            b.process.stdin.write(("READ " + number + "\n").encode()); b.process.stdin.flush(); b.read(); b.close()
            lifecycle = [r for r in b.rows if "stage" in r]
            end = [r for r in b.rows if r.get("kind") == "permissionEnd" and r.get("request") == 4]
            records = [r["data"] for r in b.rows if r.get("kind") == "permissionRow" and r.get("request") == 4]
            if len(lifecycle) != 4 or not all(r.get("ok") for r in lifecycle) or len(end) != 1:
                raise WriteBlocked("EXACT_CARD_READ_INCOMPLETE")
            if not records and end[0].get("code") == "SDK_ERROR" and end[0].get("error") == 1903:
                self.last_card = None; self.last_raw = None; return None
            if end[0].get("code") != "OK" or len(records) != 1 or records[0]["card"] != number:
                raise WriteBlocked("EXACT_CARD_READ_FAILED")
            self.last_card = records[0]
            self.last_raw = next((r for r in b.rows if r.get("kind") == "validityBytes" and r.get("request") == 4), None)
            if not self.last_raw or self.last_raw.get("nativeSize") != self.last_raw.get("declaredSize") or self.last_raw.get("callbackLength") != 2708:
                raise WriteBlocked("CARD_NATIVE_SIZE_MISMATCH")
            return self.last_card
        finally:
            b.close()
            self.rows.extend(b.rows)
    def matches_card(self, record, intent, plan):
        strict = card_matches(record, intent, plan)
        matches = compatible_match(record, intent, plan, self.scope["validityProfile"], self.last_raw)
        self.used_compatibility |= matches and not strict
        return matches
    def write_card(self, intent, plan):
        if plan not in {None, 3} or intent["card"] not in self.scope["cards"] or intent["card"] in BANNED:
            raise WriteBlocked("CARD_SCOPE_CHANGED")
        previous = self.scope.get("lastRecords", {}).get(intent["card"])
        if digest(self.last_card) != digest(previous):
            raise WriteBlocked("TEST_CARD_CHANGED_RECONCILE_FIRST")
        b, actual = self.preflight()
        try:
            current = next((c for c in actual["cards"] if c["card"] == intent["card"]), None)
            if digest(current) != digest(self.last_card): raise WriteBlocked("TEST_CARD_CHANGED_BEFORE_WRITE")
            b.send(intent); b.close()
            lifecycle = [r for r in b.rows if "stage" in r]
            if len(lifecycle) != 4 or not all(r.get("ok") for r in lifecycle):
                raise WriteBlocked("CARD_WRITE_LIFECYCLE_UNCERTAIN")
            ends = [r for r in b.rows if r.get("kind") == "permissionEnd" and r.get("request") == 3]
            if len(ends) != 1 or ends[0].get("code") != "OK" or ends[0].get("error"):
                raise WriteBlocked("CARD_WRITE_FAILED_OR_UNCERTAIN")
            if intent["enabled"]:
                packet = next((r for r in b.rows if r.get("kind") == "validityBytes" and r.get("direction") == "WRITE"), None)
                if not packet or bytes.fromhex(packet["hex"]) != expected_validity(intent):
                    raise WriteBlocked("WRITTEN_VALIDITY_BYTES_MISMATCH")
        finally:
            b.close()
            self.rows.extend(b.rows)
    def failure_evidence(self, code):
        return {"failureArtifact": save("normal-workflow-card-failed", {
            "code": code, "native": self.rows, "lastObservedRecord": self.last_card,
            "lastRawReadback": self.last_raw, "readbackVerified": False})}
    def evidence(self):
        b, _ = self.preflight(); b.close()
        return {"existingTemplateBaselineHash": digest(self.scope["baseline"]["resources"]),
                "protectedReadbackVerified": True, "actualRecord": self.last_card,
                "validityCompatibilityApplied": self.used_compatibility,
                "validityRawReadback": self.last_raw,
                "artifact": save("normal-workflow-card", {"native": self.rows, "actualRecord": self.last_card})}
    def close(self):
        if self.binary:
            if self.binary.parent != self.settings.hikvision_bridge_path.parent:
                self.binary.unlink(missing_ok=True)
            self.binary = None


def validity_profile(db, probe):
    if probe["device"] != DEVICE or not probe["lifecycle"] or not probe["rawMatchesParsed"] or not probe["legacyMatchesV50Validity"]:
        raise WriteBlocked("VALIDITY_ABI_EVIDENCE_INCOMPLETE")
    legacy = probe.get("legacy", {})
    if [legacy.get(k) for k in ["command", "nativeSize", "declaredSize", "callbackLength"]] != [2116, 272, 272, 272]:
        raise WriteBlocked("LEGACY_VALIDITY_READ_INCOMPLETE")
    if len(probe["endResults"]) != 2 or any(r.get("code") != "OK" or r.get("error") for r in probe["endResults"]):
        raise WriteBlocked("VALIDITY_READ_INCOMPLETE")
    op = db.get(Operation, "fe0da5b6-89d3-4d1e-81a1-4ff9bb113b00")
    evidence = op.data.get("physicalEvidence", {}) if op else {}
    observations = evidence.get("observations", [])
    if len(observations) != 3 or [o.get("result") for o in observations] != ["DENIED", "GRANTED", "DENIED"]:
        raise WriteBlocked("OWNER_TIMED_VALIDITY_EVIDENCE_REQUIRED")
    for o in observations:
        event = db.get(AccessEvent, o["eventId"])
        if not o.get("ownerConfirmedPhysical") or not event or event.simulated or event.card != "1051305927" or event.result != o["result"]:
            raise WriteBlocked("OWNER_TIMED_VALIDITY_EVIDENCE_REQUIRED")
    report = json.loads(open(probe["artifact"]).read())
    abi = probe["abi"]
    return {"device": DEVICE, "ownerTimedEvidenceVerified": True, "ownerEvidenceOperation": op.id,
            "headerSha256": report["headerSha256"], "librarySha256": report["librarySha256"],
            "abi": {k: abi[k] for k in ["validitySize", "v50Size", "v50ValidityOffset", "timeExSize"]},
            "artifact": probe["artifact"], "manufacturerSemanticsUnresolved": True}


def verify_retained_paid(db, number, scope, record, raw):
    """Closing execution may retain a genuinely issued paid entitlement, never a test visitor."""
    if number in BANNED or scope.get("cards", {}).get(number, {}).get("kind") != "PERSONAL":
        raise WriteBlocked("PAID_PERSONAL_CARD_REQUIRED")
    card = db.scalar(select(Card).where(Card.number == number))
    assignment = db.scalar(select(Assignment).where(Assignment.card_id == card.id, Assignment.ended_at.is_(None))) if card else None
    application = db.scalar(select(Application).where(Application.assignment_id == assignment.id)) if assignment else None
    if not card or card.revoked or card.disabled or card.privileged or card.ever_privileged or not assignment or assignment.state != "ACTIVE" or assignment.sync != "SYNCHRONIZED" or not application or application.state != "ISSUED" or not application.evidence_id or not application.reviewer_id:
        raise WriteBlocked("PAID_ENTITLEMENT_NOT_CONFIRMED")
    op = db.scalar(select(Operation).where(Operation.target_id == assignment.id, Operation.version == assignment.version, Operation.status == "CONFIRMED", Operation.controller_id == "controller-1"))
    if not op or not op.data.get("readbackVerified") or op.id not in scope.get("reviewedOperations", []):
        raise WriteBlocked("PAID_ENTITLEMENT_NOT_CONFIRMED")
    intent = current_intent(db, op, scope, now())
    if not compatible_match(record, intent, 3, scope["validityProfile"], raw):
        raise WriteBlocked("PAID_ENTITLEMENT_READBACK_MISMATCH")
    return {"card": number, "assignmentId": assignment.id, "operationId": op.id, "validity": {"start": intent["start"], "expiry": intent["expiry"]}}


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("activate").add_argument("--manifest", required=True)
    sub.add_parser("review").add_argument("--operation", required=True)
    sub.add_parser("run").add_argument("--operation", required=True)
    sub.add_parser("close").add_argument("--retain-paid-card")
    args = p.parse_args(); settings = Settings(); database = Database(settings)
    key = scope_key(settings.hikvision_controller_id)
    try:
        if args.command == "activate":
            manifest = json.loads(open(args.manifest).read())
            cards = manifest["cards"]
            probe = inspect(settings, "1051305927")
            bridge, baseline = capture(settings, cards); bridge.close()
            with database.session() as db:
                email = db.scalar(select(Person.email).join(Account).where(Account.role == "SUPER_ADMIN"))
                if not sys.stdin.isatty(): raise WriteBlocked("PRIVATE_ADMIN_LOGIN_REQUIRED")
                actor = login_account(db, email, getpass("Existing Super Admin password (hidden): "), SimpleNamespace(client=SimpleNamespace(host="local-existing-template")))
                db.commit(); domain_lock(db)
                if actor.role != "SUPER_ADMIN" or get_setting(db, key).get("enabled"):
                    raise WriteBlocked("SCOPE_REVIEW_REQUIRED")
                validate_designation(db, cards, baseline)
                scope = {"kind": "EXISTING_TEMPLATE", "existingTemplate": 3, "enabled": True,
                    "epoch": str(uuid4()), "actorId": actor.id, "cards": cards, "baseline": baseline,
                    "validityProfile": validity_profile(db, probe), "reviewedOperations": [],
                    "activatedAt": iso(now()), "expiresAt": iso(now() + timedelta(hours=3)),
                    "lastRecords": {n: next((c for c in baseline["cards"] if c["card"] == n), None) for n in cards}}
                actor_for(db, scope); set_setting(db, key, scope); audit(db, actor, "EXISTING_TEMPLATE_SCOPE", "TEMPLATE:3"); db.commit()
            print("Existing TEMPLATE 3 scope activated. No SDK write; each normal operation requires individual review.")
        else:
            with database.session() as db:
                domain_lock(db); scope = get_setting(db, key); actor = actor_for(db, scope)
                if scope.get("kind") != "EXISTING_TEMPLATE": raise WriteBlocked("EXISTING_TEMPLATE_SCOPE_REQUIRED")
                if args.command == "review":
                    op = db.get(Operation, args.operation)
                    if not op or op.status not in {"PENDING", "FAILED", "UNCERTAIN"} or op.controller_id != settings.hikvision_controller_id:
                        raise WriteBlocked("REVIEWABLE_OPERATION_REQUIRED")
                    scope["reviewedOperations"] = list(set(scope["reviewedOperations"] + [op.id]))
                    op.data = {**op.data, "writeEpoch": scope["epoch"], "reviewedBy": actor.id, "reviewedAt": iso(now())}
                    current_intent(db, op, scope, now()); op.attempts=0; op.status="PENDING"
                    set_setting(db,key,scope); audit(db,actor,"EXISTING_OPERATION_REVIEW",op.id); db.commit()
                    print(json.dumps({"operationId": op.id, "intent": current_intent(db,op,scope,now()), "controllerWrites": 0}))
                elif args.command == "close":
                    transport=ExistingTemplateTransport(settings,scope)
                    try:
                        transport.start()
                        retained = []
                        if args.retain_paid_card and args.retain_paid_card not in scope["cards"]:
                            raise WriteBlocked("CARD_OUTSIDE_DESIGNATED_SCOPE")
                        for number in scope["cards"]:
                            record = transport.read_card(number)
                            if number == args.retain_paid_card:
                                retained.append(verify_retained_paid(db, number, scope, record, transport.last_raw))
                            elif not card_matches(record, {"card": number,"enabled":False}):
                                raise WriteBlocked("CLEANUP_NOT_CONFIRMED")
                        set_setting(db,key,{**scope,"enabled":False,"retainedPaidEntitlements":retained});audit(db,actor,"EXISTING_SCOPE_CLOSED","TEMPLATE:3");db.commit()
                        print("Write scope closed after protected readback checks; verified paid entitlement retained." if retained else "Scope closed after unchanged protected baselines and inactive test-card GETs.")
                    finally:
                        transport.close()
                else:
                    db.rollback()
                    process_controlled(db, settings.model_copy(update={"controller_mode":"HIKVISION_CONTROLLED"}), operation_id=args.operation)
                    op=db.get(Operation,args.operation)
                    if not op or op.status!="CONFIRMED":
                        print(json.dumps({"status":op.status if op else "MISSING", "code":op.data.get("message") if op else "MISSING"}));raise SystemExit(1)
                    scope=get_setting(db,key); number=op.data["intent"]["card"]
                    scope["lastRecords"]={**scope["lastRecords"],number:op.data.get("actualRecord")}
                    set_setting(db,key,scope);db.commit()
                    print(json.dumps({"operationId":op.id,"status":op.status,"readbackVerified":op.data.get("readbackVerified"),
                        "validityCompatibilityApplied":op.data.get("validityCompatibilityApplied"),"artifact":op.data.get("artifact")}))
    finally: database.engine.dispose()


if __name__ == "__main__":
    main()
