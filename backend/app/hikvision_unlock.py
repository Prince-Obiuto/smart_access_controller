"""Private, one-shot development acceptance. Never drains outbox or retries a door command."""

import argparse
import hashlib
import sys
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


def validate_duration(rows):
    fields = [r for r in rows if r.get("kind") == "doorFields"]
    if (
        len(fields) != 1
        or fields[0].get("door") != 1
        or fields[0].get("openDuration") != 5
    ):
        raise WriteBlocked("FIVE_SECOND_DOOR_DURATION_UNVERIFIED")


def application_prepare(settings, db=None):
    from .hikvision_existing import DEVICE

    bridge = UnlockBridge(settings)
    try:
        baseline = bridge.start()
        if baseline["device"] != DEVICE:
            raise WriteBlocked("DESIGNATED_DEVICE_IDENTITY_CHANGED")
        fields = next((r for r in bridge.rows if r.get("kind") == "doorFields"), {})
        result = {
            "doorId": "door-1",
            "configuredSeconds": fields.get("openDuration"),
            "requiredSeconds": 5,
            "ready": fields.get("openDuration") == 5,
            "sensor": "UNKNOWN",
            "controllerStatus": "CONNECTED",
        }
        if db is not None:
            pending = db.scalar(
                select(Operation)
                .where(
                    Operation.controller_id == settings.hikvision_controller_id,
                    Operation.target_type == "DOOR",
                    Operation.target_id == "door-1",
                    Operation.desired == "UNLOCK5",
                    Operation.status == "UNCERTAIN",
                )
                .order_by(Operation.created_at.desc())
                .limit(1)
            )
            if pending:
                result.update(
                    uncertainOperationId=pending.id,
                    uncertainMessage="A previous Door 1 unlock is awaiting physical confirmation.",
                )
        return result
    finally:
        bridge.close()


def confirm_physical_unlock(db, settings, actor):
    """Close the uncertainty window only after the operator observes the door."""
    require(db, actor, "unlock", settings)
    domain_lock(db)
    op = db.scalar(
        select(Operation)
        .where(
            Operation.controller_id == settings.hikvision_controller_id,
            Operation.target_type == "DOOR",
            Operation.target_id == "door-1",
            Operation.desired == "UNLOCK5",
            Operation.status == "UNCERTAIN",
        )
        .order_by(Operation.created_at.desc())
        .limit(1)
    )
    if not op:
        raise DomainError(
            "CONFLICT",
            "There is no uncertain Door 1 unlock awaiting physical confirmation.",
            409,
        )
    op.status = "CONFIRMED"
    op.completed_at = now()
    op.data = {
        **op.data,
        "physicalState": "RELEASED_AND_RELOCKED_OBSERVED",
        "physicalObservationConfirmed": True,
        "physicalObservationAt": now().isoformat(),
        "physicalObservationBy": actor.id,
        "message": "Door 1 release and return to secured state confirmed by operator.",
        "readbackVerified": False,
    }
    audit(
        db,
        actor,
        "TIMED_UNLOCK_PHYSICAL_CONFIRMATION",
        op.id,
        result="CONFIRMED",
    )
    db.commit()
    return {
        "operationId": op.id,
        "status": op.status,
        "physicalState": op.data["physicalState"],
        "readbackVerified": False,
        "message": op.data["message"],
    }


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
                latest = db.scalar(
                    select(Operation)
                    .where(
                        Operation.controller_id == door.controller_id,
                        Operation.target_type == "DOOR",
                        Operation.target_id == door.id,
                    )
                    .order_by(Operation.created_at.desc())
                    .limit(1)
                )
                if latest and latest.status in {"PENDING", "UNCERTAIN"}:
                    audit(
                        db,
                        actor,
                        "TIMED_UNLOCK_REJECTED",
                        door.id,
                        result="DUPLICATE_BLOCKED",
                    )
                    db.commit()
                    raise DomainError(
                        "CONFLICT",
                        "A previous Door 1 unlock has an uncertain outcome. "
                        "Inspect the controller and audit record before trying again.",
                        409,
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
                "sdkAcknowledged": bool(result.get("ok")),
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
                }
                db.commit()
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
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "execute"])
    parser.add_argument("--actor-email")
    args = parser.parse_args()
    settings = Settings()
    database = Database(settings)
    bridge = UnlockBridge(settings)
    op_id = None
    try:
        baseline = bridge.start()
        from .hikvision_existing import DEVICE

        if baseline["device"] != DEVICE:
            raise WriteBlocked("DESIGNATED_DEVICE_IDENTITY_CHANGED")
        fields = next((r for r in bridge.rows if r.get("kind") == "doorFields"), {})
        if args.command == "prepare":
            bridge.close()
            artifact = save(
                "timed-unlock-readonly-preflight",
                {"baseline": baseline, "doorFields": fields, "native": bridge.rows},
            )
            print(
                {
                    "door": 1,
                    "configuredSeconds": fields.get("openDuration"),
                    "requiredSeconds": 5,
                    "ready": fields.get("openDuration") == 5,
                    "controllerWrites": 0,
                    "artifact": artifact,
                }
            )
            return
        validate_duration(bridge.rows)  # No intent or physical call if duration fails.
        if not sys.stdin.isatty() or not args.actor_email:
            raise WriteBlocked("PRIVATE_AUTHENTICATED_OPERATOR_REQUIRED")
        with database.session() as db:
            domain_lock(db)
            actor = login_account(
                db,
                args.actor_email,
                getpass("Admin password (hidden): "),
                SimpleNamespace(client=SimpleNamespace(host="local-unlock")),
            )
            require(db, actor, "unlock", settings)
            if (
                input(
                    "Observe Door 1; type ONE FIVE SECOND OPEN to authorize one command: "
                )
                != "ONE FIVE SECOND OPEN"
            ):
                raise WriteBlocked("PHYSICAL_CONFIRMATION_REQUIRED")
            door = db.get(Door, "door-1")
            if not door or door.controller_id != settings.hikvision_controller_id:
                raise WriteBlocked("DOOR_TARGET_UNVERIFIED")
            op = Operation(
                controller_id=door.controller_id,
                target_type="DOOR",
                target_id=door.id,
                desired="UNLOCK",
                status="UNCERTAIN",
                attempts=1,
                actor_id=actor.id,
                created_at=now(),
                data={
                    "simulated": False,
                    "durationSeconds": 5,
                    "purpose": "DESIGNATED_TIMED_UNLOCK_ACCEPTANCE",
                    "physicalObservationPending": True,
                },
            )
            db.add(op)
            db.flush()
            op_id = op.id
            audit(db, actor, "TIMED_UNLOCK_INTENT", op.id, result="UNCERTAIN")
            db.commit()
        # Durable UNCERTAIN precedes sending. No retry/replay path exists.
        bridge.process.stdin.write(b"UNLOCK5\n")
        bridge.process.stdin.flush()
        bridge.read()
        bridge.close()
        result = [r for r in bridge.rows if r.get("kind") == "unlockResult"]
        artifact = save(
            "timed-unlock-result", {"operationId": op_id, "native": bridge.rows}
        )
        with database.session() as db:
            domain_lock(db)
            op = db.get(Operation, op_id)
            op.data = {
                **op.data,
                "nativeResult": result,
                "artifact": artifact,
                "sdkAcknowledged": len(result) == 1 and bool(result[0].get("ok")),
            }
            if len(result) == 1 and not result[0].get("sent"):
                op.status = "FAILED"
            db.commit()
        print(
            {
                "operationId": op_id,
                "sdkAcknowledged": len(result) == 1 and bool(result[0].get("ok")),
                "physicalState": "UNKNOWN",
                "automaticRetry": False,
            }
        )
    finally:
        bridge.close()
        database.engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except WriteBlocked as exc:
        raise SystemExit(str(exc)) from None
