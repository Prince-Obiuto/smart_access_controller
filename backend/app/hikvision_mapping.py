"""Record owner-observed reader directions from two existing real events.

Changes local event classification only, never the controller or permissions.
"""

import argparse
import json

from sqlalchemy import select
from .config import Settings
from .db import Database, domain_lock
from .models import AccessEvent, Door
from .common import set_setting, now, iso
from .events import real_reader


def verify_mapping(db, settings, entry_id, exit_id):
    entry, exit = db.get(AccessEvent, entry_id), db.get(AccessEvent, exit_id)
    if not entry or not exit or entry.simulated or exit.simulated:
        raise ValueError("Two existing real events required")
    if (
        entry.controller != exit.controller
        or entry.controller != settings.hikvision_controller_id
    ):
        raise ValueError("Controller mismatch")
    if not entry.card or entry.card != exit.card or not entry.at or not exit.at:
        raise ValueError("Same card and trustworthy timestamps required")
    for row in (entry, exit):
        if (
            row.raw.get("flags")
            or row.raw.get("deviceDoor") != "1"
            or row.raw.get("deviceReader") in (None, "0")
        ):
            raise ValueError("Uncertain event cannot establish mapping")
    if entry.raw["deviceReader"] == exit.raw["deviceReader"]:
        raise ValueError("Distinct readers required")
    door = db.get(Door, "door-1")
    if not door or door.controller_id != entry.controller:
        raise ValueError("Site door mismatch")
    mapping = {
        "verified": True,
        "deviceDoor": "1",
        "doorId": door.id,
        "readers": {
            entry.raw["deviceReader"]: "ENTRY",
            exit.raw["deviceReader"]: "EXIT",
        },
        "verifiedAt": iso(now()),
        "evidence": {
            "entryEvent": entry.id,
            "exitEvent": exit.id,
            "source": "OWNER_OBSERVED_ORDINARY_SWIPES",
        },
    }
    set_setting(db, "hikvision:reader-map:" + entry.controller, mapping)
    # Original native identifiers/outcomes/times stay intact. Correct local
    # direction labels for retained history using the confirmed site mapping.
    reconcile(db, entry.controller)
    return mapping


def reconcile(db, controller_id):
    for row in db.scalars(
        select(AccessEvent).where(
            AccessEvent.simulated.is_(False), AccessEvent.controller == controller_id
        )
    ):
        door, reader, direction, verified = real_reader(
            db, controller_id, row.raw.get("deviceDoor"), row.raw.get("deviceReader")
        )
        row.door, row.reader_id, row.direction = (
            door.id if door else None,
            reader.id if reader else None,
            direction,
        )
        row.raw = {**row.raw, "readerMappingVerified": verified}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry-event", required=True)
    parser.add_argument("--exit-event", required=True)
    parser.add_argument(
        "--confirmed-physical-readers",
        action="store_true",
        required=True,
        help="Owner explicitly confirmed which physical reader produced each event.",
    )
    args = parser.parse_args(argv)
    database = None
    try:
        settings = Settings()
        if settings.controller_mode != "HIKVISION_READ_ONLY":
            raise ValueError("Read-only mode required")
        database = Database(settings)
        with database.session() as db:
            domain_lock(db)
            mapping = verify_mapping(db, settings, args.entry_event, args.exit_event)
            db.commit()
        print(
            json.dumps(
                {
                    "verified": True,
                    "deviceWritesAllowed": False,
                    "readers": mapping["readers"],
                }
            )
        )
        return 0
    except Exception:
        print(
            json.dumps(
                {
                    "verified": False,
                    "code": "MAPPING_NOT_VERIFIED",
                    "deviceWritesAllowed": False,
                }
            )
        )
        return 2
    finally:
        if database:
            database.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
