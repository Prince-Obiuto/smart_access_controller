from calendar import monthrange
from datetime import timedelta
from sqlalchemy import select, delete
from .models import (
    Person,
    Account,
    AuthSession,
    Assignment,
    Visit,
    Card,
    Upload,
    AccessEvent,
    Audit,
    Notification,
    Enrolment,
    Idempotency,
    LoginAttempt,
)
from .common import now, audit
from .service import AccessService, ACTIVE
from .uploads import remove_upload


def months(value, count):
    total = value.year * 12 + value.month - 1 + count
    year, month = divmod(total, 12)
    month += 1
    return value.replace(
        year=year, month=month, day=min(value.day, monthrange(year, month)[1])
    )


def run_retention(db, settings, at=None):
    at = at or now()
    service = AccessService(db, None, settings, at)
    for a in list(
        db.scalars(
            select(Assignment).where(
                Assignment.state.in_(ACTIVE), Assignment.expiry <= at
            )
        )
    ):
        a.state = "EXPIRED"
        a.ended_at = a.expiry
        service.assignment_queue(a, "DISABLED")
        card = db.get(Card, a.card_id)
        if card.kind == "VISITOR":
            card.pool_state = "QUARANTINED"
        audit(db, None, "ASSIGNMENT_EXPIRED", a.id, "PENDING", a.person_id, at)
    for visit in db.scalars(
        select(Visit).where(Visit.state != "ENDED", Visit.expiry <= at)
    ):
        visit.state = "ENDED"
        p = db.get(Person, visit.person_id)
        p.access_ended_at = p.access_ended_at or visit.expiry
        p.purge_at = p.purge_at or p.access_ended_at + timedelta(days=45)
    for p in list(
        db.scalars(
            select(Person).where(
                Person.kind == "VISITOR",
                Person.access_ended_at.is_not(None),
                Person.purged == False,
            )
        )
    ):
        p.purge_at = p.purge_at or p.access_ended_at + timedelta(days=45)
        if at >= p.purge_at:
            account = db.scalar(select(Account).where(Account.person_id == p.id))
            actor_name = p.first_name + " " + p.last_name
            for upload in list(
                db.scalars(select(Upload).where(Upload.person_id == p.id))
            ):
                remove_upload(db, settings, upload)
            for event in db.scalars(
                select(AccessEvent).where(AccessEvent.person_id == p.id)
            ):
                event.person = "Anonymous visitor"
                event.reference = ""
                event.raw = {"source": "SIMULATED", "anonymized": True}
            for record in db.scalars(
                select(Audit).where(
                    (Audit.person_id == p.id) | (Audit.actor == actor_name)
                )
            ):
                if record.actor == actor_name:
                    record.actor = "Anonymous visitor"
                    record.actor_id = None
                record.target = "Anonymous historical reference"
            for visit in db.scalars(select(Visit).where(Visit.person_id == p.id)):
                visit.purpose = "Purged"
                visit.host = "Purged"
                visit.approval = None
            # Account aliases may have been copied into staff-visible notifications.
            for notice in list(db.scalars(select(Notification))):
                if notice.person_id == p.id:
                    db.delete(notice)
                elif actor_name in notice.detail:
                    notice.detail = (
                        "Anonymous historical operation. Personal copy purged."
                    )
            if account:
                db.delete(account)
            p.email = None
            p.reference = "ANON-" + p.id
            p.first_name = "Anonymous"
            p.last_name = "visitor"
            p.phone = ""
            p.department = ""
            p.status = "DISABLED"
            p.purged = True
            p.deleted_at = p.deleted_at or p.access_ended_at + timedelta(days=15)
        elif at >= p.access_ended_at + timedelta(days=15) and not p.restored:
            p.deleted_at = p.deleted_at or p.access_ended_at + timedelta(days=15)
    for model, soft_date, purge_date in [
        (
            AccessEvent,
            lambda r: (r.at or r.received_at) + timedelta(days=90),
            lambda r: (r.at or r.received_at) + timedelta(days=120),
        ),
        (
            Audit,
            lambda r: months(r.at, 6),
            lambda r: months(r.at, 6) + timedelta(days=30),
        ),
    ]:
        for row in list(db.scalars(select(model))):
            row.purge_at = row.purge_at or purge_date(row)
            if at >= row.purge_at:
                db.delete(row)
            elif at >= soft_date(row) and not row.restored:
                row.deleted_at = row.deleted_at or soft_date(row)
    db.execute(delete(AuthSession).where(AuthSession.expires_at <= now()))
    db.execute(
        delete(Enrolment).where(Enrolment.expires_at < now() - timedelta(days=1))
    )
    db.execute(
        delete(LoginAttempt).where(LoginAttempt.until < now() - timedelta(days=1))
    )
    db.execute(delete(Idempotency).where(Idempotency.at < now() - timedelta(days=7)))
    db.flush()
