"""Durable controller outbox. Uncertain door commands are never replayed."""

from sqlalchemy import select, update
from .models import (
    Operation,
    Assignment,
    Confirmation,
    Card,
    Application,
    Visit,
    Controller,
    Person,
    Schedule,
    Account,
)
from .common import get_setting, set_setting, audit, notify, now, iso
from .controllers import SimulatedAdapter
from .service import AccessService, ENDED
from .security import DomainError
from .db import domain_lock


def target_operations(db, target_type, target_id, version):
    return list(
        db.scalars(
            select(Operation).where(
                Operation.target_type == target_type,
                Operation.target_id == target_id,
                Operation.version == version,
            )
        )
    )


def finalize_assignment(db, a, at):
    confirmations = list(
        db.scalars(select(Confirmation).where(Confirmation.assignment_id == a.id))
    )
    if not confirmations:
        return
    all_confirmed = all(
        c.state == "SYNCHRONIZED"
        and c.desired == c.last_confirmed
        and c.version == a.version
        for c in confirmations
    )
    a.sync = (
        "SYNCHRONIZED"
        if all_confirmed
        else "FAILED"
        if any(c.state == "FAILED" for c in confirmations)
        else "PENDING"
    )
    if not all_confirmed:
        return
    intended = confirmations[0].desired
    card = db.get(Card, a.card_id)
    if intended == "DISABLED":
        card.disabled = True
        card.privileged = False
        if (
            card.kind == "VISITOR"
            and card.pool_state == "RETURN_PENDING_DISABLE"
            and a.returned_at
        ):
            card.pool_state = "AVAILABLE_DISABLED"
    else:
        card.disabled = False
        if intended == "ENABLED":
            a.state = "ACTIVE"
            if card.kind == "VISITOR":
                card.pool_state = "ASSIGNED"
        person = db.get(Person, a.person_id)
        person.sync = "SYNCHRONIZED"
        for app in db.scalars(
            select(Application).where(
                Application.assignment_id == a.id,
                Application.state == "ISSUANCE_PENDING",
            )
        ):
            app.state = "ISSUED"
            app.issued_at = at
            notify(
                db,
                "Card issued",
                "Simulated activation confirmed. No physical hardware has been validated.",
                "INFO",
                a.person_id,
                at,
            )
        for visit in db.scalars(
            select(Visit).where(Visit.assignment_id == a.id, Visit.state == "PENDING")
        ):
            visit.state = "ACTIVE"


def process_operations(db, settings, at=None, adapter=None):
    at = at or now()
    adapter = adapter or SimulatedAdapter()
    domain_lock(db)
    controls = get_setting(db, "simulation")
    pending = list(
        db.scalars(
            select(Operation)
            .where(Operation.status == "PENDING")
            .order_by(Operation.created_at, Operation.id)
            .with_for_update(skip_locked=True)
            .limit(100)
        )
    )
    for op in pending:
        db.refresh(op)
        if op.status != "PENDING":
            continue
        if op.target_type == "ASSIGNMENT":
            assignment = db.get(Assignment, op.target_id)
            if (
                not assignment
                or assignment.version != op.version
                or (assignment.state in ENDED and op.desired != "DISABLED")
            ):
                op.status = "SUPERSEDED"
                continue
            if op.desired != "DISABLED":
                person = db.get(Person, assignment.person_id)
                card = db.get(Card, assignment.card_id)
                service = AccessService(db, None, settings, at)
                if (
                    person.purged
                    or person.deleted_at
                    or person.status != "ACTIVE"
                    or assignment.expiry <= at
                    or card.revoked
                ):
                    assignment.state = (
                        "EXPIRED" if assignment.expiry <= at else "REVOKED"
                    )
                    service.assignment_queue(assignment, "DISABLED")
                    op.status = "SUPERSEDED"
                    continue
                if not service.old_removed(assignment.person_id):
                    continue
        if op.target_type == "PRIVILEGE":
            designation = get_setting(db, "pending_designation")
            card = db.get(Card, op.target_id)
            assignment = db.scalar(
                select(Assignment).where(
                    Assignment.card_id == op.target_id, Assignment.state == "ACTIVE"
                )
            )
            owner = (
                db.scalar(
                    select(Account).where(Account.person_id == assignment.person_id)
                )
                if assignment
                else None
            )
            old_enabled = db.scalar(
                select(Card).where(
                    Card.ever_privileged == True,
                    Card.id != op.target_id,
                    Card.disabled == False,
                )
            )
            if (
                designation.get("cardId") != op.target_id
                or not assignment
                or not owner
                or owner.role != "SUPER_ADMIN"
                or card.revoked
                or old_enabled
            ):
                op.status = "SUPERSEDED"
                continue
        # Mark door attempt durably before transport. A crash between send and
        # completion is UNCERTAIN, never a second send after process restart.
        if op.target_type == "DOOR":
            op.status = "UNCERTAIN"
            op.attempts += 1
            db.commit()
            domain_lock(db)
            db.expire_all()
        else:
            op.attempts += 1
        device = db.get(Controller, op.controller_id)
        outcome = adapter.execute(op, device, controls)
        op.status = outcome.status
        op.data = {
            **op.data,
            "message": outcome.message,
            "confirmedState": outcome.confirmed_state,
            "simulated": True,
        }
        if outcome.status != "PENDING":
            op.completed_at = at
        if outcome.status == "CONFIRMED":
            device.last_communication = at
        if op.target_type == "ASSIGNMENT":
            a = db.get(Assignment, op.target_id)
            c = db.scalar(
                select(Confirmation).where(
                    Confirmation.assignment_id == a.id,
                    Confirmation.controller_id == op.controller_id,
                )
            )
            if c and c.version == op.version:
                c.state = (
                    "SYNCHRONIZED"
                    if outcome.status == "CONFIRMED"
                    else "FAILED"
                    if outcome.status == "FAILED"
                    else "PENDING"
                )
                if outcome.status == "CONFIRMED":
                    c.last_confirmed = op.desired
                    c.confirmed_at = at
                db.flush()
                finalize_assignment(db, a, at)
            if outcome.status == "FAILED":
                notify(
                    db,
                    "Revoked locally — controller disable not confirmed."
                    if op.desired == "DISABLED"
                    else "Failed synchronization",
                    outcome.message,
                    "CRITICAL" if op.desired == "DISABLED" else "WARNING",
                    a.person_id,
                    at,
                )
        elif op.target_type == "POOL":
            group = target_operations(db, "POOL", op.target_id, op.version)
            if all(o.status == "CONFIRMED" for o in group):
                card = db.get(Card, op.target_id)
                card.disabled = True
                card.pool_state = "AVAILABLE_DISABLED"
        elif op.target_type == "PERSON":
            group = target_operations(db, "PERSON", op.target_id, op.version)
            p = db.get(Person, op.target_id)
            p.sync = (
                "SYNCHRONIZED"
                if all(o.status == "CONFIRMED" for o in group)
                else "FAILED"
                if any(o.status == "FAILED" for o in group)
                else "PENDING"
            )
        elif op.target_type == "SCHEDULE":
            schedule = db.get(Schedule, "normal")
            if schedule.version == op.version:
                group = target_operations(db, "SCHEDULE", "normal", op.version)
                schedule.sync = (
                    "SYNCHRONIZED"
                    if all(o.status == "CONFIRMED" for o in group)
                    else "FAILED"
                    if any(o.status == "FAILED" for o in group)
                    else "PENDING"
                )
        elif op.target_type == "PRIVILEGE":
            if outcome.status == "FAILED":
                notify(
                    db,
                    "Privileged schedule unconfirmed",
                    outcome.message,
                    "CRITICAL",
                    at=at,
                )
            group = target_operations(db, "PRIVILEGE", op.target_id, op.version)
            pending_designation = get_setting(db, "pending_designation")
            if (
                all(o.status == "CONFIRMED" for o in group)
                and pending_designation.get("cardId") == op.target_id
            ):
                card = db.get(Card, op.target_id)
                old_enabled = db.scalar(
                    select(Card).where(
                        Card.ever_privileged == True,
                        Card.id != card.id,
                        Card.disabled == False,
                    )
                )
                active = db.scalar(
                    select(Assignment).where(
                        Assignment.card_id == card.id, Assignment.state == "ACTIVE"
                    )
                )
                owner = (
                    db.scalar(
                        select(Account).where(Account.person_id == active.person_id)
                    )
                    if active
                    else None
                )
                if (
                    not old_enabled
                    and not card.revoked
                    and active
                    and owner
                    and owner.role == "SUPER_ADMIN"
                ):
                    db.execute(update(Card).values(privileged=False))
                    db.flush()
                    db.refresh(card)
                    card.privileged = True
                    card.ever_privileged = True
                    set_setting(db, "pending_designation", {})
        if op.target_type == "DOOR":
            notify(
                db,
                "Remote unlock result",
                outcome.message,
                "WARNING" if outcome.status != "CONFIRMED" else "INFO",
                at=at,
            )
        if outcome.status != "PENDING":
            audit(
                db,
                db.get(Account, op.actor_id) if op.actor_id else None,
                "CONTROLLER_" + op.desired,
                op.target_id,
                outcome.status,
                at=at,
            )
    db.flush()
    # A persisted replacement request resumes only after every old permission is
    # removed. Issuer authorization is rechecked at this later execution time.
    for app in list(
        db.scalars(
            select(Application).where(
                Application.state == "ISSUANCE_PENDING",
                Application.assignment_id.is_(None),
                Application.requested_number.is_not(None),
            )
        )
    ):
        issuer = db.get(Account, app.issuer_id)
        service = AccessService(db, issuer, settings, at)
        if issuer and service.old_removed(app.person_id):
            try:
                service.issue(app, app.requested_number)
            except DomainError:
                continue
    designation = get_setting(db, "pending_designation")
    if designation:
        card = db.get(Card, designation["cardId"])
        actor = db.get(Account, designation["actorId"])
        old_enabled = db.scalar(
            select(Card).where(
                Card.ever_privileged == True, Card.id != card.id, Card.disabled == False
            )
        )
        existing = db.scalar(
            select(Operation).where(
                Operation.target_type == "PRIVILEGE",
                Operation.target_id == card.id,
                Operation.status == "PENDING",
            )
        )
        if actor and actor.role == "SUPER_ADMIN" and not old_enabled and not existing:
            AccessService(db, actor, settings, at).queue(
                "PRIVILEGE", card.id, "UPDATED", int(at.timestamp() * 1000000)
            )
    db.commit()


def operation_result(db, result):
    ids = result.get("operationIds", [])
    ops = [db.get(Operation, id) for id in ids]
    ops = [o for o in ops if o]
    if not ops:
        return {k: v for k, v in result.items() if k != "operationIds"}
    statuses = {o.status for o in ops}
    status = (
        "UNCERTAIN"
        if "UNCERTAIN" in statuses
        else "FAILED"
        if "FAILED" in statuses
        else "PENDING"
        if "PENDING" in statuses
        else "PENDING"
        if "SUPERSEDED" in statuses
        else "CONFIRMED"
    )
    message = next(
        (
            o.data.get("message")
            for o in ops
            if o.status == status and o.data.get("message")
        ),
        result["message"],
    )
    if result.get("result") == status and result.get("confirmations"):
        message = result["message"]
    return {
        "intendedState": ops[-1].desired,
        "result": status,
        "message": message,
        "confirmations": [
            {
                "controllerId": o.controller_id,
                "confirmed": o.status == "CONFIRMED",
                "state": "SYNCHRONIZED"
                if o.status == "CONFIRMED"
                else "FAILED"
                if o.status in ["FAILED", "UNCERTAIN"]
                else "PENDING",
                "intendedState": o.desired,
                "at": iso(o.completed_at) if o.status == "CONFIRMED" else None,
                "lastConfirmedState": o.data.get("confirmedState"),
                "simulated": True,
            }
            for o in ops
        ],
        "operationIds": ids,
    }
