"""Explicit web management: fresh previews, durable intents, typed SDK readback.

This path never opens the legacy scope or adopts a backlog. Each confirmation
names one preview. Controller I/O runs in FastAPI's synchronous worker threads.
"""

import copy
import hashlib
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from xml.etree import ElementTree as ET

from sqlalchemy import select

from .common import audit, get_setting, iso, now, set_setting
from .controllers import ControllerReadUnavailable
from .db import domain_lock
from .hikvision import NativeSession
from .hikvision_existing import DEVICE
from .hikvision_unlock import command_lock
from .hikvision_writes import WriteBlocked, current_intent, digest
from .models import (
    AccessEvent,
    Account,
    Application,
    Assignment,
    Card,
    Confirmation,
    Operation,
    Person,
    Visit,
)
from .security import DomainError, require

REVOKED = {"1051305927", "1052625607"}
KINDS = {"WEEK", "TEMPLATE", "GROUP", "HOLIDAY"}


def authorize(db, actor, settings, privileged=False):
    require(db, actor, "settings" if privileged else "issue", settings)
    if settings.controller_mode != "HIKVISION_CONTROLLED":
        raise DomainError(
            "NOT_CONNECTED",
            "Live controller management requires Hikvision controlled mode.",
            409,
        )


def number(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4,20}", value):
        raise DomainError(
            "VALIDATION_ERROR", "Use the complete card number as a string."
        )
    return value


def public(record):
    if record is None:
        return None
    return {k: v for k, v in record.items() if k != "nativeHex"}


class Transport:
    def __init__(self, settings):
        self.session = NativeSession(settings, permissions=True)
        self.deadline = time.monotonic() + 120
        self.last_response = None

    def start(self):
        self.session.start()
        meta = self.session.metadata
        if (
            self.session.identity
            != hashlib.sha256(DEVICE["serial"].encode()).hexdigest()
            or meta.get("model") != DEVICE["model"]
            or meta.get("firmwareRaw") != DEVICE["firmware"]
            or meta.get("buildDateRaw") != DEVICE["build"]
            or not meta.get("clockVerified")
        ):
            raise WriteBlocked("DEVICE_IDENTITY_OR_CLOCK_UNVERIFIED")

    def request(self, action, fields):
        if time.monotonic() >= self.deadline:
            raise WriteBlocked("MANAGEMENT_READ_DEADLINE_EXCEEDED")
        try:
            rows, end = self.session.permission_request(action, fields)
        except ControllerReadUnavailable as exc:
            self.last_response = {
                "command": action,
                "verb": fields[0] if fields else None,
                "error": str(exc),
            }
            raise
        self.last_response = {
            "command": action,
            "verb": fields[0] if fields else None,
            "end": end,
            "rows": rows if end.get("code") != "OK" else [],
            "rowCount": len(rows),
        }
        if (
            end.get("error") == 1903
            and action == "MANAGE"
            and fields[:2] == ["READ", "CARD"]
            and len(fields) == 3
            and fields[2] != "ALL"
            and not rows
        ):
            return []
        if end.get("code") != "OK":
            raise WriteBlocked(f"{end.get('code', 'UNCERTAIN')}:{end.get('error', 0)}")
        return rows

    def card(self, value):
        rows = self.request("MANAGE", ["READ", "CARD", value])
        if value == "ALL":
            if len({r.get("card") for r in rows}) != len(rows):
                raise WriteBlocked("CARD_INVENTORY_DUPLICATES")
            return rows
        if len(rows) > 1 or rows and rows[0].get("card") != value:
            raise WriteBlocked("CARD_READBACK_IDENTITY_MISMATCH")
        return rows[0] if rows else None

    def plan(self, kind, slot):
        rows = self.request("MANAGE", ["READ", kind, slot])
        if len(rows) != 1:
            raise WriteBlocked("PLAN_READBACK_MISSING")
        return rows[0]

    def plan_capability(self, kind, slot, proposed):
        self.capability()
        caps = ET.fromstring(
            self.session.metadata["permissionCapabilities"]["CardRightPlan"]
        )
        paths = {
            "WEEK": ("WeekPlan/weekPlanNo", "WeekPlan/maxDaySegment"),
            "HOLIDAY": ("HolidayPlan/holidayPlanNo", "HolidayPlan/maxDaySegment"),
            "GROUP": ("HolidayGroup/holidayGroupNo", "HolidayGroup/maxHolidayPlanNum"),
            "TEMPLATE": ("PlanTemplate/templateNo", "PlanTemplate/maxHolidayGroupNum"),
        }
        range_node = caps.find(paths[kind][0])
        if range_node is not None and not int(
            range_node.get("min", "1")
        ) <= slot <= int(range_node.get("max", "0")):
            raise WriteBlocked("SLOT_OUTSIDE_ADVERTISED_CARD_ACCESS_RANGE")
        if kind != "TEMPLATE" and range_node is None:
            raise WriteBlocked("CARD_ACCESS_RANGE_NOT_ADVERTISED")
        count = caps.find(paths[kind][1])
        maximum = (
            int(count.text)
            if count is not None and count.text and count.text.strip().isdigit()
            else int(count.get("max", "0"))
            if count is not None
            else 0
        )
        r = proposed["record"]
        needed = (
            max(sum(bool(s[0]) for s in day) for day in r["days"])
            if kind == "WEEK"
            else sum(bool(s[0]) for s in r["segments"])
            if kind == "HOLIDAY"
            else sum(bool(n) for n in r["plans"] if n)
            if kind == "GROUP"
            else sum(bool(n) for n in r["groups"])
        )
        if maximum <= 0 or needed > maximum:
            raise WriteBlocked("PLAN_LINK_OR_INTERVAL_CAPABILITY_UNVERIFIED")

    def graph(self, extra=()):
        cards = self.card("ALL")
        resources = {}
        pending = list(extra) + [
            ("TEMPLATE", n) for c in cards for ds in c["plans"] for n in ds if n
        ]
        while pending:
            kind, slot = pending.pop()
            key = f"{kind}:{slot}"
            if key in resources:
                continue
            if len(resources) >= 512:
                raise WriteBlocked("REFERENCE_GRAPH_LIMIT_REACHED")
            value = self.plan(kind, slot)
            resources[key] = value
            r = value["record"]
            if kind == "TEMPLATE":
                if r["week"]:
                    pending.append(("WEEK", r["week"]))
                pending.extend(("GROUP", n) for n in r["groups"] if n)
            elif kind == "GROUP":
                pending.extend(("HOLIDAY", n) for n in r["plans"] if n)
        return {
            "cards": sorted(cards, key=lambda c: c["card"]),
            "resources": resources,
            "doors": {str(d): self.plan("DOOR", d) for d in (1, 2)},
        }

    def capability(self, card=False):
        caps = self.session.metadata.get("permissionCapabilities", {})
        if card:
            root = ET.fromstring(caps.get("Card", "<missing/>"))

            def options(tag):
                node = root.find(tag)
                return (
                    set(node.get("opt", "").split(",")) if node is not None else set()
                )

            if (
                not {
                    "doorright",
                    "validtime",
                    "cardvalid",
                    "cardtype",
                    "rightplan",
                    "leadercard",
                }
                <= options("modifyParamType")
                or "second" not in options("cardValidUnit")
                or not {"normalcard", "blacklistcard"} <= options("cardType")
            ):
                raise WriteBlocked("REQUIRED_PERMISSION_CAPABILITY_UNSUPPORTED")
        elif not caps.get("CardRightPlan"):
            raise WriteBlocked("CARD_ACCESS_PLAN_CAPABILITY_UNAVAILABLE")

    def close(self):
        self.session.close()


@contextmanager
def connected(settings):
    t = Transport(settings)
    try:
        t.start()
        yield t
    except ControllerReadUnavailable as exc:
        raise WriteBlocked(str(exc)) from None
    finally:
        t.close()


def local_intent(db, op, actor, settings, template):
    authorize(db, actor, settings, op.target_type == "PRIVILEGE")
    if op.controller_id != settings.hikvision_controller_id or op.status not in {
        "PENDING",
        "FAILED",
        "UNCERTAIN",
        "CONFIRMED",
    }:
        raise WriteBlocked("CURRENT_CARD_OPERATION_REQUIRED")
    a = db.get(Assignment, op.target_id) if op.target_type == "ASSIGNMENT" else None
    c = (
        db.get(Card, a.card_id if a else op.target_id)
        if op.target_type in {"ASSIGNMENT", "POOL", "PRIVILEGE"}
        else None
    )
    if not c:
        raise WriteBlocked("CARD_OPERATION_REQUIRED")
    if op.target_type in {"POOL", "PRIVILEGE"}:
        latest = db.scalar(
            select(Operation.id)
            .where(
                Operation.controller_id == op.controller_id,
                Operation.target_type == "POOL",
                Operation.target_id == op.target_id,
            )
            .order_by(Operation.created_at.desc(), Operation.version.desc())
            .limit(1)
        )
        if latest != op.id:
            raise WriteBlocked("SUPERSEDED")
    if not a and op.target_type == "PRIVILEGE":
        a = db.scalar(
            select(Assignment).where(
                Assignment.card_id == c.id, Assignment.state == "ACTIVE"
            )
        )
    if op.desired != "DISABLED":
        if c.revoked or c.number in REVOKED:
            raise WriteBlocked("PERMANENTLY_REVOKED_CARD")
        if not a:
            raise WriteBlocked("ASSIGNMENT_REQUIRED")
        person = db.get(Person, a.person_id)
        account = db.scalar(select(Account).where(Account.person_id == a.person_id))
        if (
            not account
            or person.status != "ACTIVE"
            or person.deleted_at
            or person.purged
        ):
            raise WriteBlocked("ACTIVE_ACCOUNT_REQUIRED")
        if c.kind == "VISITOR":
            v = db.scalar(select(Visit).where(Visit.assignment_id == a.id))
            if (
                not v
                or v.state not in {"PENDING", "ACTIVE"}
                or not v.approval
                or v.approval.get("action") != "EXTERNAL_APPROVAL_CONFIRMED"
                or (v.start, v.expiry, v.person_id) != (a.start, a.expiry, a.person_id)
            ):
                raise WriteBlocked("NORMAL_VISITOR_APPROVAL_REQUIRED")
        else:
            app = db.scalar(
                select(Application).where(Application.assignment_id == a.id)
            )
            if (
                not app
                or app.state not in {"ISSUANCE_PENDING", "ISSUED"}
                or not app.evidence_id
                or not app.reviewer_id
            ):
                raise WriteBlocked("NORMAL_PAID_ISSUANCE_REQUIRED")
    # Reuse the ordinary expiry/replacement/designation guards with an ephemeral
    # in-memory scope. Never persist it or stamp unrelated operations.
    scope = {
        "enabled": True,
        "epoch": "web-preview",
        "cards": {c.number: {"kind": c.kind, "personIds": [a.person_id] if a else []}},
        "unrestrictedCard": c.number
        if c.privileged or op.target_type == "PRIVILEGE"
        else None,
    }
    proxy = SimpleNamespace(
        **{
            key: getattr(op, key)
            for key in ("target_type", "target_id", "version", "desired")
        },
        data={"writeEpoch": "web-preview"},
    )
    if c.privileged or op.target_type == "PRIVILEGE":
        authorize(db, actor, settings, True)
    intent = current_intent(db, proxy, scope, now())
    intent.pop("schedule", None)
    intent.pop("exceptions", None)
    intent["existingTemplate"] = template
    return intent


def operation_view(op):
    d = op.data
    return {
        "id": op.id,
        "status": d.get("managementState", op.status),
        "message": d.get("message", "Review required"),
        "readbackVerified": bool(d.get("readbackVerified")),
        "validityCompatibilityUsed": bool(d.get("validityCompatibilityUsed")),
        "actual": public(d.get("actual"))
        if d.get("kind") == "CARD"
        else (d.get("actual") or {}).get("record"),
        "readbackOnly": bool(d.get("readbackOnly")),
        "kind": d.get("kind"),
        "slot": d.get("slot"),
        "sourceOperationId": d.get("sourceOperationId"),
        "before": d.get("beforePublic"),
        "proposed": d.get("proposedPublic"),
        "affected": d.get("affected", []),
        "expiresAt": d.get("expiresAt"),
        "limitations": [
            "Total template capacity remains unknown. Only successfully read slots and current-card references are inspected.",
            "External iVMS edits must pause while applying a reviewed change.",
        ],
    }


def read_card(db, settings, actor, value):
    authorize(db, actor, settings)
    number(value)
    db.commit()
    with connected(settings) as t:
        r = t.card(value)
    c = db.scalar(select(Card).where(Card.number == value))
    a = (
        db.scalar(
            select(Assignment).where(
                Assignment.card_id == c.id, Assignment.ended_at.is_(None)
            )
        )
        if c
        else None
    )
    return {
        "card": value,
        "readAt": iso(now()),
        "record": public(r),
        "local": {
            "cardId": c.id,
            "revoked": c.revoked,
            "disabled": c.disabled,
            "assignmentId": a.id if a else None,
            "personId": a.person_id if a else None,
            "start": iso(a.start) if a else None,
            "expiry": iso(a.expiry) if a else None,
        }
        if c
        else None,
    }


def read_plan(db, settings, actor, kind, slot):
    authorize(db, actor, settings, True)
    if kind not in KINDS or not 1 <= slot <= 65535:
        raise WriteBlocked("SPECIFIC_SUPPORTED_SLOT_REQUIRED")
    db.commit()
    with connected(settings) as t:
        value = t.plan(kind, slot)
    domain_lock(db)
    set_setting(
        db,
        f"hikvision:observed:{settings.hikvision_controller_id}:{kind}:{slot}",
        {**value, "readAt": iso(now())},
    )
    audit(db, actor, "CONTROLLER_PLAN_REFRESH", f"{kind}:{slot}")
    db.commit()
    return {
        "kind": kind,
        "slot": slot,
        "record": value["record"],
        "revision": digest(value),
        "readAt": iso(now()),
        "weekdayOrder": get_setting(db, "hikvision:weekday-evidence").get(
            "indexToWeekday"
        ),
    }


def validate_segments(segments, count):
    if not isinstance(segments, list) or len(segments) != count:
        raise WriteBlocked("INVALID_INTERVAL_COUNT")
    for s in segments:
        if not isinstance(s, list) or len(s) != 3 or any(type(x) is not int for x in s):
            raise WriteBlocked("INVALID_INTERVAL")
        enabled, begin, end = s
        if (
            enabled not in (0, 1)
            or begin % 60
            or end % 60
            or not 0 <= begin <= 86399
            or not 0 <= end <= 86400
            or enabled
            and begin >= end
        ):
            raise WriteBlocked("INVALID_INTERVAL")
    active = sorted((s[1], s[2]) for s in segments if s[0])
    if any(a[1] > b[0] for a, b in zip(active, active[1:])):
        raise WriteBlocked("OVERLAPPING_INTERVALS")
    return [x for s in segments for x in s]


def plan_payload(db, kind, desired):
    allowed = {
        "WEEK": {"enabled", "days"},
        "HOLIDAY": {"enabled", "start", "end", "segments"},
        "GROUP": {"enabled", "plans"},
        "TEMPLATE": {"enabled", "week", "groups"},
    }[kind]
    if (
        set(desired) != allowed
        or type(desired["enabled"]) is not int
        or desired["enabled"] not in (0, 1)
    ):
        raise WriteBlocked("INVALID_PLAN_FIELDS")
    if kind == "WEEK":
        days = desired["days"]
        if not isinstance(days, list) or len(days) != 7:
            raise WriteBlocked("SEVEN_LABELLED_DAYS_REQUIRED")
        mapping = get_setting(db, "hikvision:weekday-evidence").get("indexToWeekday")
        if any(d != days[0] for d in days[1:]):
            if not mapping or sorted(mapping) != list(range(7)):
                raise WriteBlocked("WEEKDAY_ORDER_UNVERIFIED")
            days = [days[n] for n in mapping]
        return [x for day in days for x in validate_segments(day, 8)]
    if kind == "HOLIDAY":
        try:
            start, end = datetime(*desired["start"]), datetime(*desired["end"])
            if not 1970 <= start.year <= end.year <= 2037 or end < start:
                raise ValueError()
        except (ValueError, TypeError):
            raise WriteBlocked("INVALID_HOLIDAY_DATES") from None
        return (
            desired["start"]
            + desired["end"]
            + validate_segments(desired["segments"], 8)
        )
    values = desired["plans"] if kind == "GROUP" else desired["groups"]
    if (
        not isinstance(values, list)
        or len(values) != 16
        or any(type(n) is not int or not 0 <= n <= 65535 for n in values)
    ):
        raise WriteBlocked("INVALID_LINKED_SLOTS")
    if kind == "TEMPLATE" and (
        type(desired["week"]) is not int or not 1 <= desired["week"] <= 64
    ):
        raise WriteBlocked("INVALID_WEEK_SLOT")
    return values if kind == "GROUP" else [desired["week"], *values]


def references(graph, kind, slot):
    def reaches(template):
        key = f"TEMPLATE:{template}"
        if kind == "TEMPLATE":
            return slot == template
        r = graph["resources"].get(key, {}).get("record", {})
        if kind == "WEEK":
            return r.get("week") == slot
        if kind == "GROUP":
            return slot in r.get("groups", [])
        return any(
            slot
            in graph["resources"]
            .get(f"GROUP:{n}", {})
            .get("record", {})
            .get("plans", [])
            for n in r.get("groups", [])
            if n
        )

    return [
        {
            "card": c["card"],
            "doors": [
                i + 1
                for i, ps in enumerate(c["plans"])
                if c["doors"][i] and any(reaches(n) for n in ps if n)
            ],
        }
        for c in graph["cards"]
        if any(
            c["doors"][i] and any(reaches(n) for n in ps if n)
            for i, ps in enumerate(c["plans"])
        )
    ]


def save_preview(db, settings, actor, key, data):
    if not key or len(key) > 100:
        raise DomainError("VALIDATION_ERROR", "An idempotency key is required.")
    domain_lock(db)
    existing = db.scalar(
        select(Operation).where(
            Operation.target_type == "MANAGEMENT",
            Operation.actor_id == actor.id,
            Operation.data["requestKey"].as_string() == key,
        )
    )
    if existing:
        if existing.data.get("requestHash") != data["requestHash"]:
            raise DomainError(
                "CONFLICT", "This request key was used for different changes.", 409
            )
        return operation_view(existing)
    op = Operation(
        controller_id=settings.hikvision_controller_id,
        target_type="MANAGEMENT",
        target_id=str(uuid4()),
        desired="UPDATE",
        version=1,
        status="PENDING",
        actor_id=actor.id,
        created_at=now(),
        data={
            **data,
            "requestKey": key,
            "expiresAt": iso(now() + timedelta(minutes=10)),
            "simulated": False,
            "readbackVerified": False,
        },
    )
    db.add(op)
    audit(db, actor, "CONTROLLER_MANAGEMENT_PREVIEW", op.target_id)
    db.commit()
    return operation_view(op)


def preview_card(db, settings, actor, source_id, template, reconcile, reason, key):
    authorize(db, actor, settings)
    if get_setting(db, scope_key_legacy(settings)).get("enabled"):
        raise WriteBlocked("CLOSE_LEGACY_AUTHORIZATION_FIRST")
    op = db.get(Operation, source_id)
    if not op:
        raise WriteBlocked("OPERATION_NOT_FOUND")
    intent = local_intent(db, op, actor, settings, template)
    db.commit()
    with connected(settings) as t:
        t.capability(card=True)
        graph = t.graph([("TEMPLATE", template)] if intent["enabled"] else [])
        before = next((c for c in graph["cards"] if c["card"] == intent["card"]), None)
        if before and any(before["doors"][1:]) and intent["enabled"]:
            raise WriteBlocked("OTHER_DOOR_PERMISSION_REQUIRES_SEPARATE_REVIEW")
        if intent["enabled"]:
            resources = graph["resources"]
            tr = resources[f"TEMPLATE:{template}"]["record"]
            if (
                not tr["enabled"]
                or not tr["week"]
                or not resources[f"WEEK:{tr['week']}"]["record"]["enabled"]
            ):
                raise WriteBlocked("ENABLED_TEMPLATE_AND_WEEK_REQUIRED")
            if intent.get("privileged"):
                week = resources[f"WEEK:{tr['week']}"]["record"]
                if any(tr["groups"]) or any(
                    not any(s[:3] == [1, 0, 86400] for s in day) for day in week["days"]
                ):
                    raise WriteBlocked("VERIFIED_UNRESTRICTED_TEMPLATE_REQUIRED")
        if not intent["enabled"] and before is None:
            proposed = None  # Authoritative absence is already disabled.
        else:
            proposed = t.request(
                "MANAGE",
                [
                    "PREPARE",
                    "CARD",
                    intent["card"],
                    before["nativeHex"] if before else "-",
                    int(intent["enabled"]),
                    template,
                    *intent.get("start", []),
                    *intent.get("expiry", []),
                ],
            )[0]
    adopted = get_setting(db, "hikvision:adopted-card:" + intent["card"])
    prior = op.data.get("actualRecord") or adopted.get("record")
    changed = bool(before and prior and public(before) != public(prior))
    external = bool(
        before and not prior and before["type"] != 3 and any(before["doors"])
    )
    if (changed or external) and (not reconcile or len(reason.strip()) < 10):
        raise DomainError(
            "REQUIRES_RECONCILIATION",
            "Controller permissions differ from the application evidence. Inspect the card, then explicitly reconcile the current record with a reason. No write was sent.",
            409,
        )
    data = {
        "kind": "CARD",
        "slot": intent["card"],
        "sourceOperationId": op.id,
        "sourceVersion": op.version,
        "intent": intent,
        "before": before,
        "proposed": proposed,
        "beforePublic": public(before),
        "proposedPublic": public(proposed),
        "baseline": graph,
        "affected": [
            {
                "card": intent["card"],
                "doors": [1]
                if intent["enabled"]
                else [i + 1 for i, d in enumerate(before["doors"]) if d]
                if before
                else [],
            }
        ],
        "reconciliationReason": reason,
        "message": "Fresh controller comparison saved. Confirm this exact operation to apply.",
        "requestHash": digest([source_id, template, reconcile, reason]),
    }
    return save_preview(db, settings, actor, key, data)


def preview_plan(
    db, settings, actor, kind, slot, revision, desired, key, rollback_id=None
):
    authorize(db, actor, settings, True)
    if get_setting(db, scope_key_legacy(settings)).get("enabled"):
        raise WriteBlocked("CLOSE_LEGACY_AUTHORIZATION_FIRST")
    if kind not in KINDS or not 1 <= slot <= 65535:
        raise WriteBlocked("SPECIFIC_SUPPORTED_SLOT_REQUIRED")
    fields = plan_payload(db, kind, desired) if not rollback_id else []
    rollback = db.get(Operation, rollback_id) if rollback_id else None
    if rollback_id and (
        not rollback
        or rollback.target_type != "MANAGEMENT"
        or rollback.controller_id != settings.hikvision_controller_id
        or rollback.data.get("kind") != kind
        or rollback.data.get("slot") != slot
    ):
        raise WriteBlocked("ROLLBACK_PREIMAGE_NOT_FOUND")
    db.commit()
    with connected(settings) as t:
        t.capability()
        extra = [(kind, slot)]
        if kind == "TEMPLATE" and not rollback:
            extra += [("WEEK", desired["week"])] + [
                ("GROUP", n) for n in desired["groups"] if n
            ]
        if kind == "GROUP" and not rollback:
            extra += [("HOLIDAY", n) for n in desired["plans"] if n]
        graph = t.graph(extra)
        before = graph["resources"][f"{kind}:{slot}"]
        if digest(before) != revision:
            raise DomainError(
                "REQUIRES_RECONCILIATION",
                "The controller schedule changed. Refresh from controller before preparing another preview.",
                409,
            )
        if rollback:
            proposed = rollback.data["before"]
            restore = proposed["record"]
            linked = (
                [("WEEK", restore["week"])]
                + [("GROUP", n) for n in restore["groups"] if n]
                if kind == "TEMPLATE"
                else [("HOLIDAY", n) for n in restore["plans"] if n]
                if kind == "GROUP"
                else []
            )
            if linked:
                graph = t.graph(extra + linked)
        else:
            proposed = t.request(
                "MANAGE",
                [
                    "PREPARE",
                    kind,
                    slot,
                    before["nativeHex"],
                    desired["enabled"],
                    *fields,
                ],
            )[0]
        t.plan_capability(kind, slot, proposed)
        affected = references(graph, kind, slot)
        if any(any(d != 1 for d in c["doors"]) for c in affected):
            raise WriteBlocked("DOOR_2_SHARED_PLAN_CHANGE_BLOCKED")
    return save_preview(
        db,
        settings,
        actor,
        key,
        {
            "kind": kind,
            "slot": slot,
            "before": before,
            "proposed": proposed,
            "beforePublic": before["record"],
            "proposedPublic": proposed["record"],
            "baseline": graph,
            "affected": affected,
            "rollbackOf": rollback_id,
            "message": "Inspect the shared-card impact before applying this exact plan change.",
            "requestHash": digest([kind, slot, revision, desired, rollback_id]),
        },
    )


def finalize(db, settings, source, intent, actual):
    previous_status = source.status
    source.status = "CONFIRMED"
    source.completed_at = now()
    history = [
        *source.data.get("managementHistory", []),
        {
            "status": previous_status,
            "data": copy.deepcopy(source.data),
            "at": iso(now()),
        },
    ]
    source.data = {
        **source.data,
        "managementHistory": history,
        "managementVerified": True,
        "readbackVerified": True,
        "protectedReadbackVerified": True,
        "simulated": False,
        "actualRecord": public(actual),
        "intent": intent,
        "confirmedState": source.desired,
        "message": "Controller state verified by fresh SDK readback.",
    }
    if source.target_type == "ASSIGNMENT":
        a = db.get(Assignment, source.target_id)
        c = db.scalar(
            select(Confirmation).where(
                Confirmation.assignment_id == a.id,
                Confirmation.controller_id == source.controller_id,
            )
        )
        if c and c.version == a.version:
            c.state, c.last_confirmed, c.confirmed_at = (
                "SYNCHRONIZED",
                source.desired,
                now(),
            )
            db.flush()
            from .operations import finalize_assignment

            finalize_assignment(db, a, now(), settings)
    elif source.target_type == "POOL":
        card = db.get(Card, source.target_id)
        group = list(
            db.scalars(
                select(Operation).where(
                    Operation.target_type == "POOL",
                    Operation.target_id == card.id,
                    Operation.version == source.version,
                )
            )
        )
        if group and all(
            o.status == "CONFIRMED"
            and o.data.get("readbackVerified")
            and o.data.get("simulated") is False
            for o in group
        ):
            card.disabled, card.pool_state = True, "AVAILABLE_DISABLED"
    elif source.target_type == "PRIVILEGE":
        group = list(
            db.scalars(
                select(Operation).where(
                    Operation.target_type == "PRIVILEGE",
                    Operation.target_id == source.target_id,
                    Operation.version == source.version,
                )
            )
        )
        if group and all(
            o.status == "CONFIRMED"
            and o.data.get("readbackVerified")
            and o.data.get("simulated") is False
            for o in group
        ):
            card = db.get(Card, source.target_id)
            card.privileged = card.ever_privileged = True
            set_setting(db, "pending_designation", {})


def observed_validity_profile(db, settings):
    """Only the GET-proven ABI/SDK and retained physical timing observations.

    This is an observed firmware response exception, not a change to the global
    strict matcher or a claim that the manufacturer's zero-flag semantics are
    settled. The original bytes are never rewritten or synthesized.
    """
    profile = get_setting(
        db, "hikvision:validity-observed-profile:" + settings.hikvision_controller_id
    )
    try:
        if profile.get("device") != DEVICE or not profile.get(
            "ownerTimedEvidenceVerified"
        ):
            return None
        for field, file in [
            ("headerSha256", "incEn/HCNetSDK.h"),
            ("librarySha256", "lib/libhcnetsdk.so"),
        ]:
            if hashlib.sha256(
                (settings.hikvision_sdk_dir / file).read_bytes()
            ).hexdigest() != profile.get(field):
                return None
        operation = db.get(Operation, profile["ownerEvidenceOperation"])
        observations = (
            operation.data.get("physicalEvidence", {}).get("observations", [])
            if operation
            else []
        )
        if len(observations) != 3 or [o.get("result") for o in observations] != [
            "DENIED",
            "GRANTED",
            "DENIED",
        ]:
            return None
        for o in observations:
            event = db.get(AccessEvent, o["eventId"])
            if (
                not o.get("ownerConfirmedPhysical")
                or not event
                or event.simulated
                or event.card != "1051305927"
                or event.result != o["result"]
            ):
                return None
        return profile
    except (KeyError, OSError, TypeError):
        return None


def matching_card_readback(actual, expected, profile=None):
    if public(actual) == public(expected):
        return True
    if (
        not profile
        or not actual
        or not expected
        or profile.get("device") != DEVICE
        or not profile.get("ownerTimedEvidenceVerified")
    ):
        return False
    # This observation applies only to ordinary Door 1/TEMPLATE 3 permissions,
    # the exact configuration used in the retained physical timing sequence.
    if (
        expected.get("type") != 1
        or expected.get("leader") != 0
        or expected.get("valid") != 1
        or expected.get("doors") != [1] + [0] * 255
        or expected.get("plans") != [[3, 0, 0, 0]] + [[0] * 4 for _ in range(255)]
        or expected.get("validityEnabled") != 1
        or expected.get("timeType") != 0
    ):
        return False
    if [
        expected.get("beginFlag"),
        expected.get("endFlag"),
        actual.get("beginFlag"),
        actual.get("endFlag"),
    ] != [1, 1, 0, 0]:
        return False
    # Compare every other parsed field, including both exact date boundaries.
    if {
        k: v for k, v in public(actual).items() if k not in {"beginFlag", "endFlag"}
    } != {
        k: v for k, v in public(expected).items() if k not in {"beginFlag", "endFlag"}
    }:
        return False
    try:
        abi, offsets = profile["abi"], profile["offsets"]
        written, returned = (
            bytes.fromhex(expected["nativeHex"]),
            bytes.fromhex(actual["nativeHex"]),
        )
        if len(written) != abi["v50Size"] or len(returned) != abi["v50Size"]:
            return False
        begin, size = abi["v50ValidityOffset"], abi["validitySize"]
        left = bytearray(written[begin : begin + size])
        right = returned[begin : begin + size]
        if len(left) != size or len(right) != size:
            return False
        for key in ["beginFlag", "endFlag"]:
            pos = offsets[key]
            if left[pos] != 1 or right[pos] != 0:
                return False
            left[pos] = 0
        return bytes(left) == right
    except (ValueError, KeyError, IndexError, TypeError):
        return False


def execute(db, settings, actor, preview_id, affected, reconcile_only=False):
    authorize(db, actor, settings)
    with command_lock(db, settings):
        domain_lock(db)
        db.refresh(actor)
        if get_setting(db, scope_key_legacy(settings)).get("enabled"):
            raise WriteBlocked("CLOSE_LEGACY_AUTHORIZATION_FIRST")
        op = db.get(Operation, preview_id)
        if (
            not op
            or op.target_type != "MANAGEMENT"
            or op.controller_id != settings.hikvision_controller_id
        ):
            raise WriteBlocked("MANAGEMENT_PREVIEW_REQUIRED")
        d = copy.deepcopy(op.data)
        authorize(db, actor, settings, d["kind"] != "CARD")
        if op.status == "CONFIRMED" or d.get("reconciledBy"):
            return operation_view(op)
        if (op.status != "PENDING" and not reconcile_only) or (
            reconcile_only
            and (
                d["kind"] != "CARD"
                or op.status not in {"PENDING", "UNCERTAIN", "FAILED"}
            )
        ):
            raise DomainError(
                "REQUIRES_RECONCILIATION",
                "This attempt is unresolved. Refresh device state and prepare a new comparison; it will not be replayed.",
                409,
            )
        if (
            not reconcile_only and now() >= datetime.fromisoformat(d["expiresAt"])
        ) or sorted(affected) != sorted(c["card"] for c in d["affected"]):
            raise WriteBlocked("FRESH_PREVIEW_AND_EXACT_IMPACT_CONFIRMATION_REQUIRED")
        source = (
            db.get(Operation, d.get("sourceOperationId"))
            if d["kind"] == "CARD"
            else None
        )
        if d["kind"] == "CARD" and not source:
            raise WriteBlocked("OPERATION_NOT_FOUND")
        if source:
            if (
                local_intent(
                    db, source, actor, settings, d["intent"]["existingTemplate"]
                )
                != d["intent"]
            ):
                raise WriteBlocked("LOCAL_INTENT_CHANGED")
        profile = (
            observed_validity_profile(db, settings)
            if source and not d["intent"].get("privileged")
            else None
        )
        # Retain the domain lock through bounded dispatch/readback to prevent a
        # concurrent local revoke or assignment change between validation and SET.
        with connected(settings) as t:
            baseline = t.graph(
                [
                    (k.split(":")[0], int(k.split(":")[1]))
                    for k in d["baseline"]["resources"]
                ]
            )
            protected_baseline = copy.deepcopy(d["baseline"])
            current_card = (
                next((c for c in baseline["cards"] if c["card"] == d["slot"]), None)
                if source
                else None
            )
            if reconcile_only:
                if not matching_card_readback(current_card, d["proposed"], profile):
                    raise DomainError(
                        "REQUIRES_RECONCILIATION",
                        "Fresh GET still differs from the reviewed permissions. No write was sent; inspect the actual state before preparing a new change.",
                        409,
                    )
                protected_baseline["cards"] = [
                    c for c in protected_baseline["cards"] if c["card"] != d["slot"]
                ]
                if current_card:
                    protected_baseline["cards"].append(current_card)
                protected_baseline["cards"].sort(key=lambda c: c["card"])
            if digest(baseline) != digest(protected_baseline):
                op.status = "FAILED"
                op.data = {
                    **d,
                    "managementState": "REQUIRES_RECONCILIATION",
                    "message": "Controller state changed since preview. Refresh and reconcile; no write was sent.",
                }
                db.commit()
                return operation_view(op)
            previous_status = op.status
            op.status = "UNCERTAIN"
            if not reconcile_only:
                op.attempts += 1
            op.data = {
                **d,
                "reconciliationHistory": [
                    *d.get("reconciliationHistory", []),
                    {
                        "status": previous_status,
                        "at": iso(now()),
                        "data": {
                            k: v for k, v in d.items() if k != "reconciliationHistory"
                        },
                    },
                ]
                if reconcile_only
                else d.get("reconciliationHistory", []),
                "message": "Read-only reconciliation; no write will be sent."
                if reconcile_only
                else "Durable intent recorded; dispatch/readback pending.",
                "dispatchAt": d.get("dispatchAt") if reconcile_only else iso(now()),
                "readbackCheckedAt": iso(now()) if reconcile_only else None,
            }
            db.commit()  # durable intent before SDK, controller session lock held
            domain_lock(db)
            db.expire_all()
            authorize(db, actor, settings, d["kind"] != "CARD")
            if (
                source
                and local_intent(
                    db, source, actor, settings, d["intent"]["existingTemplate"]
                )
                != d["intent"]
            ):
                op.status = "FAILED"
                op.data = {
                    **op.data,
                    "managementState": "REQUIRES_RECONCILIATION",
                    "message": "Local assignment changed before dispatch; no write sent.",
                }
                db.commit()
                return operation_view(op)
            try:
                t.capability(card=True) if source else t.plan_capability(
                    d["kind"], d["slot"], d["proposed"]
                )
                slots = (
                    [d["intent"]["existingTemplate"]]
                    if source and d["intent"]["enabled"]
                    else []
                    if source
                    else [d["slot"]]
                )
                t.request(
                    "AUTHORIZE",
                    [
                        1 if source else 0,
                        *([d["slot"]] if source else []),
                        len(slots),
                        *slots,
                    ],
                )
                already_applied = (
                    matching_card_readback(current_card, d["proposed"], profile)
                    if source
                    else d["before"] == d["proposed"]
                )
                if source:
                    if reconcile_only and not already_applied:
                        raise WriteBlocked("READ_ONLY_RECONCILIATION_MISMATCH")
                    if not already_applied:
                        op.data = {
                            **op.data,
                            "sdkWriteAttemptedAt": iso(now()),
                            "sdkWriteAcknowledged": None,
                        }
                        t.request(
                            "MANAGE",
                            [
                                "APPLY",
                                "CARD",
                                d["slot"],
                                d["before"]["nativeHex"] if d["before"] else "-",
                                d["proposed"]["nativeHex"],
                            ],
                        )
                        op.data = {**op.data, "sdkWriteAcknowledged": True}
                    actual = t.card(d["slot"])
                    matched = matching_card_readback(actual, d["proposed"], profile)
                else:
                    if not already_applied:
                        t.request(
                            "MANAGE",
                            [
                                "APPLY",
                                d["kind"],
                                d["slot"],
                                d["before"]["nativeHex"],
                                d["proposed"]["nativeHex"],
                            ],
                        )
                    actual = t.plan(d["kind"], d["slot"])
                    matched = actual == d["proposed"]
                op.data = {**op.data, "readbackOnly": already_applied}
                after = t.graph(
                    [
                        (k.split(":")[0], int(k.split(":")[1]))
                        for k in d["baseline"]["resources"]
                    ]
                )
                expected = copy.deepcopy(baseline)
                if source:
                    expected["cards"] = [
                        c for c in baseline["cards"] if c["card"] != d["slot"]
                    ]
                    if actual:
                        expected["cards"].append(actual)
                else:
                    expected["resources"][f"{d['kind']}:{d['slot']}"] = actual
                # Card enumeration order is not a device-state change.
                for graph in (expected, after):
                    graph["cards"] = sorted(graph["cards"], key=lambda c: c["card"])
                protected = digest(expected) == digest(after)
                compatibility_used = bool(
                    source and matched and public(actual) != public(d["proposed"])
                )
                op.data = {
                    **op.data,
                    "managementState": "CONFIRMED"
                    if matched and protected
                    else "REQUIRES_RECONCILIATION",
                    "validityCompatibilityUsed": compatibility_used,
                    "validityProfileEvidence": profile if compatibility_used else None,
                    "actual": actual,
                    "protectedReadbackVerified": protected,
                    "readbackVerified": matched and protected,
                    "message": "Current permissions and unaffected resources verified using the pinned firmware observation profile. Actual limit flags remain zero; backend-stopped expiry acceptance remains pending."
                    if matched and protected and compatibility_used
                    else "Device change and unaffected resources verified."
                    if matched and protected
                    else "SDK completed; readback differs. Inspect actual state before another operation.",
                }
                if matched and protected:
                    op.status, op.completed_at = "CONFIRMED", now()
                    if source:
                        finalize(db, settings, source, d["intent"], actual)
                        source.data = {
                            **source.data,
                            "validityCompatibilityUsed": compatibility_used,
                            "validityProfileEvidence": profile
                            if compatibility_used
                            else None,
                        }
                else:
                    op.data = {**op.data, "managementState": "REQUIRES_RECONCILIATION"}
            except Exception as exc:
                # Never hide or replay a possibly dispatched command.
                op.data = {
                    **op.data,
                    "managementState": "REQUIRES_RECONCILIATION",
                    "error": str(exc)[:200],
                    "nativeFailure": getattr(t, "last_response", None),
                    "message": "No verified final state. Refresh controller state; this attempt will not be replayed.",
                    "readbackVerified": False,
                }
            audit(
                db,
                actor,
                "CONTROLLER_MANAGEMENT_RECONCILE"
                if reconcile_only
                else "CONTROLLER_MANAGEMENT_APPLY",
                op.id,
                op.status,
            )
            db.commit()
        return operation_view(op)


def status(db, settings):
    from .controller_access import operation_card

    cards = list(db.scalars(select(Card.number).order_by(Card.number)))
    operations, history = [], []
    for op in db.scalars(
        select(Operation)
        .where(Operation.controller_id == settings.hikvision_controller_id)
        .order_by(Operation.created_at.desc())
    ):
        if op.target_type == "MANAGEMENT":
            if len(history) < 30:
                history.append(operation_view(op))
            continue
        if op.status not in {"PENDING", "FAILED", "UNCERTAIN"}:
            continue
        c = operation_card(db, op)
        if not c and op.target_type == "PRIVILEGE":
            c = db.get(Card, op.target_id)
        if c:
            operations.append(
                {
                    "id": op.id,
                    "card": c.number,
                    "desired": op.desired,
                    "status": op.status,
                    "message": op.data.get(
                        "message",
                        "Fresh comparison and individual authorization required.",
                    ),
                }
            )
    scope = get_setting(db, scope_key_legacy(settings))
    return {
        "enabled": True,
        "expired": False,
        "template": 3,
        "cards": cards,
        "operations": operations,
        "management": history,
        "legacyScopeEnabled": bool(scope.get("enabled")),
    }


def scope_key_legacy(settings):
    return "hikvision:write-scope:" + settings.hikvision_controller_id


def attest_weekdays(db, settings, actor, slot, labelled, source, retrieved):
    authorize(db, actor, settings, True)
    if not retrieved or len(source.strip()) < 10:
        raise WriteBlocked("INDEPENDENT_CONTROLLER_RETRIEVED_IVMS_VIEW_REQUIRED")
    if len(labelled) != 7:
        raise WriteBlocked("SEVEN_LABELLED_DAYS_REQUIRED")
    for day in labelled:
        validate_segments(day, 8)
    # Seven distinct patterns establish all indices independently. Uniform or
    # partially repeating weeks cannot prove a complete weekday mapping.
    signatures = [digest(day) for day in labelled]
    if len(set(signatures)) != 7:
        raise WriteBlocked("PARTIAL_WEEKDAY_EVIDENCE_ONLY")
    db.commit()
    with connected(settings) as t:
        week = t.plan("WEEK", slot)
    actual = [digest([s[:3] for s in day]) for day in week["record"]["days"]]
    if sorted(actual) != sorted(signatures):
        raise WriteBlocked("LABELLED_VIEW_DOES_NOT_MATCH_NATIVE_READBACK")
    mapping = [signatures.index(s) for s in actual]
    domain_lock(db)
    set_setting(
        db,
        "hikvision:weekday-evidence",
        {
            "indexToWeekday": mapping,
            "source": source,
            "slot": slot,
            "device": DEVICE,
            "nativeBaseline": week,
            "labelledDays": labelled,
            "actorId": actor.id,
            "recordedAt": iso(now()),
        },
    )
    audit(db, actor, "CONTROLLER_WEEKDAY_EVIDENCE", str(slot))
    db.commit()
    return {"indexToWeekday": mapping, "verified": True}


def adopt_card(db, settings, actor, value, reason):
    """Adopt an observed baseline, not an unapproved entitlement or identity."""
    authorize(db, actor, settings, True)
    number(value)
    if value in REVOKED or len(reason.strip()) < 10:
        raise WriteBlocked("REVOKED_CARD_OR_MISSING_RECONCILIATION_REASON")
    with command_lock(db, settings):
        with connected(settings) as t:
            actual = t.card(value)
        domain_lock(db)
        c = db.scalar(select(Card).where(Card.number == value))
        if c and c.revoked:
            raise WriteBlocked("PERMANENTLY_REVOKED_CARD")
        key = "hikvision:adopted-card:" + value
        previous = get_setting(db, key)
        set_setting(
            db,
            key,
            {
                "record": public(actual),
                "nativeHex": actual.get("nativeHex") if actual else None,
                "actorId": actor.id,
                "reason": reason,
                "readAt": iso(now()),
                "history": [
                    *previous.get("history", []),
                    {k: v for k, v in previous.items() if k != "history"},
                ]
                if previous
                else [],
            },
        )
        audit(db, actor, "CONTROLLER_CARD_BASELINE_ADOPTED", value)
        db.commit()
        return {
            "card": value,
            "record": public(actual),
            "message": "Observed reconciliation baseline adopted. Identity ownership, approvals, synchronization and physical permissions are unchanged.",
        }


def error_message(code):
    messages = {
        "CLOSE_LEGACY_AUTHORIZATION_FIRST": "Ask Super Admin to close the legacy operator scope in Controller card access before using web management. Closing it changes no physical permission.",
        "SDK_ERROR:23": "The controller firmware reports this command as unsupported. No simulated confirmation is available; inspect its actual state before continuing.",
        "CURRENT_CARD_OPERATION_REQUIRED": "This source operation was superseded. Compare the current assignment operation; an old command will not be revived.",
        "FRESH_PREVIEW_AND_EXACT_IMPACT_CONFIRMATION_REQUIRED": "This preview expired or its listed impact was not confirmed exactly. Close it, compare the current operation again, and review the fresh preview.",
        "PERMANENTLY_REVOKED_CARD": "This personal card is permanently revoked. Use a fresh card; it cannot be reactivated.",
        "VALIDITY_RANGE_UNSUPPORTED": "This controller interface requires validity between 1970 and 2037. Correct the person's approved dates before retrying.",
        "WEEKDAY_ORDER_UNVERIFIED": "Named weekday editing needs independently labelled iVMS evidence. Refresh and record that evidence, or prepare an identical pattern for all seven days.",
        "PARTIAL_WEEKDAY_EVIDENCE_ONLY": "These repeated daily patterns prove only partial weekday information. Supply seven distinct patterns from a controller-retrieved, independently labelled view.",
        "DOOR_2_SHARED_PLAN_CHANGE_BLOCKED": "This plan is shared with Door 2. It cannot be changed in the current Door 1 workflow. Use a separately reviewed plan.",
        "OTHER_DOOR_PERMISSION_REQUIRES_SEPARATE_REVIEW": "This card has rights on another door. Inspect those rights and arrange a separate reviewed change; they will not be erased here.",
        "NORMAL_PAID_ISSUANCE_REQUIRED": "A genuine approved personal application must own this assignment. Complete payment review and normal issuance first.",
        "NORMAL_VISITOR_APPROVAL_REQUIRED": "The assignment needs a current genuinely approved visit with exactly matching identity and dates.",
        "OLD_DISABLE_UNCONFIRMED": "Confirm the previous card's disable before activating its replacement.",
        "SUPERSEDED": "This operation no longer matches the current assignment. Refresh the operations list and choose its current version.",
        "SDK_ERROR:11": "The controller rejected this specific slot or command (SDK error 11). Select a successfully read supported slot; this does not establish total capacity.",
    }
    return messages.get(
        code,
        "Controller verification stopped. Refresh the device state and inspect this diagnostic before retrying: "
        + code,
    )
