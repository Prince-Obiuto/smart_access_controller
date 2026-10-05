from datetime import datetime, timezone
from sqlalchemy import select
from .models import (
    Account,
    Person,
    Audit,
    Notification,
    Setting,
    Schedule,
    Controller,
    Door,
    Reader,
    Role,
)
from .security import capabilities


def now():
    return datetime.now(timezone.utc)


def iso(value):
    return value.isoformat() if value else None


def all_rows(db, model):
    return list(db.scalars(select(model)))


def get_setting(db, key, default=None):
    row = db.get(Setting, key)
    return row.value if row else (default if default is not None else {})


def set_setting(db, key, value):
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def name(db, account_id):
    account = db.get(Account, account_id) if account_id else None
    person = db.get(Person, account.person_id) if account else None
    return (
        person.first_name + " " + person.last_name
        if person and not person.purged
        else "Anonymous / local operator"
    )


def identity(db, account):
    person = db.get(Person, account.person_id)
    return {
        "id": account.id,
        "personId": person.id,
        "role": account.role,
        "name": name(db, account.id),
        "email": person.email,
    }


def audit(db, actor, action, target, result="SUCCESS", person_id=None, at=None):
    row = Audit(
        at=at or now(),
        actor_id=actor.id if actor else None,
        actor=name(db, actor.id) if actor else "Local system",
        action=action,
        target=str(target),
        result=result,
        person_id=person_id,
    )
    db.add(row)
    return row


def notify(db, title, detail, severity="INFO", person_id=None, at=None):
    row = Notification(
        at=at or now(),
        severity=severity,
        title=title,
        detail=detail,
        person_id=person_id,
    )
    db.add(row)
    return row


def initialize(db, settings):
    permissions = capabilities(settings)
    for role in ["SUPER_ADMIN", "ADMIN", "MANAGER", "MANAGEMENT", "USER"]:
        caps = [cap for cap, roles in permissions.items() if role in roles]
        row = db.get(Role, role)
        if row:
            row.capabilities = caps
        else:
            db.add(Role(name=role, capabilities=caps))
    if not db.get(Setting, "payment"):
        set_setting(
            db,
            "payment",
            {
                "payment": "Example only — payment instructions have not been configured by the owner. No bank account is configured.",
                "uploadLimit": settings.upload_limit,
            },
        )
    if not db.get(Setting, "simulation"):
        set_setting(
            db,
            "simulation",
            {
                "offline": False,
                "failDisable": False,
                "delayActivation": False,
                "unlockTimeout": False,
            },
        )
    if not db.get(Schedule, "normal"):
        db.add(
            Schedule(
                id="normal",
                data={"weekdays": [1, 2, 3, 4, 5], "opens": "08:00", "closes": "18:00"},
                sync="PENDING",
                version=0,
            )
        )
    if not db.get(Controller, "controller-1"):
        db.add(
            Controller(
                id="controller-1",
                name="Library controller (simulated)",
                model="Hikvision DS-K2802",
                online=True,
            )
        )
        db.flush()
        db.add(
            Door(
                id="door-1",
                controller_id="controller-1",
                name="E-Library main door",
                sensor="UNKNOWN",
            )
        )
        db.flush()
        for direction in ["ENTRY", "EXIT"]:
            db.add(
                Reader(
                    id="reader-" + direction.lower(),
                    door_id="door-1",
                    direction=direction,
                )
            )
    db.flush()
