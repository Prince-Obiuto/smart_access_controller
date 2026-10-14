"""Bounded reader capture from real events; capture never grants permissions."""
from datetime import datetime, timedelta
import re
from sqlalchemy import select
from .common import now, iso, get_setting, set_setting
from .models import Enrolment, AccessEvent, Card
from .security import DomainError


def start_real(db, actor, settings):
    at = now()
    status = get_setting(db, "hikvision:" + settings.hikvision_controller_id)
    last = datetime.fromisoformat(status.get("lastCommunication", "1970-01-01T00:00:00+00:00"))
    if status.get("state") != "CONNECTED" or not timedelta(0) <= at - last <= timedelta(seconds=120):
        raise DomainError("NOT_CONNECTED", "Fresh controller communication is required for reader capture.", 409)
    if db.scalar(select(Enrolment.id).where(Enrolment.state.in_(["WAITING", "DETECTED"]), Enrolment.expires_at > at)):
        raise DomainError("CONFLICT", "Another bounded scan is already open.")
    scan = Enrolment(actor_id=actor.id, state="WAITING", expires_at=at + timedelta(seconds=120))
    db.add(scan); db.flush()
    set_setting(db, "enrolment:" + scan.id, {"source": "REAL", "startedAt": iso(at),
                "controllerId": settings.hikvision_controller_id, "eventIds": []})
    return scan


def poll_real(db, scan):
    meta = get_setting(db, "enrolment:" + scan.id)
    if meta.get("source") != "REAL":
        raise DomainError("NOT_CONNECTED", "A simulated scan cannot be reused as real reader evidence.", 409)
    if scan.state not in {"WAITING", "DETECTED"}:
        return scan
    start = datetime.fromisoformat(meta["startedAt"])
    events = [e for e in db.scalars(select(AccessEvent).where(
        AccessEvent.simulated.is_(False), AccessEvent.controller == meta["controllerId"],
        AccessEvent.at >= start, AccessEvent.at <= scan.expires_at,
        AccessEvent.received_at >= start, AccessEvent.received_at <= scan.expires_at + timedelta(seconds=30)))
        if e.raw.get("source") == "HIKVISION" and e.raw.get("major") == 5
        and e.raw.get("deviceDoor") == "1" and e.raw.get("deviceReader") == "1"
        and e.raw.get("readerMappingVerified") and not e.raw.get("flags")
        and e.raw.get("delivery") in {"LIVE", "HISTORY"} and re.fullmatch(r"[0-9]{4,32}", e.card)]
    numbers = {e.card for e in events}
    scan.number = next(iter(numbers)) if len(numbers) == 1 else None
    scan.state = "AMBIGUOUS" if len(numbers) > 1 else "DETECTED" if numbers else "WAITING"
    meta["eventIds"] = [e.id for e in events]
    set_setting(db, "enrolment:" + scan.id, meta)
    return scan


def confirm_real(db, scan):
    poll_real(db, scan)
    if scan.state != "DETECTED" or scan.expires_at <= now():
        raise DomainError("CONFLICT", "Scan expired or did not detect exactly one fresh card.")
    if db.scalar(select(Card.id).where(Card.number == scan.number)):
        raise DomainError("CONFLICT", "Card already has a local record; inspect it rather than overwriting it.")
    if scan.number in {"1051305927", "1052625607", "1050853895", "1052815031"}:
        raise DomainError("CONFLICT", "Protected, revoked or visitor test card cannot be a new personal credential.")
