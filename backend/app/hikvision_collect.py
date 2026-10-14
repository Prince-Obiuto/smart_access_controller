"""Bounded local real-event collection; no outbox execution, retention or API startup."""

import argparse
import json
import time

from pydantic import ValidationError
from sqlalchemy import func, select

from .config import Settings
from .controllers import adapter_for
from .db import Database
from .jobs import collect_real, close_real
from .models import AccessEvent
from .common import get_setting


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args(argv)
    if not 1 <= args.seconds <= 300:
        parser.error("seconds must be 1..300")
    database = adapter = None
    try:
        settings = Settings()
        if settings.controller_mode == "SIMULATED":
            print(
                json.dumps(
                    {"code": "READ_ONLY_MODE_REQUIRED", "deviceWritesAllowed": False}
                )
            )
            return 2
        database = Database(settings)
        adapter = adapter_for(settings)
        deadline, next_history = time.monotonic() + args.seconds, 0
        while time.monotonic() < deadline:
            due = time.monotonic() >= next_history
            if due:
                next_history = time.monotonic() + 120
            collect_real(database, settings, adapter, collect=due)
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        with database.session() as db:
            total = db.scalar(
                select(func.count())
                .select_from(AccessEvent)
                .where(AccessEvent.simulated.is_(False))
            )
            state = get_setting(db, "hikvision:" + settings.hikvision_controller_id)
            checkpoint = get_setting(
                db, "backfill:HIKVISION_CONTROLLED:" + settings.hikvision_controller_id
            )
        print(
            json.dumps(
                {
                    "state": state.get("state", "BLOCKED"),
                    "code": state.get("code", "NO_CONTROLLER_RECORD"),
                    "realEventTotal": total,
                    "checkpoint": checkpoint,
                    "liveReceived": state.get("liveReceived", 0),
                    "historyReceived": state.get("historyReceived", 0),
                    "subscription": state.get("stages", {}).get("subscription", {}),
                    "deviceWritesAllowed": False,
                }
            )
        )
        return 0 if state.get("state") == "CONNECTED" else 2
    except (ValidationError, OSError):
        print(
            json.dumps(
                {
                    "code": "INVALID_LOCAL_CONFIGURATION_OR_RUNTIME",
                    "deviceWritesAllowed": False,
                }
            )
        )
        return 2
    except Exception:
        # SQL/native error strings can contain local connection configuration.
        print(
            json.dumps(
                {
                    "code": "COLLECTION_FAILED_CHECK_DATABASE_MIGRATIONS",
                    "deviceWritesAllowed": False,
                }
            )
        )
        return 2
    finally:
        if adapter:
            try:
                close_real(database, settings, adapter)
            except Exception:
                adapter.session.close()
                print(json.dumps({"code": "CLEANUP_STATUS_NOT_PERSISTED"}))
        if database:
            database.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
