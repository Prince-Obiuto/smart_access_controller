from datetime import timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from .models import (
    AccessEvent,
    Assignment,
    Card,
    Person,
    Door,
    Reader,
    Controller,
    Schedule,
    CalendarException,
)
from .common import notify, now, get_setting, set_setting, iso
from .controllers import SimulatedAdapter


def schedule_allows(db, at):
    local = at.astimezone(ZoneInfo("Africa/Lagos"))
    schedule = db.get(Schedule, "normal").data
    ex = db.get(CalendarException, local.date().isoformat())
    config = ex.data if ex else schedule
    if ex and config["closed"]:
        return False
    weekday = (local.weekday() + 1) % 7
    time = local.strftime("%H:%M")
    return (bool(ex) or weekday in schedule["weekdays"]) and config.get(
        "opens", schedule["opens"]
    ) <= time < config.get("closes", schedule["closes"])


def store_event(
    db,
    source_key,
    at,
    card_number,
    direction="ENTRY",
    event_type="CARD_SWIPE",
    controller_id="controller-1",
    received_at=None,
):
    card = db.scalar(select(Card).where(Card.number == card_number))
    assignment = None
    # Match event-time validity to the immutable historical assignment, not the
    # card's current owner. Revocation/checkout end time is persisted separately.
    if card:
        candidates = list(
            db.scalars(
                select(Assignment)
                .where(
                    Assignment.card_id == card.id,
                    Assignment.start <= at,
                    Assignment.expiry > at,
                    Assignment.created_at <= at,
                )
                .order_by(Assignment.start.desc())
            )
        )
        assignment = next(
            (a for a in candidates if not a.ended_at or at < a.ended_at), None
        )
    person = db.get(Person, assignment.person_id) if assignment else None
    door = db.scalar(select(Door).where(Door.controller_id == controller_id))
    reader = db.scalar(
        select(Reader).where(Reader.door_id == door.id, Reader.direction == direction)
    )
    granted = bool(
        assignment
        and assignment.state == "ACTIVE"
        and not card.revoked
        and not card.disabled
        and person.status == "ACTIVE"
        and not person.purged
        and (card.privileged or schedule_allows(db, at))
        and event_type == "CARD_SWIPE"
    )
    values = {
        "source_key": source_key,
        "at": at,
        "received_at": received_at or now(),
        "person_id": person.id if person else None,
        "assignment_id": assignment.id if assignment else None,
        "person": (person.first_name + " " + person.last_name)
        if person and not person.purged
        else "Anonymous visitor"
        if person
        else "Unknown credential",
        "reference": person.reference if person and not person.purged else "",
        "card": card_number,
        "controller": controller_id,
        "door": door.id,
        "reader_id": reader.id,
        "direction": direction,
        "result": "GRANTED" if granted else "DENIED",
        "event_type": event_type,
        "simulated": True,
        "raw": {"source": "SIMULATED", "credential": card_number},
        "restored": False,
    }
    id = db.scalar(
        insert(AccessEvent)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["source_key"])
        .returning(AccessEvent.id)
    )
    if id and not granted:
        notify(
            db,
            "Unknown card attempt"
            if not person
            else "Revoked card attempt"
            if card.revoked
            else "Access denied",
            f"Simulated {event_type} at {direction} reader. Credential {card_number}.",
            "CRITICAL" if event_type in ["REVOKED_CARD", "FORCED_OPEN"] else "WARNING",
        )
    return id


def simulate_event(db, kind, at, person_id=None):
    a = (
        db.scalar(
            select(Assignment).where(
                Assignment.person_id == person_id, Assignment.state == "ACTIVE"
            )
        )
        if person_id
        else None
    )
    if not a:
        a = db.scalar(select(Assignment).where(Assignment.state == "ACTIVE"))
    if kind == "REVOKED":
        a = db.scalar(select(Assignment).where(Assignment.state == "REVOKED"))
    card = db.get(Card, a.card_id) if a else None
    event_type = {
        "UNKNOWN": "UNKNOWN_CARD",
        "REVOKED": "REVOKED_CARD",
        "FORCED_OPEN": "FORCED_OPEN",
        "HELD_OPEN": "HELD_OPEN",
        "READER_FAULT": "READER_FAULT",
    }.get(kind, "CARD_SWIPE")
    return store_event(
        db,
        "manual:" + __import__("uuid").uuid4().hex,
        at,
        card.number
        if card and kind not in ["UNKNOWN", "FORCED_OPEN", "HELD_OPEN", "READER_FAULT"]
        else "0000000000",
        "EXIT" if kind == "EXIT" else "ENTRY",
        event_type,
    )


def backfill(db, at=None, adapter=None):
    at = at or now()
    adapter = adapter or SimulatedAdapter()
    for controller in db.scalars(select(Controller)):
        checkpoint = get_setting(db, "backfill:" + controller.id)
        start = (
            __import__("datetime").datetime.fromisoformat(checkpoint["at"])
            if checkpoint.get("at")
            else at - timedelta(minutes=2)
        )
        # A small overlap intentionally exercises source-key deduplication.
        for event in adapter.collect_events(
            controller, start - timedelta(minutes=2), at
        ):
            store_event(
                db,
                event["source_key"],
                event["at"],
                event["card"],
                event["direction"],
                event["type"],
                controller.id,
                at,
            )
        if controller.online:
            set_setting(db, "backfill:" + controller.id, {"at": iso(at)})
