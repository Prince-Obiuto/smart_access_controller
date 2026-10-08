from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import (
    String,
    Text,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    BigInteger,
    JSON,
    Index,
    CheckConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Row:
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)


class Retained:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restored: Mapped[bool] = mapped_column(Boolean, default=False)


class Role(Base):
    __tablename__ = "roles"
    name: Mapped[str] = mapped_column(String(20), primary_key=True)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)


class Person(Row, Retained, Base):
    __tablename__ = "persons"
    kind: Mapped[str] = mapped_column(String(10))
    reference: Mapped[str] = mapped_column(String(100), unique=True)
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(40), default="")
    department: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync: Mapped[str] = mapped_column(String(20), default="PENDING")
    access_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        CheckConstraint("kind IN ('STUDENT','STAFF','VISITOR')"),
        CheckConstraint("expiry IS NULL OR start IS NOT NULL AND expiry > start"),
        CheckConstraint("status IN ('ACTIVE','DISABLED')"),
    )


class Account(Row, Base):
    __tablename__ = "accounts"
    person_id: Mapped[str] = mapped_column(ForeignKey("persons.id"), unique=True)
    role: Mapped[str] = mapped_column(ForeignKey("roles.name"), default="USER")
    password_hash: Mapped[str] = mapped_column(Text)


class AuthSession(Base):
    __tablename__ = "sessions"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    csrf: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Upload(Row, Base):
    __tablename__ = "uploads"
    person_id: Mapped[str] = mapped_column(ForeignKey("persons.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(10))
    stored_name: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(50))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Card(Row, Base):
    __tablename__ = "cards"
    number: Mapped[str] = mapped_column(String(32), unique=True)
    kind: Mapped[str] = mapped_column(String(10))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=True)
    pool_state: Mapped[str | None] = mapped_column(String(32))
    privileged: Mapped[bool] = mapped_column(Boolean, default=False)
    ever_privileged: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        CheckConstraint("kind IN ('PERSONAL','VISITOR')"),
        Index(
            "one_privileged_card",
            "privileged",
            unique=True,
            postgresql_where=text("privileged = true"),
        ),
    )


class Assignment(Row, Base):
    __tablename__ = "assignments"
    person_id: Mapped[str] = mapped_column(ForeignKey("persons.id"), index=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("cards.id"), index=True)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(12), default="PENDING")
    sync: Mapped[str] = mapped_column(String(20), default="PENDING")
    reason: Mapped[str | None] = mapped_column(String(20))
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issuer_id: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        CheckConstraint("expiry > start"),
        CheckConstraint("state IN ('PENDING','ACTIVE','REVOKED','EXPIRED','CLOSED')"),
        Index(
            "one_current_assignment_per_person",
            "person_id",
            unique=True,
            postgresql_where=text("state IN ('PENDING','ACTIVE')"),
        ),
        Index(
            "one_current_assignment_per_card",
            "card_id",
            unique=True,
            postgresql_where=text("state IN ('PENDING','ACTIVE')"),
        ),
    )


class Application(Row, Base):
    __tablename__ = "applications"
    person_id: Mapped[str] = mapped_column(ForeignKey("persons.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(24), default="DRAFT")
    evidence_id: Mapped[str | None] = mapped_column(
        ForeignKey("uploads.id", ondelete="SET NULL")
    )
    reviewer_id: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issuer_id: Mapped[str | None] = mapped_column(String(64))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_number: Mapped[str | None] = mapped_column(String(32))
    assignment_id: Mapped[str | None] = mapped_column(ForeignKey("assignments.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index(
            "one_open_application",
            "person_id",
            unique=True,
            postgresql_where=text("state <> 'ISSUED'"),
        ),
        CheckConstraint("kind IN ('FIRST','REPLACEMENT')"),
    )


class ReviewHistory(Row, Base):
    __tablename__ = "review_history"
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    action: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str | None] = mapped_column(Text)


class Visit(Row, Base):
    __tablename__ = "visits"
    person_id: Mapped[str] = mapped_column(ForeignKey("persons.id"), index=True)
    purpose: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(String(200))
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16), default="REQUESTED")
    approval: Mapped[dict | None] = mapped_column(JSON)
    assignment_id: Mapped[str | None] = mapped_column(ForeignKey("assignments.id"))
    returned: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        CheckConstraint("expiry > start"),
        Index(
            "one_open_visit",
            "person_id",
            unique=True,
            postgresql_where=text("state <> 'ENDED'"),
        ),
    )


class Controller(Row, Base):
    __tablename__ = "controllers"
    name: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100), default="Hikvision DS-K2802")
    online: Mapped[bool] = mapped_column(Boolean, default=True)
    last_communication: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Door(Row, Base):
    __tablename__ = "doors"
    controller_id: Mapped[str] = mapped_column(ForeignKey("controllers.id"))
    name: Mapped[str] = mapped_column(String(100))
    sensor: Mapped[str] = mapped_column(String(10), default="UNKNOWN")


class Reader(Row, Base):
    __tablename__ = "readers"
    door_id: Mapped[str] = mapped_column(ForeignKey("doors.id"))
    direction: Mapped[str] = mapped_column(String(8))
    __table_args__ = (
        UniqueConstraint("door_id", "direction"),
        CheckConstraint("direction IN ('ENTRY','EXIT')"),
    )


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    sync: Mapped[str] = mapped_column(String(20), default="PENDING")
    version: Mapped[int] = mapped_column(Integer, default=0)


class CalendarException(Base):
    __tablename__ = "calendar_exceptions"
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class Confirmation(Row, Base):
    __tablename__ = "confirmations"
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignments.id"), index=True)
    controller_id: Mapped[str] = mapped_column(ForeignKey("controllers.id"))
    desired: Mapped[str] = mapped_column(String(20))
    last_confirmed: Mapped[str | None] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default="PENDING")
    version: Mapped[int] = mapped_column(Integer)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("assignment_id", "controller_id"),)


class Operation(Row, Base):
    __tablename__ = "controller_operations"
    controller_id: Mapped[str] = mapped_column(ForeignKey("controllers.id"))
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(64))
    desired: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (
        UniqueConstraint("controller_id", "target_type", "target_id", "version"),
    )


class AccessEvent(Row, Retained, Base):
    __tablename__ = "access_events"
    source_key: Mapped[str] = mapped_column(String(150), unique=True)
    at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    person_id: Mapped[str | None] = mapped_column(ForeignKey("persons.id"), index=True)
    assignment_id: Mapped[str | None] = mapped_column(ForeignKey("assignments.id"))
    person: Mapped[str] = mapped_column(String(220))
    reference: Mapped[str] = mapped_column(String(100))
    card: Mapped[str] = mapped_column(String(32))
    controller: Mapped[str] = mapped_column(ForeignKey("controllers.id"))
    door: Mapped[str | None] = mapped_column(ForeignKey("doors.id"))
    reader_id: Mapped[str | None] = mapped_column(ForeignKey("readers.id"))
    direction: Mapped[str] = mapped_column(String(8))
    result: Mapped[str] = mapped_column(String(8))
    event_type: Mapped[str] = mapped_column(String(40))
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class Audit(Row, Retained, Base):
    __tablename__ = "audit_records"
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(220))
    action: Mapped[str] = mapped_column(String(50))
    target: Mapped[str] = mapped_column(String(200))
    result: Mapped[str] = mapped_column(String(20))
    person_id: Mapped[str | None] = mapped_column(ForeignKey("persons.id"))


class Notification(Row, Base):
    __tablename__ = "notifications"
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text)
    person_id: Mapped[str | None] = mapped_column(ForeignKey("persons.id"), index=True)


class NotificationRead(Base):
    __tablename__ = "notification_reads"
    notification_id: Mapped[str] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )


class Idempotency(Base):
    __tablename__ = "idempotency_keys"
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Enrolment(Row, Base):
    __tablename__ = "enrolment_sessions"
    actor_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(16), default="WAITING")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    number: Mapped[str | None] = mapped_column(String(32))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
