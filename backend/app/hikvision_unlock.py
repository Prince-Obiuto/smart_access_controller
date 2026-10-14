"""Private, one-shot development acceptance. Never drains outbox or retries a door command."""

import argparse
import hashlib
import sys
from contextlib import contextmanager
from getpass import getpass
from types import SimpleNamespace
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from .common import audit, now
from .config import Settings
from .db import Database, domain_lock
from .hikvision_card_diagnostic import Bridge, save
from .hikvision_writes import WriteBlocked
from .models import Door, Operation, Idempotency
from .security import login_account, require, DomainError


class UnlockBridge(Bridge):
    source_name = "hikvision_timed_unlock.cpp"


UNLOCK_COMMANDS = {"UNLOCK", "UNLOCK5"}
UNRESOLVED_STATUSES = {"PENDING", "UNCERTAIN"}


@contextmanager
def command_lock(db, settings):
    lock_id = int.from_bytes(
        hashlib.sha256(
            ("ift-write:" + settings.hikvision_controller_id).encode()
        ).digest()[:8],
        "big",
        signed=True,
    )
    db.commit()
    with db.get_bind().connect() as lock:
        if not lock.execute(
            text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id}
        ).scalar():
            raise DomainError(
                "CONFLICT", "Another controller operation is in progress.", 409
            )
        try:
            yield
        finally:
            db.rollback()
            lock.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
            lock.commit()


def unresolved_unlocks(db, settings):
    return list(
        db.scalars(
            select(Operation)
            .where(
                Operation.controller_id == settings.hikvision_controller_id,
                Operation.target_type == "DOOR",
                Operation.target_id == "door-1",
                Operation.desired.in_(UNLOCK_COMMANDS),
                Operation.status.in_(UNRESOLVED_STATUSES),
            )
            .order_by(Operation.created_at, Operation.id)
        )
    )


def operation_view(op):
    rows = [r for r in (op.data.get("nativeResult") or []) if isinstance(r, dict)]
    acknowledgement = op.data.get("sdkAcknowledged")
    if (
        acknowledgement is None
        and len(rows) == 1
        and isinstance(rows[0].get("ok"), bool)
    ):
        acknowledgement = rows[0]["ok"]
    sdk_status = (
        "ACKNOWLEDGED"
        if acknowledgement is True
        else "NOT_SENT"
        if any(r.get("sent") is False for r in rows)
        else "NOT_ACKNOWLEDGED"
        if acknowledgement is False
        else "UNKNOWN"
    )
    if op.data.get("simulated") is True:
        acknowledgement, sdk_status = None, "SIMULATED"
    physical = op.data.get("physicalState", "UNKNOWN")
    message = (
        "Door 1 release and relocking confirmed by operator."
        if op.data.get("physicalObservationConfirmed")
        else "Door 1 is secured; the previous release remains unobserved."
        if op.status == "RECONCILED"
        else "Native checks rejected this command before dispatch; no unlock was sent."
        if sdk_status == "NOT_SENT"
        else "Command remains pending; no successful SDK acknowledgement is recorded."
        if op.status == "PENDING"
        else "SDK acknowledged the command; physical release and relocking await observation."
        if acknowledgement is True
        else "No successful SDK acknowledgement is recorded; the door command outcome remains uncertain."
    )
    return {
        "operationId": op.id,
        "command": op.desired,
        "status": op.status,
        "createdAt": op.created_at.isoformat(),
        "sdkAcknowledged": acknowledgement,
        "sdkStatus": sdk_status,
        "transportStatus": op.data.get("transportStatus", sdk_status),
        "physicalState": physical,
        "readbackVerified": False,
        "message": message,
    }


def bound_unlock(db, settings, operation_id):
    op = db.scalar(
        select(Operation)
        .where(Operation.id == operation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not op
        or op.controller_id != settings.hikvision_controller_id
        or op.target_type != "DOOR"
        or op.target_id != "door-1"
        or op.desired not in UNLOCK_COMMANDS
    ):
        raise DomainError(
            "CONFLICT",
            "This operation is not a Door 1 unlock on the configured controller.",
            409,
        )
    return op


def validate_duration(rows):
    fields = [r for r in rows if r.get("kind") == "doorFields"]
    if (
        len(fields) != 1
        or fields[0].get("door") != 1
        or fields[0].get("openDuration") != 5
    ):
        raise WriteBlocked("FIVE_SECOND_DOOR_DURATION_UNVERIFIED")


def application_prepare(settings, db=None, operation_id=None):
    from .hikvision_existing import DEVICE

    result = {
        "doorId": "door-1",
        "configuredSeconds": None,
        "requiredSeconds": 5,
        "ready": False,
        "sensor": "UNKNOWN",
        "controllerStatus": "FAILED",
    }
    bridge = UnlockBridge(settings)
    try:
        baseline = bridge.start()
        if baseline["device"] != DEVICE:
            raise WriteBlocked("DESIGNATED_DEVICE_IDENTITY_CHANGED")
        fields = next((r for r in bridge.rows if r.get("kind") == "doorFields"), {})
        result.update(
            configuredSeconds=fields.get("openDuration"),
            ready=fields.get("openDuration") == 5,
            controllerStatus="CONNECTED",
        )
    except (WriteBlocked, OSError) as exc:
        result["controllerError"] = (
            str(exc) if isinstance(exc, WriteBlocked) else "NATIVE_IO_FAILED"
        )
    finally:
        bridge.close()
    if db is not None:
        pending = unresolved_unlocks(db, settings)
        result["unresolvedOperations"] = [operation_view(op) for op in pending]
        result["ready"] = result["ready"] and not pending
        if operation_id:
            result["operation"] = operation_view(
                bound_unlock(db, settings, operation_id)
            )
    return result


def confirm_physical_unlock(
    db, settings, actor, operation_id, *, secured_only=False, reason=None
):
    """Close the uncertainty window only after the operator observes the door."""
    require(db, actor, "unlock", settings)
    with command_lock(db, settings):
        domain_lock(db)
        db.refresh(actor)
        require(db, actor, "unlock", settings)
        op = bound_unlock(db, settings, operation_id)
        allowed = UNRESOLVED_STATUSES if secured_only else {"UNCERTAIN"}
        if op.status not in allowed:
            raise DomainError(
                "CONFLICT",
                "This operation is no longer awaiting that observation. Refresh its status.",
                409,
            )
        if not secured_only and op.data.get("simulated") is True:
            raise DomainError(
                "CONFLICT",
                "A simulated record requires reconciliation and cannot confirm a physical release.",
                409,
            )
        prior_status = op.status
        op.status = "RECONCILED" if secured_only else "CONFIRMED"
        op.completed_at = now()
        op.data = {
            **op.data,
            "physicalState": "SECURED_OBSERVED_RELEASE_UNKNOWN"
            if secured_only
            else "RELEASED_AND_RELOCKED_OBSERVED",
            "physicalObservationConfirmed": not secured_only,
            "physicalObservationPending": False,
            "physicalObservationAt": now().isoformat(),
            "physicalObservationBy": actor.id,
            "observationHistory": [
                *op.data.get("observationHistory", []),
                {
                    "priorStatus": prior_status,
                    "actorId": actor.id,
                    "at": now().isoformat(),
                    "releasedAndRelocked": not secured_only,
                    "securedOnly": secured_only,
                    "reason": reason,
                },
            ],
            "readbackVerified": False,
        }
        audit(
            db,
            actor,
            "TIMED_UNLOCK_RECONCILIATION"
            if secured_only
            else "TIMED_UNLOCK_PHYSICAL_CONFIRMATION",
            op.id,
            result=op.status,
        )
        db.commit()
        return operation_view(op)


def application_command(db, settings, actor, key, configure=False):
    """Normal authenticated UI; durable idempotency precedes the one native send."""
    require(db, actor, "settings" if configure else "unlock", settings)
    if settings.controller_mode == "SIMULATED":
        raise DomainError(
            "NOT_CONNECTED", "Real controller access is unavailable in demo mode.", 409
        )
    if not key or len(key) > 80:
        raise DomainError("VALIDATION_ERROR", "An Idempotency-Key is required.")
    command = "DURATION5" if configure else "UNLOCK5"
    fingerprint = hashlib.sha256(command.encode()).hexdigest()
    lock_id = int.from_bytes(
        hashlib.sha256(
            ("ift-write:" + settings.hikvision_controller_id).encode()
        ).digest()[:8],
        "big",
        signed=True,
    )
    db.commit()
    with db.get_bind().connect() as lock:
        if not lock.execute(
            text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id}
        ).scalar():
            raise DomainError(
                "CONFLICT", "Another controller operation is in progress.", 409
            )
        bridge = UnlockBridge(settings)
        op = None
        try:
            domain_lock(db)
            previous = db.get(Idempotency, (actor.id, key))
            if previous:
                if previous.fingerprint != fingerprint:
                    raise DomainError(
                        "CONFLICT", "Idempotency key belongs to another action.", 409
                    )
                return previous.result
            if not configure and unresolved_unlocks(db, settings):
                audit(
                    db,
                    actor,
                    "TIMED_UNLOCK_REJECTED",
                    "door-1",
                    result="DUPLICATE_BLOCKED",
                )
                db.commit()
                raise DomainError(
                    "CONFLICT",
                    "A previous Door 1 unlock has an uncertain outcome or remains pending. Observe or reconcile its exact operation before trying again.",
                    409,
                )
            db.commit()
            baseline = bridge.start()
            from .hikvision_existing import DEVICE

            if baseline["device"] != DEVICE:
                raise WriteBlocked("DESIGNATED_DEVICE_IDENTITY_CHANGED")
            if not configure:
                validate_duration(bridge.rows)
            domain_lock(db)
            db.refresh(actor)
            require(db, actor, "settings" if configure else "unlock", settings)
            door = db.get(Door, "door-1")
            if not door or door.controller_id != settings.hikvision_controller_id:
                raise WriteBlocked("DOOR_TARGET_UNVERIFIED")
            if not configure:
                if unresolved_unlocks(db, settings):
                    raise DomainError(
                        "CONFLICT", "A previous Door 1 unlock remains unresolved.", 409
                    )
                latest = db.scalar(
                    select(Operation)
                    .where(
                        Operation.controller_id == door.controller_id,
                        Operation.target_type == "DOOR",
                        Operation.target_id == door.id,
                        Operation.desired.in_(UNLOCK_COMMANDS),
                    )
                    .order_by(Operation.created_at.desc())
                    .limit(1)
                )
                if (
                    latest
                    and latest.status == "CONFIRMED"
                    and latest.created_at
                    and (now() - latest.created_at).total_seconds() < 5
                ):
                    audit(
                        db,
                        actor,
                        "TIMED_UNLOCK_REJECTED",
                        door.id,
                        result="COOLDOWN",
                    )
                    db.commit()
                    raise DomainError(
                        "CONFLICT",
                        "Door 1 was just unlocked. Wait for the five-second release "
                        "period to finish before trying again.",
                        409,
                    )
            next_version = (
                db.scalar(
                    select(func.max(Operation.version)).where(
                        Operation.controller_id == door.controller_id,
                        Operation.target_type == "DOOR",
                        Operation.target_id == door.id,
                    )
                )
                or 0
            ) + 1
            op = Operation(
                controller_id=door.controller_id,
                target_type="DOOR",
                target_id=door.id,
                desired=command,
                version=next_version,
                status="UNCERTAIN",
                attempts=1,
                actor_id=actor.id,
                created_at=now(),
                data={
                    "simulated": False,
                    "durationSeconds": 5,
                    "physicalObservationPending": not configure,
                    "transportStatus": "UNCERTAIN",
                },
            )
            db.add(op)
            db.flush()
            placeholder = {
                "operationId": op.id,
                "status": "UNCERTAIN",
                "physicalState": "UNKNOWN",
                "readbackVerified": False,
                "message": "Outcome uncertain; the command will not be repeated automatically.",
                "sdkAcknowledged": None,
                "sdkStatus": "UNKNOWN",
                "transportStatus": "UNCERTAIN",
            }
            entry = Idempotency(
                account_id=actor.id,
                key=key,
                fingerprint=fingerprint,
                result=placeholder,
                at=now(),
            )
            db.add(entry)
            audit(
                db,
                actor,
                "DOOR_DURATION_INTENT" if configure else "TIMED_UNLOCK_INTENT",
                op.id,
                result="UNCERTAIN",
            )
            db.commit()
            op.data = {**op.data, "sentAt": now().isoformat()}
            db.commit()
            bridge.process.stdin.write((command + "\n").encode())
            bridge.process.stdin.flush()
            bridge.read()
            bridge.close()
            rows = [
                r
                for r in bridge.rows
                if r.get("kind") == ("durationResult" if configure else "unlockResult")
            ]
            result = rows[0] if len(rows) == 1 else {}
            verified = (
                configure
                and result.get("ok") is True
                and result.get("readbackVerified") is True
            )
            op.status = (
                "CONFIRMED"
                if verified
                else "FAILED"
                if result.get("sent") is False
                else "UNCERTAIN"
            )
            safe_rows = [
                {
                    k: r[k]
                    for k in ["kind", "sent", "ok", "error", "code", "readbackVerified"]
                    if k in r
                }
                for r in rows
            ]
            op.data = {
                **op.data,
                "nativeResult": safe_rows,
                "readbackVerified": bool(verified),
                "sdkAcknowledged": result.get("ok") if len(rows) == 1 else None,
                "transportStatus": "NOT_SENT"
                if result.get("sent") is False
                else "ACKNOWLEDGED"
                if result.get("ok") is True
                else "UNCERTAIN",
                "artifact": save(
                    "application-door-result",
                    {"operationId": op.id, "native": bridge.rows},
                ),
            }
            output = {
                **placeholder,
                "status": op.status,
                "readbackVerified": bool(verified),
                "sdkAcknowledged": bool(result.get("ok")),
                "message": "Five-second release duration verified; other door settings unchanged."
                if verified
                else "Controller acknowledged the unlock; physical door state is unknown."
                if result.get("ok")
                else "Controller command is unconfirmed. Inspect the recorded result before any new action.",
            }
            entry.result = output
            if not configure:
                output = operation_view(op)
                entry.result = output
            audit(
                db,
                actor,
                "DOOR_DURATION_RESULT" if configure else "TIMED_UNLOCK_RESULT",
                op.id,
                result=op.status,
            )
            db.commit()
            return output
        except (WriteBlocked, OSError, IntegrityError) as exc:
            if isinstance(exc, IntegrityError):
                db.rollback()
                domain_lock(db)
                audit(
                    db,
                    actor,
                    "DOOR_DURATION_REJECTED" if configure else "TIMED_UNLOCK_REJECTED",
                    "door-1",
                    result="DUPLICATE_BLOCKED",
                )
                db.commit()
                raise DomainError(
                    "CONFLICT",
                    "Another Door 1 operation already exists. Refresh status before retrying.",
                    409,
                ) from None
            if op:
                db.rollback()
                domain_lock(db)
                db.refresh(op)
                op.data = {
                    **op.data,
                    "message": str(exc)
                    if isinstance(exc, WriteBlocked)
                    else "NATIVE_IO_FAILED",
                    "artifact": save(
                        "application-door-failure",
                        {"operationId": op.id, "native": bridge.rows},
                    ),
                    "readbackVerified": False,
                    "transportStatus": "UNCERTAIN",
                    "transportError": str(exc)
                    if isinstance(exc, WriteBlocked)
                    else "NATIVE_IO_FAILED",
                    "sdkAcknowledged": next(
                        (
                            r.get("ok")
                            for r in bridge.rows
                            if r.get("kind") == "unlockResult"
                        ),
                        None,
                    ),
                }
                if not configure:
                    entry = db.get(Idempotency, (actor.id, key))
                    entry.result = operation_view(op)
                    audit(db, actor, "TIMED_UNLOCK_RESULT", op.id, result="UNCERTAIN")
                db.commit()
                if not configure:
                    return operation_view(op)
            else:
                audit(
                    db,
                    actor,
                    "DOOR_DURATION_RESULT" if configure else "TIMED_UNLOCK_RESULT",
                    "door-1",
                    result="FAILED",
                )
                db.commit()
            raise
        finally:
            bridge.close()
            db.rollback()
            lock.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
            lock.commit()


def main():
    from uuid import uuid4

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "execute"])
    parser.add_argument("--actor-email")
    args = parser.parse_args()
    settings = Settings()
    database = Database(settings)
    try:
        with database.session() as db:
            if args.command == "prepare":
                print(application_prepare(settings, db))
                return
            if not sys.stdin.isatty() or not args.actor_email:
                raise WriteBlocked("PRIVATE_AUTHENTICATED_OPERATOR_REQUIRED")
            actor = login_account(
                db,
                args.actor_email,
                getpass("Admin password (hidden): "),
                SimpleNamespace(client=SimpleNamespace(host="local-unlock")),
            )
            require(db, actor, "unlock", settings)
            db.commit()
            if (
                input(
                    "Observe Door 1; type ONE FIVE SECOND OPEN to authorize one command: "
                )
                != "ONE FIVE SECOND OPEN"
            ):
                raise WriteBlocked("PHYSICAL_CONFIRMATION_REQUIRED")
            print(
                application_command(db, settings, actor, "local-unlock-" + str(uuid4()))
            )
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
