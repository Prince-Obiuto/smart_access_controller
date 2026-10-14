"""Authenticated application access to the existing, card-only native transport."""

from datetime import datetime, timedelta
from uuid import uuid4
from sqlalchemy import select
from .common import audit, get_setting, iso, now, set_setting
from .db import domain_lock
from .hikvision_card_diagnostic import brief_card
from .hikvision_existing import (
    BANNED,
    capture,
    compatible_match,
    ExistingTemplateTransport,
    validate_designation,
    validity_profile,
)
from .hikvision_validity_probe import inspect
from .hikvision_writes import (
    WriteBlocked,
    current_intent,
    process_controlled,
    scope_key,
)
from .models import Account, Application, Assignment, Card, Operation, Person, Visit
from .security import DomainError, require


def authorize(db, actor, settings, privileged=False):
    require(db, actor, "settings" if privileged else "issue", settings)
    if settings.controller_mode == "SIMULATED":
        raise DomainError(
            "NOT_CONNECTED", "Real controller access is unavailable in demo mode.", 409
        )


def operation_card(db, op):
    if op.target_type == "ASSIGNMENT":
        a = db.get(Assignment, op.target_id)
        return db.get(Card, a.card_id) if a and a.version == op.version else None
    if op.target_type == "POOL":
        c = db.get(Card, op.target_id)
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
        return c if c and latest == op.id else None
    return None


def status(db, settings):
    scope = get_setting(db, scope_key(settings.hikvision_controller_id))
    cards = scope.get("cards", {})
    operations = []
    for op in db.scalars(
        select(Operation)
        .where(
            Operation.controller_id == settings.hikvision_controller_id,
            Operation.status.in_(["PENDING", "FAILED", "UNCERTAIN"]),
        )
        .order_by(Operation.created_at)
    ):
        card = operation_card(db, op)
        if not card or card.number not in cards or card.number in BANNED:
            continue
        assignment = (
            db.get(Assignment, op.target_id) if op.target_type == "ASSIGNMENT" else None
        )
        if assignment and assignment.person_id not in cards[card.number]["personIds"]:
            continue
        operations.append(
            {
                "id": op.id,
                "card": card.number,
                "desired": op.desired,
                "status": op.status,
                "message": op.data.get("message", "Awaiting controller verification"),
            }
        )
    return {
        "enabled": bool(scope.get("enabled")),
        "expiresAt": scope.get("expiresAt"),
        "expired": bool(
            scope.get("expiresAt")
            and now() >= datetime.fromisoformat(scope["expiresAt"])
        ),
        "template": 3,
        "cards": list(cards),
        "operations": operations,
    }


def observe(settings, cards):
    probe = inspect(settings, "1051305927")  # GET only; revoked card never enabled.
    bridge, baseline = capture(settings, cards)
    rows = bridge.rows
    bridge.close()
    return probe, baseline, rows


def activate(db, settings, actor, cards):
    authorize(db, actor, settings, True)
    if not 1 <= len(cards) <= 4 or any(n in BANNED for n in cards):
        raise WriteBlocked("INVALID_DESIGNATED_SCOPE")
    previous_scope = get_setting(db, scope_key(settings.hikvision_controller_id))
    if previous_scope.get("enabled"):
        raise WriteBlocked("CLOSE_PREVIOUS_AUTHORIZATION_FIRST")
    db.commit()  # No workflow lock held during bounded native GETs.
    probe, baseline, rows = observe(settings, cards)
    domain_lock(db)
    db.refresh(actor)
    authorize(db, actor, settings, True)
    if get_setting(db, scope_key(settings.hikvision_controller_id)).get("enabled"):
        raise WriteBlocked("CLOSE_PREVIOUS_AUTHORIZATION_FIRST")
    profile = validity_profile(db, probe)
    validate_designation(db, cards, baseline, previous_scope.get("cards"))
    for number, bound in cards.items():
        for person_id in bound.get("personIds", []):
            person = db.get(Person, person_id)
            account = db.scalar(select(Account).where(Account.person_id == person_id))
            if (
                not person
                or person.status != "ACTIVE"
                or person.deleted_at
                or person.purged
                or not account
                or account.role != "USER"
                or (person.kind == "VISITOR") != (bound.get("kind") == "VISITOR")
            ):
                raise WriteBlocked("ACTIVE_ORDINARY_IDENTITY_REQUIRED")
        card = db.scalar(select(Card).where(Card.number == number))
        record = next((r for r in baseline["cards"] if r["card"] == number), None)
        assignment = (
            db.scalar(
                select(Assignment).where(
                    Assignment.card_id == card.id, Assignment.ended_at.is_(None)
                )
            )
            if card
            else None
        )
        if not assignment or assignment.state != "ACTIVE":
            validate_designation(
                db, {number: bound}, baseline, previous_scope.get("cards")
            )
            continue
        # Adopt only a previously confirmed managed entitlement, never iVMS cards.
        account = db.scalar(
            select(Account).where(Account.person_id == assignment.person_id)
        )
        person = db.get(Person, assignment.person_id)
        op = db.scalar(
            select(Operation).where(
                Operation.target_id == assignment.id,
                Operation.status == "CONFIRMED",
                Operation.controller_id == settings.hikvision_controller_id,
            ).order_by(Operation.version.desc())
        )
        raw = next(
            (
                r
                for r in rows
                if r.get("kind") == "validityBytes"
                and r.get("card") == number
                and r.get("direction") == "READ"
            ),
            None,
        )
        if (
            card.revoked
            or card.disabled
            or card.privileged
            or card.ever_privileged
            or card.kind != bound.get("kind")
            or assignment.person_id not in bound.get("personIds", [])
            or assignment.sync not in {"SYNCHRONIZED", "PENDING"}
            or assignment.expiry <= now()
            or not account
            or account.role != "USER"
            or person.status != "ACTIVE"
            or person.deleted_at
            or person.purged
            or not op
            or op.data.get("simulated") is not False
            or not op.data.get("readbackVerified")
            or not compatible_match(record, op.data.get("intent", {}), 3, profile, raw)
        ):
            raise WriteBlocked("EXISTING_PERMISSION_NOT_VERIFIED_AS_MANAGED")
        if card.kind == "PERSONAL":
            application = db.scalar(
                select(Application).where(Application.assignment_id == assignment.id)
            )
            if (
                not application
                or application.state != "ISSUED"
                or not application.evidence_id
                or not application.reviewer_id
            ):
                raise WriteBlocked("NORMAL_PAID_ISSUANCE_REQUIRED")
        else:
            visit = db.scalar(select(Visit).where(Visit.assignment_id == assignment.id))
            if (
                not visit
                or visit.state != "ACTIVE"
                or not visit.approval
                or visit.approval.get("action") != "EXTERNAL_APPROVAL_CONFIRMED"
            ):
                raise WriteBlocked("NORMAL_VISITOR_APPROVAL_REQUIRED")
    scope = {
        "kind": "EXISTING_TEMPLATE",
        "existingTemplate": 3,
        "enabled": True,
        "epoch": str(uuid4()),
        "actorId": actor.id,
        "cards": cards,
        "baseline": baseline,
        "validityProfile": profile,
        "reviewedOperations": [],
        "activatedAt": iso(now()),
        "expiresAt": iso(now() + timedelta(hours=3)),
        "lastRecords": {
            n: next((r for r in baseline["cards"] if r["card"] == n), None)
            for n in cards
        },
    }
    set_setting(db, scope_key(settings.hikvision_controller_id), scope)
    audit(db, actor, "CONTROLLER_CARD_AUTHORIZATION", ",".join(cards))
    db.commit()
    return status(db, settings)


def read_card(db, settings, number):
    scope = get_setting(db, scope_key(settings.hikvision_controller_id))
    if number not in scope.get("cards", {}) or number in BANNED:
        raise WriteBlocked("CARD_OUTSIDE_AUTHORIZATION")
    db.commit()
    transport = ExistingTemplateTransport(settings, scope)
    try:
        transport.start()
        return {
            "card": number,
            "record": brief_card(transport.read_card(number)),
            "readAt": iso(now()),
        }
    finally:
        transport.close()


def execute(db, settings, actor, operation_id):
    authorize(db, actor, settings)
    domain_lock(db)
    scope = get_setting(db, scope_key(settings.hikvision_controller_id))
    op = db.get(Operation, operation_id)
    if not op or op.controller_id != settings.hikvision_controller_id:
        raise WriteBlocked("OPERATION_NOT_FOUND")
    card = operation_card(db, op)
    if not card or card.number not in scope.get("cards", {}) or card.number in BANNED:
        raise WriteBlocked("CARD_OUTSIDE_AUTHORIZATION")
    if (
        op.status == "CONFIRMED"
        and op.data.get("readbackVerified")
        and op.data.get("simulated") is False
    ):
        return {"operationId": op.id, "status": op.status, "readbackVerified": True}
    if op.status not in {"PENDING", "FAILED", "UNCERTAIN"}:
        raise WriteBlocked("CURRENT_OPERATION_REQUIRED")
    scope["reviewedOperations"] = list(
        set(scope.get("reviewedOperations", []) + [op.id])
    )
    previous = {
        "status": op.status,
        "message": op.data.get("message"),
        "failureArtifact": op.data.get("failureArtifact"),
        "at": iso(now()),
    }
    op.data = {
        **op.data,
        "writeEpoch": scope.get("epoch"),
        "reviewedBy": actor.id,
        "reviewedAt": iso(now()),
        "reviewHistory": [*op.data.get("reviewHistory", []), previous],
    }
    current_intent(
        db, op, scope, now()
    )  # Payment, visit, expiry, replacement and rights gates.
    op.attempts, op.status = 0, "PENDING"
    set_setting(db, scope_key(settings.hikvision_controller_id), scope)
    audit(db, actor, "CONTROLLER_CARD_OPERATION", op.id)
    db.commit()
    process_controlled(
        db,
        settings.model_copy(update={"controller_mode": "HIKVISION_CONTROLLED"}),
        operation_id=op.id,
    )
    db.expire_all()
    op = db.get(Operation, operation_id)
    if op.status == "CONFIRMED":
        scope = get_setting(db, scope_key(settings.hikvision_controller_id))
        scope["lastRecords"] = {
            **scope.get("lastRecords", {}),
            card.number: op.data.get("actualRecord"),
        }
        set_setting(db, scope_key(settings.hikvision_controller_id), scope)
        db.commit()
    return {
        "operationId": op.id,
        "status": op.status,
        "readbackVerified": bool(op.data.get("readbackVerified")),
        "message": op.data.get("message", "Controller reconciliation pending"),
    }


def close(db, settings, actor):
    authorize(db, actor, settings, True)
    domain_lock(db)
    scope = get_setting(db, scope_key(settings.hikvision_controller_id))
    # Closing authorization never changes card permissions; paid rights survive.
    set_setting(
        db, scope_key(settings.hikvision_controller_id), {**scope, "enabled": False}
    )
    audit(
        db,
        actor,
        "CONTROLLER_CARD_AUTHORIZATION_CLOSED",
        settings.hikvision_controller_id,
    )
    db.commit()
    return status(db, settings)
