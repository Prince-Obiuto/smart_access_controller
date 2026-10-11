"""Local inventory and authenticated, explicitly scoped test-write activation.

This tool never writes the controller. SDK writes are only durable worker jobs.
"""

import argparse
import json
import os
import time
from xml.etree import ElementTree as ET
from getpass import getpass
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from pydantic import ValidationError

from .config import Settings
from .db import Database, domain_lock
from .common import now, iso, get_setting, set_setting, audit
from .security import login_account, DomainError
from .models import Card, Person, Assignment, Account, Operation, Controller
from .hikvision import NativeSession
from .controllers import ControllerReadUnavailable
from .hikvision_writes import digest, scope_key, current_intent, WriteBlocked


def inventory(settings):
    session = NativeSession(settings, permissions=True)
    try:
        session.start()
        deadline = time.monotonic() + 300
        cards, result = session.permission_request("CARD_READ", ["ALL"])
        if result.get("code") != "OK":
            raise WriteBlocked("CARD_INVENTORY_INCOMPLETE")
        capabilities = session.metadata.get("permissionCapabilities", {})
        plan_capability = ET.fromstring(capabilities.get("CardRightPlan", "<missing/>"))
        template_range = plan_capability.find("PlanTemplate/templateNo")
        verified_max = (
            int(template_range.get("max", "0")) if template_range is not None else 0
        )
        plans, rejected, failures = {}, {}, {}
        # Conservative supported resource range; inspect the entire legacy
        # byte-addressable template namespace for indirect foreign references.
        for kind in ["WEEK", "HOLIDAY", "GROUP", "TEMPLATE"]:
            maximum = (
                (verified_max if 1 <= verified_max <= 255 else 255)
                if kind == "TEMPLATE"
                else 64
            )
            for number in range(1, maximum + 1):
                if time.monotonic() >= deadline:
                    raise WriteBlocked("INVENTORY_DEADLINE_EXCEEDED")
                rows, end = session.permission_request("PLAN_READ", [kind, number])
                key = kind + ":" + str(number)
                if end.get("code") == "OK" and len(rows) == 1:
                    plans[key] = rows[0]
                elif (
                    not verified_max
                    and kind == "TEMPLATE"
                    and number > 64
                    and end.get("error") == 17
                ):
                    rejected[key] = 17
                else:
                    failures[key] = {"code": end.get("code"), "error": end.get("error")}
        return {
            "at": iso(now()),
            "deviceIdentity": session.identity,
            "model": session.metadata.get("model"),
            "firmware": session.metadata.get("firmwarePacked"),
            "build": session.metadata.get("firmwareBuild"),
            "capabilities": session.metadata.get("permissionCapabilities", {}),
            "nativeSizes": session.metadata.get("permissionAbi", {}),
            "cards": cards,
            "plans": plans,
            "rejectedTemplateIds": rejected,
            "complete": not failures and 1 <= verified_max <= 255,
            "templateCapacityVerified": 1 <= verified_max <= 255,
            "templateCapacity": verified_max or None,
            "failures": failures,
        }
    finally:
        session.close()


def activate(db, settings, actor, manifest, observed):
    """Reserve only freshly observed disabled/unreferenced records; no allocation writes."""
    db.refresh(actor)
    operator = db.get(Person, actor.person_id)
    if (
        actor.role != "SUPER_ADMIN"
        or operator.status != "ACTIVE"
        or operator.purged
        or operator.deleted_at
    ):
        raise WriteBlocked("SUPER_ADMIN_REQUIRED")
    if manifest.get("controllerId") != settings.hikvision_controller_id or not db.get(
        Controller, manifest["controllerId"]
    ):
        raise WriteBlocked("CONTROLLER_SCOPE_MISMATCH")
    if not observed.get("complete") or observed.get("model") != "DS-K2802":
        raise WriteBlocked("INVENTORY_REQUIRED")
    designated = manifest.get("cards", {})
    if not isinstance(designated, dict) or not 1 <= len(designated) <= 32:
        raise WriteBlocked("OWNER_TEST_CARD_DESIGNATION_REQUIRED")
    for number, bound in designated.items():
        card = db.scalar(select(Card).where(Card.number == number))
        if (
            not card
            or not isinstance(number, str)
            or not number.isascii()
            or not number.isdigit()
            or len(number) > 20
            or card.kind != bound.get("kind")
        ):
            raise WriteBlocked("USE_NORMAL_CARD_WORKFLOW_FIRST")
        if not bound.get("personIds") or any(
            not db.get(Person, p) for p in bound["personIds"]
        ):
            raise WriteBlocked("TEST_PERSON_DESIGNATION_REQUIRED")
    unrestricted = manifest.get("unrestrictedCard")
    if unrestricted:
        card = db.scalar(select(Card).where(Card.number == unrestricted))
        assignment = (
            db.scalar(
                select(Assignment).where(
                    Assignment.card_id == card.id, Assignment.state == "ACTIVE"
                )
            )
            if card
            else None
        )
        owner = (
            db.scalar(select(Account).where(Account.person_id == assignment.person_id))
            if assignment
            else None
        )
        if unrestricted not in designated or not owner or owner.role != "SUPER_ADMIN":
            raise WriteBlocked("UNRESTRICTED_OWNER_REQUIRED")
    keys = []
    previous_scope = get_setting(db, scope_key(manifest["controllerId"]))
    for label in ["normal", "unrestricted"]:
        slots = manifest.get(label)
        if not slots:
            if label == "normal" or unrestricted:
                raise WriteBlocked("OWNED_SCHEDULE_REQUIRED")
            continue
        for field, kind in [
            ("week", "WEEK"),
            ("template", "TEMPLATE"),
            ("group", "GROUP"),
        ]:
            number = slots.get(field)
            if number is not None:
                if type(number) is not int or not 3 <= number <= 64:
                    raise WriteBlocked("TEST_RESOURCE_RANGE_INVALID")
                keys.append(kind + ":" + str(number))
        if not slots.get("week") or not slots.get("template"):
            raise WriteBlocked("OWNED_SCHEDULE_REQUIRED")
        for number in slots.get("holidays", []):
            if type(number) is not int or not 3 <= number <= 64:
                raise WriteBlocked("TEST_RESOURCE_RANGE_INVALID")
            keys.append("HOLIDAY:" + str(number))
    if len(keys) != len(set(keys)) or len({int(k.split(":")[1]) for k in keys}) > 64:
        raise WriteBlocked("TEST_RESOURCE_OVERLAP")
    for key in keys:
        record = observed["plans"].get(key)
        if record is None or (
            record.get("enabled")
            and previous_scope.get("resources", {}).get(key) != digest(record)
        ):
            raise WriteBlocked("EXISTING_RESOURCE_PROTECTED")
    test_templates = {int(k.split(":")[1]) for k in keys if k.startswith("TEMPLATE:")}
    for card in observed["cards"]:
        if any(n in test_templates for plans in card["plans"] for n in plans) and (
            card["card"] not in designated
            or card["card"] not in previous_scope.get("cards", {})
        ):
            raise WriteBlocked("EXISTING_CARD_REFERENCE_PROTECTED")
    for key, record in observed["plans"].items():
        if (
            key.startswith("TEMPLATE:")
            and key not in keys
            and (
                "WEEK:" + str(record["week"]) in keys
                or any("GROUP:" + str(n) in keys for n in record["groups"] if n)
            )
        ):
            raise WriteBlocked("EXISTING_TEMPLATE_REFERENCE_PROTECTED")
        if (
            key.startswith("GROUP:")
            and key not in keys
            and any("HOLIDAY:" + str(n) in keys for n in record["plans"] if n)
        ):
            raise WriteBlocked("EXISTING_HOLIDAY_REFERENCE_PROTECTED")
    if get_setting(db, scope_key(manifest["controllerId"])).get("enabled"):
        raise WriteBlocked("DEACTIVATE_EXISTING_SCOPE_FIRST")
    scope = {
        k: manifest[k]
        for k in [
            "controllerId",
            "cards",
            "normal",
            "unrestricted",
            "unrestrictedCard",
            "weekdayOrder",
        ]
        if k in manifest
    }
    # Weekday order cannot be inferred from SDK array size. An explicit operator
    # attestation/reference is required before writing a nonuniform weekly plan.
    if scope.get("weekdayOrder") and not manifest.get("weekdayEvidence"):
        raise WriteBlocked("WEEKDAY_EVIDENCE_REQUIRED")
    scope.update(
        enabled=True,
        epoch=str(uuid4()),
        activatedAt=iso(now()),
        actorId=actor.id,
        deviceIdentity=observed["deviceIdentity"],
        templateCapacity=observed.get("templateCapacity"),
        resources={key: digest(observed["plans"][key]) for key in keys},
        weekdayEvidence=manifest.get("weekdayEvidence"),
    )
    set_setting(db, scope_key(manifest["controllerId"]), scope)
    audit(db, actor, "HIKVISION_TEST_SCOPE_ACTIVATED", manifest["controllerId"])
    # Existing pending operations deliberately receive no epoch/review stamp.
    return scope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inventory")
    p.add_argument("--output", type=Path, required=True)
    for name in ["activate", "deactivate", "review"]:
        p = sub.add_parser(name)
        p.add_argument("--actor-email", required=True)
        if name == "activate":
            p.add_argument("--manifest", type=Path, required=True)
        if name == "review":
            p.add_argument("--operation", required=True)
    args = parser.parse_args()
    settings = Settings()
    if settings.controller_mode == "SIMULATED":
        raise WriteBlocked("SELECT_EXPLICIT_HIKVISION_MODE")
    if args.command == "inventory":
        observed = inventory(settings)
        fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(observed, f)
        print(
            json.dumps(
                {
                    "complete": observed["complete"],
                    "cards": len(observed["cards"]),
                    "plans": len(observed["plans"]),
                    "deviceWrites": 0,
                    "unreadableScheduleIds": len(observed["failures"]),
                }
            )
        )
        return
    database = Database(settings)
    try:
        with database.session() as db:
            actor = login_account(
                db,
                args.actor_email,
                getpass("Existing Super Admin password: "),
                SimpleNamespace(client=SimpleNamespace(host="local-hikvision-control")),
            )
            if actor.role != "SUPER_ADMIN":
                raise WriteBlocked("SUPER_ADMIN_REQUIRED")
            db.commit()
            if args.command == "activate":
                manifest = json.loads(args.manifest.read_text())
                observed = inventory(settings)
                domain_lock(db)
                activate(db, settings, actor, manifest, observed)
            else:
                domain_lock(db)
                db.refresh(actor)
                operator = db.get(Person, actor.person_id)
                if (
                    actor.role != "SUPER_ADMIN"
                    or operator.status != "ACTIVE"
                    or operator.purged
                    or operator.deleted_at
                ):
                    raise WriteBlocked("SUPER_ADMIN_REQUIRED")
                key = scope_key(settings.hikvision_controller_id)
                scope = get_setting(db, key)
                if args.command == "deactivate":
                    set_setting(db, key, {**scope, "enabled": False})
                    audit(
                        db,
                        actor,
                        "HIKVISION_TEST_SCOPE_DEACTIVATED",
                        settings.hikvision_controller_id,
                    )
                else:
                    op = db.get(Operation, args.operation)
                    if (
                        not op
                        or op.controller_id != settings.hikvision_controller_id
                        or op.status not in {"PENDING", "FAILED", "UNCERTAIN"}
                    ):
                        raise WriteBlocked("REVIEWABLE_OPERATION_REQUIRED")
                    original = op.data
                    op.data = {**original, "writeEpoch": scope.get("epoch")}
                    current_intent(db, op, scope, now())
                    op.attempts = 0
                    op.status = "PENDING"
                    op.data = {
                        **op.data,
                        "reviewedBy": actor.id,
                        "reviewedAt": iso(now()),
                        "retryAt": None,
                    }
                    audit(db, actor, "HIKVISION_OPERATION_REVIEWED", op.id)
            db.commit()
            print(
                "Local scope updated and audited. No SDK write issued; old backlog remains held unless individually reviewed."
            )
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except (WriteBlocked, ControllerReadUnavailable) as exc:
        raise SystemExit(str(exc)) from None
    except DomainError as exc:
        raise SystemExit(exc.message) from None
    except (ValidationError, SQLAlchemyError, OSError, ValueError, KeyError, TypeError):
        raise SystemExit(
            "Local control failed; check private configuration, manifest and database. No credentials printed."
        ) from None
