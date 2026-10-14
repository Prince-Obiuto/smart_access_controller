"""Transactional application workflows; no HTTP or controller transport code."""

from datetime import timedelta
from sqlalchemy import select, delete, update
from .models import (
    Person,
    Account,
    Card,
    Assignment,
    Application,
    ReviewHistory,
    Visit,
    Controller,
    Door,
    Schedule,
    CalendarException,
    Confirmation,
    Operation,
    Notification,
    NotificationRead,
    Audit,
    AccessEvent,
)
from .common import now, audit, notify, name, get_setting, set_setting, iso
from .security import DomainError, require
from .schemas import timestamp

ACTIVE = ["ACTIVE", "PENDING"]
ENDED = ["REVOKED", "EXPIRED", "CLOSED"]


class AccessService:
    def __init__(self, db, actor, settings, clock=None):
        self.db, self.actor, self.settings, self.at = (
            db,
            actor,
            settings,
            clock or now(),
        )
        self.ops = []
        self.message = "Saved."

    def need(self, cap):
        require(self.db, self.actor, cap, self.settings)

    def get(self, model, id):
        row = self.db.get(model, id)
        if not row:
            raise DomainError("VALIDATION_ERROR", "Record not found.", 404)
        return row

    def person(self, id):
        row = self.get(Person, id)
        if row.purged or row.deleted_at:
            raise DomainError("CONFLICT", "Person details are not currently available.")
        return row

    def valid_dates(self, start, expiry):
        try:
            start, expiry = timestamp(start), timestamp(expiry)
        except (ValueError, TypeError, AttributeError) as exc:
            raise DomainError(
                "VALIDATION_ERROR",
                "Valid timezone-aware start and expiry are required.",
            ) from exc
        if expiry <= start:
            raise DomainError("VALIDATION_ERROR", "Access expiry must follow start.")
        return start, expiry

    def history(self, application, action, reason=None):
        self.db.add(
            ReviewHistory(
                application_id=application.id,
                actor_id=self.actor.id,
                at=self.at,
                action=action,
                reason=reason,
            )
        )

    def queue(
        self, target_type, target_id, desired, version, controller_id=None, data=None
    ):
        devices = (
            [self.get(Controller, controller_id)]
            if controller_id
            else list(self.db.scalars(select(Controller)))
        )
        if not devices:
            raise DomainError("CONFLICT", "Configure a target controller first.")
        for device in devices:
            scope = get_setting(self.db, "hikvision:write-scope:" + device.id)
            op = Operation(
                controller_id=device.id,
                target_type=target_type,
                target_id=target_id,
                desired=desired,
                version=version,
                status="PENDING",
                actor_id=self.actor.id if self.actor else None,
                created_at=self.at,
                data={
                    **(data or {}),
                    **(
                        {"writeEpoch": scope["epoch"]}
                        if self.settings.controller_mode == "HIKVISION_CONTROLLED"
                        and scope.get("enabled")
                        else {}
                    ),
                },
            )
            self.db.add(op)
            self.db.flush()
            self.ops.append(op.id)

    def assignment_queue(self, a, desired):
        a.version += 1
        a.sync = "PENDING"
        self.db.execute(
            update(Operation)
            .where(
                Operation.target_type == "ASSIGNMENT",
                Operation.target_id == a.id,
                Operation.status == "PENDING",
            )
            .values(status="SUPERSEDED", completed_at=self.at)
        )
        for device in self.db.scalars(select(Controller)):
            c = self.db.scalar(
                select(Confirmation).where(
                    Confirmation.assignment_id == a.id,
                    Confirmation.controller_id == device.id,
                )
            )
            if not c:
                c = Confirmation(
                    assignment_id=a.id,
                    controller_id=device.id,
                    desired=desired,
                    version=a.version,
                    state="PENDING",
                )
                self.db.add(c)
            else:
                c.desired = desired
                c.state = "PENDING"
                c.version = a.version
        self.queue(
            "ASSIGNMENT",
            a.id,
            desired,
            a.version,
            data={"start": iso(a.start), "expiry": iso(a.expiry), "schedule": "normal"},
        )

    def revoke(self, a, reason):
        a.state = "REVOKED"
        a.reason = reason
        a.ended_at = self.at
        card = self.get(Card, a.card_id)
        if card.kind == "PERSONAL":
            card.revoked = True
        if card.kind == "VISITOR":
            card.pool_state = (
                reason if reason in ("MISSING", "DAMAGED") else "QUARANTINED"
            )
        if card.kind == "VISITOR":
            person = self.get(Person, a.person_id)
            person.access_ended_at = person.access_ended_at or self.at
            person.purge_at = person.purge_at or person.access_ended_at + timedelta(
                days=45
            )
            for visit in self.db.scalars(
                select(Visit).where(Visit.assignment_id == a.id)
            ):
                visit.state = "ENDED"
        self.assignment_queue(a, "DISABLED")
        self.message = "Revoked locally — controller disable not confirmed."

    def removed(self, a):
        confirmations = list(
            self.db.scalars(
                select(Confirmation).where(Confirmation.assignment_id == a.id)
            )
        )
        devices = list(self.db.scalars(select(Controller)))
        from .hikvision_writes import physical_confirmation

        return len(confirmations) == len(devices) > 0 and all(
            c.desired == "DISABLED"
            and c.last_confirmed == "DISABLED"
            and c.state == "SYNCHRONIZED"
            and (
                self.settings.controller_mode == "SIMULATED"
                or physical_confirmation(self.db, a, c)
            )
            for c in confirmations
        )

    def old_removed(self, person_id):
        ended = self.db.scalars(
            select(Assignment)
            .join(Card)
            .where(
                Assignment.person_id == person_id,
                Assignment.state.in_(ENDED),
                Card.kind == "PERSONAL",
            )
        )
        return all(self.removed(a) for a in ended)

    def assign(self, p, card, start, expiry):
        start, expiry = self.valid_dates(start, expiry)
        if p.status != "ACTIVE" or expiry <= self.at:
            raise DomainError(
                "CONFLICT", "Account must be active and validity must not have expired."
            )
        if card.revoked or not card.disabled:
            raise DomainError(
                "CONFLICT",
                "Card must be usable and confirmed disabled before assignment.",
            )
        if (
            card.kind == "VISITOR"
            and self.settings.controller_mode == "HIKVISION_CONTROLLED"
        ):
            from .hikvision_writes import physical_confirmation

            devices = list(self.db.scalars(select(Controller)))
            for device in devices:
                previous = self.db.scalar(
                    select(Assignment)
                    .where(Assignment.card_id == card.id)
                    .order_by(Assignment.created_at.desc())
                )
                confirmation = (
                    self.db.scalar(
                        select(Confirmation).where(
                            Confirmation.assignment_id == previous.id,
                            Confirmation.controller_id == device.id,
                        )
                    )
                    if previous
                    else None
                )
                pool_op = self.db.scalar(
                    select(Operation)
                    .where(
                        Operation.target_type == "POOL",
                        Operation.target_id == card.id,
                        Operation.controller_id == device.id,
                    )
                    .order_by(Operation.created_at.desc())
                )
                proven = (
                    (
                        confirmation
                        and confirmation.last_confirmed == "DISABLED"
                        and physical_confirmation(self.db, previous, confirmation)
                    )
                    if previous
                    else (
                        pool_op
                        and pool_op.status == "CONFIRMED"
                        and pool_op.data.get("simulated") is False
                        and pool_op.data.get("readbackVerified")
                    )
                )
                if not proven:
                    raise DomainError(
                        "CONFLICT",
                        "Visitor card disable requires physical readback before reuse.",
                    )
        if self.db.scalar(
            select(Assignment).where(
                Assignment.state.in_(ACTIVE),
                (Assignment.person_id == p.id) | (Assignment.card_id == card.id),
            )
        ):
            raise DomainError(
                "CONFLICT", "Person or physical card already has a current assignment."
            )
        a = Assignment(
            person_id=p.id,
            card_id=card.id,
            start=start,
            expiry=expiry,
            state="PENDING",
            sync="PENDING",
            issuer_id=self.actor.id,
            version=0,
            created_at=self.at,
        )
        self.db.add(a)
        self.db.flush()
        self.assignment_queue(a, "ENABLED")
        return a

    def issue(self, app, number):
        self.need("issue")
        if app.state not in ("APPROVED", "ISSUANCE_PENDING"):
            raise DomainError(
                "CONFLICT", "Payment approval must precede card issuance."
            )
        p = self.person(app.person_id)
        self.valid_dates(p.start, p.expiry)
        if app.requested_number and app.requested_number != number:
            raise DomainError("CONFLICT", "Retry using the pending card number.")
        if app.assignment_id:
            a = self.get(Assignment, app.assignment_id)
            if a.state not in ACTIVE:
                raise DomainError(
                    "CONFLICT",
                    "The pending assignment has ended; it cannot be activated.",
                )
            if not self.old_removed(p.id):
                raise DomainError(
                    "CONFLICT", "Previous credential removal remains unconfirmed."
                )
            self.assignment_queue(a, "ENABLED")
            self.message = (
                "Activation remains pending until device readback."
                if self.settings.controller_mode != "SIMULATED"
                else "Activation remains pending until simulated confirmation."
            )
            return
        card = self.db.scalar(select(Card).where(Card.number == number))
        if card and (
            card.kind != "PERSONAL"
            or card.revoked
            or not card.disabled
            or self.db.scalar(
                select(Assignment).where(
                    Assignment.card_id == card.id, Assignment.state.in_(ACTIVE)
                )
            )
        ):
            raise DomainError(
                "CONFLICT",
                "Card is revoked, assigned, reserved for visitors, or removal is unconfirmed.",
            )
        app.requested_number = number
        app.issuer_id = self.actor.id
        app.state = "ISSUANCE_PENDING"
        if app.kind == "REPLACEMENT":
            for old in list(
                self.db.scalars(
                    select(Assignment).where(
                        Assignment.person_id == p.id, Assignment.state.in_(ACTIVE)
                    )
                )
            ):
                self.revoke(old, "DAMAGED")
        self.db.flush()
        if not self.old_removed(p.id):
            notify(
                self.db,
                "Replacement activation blocked",
                "Old credential removal must be confirmed by every target controller.",
                "CRITICAL",
                p.id,
                self.at,
            )
            self.message = "Replacement pending: old-card removal must be confirmed."
            return
        if not card:
            card = Card(
                number=number,
                kind="PERSONAL",
                disabled=True,
                revoked=False,
                privileged=False,
                ever_privileged=False,
            )
            self.db.add(card)
            self.db.flush()
        a = self.assign(p, card, p.start, p.expiry)
        app.assignment_id = a.id
        self.history(app, "ISSUANCE_PENDING")
        self.message = (
            "Activation remains pending until device readback."
            if self.settings.controller_mode != "SIMULATED"
            else "Activation remains pending until simulated confirmation."
        )

    def execute(self, c):
        t = c["type"]
        target = c.get("id") or c.get("personId") or c.get("assignmentId") or t
        person_id = None
        if t == "person":
            self.need("people")
            data = c["data"]
            p = (
                self.person(c["id"])
                if c.get("id")
                else Person(sync="PENDING", purged=False, restored=False)
            )
            account = (
                self.db.scalar(select(Account).where(Account.person_id == p.id))
                if p.id
                else None
            )
            if account and account.role != "USER":
                self.need("settings")
            previous = (p.start, p.expiry)
            p.kind = data["type"]
            p.reference = data["reference"]
            p.email = data["email"]
            p.first_name = data["firstName"]
            p.last_name = data["lastName"]
            p.phone = data["phone"]
            p.department = data["department"]
            p.status = data["status"]
            p.start = timestamp(data["start"]) if data.get("start") else None
            p.expiry = timestamp(data["expiry"]) if data.get("expiry") else None
            self.db.add(p)
            self.db.flush()
            target = person_id = p.id
            if previous != (p.start, p.expiry):
                p.sync = "PENDING"
                self.update_assignments(p)
            if p.status == "DISABLED":
                for a in self.db.scalars(
                    select(Assignment).where(
                        Assignment.person_id == p.id, Assignment.state.in_(ACTIVE)
                    )
                ):
                    self.revoke(a, "NOT_PERMITTED")
        elif t == "dates":
            self.need("people")
            p = self.person(c["personId"])
            account = self.db.scalar(select(Account).where(Account.person_id == p.id))
            if account and account.role != "USER":
                self.need("settings")
            if p.kind == "VISITOR":
                raise DomainError(
                    "CONFLICT",
                    "Visitor validity is recorded through its approved visit.",
                )
            p.start, p.expiry = self.valid_dates(c["start"], c["expiry"])
            p.sync = "PENDING"
            self.update_assignments(p)
            person_id = p.id
            self.message = "Dates saved — controller synchronization pending."
        elif t == "application":
            p = self.person(self.actor.person_id)
            if p.kind == "VISITOR":
                raise DomainError(
                    "FORBIDDEN", "Visitors have no payment application.", 403
                )
            if self.db.scalar(
                select(Application).where(
                    Application.person_id == p.id, Application.state != "ISSUED"
                )
            ):
                raise DomainError("CONFLICT", "An open application already exists.")
            exists = (
                self.db.scalar(select(Assignment).where(Assignment.person_id == p.id))
                is not None
            )
            if (c["kind"] == "FIRST" and exists) or (
                c["kind"] == "REPLACEMENT" and not exists
            ):
                raise DomainError(
                    "CONFLICT",
                    "Choose the request type appropriate to the assignment history.",
                )
            a = Application(person_id=p.id, kind=c["kind"], state="DRAFT")
            self.db.add(a)
            self.db.flush()
            self.history(a, "DRAFT")
            target = a.id
            person_id = p.id
        elif t == "review":
            self.need("review")
            a = self.get(Application, c["id"])
            if a.state != "SUBMITTED":
                raise DomainError(
                    "CONFLICT", "Only submitted evidence can be reviewed."
                )
            if not c["approve"] and not (c.get("reason") or "").strip():
                raise DomainError(
                    "VALIDATION_ERROR", "A visible rejection reason is required."
                )
            a.state = "APPROVED" if c["approve"] else "REJECTED"
            a.reason = None if c["approve"] else c["reason"].strip()
            a.reviewer_id = self.actor.id
            a.reviewed_at = self.at
            self.history(a, a.state, a.reason)
            person_id = a.person_id
            notify(
                self.db,
                "Payment review: " + a.state,
                a.reason or "Approved. Issuance is a separate recorded action.",
                "INFO",
                a.person_id,
                self.at,
            )
        elif t == "issue":
            self.need("issue")
            self.issue(self.get(Application, c["id"]), c["number"])
        elif t == "revoke":
            if not c.get("own"):
                self.need("issue")
            a = self.get(Assignment, c["assignmentId"])
            if c.get("own"):
                if a.person_id != self.actor.person_id or c["reason"] != "MISSING":
                    raise DomainError(
                        "FORBIDDEN", "Only your own missing card may be reported.", 403
                    )
            else:
                self.need("issue")
            if a.state not in ACTIVE:
                raise DomainError("CONFLICT", "Assignment has already ended.")
            if self.get(Card, a.card_id).privileged and not c.get("own"):
                self.need("settings")
            self.revoke(a, c["reason"])
            person_id = a.person_id
        elif t == "retry-sync":
            self.need("issue")
            if (
                sum(bool(c.get(k)) for k in ("assignmentId", "personId", "schedule"))
                != 1
            ):
                raise DomainError(
                    "VALIDATION_ERROR", "Select one synchronization target."
                )
            if c.get("assignmentId"):
                a = self.get(Assignment, c["assignmentId"])
                desired = (
                    "DISABLED"
                    if a.state in ENDED
                    else "ENABLED"
                    if a.state == "PENDING"
                    else "UPDATED"
                )
                if desired != "DISABLED" and not self.old_removed(a.person_id):
                    raise DomainError(
                        "CONFLICT", "Old removal must be confirmed first."
                    )
                self.assignment_queue(a, desired)
            elif c.get("personId"):
                p = self.person(c["personId"])
                self.valid_dates(p.start, p.expiry)
                p.sync = "PENDING"
                self.update_assignments(p, queue=True)
                if not self.db.scalar(
                    select(Assignment).where(
                        Assignment.person_id == p.id, Assignment.state.in_(ACTIVE)
                    )
                ):
                    self.queue(
                        "PERSON",
                        p.id,
                        "UPDATED",
                        int(self.at.timestamp() * 1000000),
                        data={"start": iso(p.start), "expiry": iso(p.expiry)},
                    )
            else:
                schedule = self.get(Schedule, "normal")
                schedule.version += 1
                schedule.sync = "PENDING"
                self.queue(
                    "SCHEDULE",
                    "normal",
                    "UPDATED",
                    schedule.version,
                    data={
                        "schedule": schedule.data,
                        "exceptions": [
                            e.data for e in self.db.scalars(select(CalendarException))
                        ],
                    },
                )
        elif t in {"visit", "visitor-request"}:
            if t == "visitor-request":
                self.need("visitors")
            p = self.person(c["personId"] if t == "visitor-request" else self.actor.person_id)
            if p.kind != "VISITOR":
                raise DomainError("FORBIDDEN", "Visitor accounts only.", 403)
            if p.access_ended_at:
                raise DomainError(
                    "CONFLICT",
                    "This temporary account has ended; restoration never renews access or extends retention.",
                )
            start, expiry = self.valid_dates(c["start"], c["expiry"])
            if expiry <= self.at:
                raise DomainError(
                    "VALIDATION_ERROR", "Visit expiry must be in the future."
                )
            if self.db.scalar(
                select(Visit).where(Visit.person_id == p.id, Visit.state != "ENDED")
            ):
                raise DomainError("CONFLICT", "An open visit already exists.")
            v = Visit(
                person_id=p.id,
                purpose=c["purpose"],
                host=c["host"],
                start=start,
                expiry=expiry,
                state="REQUESTED",
                returned=False,
            )
            self.db.add(v)
            self.db.flush()
            target = v.id
            person_id = p.id
        elif t == "visitor-approve":
            self.need("visitors")
            v = self.get(Visit, c["id"])
            if v.state != "REQUESTED" or v.expiry <= self.at:
                raise DomainError("CONFLICT", "Visit is not eligible for approval.")
            v.approval = {
                "at": iso(self.at),
                "actor": name(self.db, self.actor.id),
                "actorId": self.actor.id,
                "action": "EXTERNAL_APPROVAL_CONFIRMED",
                "reason": c["note"],
            }
            v.state = "APPROVED"
            person_id = v.person_id
        elif t == "visitor-assign":
            self.need("visitors")
            v = self.get(Visit, c["id"])
            card = self.get(Card, c["cardId"])
            if v.state != "APPROVED" or not v.approval:
                raise DomainError(
                    "CONFLICT", "External approval must be recorded first."
                )
            if (
                card.kind != "VISITOR"
                or card.pool_state != "AVAILABLE_DISABLED"
                or not card.disabled
            ):
                raise DomainError(
                    "CONFLICT",
                    "Pool card is not available with confirmed disabled permission.",
                )
            a = self.assign(self.person(v.person_id), card, v.start, v.expiry)
            v.assignment_id = a.id
            v.state = "PENDING"
            card.pool_state = "ASSIGNMENT_PENDING"
            p = self.person(v.person_id)
            p.start = v.start
            p.expiry = v.expiry
            person_id = p.id
        elif t == "visitor-return":
            self.need("visitors")
            v = self.get(Visit, c["id"])
            if not v.assignment_id or v.returned:
                raise DomainError("CONFLICT", "Visit has no outstanding card.")
            if c["condition"] == "GOOD" and not c["returned"]:
                raise DomainError(
                    "VALIDATION_ERROR", "Record physical card return before checkout."
                )
            a = self.get(Assignment, v.assignment_id)
            card = self.get(Card, a.card_id)
            a.state = "CLOSED"
            a.ended_at = a.ended_at or self.at
            a.returned_at = self.at if c["returned"] else None
            v.state = "ENDED"
            v.returned = c["returned"]
            card.pool_state = (
                "RETURN_PENDING_DISABLE" if c["condition"] == "GOOD" else c["condition"]
            )
            p = self.person(v.person_id)
            p.access_ended_at = p.access_ended_at or self.at
            p.purge_at = p.purge_at or p.access_ended_at + timedelta(days=45)
            self.assignment_queue(a, "DISABLED")
            person_id = p.id
        elif t == "schedule":
            self.need("schedules")
            schedule = self.get(Schedule, "normal")
            data = dict(c["schedule"])
            exceptions = data.pop("exceptions", [])
            data.pop("sync", None)
            schedule.version += 1
            self.db.execute(
                update(Operation)
                .where(
                    Operation.target_type == "SCHEDULE", Operation.status == "PENDING"
                )
                .values(status="SUPERSEDED", completed_at=self.at)
            )
            schedule.data = data
            schedule.sync = "PENDING"
            self.db.execute(delete(CalendarException))
            for ex in exceptions:
                self.db.add(CalendarException(date=ex["date"], data=ex))
            self.message = "Schedule saved — application to controllers is pending."
        elif t == "unlock":
            self.need("unlock")
            if self.settings.controller_mode != "SIMULATED":
                raise DomainError(
                    "NOT_CONNECTED", "Door commands unavailable in read-only mode.", 409
                )
            d = self.get(Door, c["doorId"])
            if c["confirmed"] is not True:
                raise DomainError("VALIDATION_ERROR", "Confirm the named door.")
            self.queue(
                "DOOR",
                d.id,
                "TIMED_RELEASE",
                int(self.at.timestamp() * 1000000),
                d.controller_id,
                {"seconds": 5},
            )
            self.message = "Timed unlock queued; physical door state UNKNOWN."
            notify(
                self.db,
                "Remote timed unlock",
                f"{name(self.db, self.actor.id)} requested a simulated 5-second release for {d.name}. Physical sensor UNKNOWN.",
                "WARNING",
                at=self.at,
            )
        elif t == "settings":
            self.need("settings")
            data = c["settings"]
            if data["uploadLimit"] > self.settings.upload_limit:
                raise DomainError(
                    "VALIDATION_ERROR",
                    "Upload limit exceeds the configured server maximum.",
                )
            set_setting(
                self.db,
                "payment",
                {
                    "payment": data["payment"],
                    "uploadLimit": min(data["uploadLimit"], self.settings.upload_limit),
                },
            )
        elif t == "role":
            self.need("settings")
            account = self.db.scalar(
                select(Account).where(Account.person_id == c["personId"])
            )
            if not account:
                raise DomainError("CONFLICT", "Person has no account.")
            if account.id == self.actor.id and c["role"] != "SUPER_ADMIN":
                raise DomainError(
                    "CONFLICT", "Cannot remove your own Super Admin authority."
                )
            account.role = c["role"]
            if account.role != "SUPER_ADMIN":
                for assignment in self.db.scalars(
                    select(Assignment)
                    .join(Card)
                    .where(
                        Assignment.person_id == account.person_id,
                        Assignment.state.in_(ACTIVE),
                        Card.privileged == True,
                    )
                ):
                    self.revoke(assignment, "NOT_PERMITTED")
            person_id = account.person_id
        elif t == "device":
            self.need("settings")
            self.get(Controller, c["id"]).name = c["name"]
        elif t == "pool-card":
            self.need("settings")
            card = Card(
                number=c["number"],
                kind="VISITOR",
                disabled=True,
                revoked=False,
                privileged=False,
                ever_privileged=False,
                pool_state="QUARANTINED",
            )
            self.db.add(card)
            self.db.flush()
            self.queue("POOL", card.id, "DISABLED", 1)
            target = card.id
            self.message = (
                "Pool card awaits device disable readback before availability."
                if self.settings.controller_mode != "SIMULATED"
                else "Pool card awaits simulated disable confirmation before availability."
            )
        elif t == "designate":
            self.need("settings")
            self.designate(c["cardId"])
        elif t == "notice-read":
            n = self.get(Notification, c["id"])
            if self.actor.role == "USER" and n.person_id != self.actor.person_id:
                raise DomainError("FORBIDDEN", "Notification not permitted.", 403)
            if not self.db.get(NotificationRead, (n.id, self.actor.id)):
                self.db.add(
                    NotificationRead(notification_id=n.id, account_id=self.actor.id)
                )
        elif t == "restore":
            self.need("recovery")
            model = {
                "people": Person,
                "events": AccessEvent,
                "audits": Audit,
            }[c["family"]]
            row = self.get(model, c["id"])
            if not row.deleted_at or not row.purge_at or row.purge_at <= self.at:
                raise DomainError("CONFLICT", "Record is no longer recoverable.")
            row.deleted_at = None
            row.restored = True
            self.message = "Restored. Original purge deadline retained; physical access is not reactivated."
        elif t == "demo-controls":
            self.simulation()
            old = get_setting(self.db, "simulation")
            set_setting(self.db, "simulation", c["controls"])
            for d in self.db.scalars(select(Controller)):
                d.online = not c["controls"]["offline"]
            if old.get("offline") != c["controls"]["offline"]:
                notify(
                    self.db,
                    "Controller disconnected"
                    if c["controls"]["offline"]
                    else "Controller reconnected",
                    "Simulated connection change.",
                    "WARNING",
                    at=self.at,
                )
        elif t == "demo-event":
            self.simulation()
            from .events import simulate_event

            simulate_event(self.db, c["event"], self.at, person_id=self.actor.person_id)
        else:
            raise DomainError("VALIDATION_ERROR", "Unsupported command.")
        audit(
            self.db,
            self.actor,
            t,
            target,
            "PENDING" if self.ops else "SUCCESS",
            person_id,
            self.at,
        )
        return {
            "intendedState": "UPDATED",
            "confirmations": [],
            "result": "PENDING" if self.ops else "CONFIRMED",
            "message": self.message,
            "operationIds": list(self.ops),
        }

    def update_assignments(self, p, queue=False):
        for a in self.db.scalars(
            select(Assignment).where(
                Assignment.person_id == p.id, Assignment.state.in_(ACTIVE)
            )
        ):
            if not p.start or not p.expiry:
                raise DomainError(
                    "CONFLICT", "Cannot clear validity for a current assignment."
                )
            a.start = p.start
            a.expiry = p.expiry
            a.sync = "OUT_OF_SYNC"
            if not queue:
                a.version += 1
                self.db.execute(
                    update(Operation)
                    .where(
                        Operation.target_type == "ASSIGNMENT",
                        Operation.target_id == a.id,
                        Operation.status == "PENDING",
                    )
                    .values(status="SUPERSEDED", completed_at=self.at)
                )
            if queue:
                self.assignment_queue(
                    a, "ENABLED" if a.state == "PENDING" else "UPDATED"
                )

    def simulation(self):
        self.need("settings")
        if (
            self.settings.controller_mode != "SIMULATED"
            or not self.settings.simulation_controls_enabled
        ):
            raise DomainError(
                "FORBIDDEN", "Simulation controls are explicitly disabled.", 403
            )

    def designate(self, card_id):
        self.need("settings")
        card = self.get(Card, card_id)
        a = self.db.scalar(
            select(Assignment).where(
                Assignment.card_id == card.id, Assignment.state == "ACTIVE"
            )
        )
        account = (
            self.db.scalar(select(Account).where(Account.person_id == a.person_id))
            if a
            else None
        )
        if (
            card.kind != "PERSONAL"
            or card.revoked
            or not a
            or a.sync != "SYNCHRONIZED"
            or not account
            or account.role != "SUPER_ADMIN"
        ):
            raise DomainError(
                "CONFLICT",
                "Choose an active synchronized personal card of a Super Admin.",
            )
        previous = list(
            self.db.scalars(
                select(Card).where(
                    Card.ever_privileged == True,
                    Card.id != card.id,
                    Card.disabled == False,
                )
            )
        )
        for old in previous:
            old_a = self.db.scalar(
                select(Assignment)
                .where(Assignment.card_id == old.id)
                .order_by(Assignment.start.desc())
            )
            if old_a:
                if old_a.state in ACTIVE:
                    self.revoke(old_a, "NOT_PERMITTED")
                else:
                    self.assignment_queue(old_a, "DISABLED")
        self.db.execute(
            update(Operation)
            .where(Operation.target_type == "PRIVILEGE", Operation.status == "PENDING")
            .values(status="SUPERSEDED", completed_at=self.at)
        )
        set_setting(
            self.db,
            "pending_designation",
            {"cardId": card.id, "actorId": self.actor.id},
        )
        if previous:
            self.message = "Designation pending: previous privileged credential must be disabled first."
        else:
            self.queue(
                "PRIVILEGE", card.id, "UPDATED", int(self.at.timestamp() * 1000000)
            )
            self.message = (
                "Unrestricted test schedule awaits device readback."
                if self.settings.controller_mode != "SIMULATED"
                else "Unrestricted schedule pending simulated confirmation."
            )
