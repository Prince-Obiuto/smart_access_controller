"""Scoped real permissions. Simulation and the legacy outbox never enter here.

Activation is a local, authenticated operator action, independent of SDK login.
Every command has a durable intent before transport, followed by a separate GET.
"""

import hashlib
import json
import time
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET
from types import SimpleNamespace

from sqlalchemy import select, text, case

from .common import now, iso, get_setting, set_setting, notify, audit
from .db import domain_lock
from .models import (
    Assignment,
    Card,
    Confirmation,
    Operation,
    Person,
    Schedule,
    CalendarException,
    Account,
    Application,
)
from .controllers import ControllerReadUnavailable
from .hikvision import NativeSession, LAGOS, error_code


class WriteBlocked(Exception):
    pass


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def physical_confirmation(db, assignment, confirmation):
    op = db.scalar(
        select(Operation).where(
            Operation.target_type == "ASSIGNMENT",
            Operation.target_id == assignment.id,
            Operation.controller_id == confirmation.controller_id,
            Operation.version == assignment.version,
            Operation.status == "CONFIRMED",
        )
    )
    proven = bool(
        op
        and op.data.get("simulated") is False
        and op.data.get("readbackVerified")
        and op.desired == confirmation.last_confirmed
    )
    if not proven or op.desired == "DISABLED":
        return proven
    intent = op.data.get("intent", {})
    if intent.get("privileged"):
        return True
    schedule = db.get(Schedule, "normal")
    exceptions = [
        e.data
        for e in db.scalars(select(CalendarException).order_by(CalendarException.date))
    ]
    return (
        intent.get("schedule") == schedule.data
        and intent.get("exceptions") == exceptions
    )


def scope_key(controller_id):
    return "hikvision:write-scope:" + controller_id


def current_intent(db, op, scope, at):
    """Re-evaluate current records; old queued ENABLE is never authoritative."""
    if op.data.get("writeEpoch") != scope.get("epoch") or not scope.get("enabled"):
        raise WriteBlocked("ACTIVATION_REVIEW_REQUIRED")
    if op.target_type == "SCHEDULE":
        schedule = db.get(Schedule, "normal")
        if not schedule or schedule.version != op.version:
            raise WriteBlocked("SUPERSEDED")
        return {
            "kind": "SCHEDULE",
            "schedule": schedule.data,
            "exceptions": [
                e.data
                for e in db.scalars(
                    select(CalendarException).order_by(CalendarException.date)
                )
            ],
        }
    assignment = None
    if op.target_type == "ASSIGNMENT":
        assignment = db.get(Assignment, op.target_id)
        if not assignment or assignment.version != op.version:
            raise WriteBlocked("SUPERSEDED")
        card = db.get(Card, assignment.card_id)
    elif op.target_type in {"POOL", "PRIVILEGE"}:
        card = db.get(Card, op.target_id)
        if op.target_type == "PRIVILEGE":
            assignment = db.scalar(
                select(Assignment).where(
                    Assignment.card_id == op.target_id, Assignment.state == "ACTIVE"
                )
            )
    else:
        # No remote unlock, person record writes, clock or generic SDK tunnel.
        raise WriteBlocked("OPERATION_UNSUPPORTED")
    if not card or card.number not in scope.get("cards", {}):
        raise WriteBlocked("TEST_CARD_NOT_DESIGNATED")
    bound = scope["cards"][card.number]
    if bound.get("kind") != card.kind:
        raise WriteBlocked("CARD_SCOPE_CHANGED")
    disable = op.desired == "DISABLED"
    if op.target_type == "POOL" and not disable:
        raise WriteBlocked("POOL_ENABLE_FORBIDDEN")
    if assignment:
        person = db.get(Person, assignment.person_id)
        if person.id not in bound.get("personIds", []):
            raise WriteBlocked("TEST_PERSON_NOT_DESIGNATED")
        if not disable:
            if (
                card.revoked
                or assignment.state not in {"ACTIVE", "PENDING"}
                or person.purged
                or person.deleted_at
                or person.status != "ACTIVE"
                or assignment.expiry <= at
            ):
                raise WriteBlocked("SUPERSEDED")
            from .service import AccessService

            if not AccessService(
                db, None, SimpleNamespace(controller_mode="HIKVISION_CONTROLLED"), at
            ).old_removed(person.id):
                raise WriteBlocked("OLD_DISABLE_UNCONFIRMED")
    elif not disable:
        raise WriteBlocked("ASSIGNMENT_REQUIRED")
    privileged = bool(card.privileged)
    if op.target_type == "PRIVILEGE":
        owner = (
            db.scalar(select(Account).where(Account.person_id == assignment.person_id))
            if assignment
            else None
        )
        designation = get_setting(db, "pending_designation")
        if (
            not owner
            or owner.role != "SUPER_ADMIN"
            or designation.get("cardId") != card.id
            or scope.get("unrestrictedCard") != card.number
        ):
            raise WriteBlocked("UNRESTRICTED_DESIGNATION_REQUIRED")
        other = db.scalar(
            select(Card).where(
                Card.ever_privileged.is_(True),
                Card.id != card.id,
                Card.disabled.is_(False),
            )
        )
        if other:
            raise WriteBlocked("OLD_DISABLE_UNCONFIRMED")
        privileged = True
    if privileged and scope.get("unrestrictedCard") != card.number:
        raise WriteBlocked("UNRESTRICTED_DESIGNATION_REQUIRED")
    result = {"kind": "CARD", "card": card.number, "enabled": not disable}
    if not disable:
        start = assignment.start.astimezone(LAGOS)
        if start.microsecond:
            start += timedelta(seconds=1)
        start = start.replace(microsecond=0)
        expiry = assignment.expiry.astimezone(LAGOS).replace(microsecond=0)
        if not 1970 <= start.year <= expiry.year <= 2037 or expiry <= start:
            raise WriteBlocked("VALIDITY_RANGE_UNSUPPORTED")
        result.update(
            start=list(start.timetuple()[:6]),
            expiry=list(expiry.timetuple()[:6]),
            privileged=privileged,
            schedule=db.get(Schedule, "normal").data,
            exceptions=[
                e.data
                for e in db.scalars(
                    select(CalendarException).order_by(CalendarException.date)
                )
            ],
        )
    return result


def card_matches(record, intent, plan=None):
    if record is None:
        return not intent["enabled"]
    if record.get("card") != intent["card"]:
        return False
    doors = record.get("doors", [])
    if not intent["enabled"]:
        return len(doors) == 256 and not any(doors) and record.get("type") == 3
    plans = record.get("plans", [])
    return (
        doors == [1] + [0] * 255
        and record.get("valid") == 1
        and record.get("type") == 1
        and record.get("leader") == 0
        and record.get("validityEnabled") == 1
        and record.get("beginFlag") == 1
        and record.get("endFlag") == 1
        and record.get("timeType") == 0
        and record.get("start") == intent["start"]
        and record.get("expiry") == intent["expiry"]
        and len(plans) == 256
        and plans[0] == [plan, 0, 0, 0]
        and not any(any(p) for p in plans[1:])
    )


def seconds(value):
    h, m = map(int, value.split(":"))
    if not 0 <= h <= 23 or not 0 <= m <= 59:
        raise WriteBlocked("INVALID_SCHEDULE")
    return h * 3600 + m * 60


def compile_schedule(intent, scope):
    unrestricted = intent.get("privileged", False)
    slots = scope.get("unrestricted" if unrestricted else "normal", {})
    if not slots.get("week") or not slots.get("template"):
        raise WriteBlocked("OWNED_SCHEDULE_REQUIRED")
    # Neither the local header nor its example identifies weekday array origin.
    # An attested mapping is required; do not silently shift weekly eligibility.
    order = scope.get("weekdayOrder")
    if not unrestricted and order not in {"MONDAY_FIRST", "SUNDAY_FIRST"}:
        raise WriteBlocked("WEEKDAY_ORDER_UNVERIFIED")
    schedule = intent["schedule"]
    begin, end = (
        (0, 86400)
        if unrestricted
        else (seconds(schedule["opens"]), seconds(schedule["closes"]))
    )
    days = []
    for sdk_day in range(7):
        app_day = (sdk_day + 1) % 7 if order == "MONDAY_FIRST" else sdk_day
        segment = (
            [1, begin, end, 0, 0]
            if unrestricted or app_day in schedule["weekdays"]
            else [0, 0, 0, 0, 0]
        )
        days.append([segment] + [[0, 0, 0, 0, 0] for _ in range(7)])
    resources = [("WEEK", slots["week"], {"enabled": 1, "days": days})]
    exceptions = [] if unrestricted else intent.get("exceptions", [])
    holiday_ids = slots.get("holidays", [])
    if len(exceptions) > len(holiday_ids) or len(exceptions) > 16:
        raise WriteBlocked("HOLIDAY_CAPACITY_EXCEEDED")
    for ex, plan_id in zip(exceptions, holiday_ids):
        date = list(map(int, ex["date"].split("-")))
        datetime(*date)  # exact calendar validation
        segment = (
            [0, 0, 0, 0, 0]
            if ex["closed"]
            else [1, seconds(ex["opens"]), seconds(ex["closes"]), 0, 0]
        )
        resources.append(
            (
                "HOLIDAY",
                plan_id,
                {
                    "enabled": 1,
                    "start": date,
                    "end": date,
                    "segments": [segment] + [[0, 0, 0, 0, 0] for _ in range(7)],
                },
            )
        )
    groups = [0] * 16
    if exceptions:
        if not slots.get("group"):
            raise WriteBlocked("OWNED_HOLIDAY_GROUP_REQUIRED")
        resources.append(
            (
                "GROUP",
                slots["group"],
                {
                    "enabled": 1,
                    "plans": holiday_ids[: len(exceptions)]
                    + [0] * (16 - len(exceptions)),
                },
            )
        )
        groups[0] = slots["group"]
    resources.append(
        (
            "TEMPLATE",
            slots["template"],
            {
                "enabled": 1,
                "name": "IFT TEST " + str(slots["template"]),
                "week": slots["week"],
                "groups": groups,
            },
        )
    )
    return resources, slots["template"]


def plan_fields(kind, number, expected):
    fields = [kind, number]
    if kind == "WEEK":
        fields += [
            x for day in expected["days"] for segment in day for x in segment[:3]
        ]
    elif kind == "HOLIDAY":
        fields += expected["start"] + [
            x for segment in expected["segments"] for x in segment[:3]
        ]
    elif kind == "GROUP":
        fields += expected["plans"]
    elif kind == "TEMPLATE":
        fields += [expected["week"], *expected["groups"]]
    return fields


class PermissionTransport:
    def __init__(self, settings, scope):
        self.session = NativeSession(settings, permissions=True)
        self.scope = scope
        self.deadline = time.monotonic() + 60

    def request(self, name, fields):
        if time.monotonic() >= self.deadline:
            raise ControllerReadUnavailable("PERMISSION_UNCERTAIN")
        rows, end = self.session.permission_request(name, fields)
        if end.get("error") == 1903 and name == "CARD_READ":
            return []  # Explicit vendor CARDNO_NOT_EXIST; never infer from timeout.
        if end.get("code") != "OK":
            raise WriteBlocked(
                error_code(end["error"])
                if end.get("error")
                else end.get("code", "UNCERTAIN")
            )
        return rows

    def start(self):
        self.session.start()
        if (
            self.session.identity != self.scope.get("deviceIdentity")
            or self.session.metadata.get("model") != "DS-K2802"
            or not self.session.metadata.get("clockVerified")
        ):
            raise WriteBlocked("DEVICE_IDENTITY_OR_CLOCK_UNVERIFIED")
        caps = self.session.metadata.get("permissionCapabilities", {})
        plan_caps = ET.fromstring(caps.get("CardRightPlan", "<missing/>"))
        capacity = plan_caps.find("PlanTemplate/templateNo")
        if capacity is None or int(capacity.get("max", "0")) != self.scope.get(
            "templateCapacity"
        ):
            raise WriteBlocked("TEMPLATE_CAPACITY_UNVERIFIED")
        card = ET.fromstring(caps.get("Card", "<missing/>"))
        modify = card.find("modifyParamType")
        unit = card.find("cardValidUnit")
        card_type = card.find("cardType")
        if (
            modify is None
            or not {
                "doorright",
                "validtime",
                "cardvalid",
                "cardtype",
                "rightplan",
                "leadercard",
            }.issubset(set(modify.get("opt", "").split(",")))
            or unit is None
            or "second" not in unit.get("opt", "").split(",")
            or "CardRightPlan" not in caps
            or card_type is None
            or not {"normalcard", "blacklistcard"}.issubset(
                set(card_type.get("opt", "").split(","))
            )
        ):
            raise WriteBlocked("REQUIRED_PERMISSION_CAPABILITY_UNSUPPORTED")
        plans = [int(k.split(":")[1]) for k in self.scope.get("resources", {})]
        cards = list(self.scope.get("cards", {}))
        self.request(
            "AUTHORIZE", [len(cards), *cards, len(set(plans)), *sorted(set(plans))]
        )

    def read_card(self, number):
        rows = self.request("CARD_READ", [number])
        if len(rows) > 1 or rows and rows[0].get("card") != number:
            raise WriteBlocked("CARD_READBACK_IDENTITY_MISMATCH")
        return rows[0] if rows else None

    def read_plan(self, kind, number):
        rows = self.request("PLAN_READ", [kind, number])
        if len(rows) != 1:
            raise WriteBlocked("PLAN_READBACK_MISSING")
        return rows[0]

    def verify_ownership(self):
        cards = self.request("CARD_READ", ["ALL"])
        templates = {
            int(k.split(":")[1])
            for k in self.scope["resources"]
            if k.startswith("TEMPLATE:")
        }
        for card in cards:
            if card["card"] not in self.scope["cards"] and any(
                n in templates for plans in card["plans"] for n in plans
            ):
                raise WriteBlocked("UNMANAGED_CARD_REFERENCES_TEST_PLAN")
        # Full byte-addressable legacy template namespace; reject unknown errors.
        capacity = self.scope.get("templateCapacity", 0)
        if not 1 <= capacity <= 255:
            raise WriteBlocked("TEMPLATE_CAPACITY_UNVERIFIED")
        for number in range(1, capacity + 1):
            if time.monotonic() >= self.deadline:
                raise ControllerReadUnavailable("PERMISSION_UNCERTAIN")
            rows, end = self.session.permission_request(
                "PLAN_READ", ["TEMPLATE", number]
            )
            if end.get("code") != "OK" or len(rows) != 1:
                raise WriteBlocked("TEMPLATE_INVENTORY_INCOMPLETE")
            record = rows[0]
            if number not in templates and (
                "WEEK:" + str(record["week"]) in self.scope["resources"]
                or any(
                    "GROUP:" + str(n) in self.scope["resources"]
                    for n in record["groups"]
                    if n
                )
            ):
                raise WriteBlocked("UNMANAGED_TEMPLATE_REFERENCES_TEST_PLAN")
        for number in range(1, 65):
            record = self.read_plan("GROUP", number)
            if "GROUP:" + str(number) not in self.scope["resources"] and any(
                "HOLIDAY:" + str(n) in self.scope["resources"]
                for n in record["plans"]
                if n
            ):
                raise WriteBlocked("UNMANAGED_GROUP_REFERENCES_TEST_PLAN")

    def write_plan(self, kind, number, expected):
        self.request("PLAN_WRITE", plan_fields(kind, number, expected))

    def write_card(self, intent, plan):
        self.request(
            "CARD_WRITE",
            [
                intent["card"],
                int(intent["enabled"]),
                plan or 0,
                *(
                    intent.get("start", []) + intent.get("expiry", [])
                    if intent["enabled"]
                    else []
                ),
            ],
        )

    def close(self):
        self.session.close()


def reconcile(transport, intent, scope, before_write, resource_confirmed):
    """Always GET first, including every retry after timeout/process interruption."""
    plan = None
    if intent["kind"] == "SCHEDULE" or intent.get("enabled"):
        transport.verify_ownership()
        resources, plan = compile_schedule(intent, scope)
        for kind, number, expected in resources:
            key = kind + ":" + str(number)
            baseline = scope.get("resources", {}).get(key)
            if not baseline:
                raise WriteBlocked("RESOURCE_NOT_APPLICATION_OWNED")
            actual = transport.read_plan(kind, number)
            if actual != expected:
                if digest(actual) not in {
                    baseline,
                    scope.get("preparedResources", {}).get(key),
                }:
                    # Interrupted writes can only adopt the exact durable intent.
                    raise WriteBlocked("UNMANAGED_SCHEDULE_CHANGE")
                before_write("PLAN", {"key": key, "expected": expected})
                transport.write_plan(kind, number, expected)
                actual = transport.read_plan(kind, number)
                if actual != expected:
                    raise WriteBlocked("SCHEDULE_READBACK_MISMATCH")
            resource_confirmed(key, digest(actual))
            scope["resources"][key] = digest(actual)
    if intent["kind"] == "SCHEDULE":
        return {"scheduleHash": digest(intent), "readbackVerified": True}
    actual = transport.read_card(intent["card"])
    if not card_matches(actual, intent, plan):
        before_write("CARD", intent)
        transport.write_card(intent, plan)
        actual = transport.read_card(intent["card"])
        if not card_matches(actual, intent, plan):
            raise WriteBlocked("CARD_READBACK_MISMATCH")
    return {
        "cardHash": digest(actual),
        "scheduleHash": digest(intent.get("schedule")),
        "readbackVerified": True,
    }


def process_controlled(db, settings, at=None, transport_factory=None):
    if settings.controller_mode != "HIKVISION_CONTROLLED":
        return
    at = at or now()
    transport_factory = transport_factory or PermissionTransport
    controller_id = settings.hikvision_controller_id
    # Separate session-level lock, held on a pinned connection across commits.
    # It never blocks workflow/event transactions while native I/O is running.
    lock_id = int.from_bytes(
        hashlib.sha256(("ift-write:" + controller_id).encode()).digest()[:8],
        "big",
        signed=True,
    )
    db.commit()
    with db.get_bind().connect() as lock:
        if not lock.execute(
            text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id}
        ).scalar():
            return
        try:
            domain_lock(db)
            scope = get_setting(db, scope_key(controller_id))
            if not scope.get("enabled"):
                db.rollback()
                return
            candidates = list(
                db.scalars(
                    select(Operation)
                    .where(
                        Operation.controller_id == controller_id,
                        Operation.data["writeEpoch"].as_string() == scope["epoch"],
                        Operation.status.in_(["PENDING", "UNCERTAIN", "FAILED"]),
                        Operation.attempts < 5,
                    )
                    .order_by(
                        case((Operation.desired == "DISABLED", 0), else_=1),
                        Operation.created_at,
                        Operation.id,
                    )
                )
            )
            op = next(
                (
                    o
                    for o in candidates
                    if not o.data.get("retryAt")
                    or datetime.fromisoformat(o.data["retryAt"]) <= at
                ),
                None,
            )
            if not op:
                db.rollback()
                return
            try:
                intent = current_intent(db, op, scope, at)
            except WriteBlocked as exc:
                op.status = "SUPERSEDED" if str(exc) == "SUPERSEDED" else "FAILED"
                op.data = {
                    **op.data,
                    "simulated": False,
                    "message": str(exc),
                    "readbackVerified": False,
                    "retryAt": iso(at + timedelta(minutes=5)),
                }
                op.attempts = 5  # policy blocks require a fresh, reviewed intent
                if op.desired == "DISABLED":
                    notify(
                        db,
                        "Controller disable not confirmed",
                        str(exc),
                        "CRITICAL",
                        at=at,
                    )
                db.commit()
                return
            op_id, version = op.id, op.version
            op.status = "UNCERTAIN"
            op.attempts += 1
            op.data = {
                **op.data,
                "intent": intent,
                "intentHash": digest(intent),
                "simulated": False,
                "message": "Readback reconciliation in progress",
                "readbackVerified": False,
            }
            db.commit()

            def validate_claim():
                domain_lock(db)
                db.expire_all()
                current = db.get(Operation, op_id)
                active_scope = get_setting(db, scope_key(controller_id))
                if active_scope.get("epoch") != scope["epoch"] or not active_scope.get(
                    "enabled"
                ):
                    raise WriteBlocked("ACTIVATION_REVOKED")
                if current.version != version or digest(
                    current_intent(db, current, active_scope, now())
                ) != digest(intent):
                    raise WriteBlocked("SUPERSEDED")
                return current, active_scope

            def before_write(kind, payload):
                current, active_scope = validate_claim()
                if current.desired != "DISABLED" and db.scalar(
                    select(Operation.id)
                    .where(
                        Operation.controller_id == controller_id,
                        Operation.desired == "DISABLED",
                        Operation.status.in_(["PENDING", "UNCERTAIN"]),
                        Operation.data["writeEpoch"].as_string() == scope["epoch"],
                    )
                    .limit(1)
                ):
                    raise WriteBlocked("REVOCATION_PRIORITY_RETRY")
                current.data = {
                    **current.data,
                    "lastCommand": {"kind": kind, "payloadHash": digest(payload)},
                    "sentAt": iso(now()),
                }
                if kind == "PLAN":
                    set_setting(
                        db,
                        scope_key(controller_id),
                        {
                            **active_scope,
                            "preparedResources": {
                                **active_scope.get("preparedResources", {}),
                                payload["key"]: digest(payload["expected"]),
                            },
                        },
                    )
                db.commit()  # durable before SDK; no workflow lock during native I/O

            def resource_confirmed(key, value):
                current, active_scope = validate_claim()
                active_scope = {
                    **active_scope,
                    "resources": {**active_scope["resources"], key: value},
                }
                active_scope["preparedResources"] = {
                    k: v
                    for k, v in active_scope.get("preparedResources", {}).items()
                    if k != key
                }
                set_setting(db, scope_key(controller_id), active_scope)
                db.commit()

            transport = transport_factory(settings, scope)
            try:
                transport.start()
                evidence = reconcile(
                    transport, intent, scope, before_write, resource_confirmed
                )
                op, _ = validate_claim()
                op.status = "CONFIRMED"
                op.completed_at = now()
                op.data = {
                    **op.data,
                    **evidence,
                    "simulated": False,
                    "message": "Device permissions verified by SDK readback",
                    "confirmedState": op.desired,
                }
                if op.target_type == "ASSIGNMENT":
                    assignment = db.get(Assignment, op.target_id)
                    confirmation = db.scalar(
                        select(Confirmation).where(
                            Confirmation.assignment_id == assignment.id,
                            Confirmation.controller_id == controller_id,
                        )
                    )
                    if confirmation and confirmation.version == version:
                        confirmation.state = "SYNCHRONIZED"
                        confirmation.last_confirmed = op.desired
                        confirmation.confirmed_at = op.completed_at
                        db.flush()
                        from .operations import finalize_assignment

                        finalize_assignment(db, assignment, op.completed_at, settings)
                elif op.target_type == "POOL":
                    group = list(
                        db.scalars(
                            select(Operation).where(
                                Operation.target_type == "POOL",
                                Operation.target_id == op.target_id,
                                Operation.version == version,
                            )
                        )
                    )
                    if group and all(
                        o.status == "CONFIRMED"
                        and o.data.get("readbackVerified")
                        and o.data.get("simulated") is False
                        for o in group
                    ):
                        card = db.get(Card, op.target_id)
                        card.disabled = True
                        card.pool_state = "AVAILABLE_DISABLED"
                elif op.target_type == "PRIVILEGE":
                    group = list(
                        db.scalars(
                            select(Operation).where(
                                Operation.target_type == "PRIVILEGE",
                                Operation.target_id == op.target_id,
                                Operation.version == version,
                            )
                        )
                    )
                    if group and all(
                        o.status == "CONFIRMED"
                        and o.data.get("readbackVerified")
                        and o.data.get("simulated") is False
                        for o in group
                    ):
                        card = db.get(Card, op.target_id)
                        card.privileged = True
                        card.ever_privileged = True
                        set_setting(db, "pending_designation", {})
                # A controlled test schedule is never global confirmation for all cards.
                audit(
                    db,
                    db.get(Account, op.actor_id) if op.actor_id else None,
                    "HIKVISION_" + op.desired,
                    op.target_id,
                    "CONFIRMED",
                )
                from .service import AccessService
                from .security import DomainError

                for application in db.scalars(
                    select(Application).where(
                        Application.state == "ISSUANCE_PENDING",
                        Application.assignment_id.is_(None),
                        Application.requested_number.is_not(None),
                    )
                ):
                    issuer = db.get(Account, application.issuer_id)
                    service = AccessService(db, issuer, settings)
                    if issuer and service.old_removed(application.person_id):
                        try:
                            service.issue(application, application.requested_number)
                        except DomainError:
                            pass
                db.commit()
            except (WriteBlocked, ControllerReadUnavailable) as exc:
                db.rollback()
                domain_lock(db)
                db.expire_all()
                op = db.get(Operation, op_id)
                code = str(exc)
                op.status = (
                    "SUPERSEDED"
                    if code == "SUPERSEDED"
                    else "UNCERTAIN"
                    if code
                    in {"UNCERTAIN", "PERMISSION_UNCERTAIN", "PERMISSION_TIMEOUT"}
                    else "FAILED"
                )
                op.data = {
                    **op.data,
                    "message": code,
                    "readbackVerified": False,
                    "simulated": False,
                    "retryAt": iso(
                        now() + timedelta(seconds=min(300, 5 * 2**op.attempts))
                    ),
                }
                if code in {
                    "AUTHENTICATION_FAILED",
                    "ACCOUNT_LOCKED",
                    "AUTHENTICATION_STOPPED",
                    "UNSUPPORTED",
                    "REQUIRED_PERMISSION_CAPABILITY_UNSUPPORTED",
                    "UNMANAGED_SCHEDULE_CHANGE",
                    "DEVICE_IDENTITY_OR_CLOCK_UNVERIFIED",
                }:
                    op.attempts = 5
                if op.desired == "DISABLED":
                    notify(
                        db,
                        "Controller disable not confirmed",
                        code,
                        "CRITICAL",
                        at=now(),
                    )
                if op.target_type == "ASSIGNMENT":
                    c = db.scalar(
                        select(Confirmation).where(
                            Confirmation.assignment_id == op.target_id,
                            Confirmation.controller_id == controller_id,
                        )
                    )
                    if c and c.version == version:
                        c.state = "FAILED"
                        db.get(Assignment, op.target_id).sync = "FAILED"
                db.commit()
            finally:
                transport.close()
        finally:
            db.rollback()
            lock.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
