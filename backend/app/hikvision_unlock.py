"""Private, one-shot development acceptance. Never drains outbox or retries a door command."""

import argparse
import sys
from getpass import getpass
from types import SimpleNamespace

from .common import audit, now
from .config import Settings
from .db import Database, domain_lock
from .hikvision_card_diagnostic import Bridge, save
from .hikvision_writes import WriteBlocked
from .models import Door, Operation
from .security import login_account, require


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
