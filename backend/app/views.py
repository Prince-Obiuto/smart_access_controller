from sqlalchemy import select
from .models import (
    Person,
    Upload,
    Card,
    Assignment,
    Application,
    ReviewHistory,
    Visit,
    Controller,
    Door,
    Confirmation,
    Notification,
    NotificationRead,
    CalendarException,
    Schedule,
    Operation,
)
from .common import iso, identity, name, get_setting, now
from .security import capabilities


def clean(data):
    return {k: v for k, v in data.items() if v is not None}


def person_view(db, p):
    photo = db.scalar(
        select(Upload)
        .where(Upload.person_id == p.id, Upload.purpose == "PHOTO")
        .order_by(Upload.created_at.desc())
    )
    return clean(
        {
            "id": p.id,
            "type": p.kind,
            "reference": p.reference,
            "firstName": p.first_name,
            "lastName": p.last_name,
            "email": p.email,
            "phone": p.phone,
            "department": p.department,
            "status": p.status,
            "start": iso(p.start),
            "expiry": iso(p.expiry),
            "sync": p.sync,
            "photo": f"/api/uploads/{photo.id}" if photo else None,
            "accessEndedAt": iso(p.access_ended_at),
            "deletedAt": iso(p.deleted_at),
            "purgeAt": iso(p.purge_at),
            "restored": p.restored,
        }
    )


def card_view(c):
    return clean(
        {
            "id": c.id,
            "number": c.number,
            "kind": c.kind,
            "revoked": c.revoked,
            "disabled": c.disabled,
            "poolState": c.pool_state,
            "privileged": c.privileged,
        }
    )


def confirmation_view(c, read_only=False):
    return clean(
        {
            "controllerId": c.controller_id,
            "confirmed": not read_only
            and c.state == "SYNCHRONIZED"
            and c.desired == c.last_confirmed,
            "state": "OUT_OF_SYNC" if read_only else c.state,
            "intendedState": c.desired,
            "lastConfirmedState": c.last_confirmed,
            "at": iso(c.confirmed_at),
            "simulated": True,
        }
    )


def assignment_view(db, a, read_only=False, settings=None):
    conf = list(
        db.scalars(select(Confirmation).where(Confirmation.assignment_id == a.id))
    )
    controlled = settings and settings.controller_mode != "SIMULATED"
    if controlled:
        from .hikvision_writes import physical_confirmation

        views = []
        for c in conf:
            real = physical_confirmation(db, a, c)
            views.append({**confirmation_view(c, not real), "simulated": not real})
        sync = (
            a.sync
            if views and all(c["confirmed"] for c in views)
            else "FAILED"
            if a.sync == "FAILED"
            else "OUT_OF_SYNC"
        )
    else:
        views = [confirmation_view(c, read_only) for c in conf]
        sync = "OUT_OF_SYNC" if read_only else a.sync
    return clean(
        {
            "id": a.id,
            "personId": a.person_id,
            "cardId": a.card_id,
            "start": iso(a.start),
            "expiry": iso(a.expiry),
            "state": a.state,
            "sync": sync,
            "confirmations": views,
            "reason": a.reason,
            "returnedAt": iso(a.returned_at),
        }
    )


def evidence_view(e):
    return {
        "name": e.name,
        "type": e.content_type,
        "size": e.size,
        "url": f"/api/uploads/{e.id}",
    }


def app_view(db, a):
    evidence = db.get(Upload, a.evidence_id) if a.evidence_id else None
    card = db.get(Assignment, a.assignment_id) if a.assignment_id else None
    history = db.scalars(
        select(ReviewHistory)
        .where(ReviewHistory.application_id == a.id)
        .order_by(ReviewHistory.at, ReviewHistory.id)
    )
    return clean(
        {
            "id": a.id,
            "personId": a.person_id,
            "kind": a.kind,
            "state": a.state,
            "evidence": evidence_view(evidence) if evidence else None,
            "history": [
                clean(
                    {
                        "at": iso(h.at),
                        "actor": name(db, h.actor_id),
                        "action": h.action,
                        "reason": h.reason,
                    }
                )
                for h in history
            ],
            "reviewer": name(db, a.reviewer_id) if a.reviewer_id else None,
            "reviewedAt": iso(a.reviewed_at),
            "issuer": name(db, a.issuer_id) if a.issued_at else None,
            "issuedAt": iso(a.issued_at),
            "cardId": card.card_id if card else None,
            "assignmentId": a.assignment_id,
            "reason": a.reason,
        }
    )


def visit_view(v):
    return clean(
        {
            "id": v.id,
            "personId": v.person_id,
            "purpose": v.purpose,
            "host": v.host,
            "start": iso(v.start),
            "expiry": iso(v.expiry),
            "state": v.state,
            "approval": v.approval,
            "assignmentId": v.assignment_id,
            "returned": v.returned,
        }
    )


def event_view(e):
    return clean(
        {
            "id": e.id,
            "at": iso(e.at),
            "receivedAt": iso(e.received_at),
            "personId": e.person_id,
            "person": e.person,
            "reference": e.reference,
            "card": e.card,
            "assignmentId": e.assignment_id,
            "controller": e.controller,
            "door": e.door,
            "readerId": e.reader_id,
            "direction": e.direction,
            "result": e.result,
            "type": e.event_type,
            "simulated": e.simulated,
            "deviceDoor": e.raw.get("deviceDoor"),
            "deviceReader": e.raw.get("deviceReader"),
            "timestampFlags": e.raw.get("flags", []),
            "readerMappingVerified": bool(e.raw.get("readerMappingVerified"))
            if not e.simulated
            else None,
            "deletedAt": iso(e.deleted_at),
            "purgeAt": iso(e.purge_at),
            "restored": e.restored,
        }
    )


def audit_view(a):
    return clean(
        {
            "id": a.id,
            "at": iso(a.at),
            "actor": a.actor,
            "action": a.action,
            "target": a.target,
            "result": a.result,
            "personId": a.person_id,
            "deletedAt": iso(a.deleted_at),
            "purgeAt": iso(a.purge_at),
            "restored": a.restored,
        }
    )


def snapshot(db, actor, settings, at=None):
    read_only = settings.controller_mode != "SIMULATED"
    write_scope = get_setting(
        db, "hikvision:write-scope:" + settings.hikvision_controller_id
    )
    read_state = (
        get_setting(db, "hikvision:" + settings.hikvision_controller_id)
        if read_only
        else {}
    )
    communicated = read_state.get("lastCommunication")
    connected = False
    if read_only and read_state.get("state") == "CONNECTED" and communicated:
        try:
            connected = (at or now()) - __import__("datetime").datetime.fromisoformat(
                communicated
            ) < __import__("datetime").timedelta(seconds=150)
        except (ValueError, TypeError):
            pass
    read_status = "CONNECTED" if connected else read_state.get("state", "BLOCKED")
    read_code = read_state.get("code", "NOT_CONNECTED")
    if read_only and not connected and read_status == "CONNECTED":
        read_status, read_code = "FAILED", "STALE_DEVICE_COMMUNICATION"
    perms = capabilities(settings)
    staff = actor.role != "USER"
    people = list(
        db.scalars(
            select(Person).where(Person.purged == False, Person.deleted_at.is_(None))
        )
    )
    assignments = list(db.scalars(select(Assignment)))
    if not staff:
        people = [p for p in people if p.id == actor.person_id]
        assignments = [a for a in assignments if a.person_id == actor.person_id]
    cards = list(db.scalars(select(Card)))
    if not staff:
        cards = [c for c in cards if any(a.card_id == c.id for a in assignments)]
    applications = list(db.scalars(select(Application).order_by(Application.id)))
    if actor.role not in perms["review"]:
        applications = [a for a in applications if a.person_id == actor.person_id]
    visits = list(db.scalars(select(Visit)))
    if actor.role not in perms["visitors"]:
        visits = [v for v in visits if v.person_id == actor.person_id]
    devices = []
    if actor.role in perms["dashboard"] or actor.role in perms["settings"]:
        for d in db.scalars(select(Door)):
            controller = db.get(Controller, d.controller_id)
            devices.append(
                clean(
                    {
                        "id": controller.id,
                        "name": controller.name,
                        "model": read_state.get("model", controller.model)
                        if controller.id == settings.hikvision_controller_id
                        else controller.model,
                        "online": connected
                        and controller.id == settings.hikvision_controller_id
                        if read_only
                        else controller.online,
                        "lastCommunication": communicated
                        if connected
                        and controller.id == settings.hikvision_controller_id
                        else None
                        if read_only
                        else iso(controller.last_communication),
                        "doorId": d.id,
                        "door": d.name,
                        "sensor": "UNKNOWN" if read_only else d.sensor,
                    }
                )
            )
    notices = []
    for n in db.scalars(select(Notification).order_by(Notification.at.desc())):
        if not staff and n.person_id != actor.person_id:
            continue
        notices.append(
            clean(
                {
                    "id": n.id,
                    "at": iso(n.at),
                    "severity": n.severity,
                    "title": n.title,
                    "detail": n.detail,
                    "personId": n.person_id,
                    "read": db.get(NotificationRead, (n.id, actor.id)) is not None,
                }
            )
        )
    schedule = db.get(Schedule, "normal")
    payment = get_setting(db, "payment")
    designated = db.scalar(select(Card).where(Card.privileged == True))
    assignment_views = [
        assignment_view(db, a, read_only, settings) for a in assignments
    ]
    real_designation = False
    if settings.controller_mode == "HIKVISION_CONTROLLED" and designated:
        latest = db.scalar(
            select(Operation)
            .where(
                Operation.target_type == "PRIVILEGE",
                Operation.target_id == designated.id,
            )
            .order_by(Operation.created_at.desc())
            .limit(1)
        )
        group = (
            list(
                db.scalars(
                    select(Operation).where(
                        Operation.target_type == "PRIVILEGE",
                        Operation.target_id == designated.id,
                        Operation.version == latest.version,
                    )
                )
            )
            if latest
            else []
        )
        real_designation = (
            bool(group)
            and len(group) == len(list(db.scalars(select(Controller))))
            and all(
                o.status == "CONFIRMED"
                and o.data.get("simulated") is False
                and o.data.get("readbackVerified")
                for o in group
            )
        )

    def person_sync(p):
        if not read_only:
            return p.sync
        own = [
            a
            for a in assignment_views
            if a["personId"] == p.id and a["state"] in {"ACTIVE", "PENDING"}
        ]
        return (
            p.sync
            if settings.controller_mode != "SIMULATED"
            and own
            and all(a["sync"] == "SYNCHRONIZED" for a in own)
            else "OUT_OF_SYNC"
        )

    return {
        "identity": identity(db, actor),
        "people": [{**person_view(db, p), "sync": person_sync(p)} for p in people],
        "cards": [
            {
                **card_view(c),
                **(
                    {
                        "privileged": bool(
                            real_designation and designated and c.id == designated.id
                        )
                    }
                    if read_only
                    else {}
                ),
            }
            for c in cards
        ],
        "assignments": assignment_views,
        "applications": [app_view(db, a) for a in applications],
        "visits": [visit_view(v) for v in visits],
        "devices": devices,
        "notifications": notices,
        "schedule": {
            **schedule.data,
            "sync": "OUT_OF_SYNC" if read_only else schedule.sync,
            "exceptions": [
                e.data
                for e in db.scalars(
                    select(CalendarException).order_by(CalendarException.date)
                )
            ],
        },
        "settings": {
            **payment,
            **(
                {"privilegedCardId": designated.id}
                if designated and (not read_only or real_designation)
                else {}
            ),
        },
        "controls": get_setting(db, "simulation")
        if actor.role == "SUPER_ADMIN"
        else {
            "offline": False,
            "failDisable": False,
            "delayActivation": False,
            "unlockTimeout": False,
        },
        "clock": iso(at or now()),
        "controllerMode": settings.controller_mode,
        # Broad physical actions remain unavailable; reader capture is GET-only.
        "controllerWritesAvailable": settings.controller_mode == "SIMULATED",
        "readerEnrolmentAvailable": settings.controller_mode != "SIMULATED",
        "adminVisitorRequestAvailable": True,
        "controllerTestWritesEnabled": settings.controller_mode
        == "HIKVISION_CONTROLLED"
        and bool(write_scope.get("enabled")),
        "controllerWriteMessage": "Scoped test writes enabled; existing backlog held for individual review."
        if settings.controller_mode == "HIKVISION_CONTROLLED"
        and write_scope.get("enabled")
        else "Physical writes blocked; no active test scope.",
        "controllerReadStatus": read_status if read_only else "SIMULATED",
        "controllerReadMessage": read_code if read_only else "SIMULATED",
        "controllerReaderMappingVerified": bool(
            get_setting(
                db, "hikvision:reader-map:" + settings.hikvision_controller_id
            ).get("verified")
        )
        if read_only
        else False,
        "controllerHistoryStatus": read_state.get("history", {}).get(
            "status", "UNKNOWN"
        )
        if read_only
        else "SIMULATED",
        "controllerLiveStatus": read_state.get("stages", {})
        .get("subscription", {})
        .get("status", "UNKNOWN")
        if read_only
        else "SIMULATED",
        "simulationControlsEnabled": not read_only
        and settings.simulation_controls_enabled
        and actor.role == "SUPER_ADMIN",
    }
