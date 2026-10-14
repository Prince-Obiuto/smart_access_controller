import asyncio
import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.responses import FileResponse, JSONResponse

from . import password_recovery as recovery_password, controller_access
from .common import audit, get_setting, identity, initialize, iso, now
from .config import Settings
from .db import Database, domain_lock
from .enrolment import confirm_real, poll_real, start_real
from .jobs import worker
from .models import (
    AccessEvent,
    Account,
    Application,
    Audit,
    Enrolment,
    Idempotency,
    Person,
    ReviewHistory,
    Upload,
)
from .operations import operation_result, process_operations
from .records import export_records, list_records
from .retention import run_retention
from .schemas import (
    AdminRecovery,
    CommandUnion,
    ControllerAuthorization,
    ControllerExecution,
    Filters,
    Login,
    RecoveryRequest,
    RecoveryReset,
    Registration,
)
from .security import (
    COOKIE,
    PASSWORDS,
    DomainError,
    active_account,
    check_csrf,
    create_session,
    login_account,
    require,
    session_for,
    set_cookie,
)
from .service import AccessService
from .uploads import remove_upload, save_upload
from .views import audit_view, event_view, person_view, snapshot


class BodyLimit:
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        length = headers.get(b"content-length", b"0")
        try:
            too_large = int(length) > self.limit
        except ValueError:
            too_large = True
        if too_large:
            return await JSONResponse(
                {
                    "code": "VALIDATION_ERROR",
                    "message": "Request body exceeds upload limit.",
                },
                413,
            )(scope, receive, send)
        size = 0

        async def limited_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > self.limit:
                raise HTTPException(413, "Request body exceeds upload limit.")
            return message

        await self.app(scope, limited_receive, send)


def create_app(settings=None, database=None):
    settings = settings or Settings()
    database = database or Database(settings)

    @asynccontextmanager
    async def lifespan(app):
        with database.session() as db:
            domain_lock(db)
            initialize(db, settings)
            run_retention(db, settings)
            db.commit()
        task = (
            asyncio.create_task(worker(database, settings))
            if settings.jobs_enabled
            else None
        )
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="IFT E-Library Access Control",
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.database = database
    app.state.settings = settings
    app.add_middleware(BodyLimit, limit=settings.upload_limit + 65536)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(DomainError)
    async def error(request, exc):
        return JSONResponse({"code": exc.code, "message": exc.message}, exc.status)

    from .hikvision_writes import WriteBlocked

    @app.exception_handler(WriteBlocked)
    async def controller_blocked(request, exc):
        return JSONResponse({"code": "CONTROLLER_UNAVAILABLE", "message": str(exc)}, 409)

    @app.exception_handler(IntegrityError)
    async def conflict(request, exc):
        return JSONResponse(
            {
                "code": "CONFLICT",
                "message": "Record conflicts with an existing identity, request or current card assignment.",
            },
            409,
        )

    @app.exception_handler(RequestValidationError)
    async def validation(request, exc):
        return JSONResponse(
            {
                "code": "VALIDATION_ERROR",
                "message": "Invalid input. "
                + "; ".join(str(e["msg"]) for e in exc.errors()),
                "fields": [".".join(map(str, e["loc"])) for e in exc.errors()],
            },
            422,
        )

    def db_dep():
        with database.session() as db:
            try:
                yield db
            finally:
                db.rollback()

    DB = Annotated[object, Depends(db_dep)]

    def actor_for(db, request, write=False):
        session = (
            check_csrf(db, request, settings) if write else session_for(db, request)
        )
        return active_account(db, session)

    def finish(db, result, application_id=None):
        db.commit()  # Local intended state and durable outbox precede transport.
        process_operations(db, settings)
        process_operations(db, settings)
        if application_id:
            application = db.get(Application, application_id)
            if application and application.assignment_id:
                from .models import Assignment, Operation

                assignment = db.get(Assignment, application.assignment_id)
                additional = list(
                    db.scalars(
                        select(Operation.id).where(
                            Operation.target_type == "ASSIGNMENT",
                            Operation.target_id == assignment.id,
                            Operation.version == assignment.version,
                        )
                    )
                )
                result = {
                    **result,
                    "operationIds": list(
                        dict.fromkeys(result.get("operationIds", []) + additional)
                    ),
                }
        response = operation_result(db, result, settings)
        if result.get("message", "").startswith("Replacement pending"):
            response["message"] = (
                (
                    "Replacement activation verified by SDK readback."
                    if settings.controller_mode == "HIKVISION_CONTROLLED"
                    else "Replacement issued with simulated activation confirmation."
                )
                if application and application.state == "ISSUED"
                else "Replacement pending: inspect old removal and activation confirmations."
            )
            if (
                application
                and application.state != "ISSUED"
                and response["result"] == "CONFIRMED"
            ):
                response["result"] = "PENDING"
        return response

    @app.get("/api/health")
    def health():
        try:
            database.health()
        except Exception:
            return JSONResponse(
                {"status": "unavailable", "controllerMode": settings.controller_mode},
                503,
            )
        return {"status": "healthy", "controllerMode": settings.controller_mode}

    @app.get("/api/auth/csrf")
    def csrf(request: Request, response: Response, db: DB):
        session = session_for(db, request)
        if not session:
            session, token = create_session(db, None, settings)
            db.commit()
            set_cookie(response, token, settings)
        return {"csrfToken": session.csrf}

    @app.get("/api/auth/me")
    def me(request: Request, db: DB):
        session = session_for(db, request)
        if not session or not session.account_id:
            return None
        return identity(db, active_account(db, session))

    @app.post("/api/auth/login")
    def login(body: Login, request: Request, response: Response, db: DB):
        previous = check_csrf(db, request, settings)
        domain_lock(db)
        account = login_account(db, body.email, body.password, request)
        row, token = create_session(db, account, settings, previous)
        audit(db, account, "LOGIN", "Authentication")
        db.commit()
        set_cookie(response, token, settings)
        return identity(db, account)

    @app.get("/api/auth/recovery-options")
    def recovery_options(db: DB):
        return {
            "available": recovery_password.delivery_available(db, settings),
            "contact": settings.recovery_contact,
        }

    @app.post("/api/auth/forgot-password")
    def forgot_password(
        body: RecoveryRequest, request: Request, tasks: BackgroundTasks, db: DB
    ):
        response_at = time.monotonic() + 0.25
        check_csrf(db, request, settings)
        domain_lock(db)
        address = body.email.strip().lower()
        ip = request.client.host if request.client else "local"
        ip_ok = recovery_password.throttle(db, "request-ip|" + ip, 20)
        email_ok = ip_ok and recovery_password.throttle(
            db, "request-email|" + address, 3
        )
        available = recovery_password.delivery_available(db, settings)
        if available and ip_ok and email_ok:
            person = db.scalar(select(Person).where(Person.email == address))
            account = (
                db.scalar(select(Account).where(Account.person_id == person.id))
                if person
                else None
            )
            if recovery_password.eligible(db, account):
                token = recovery_password.issue(db, account)
                tasks.add_task(
                    recovery_password.deliver, database, settings, account.id, token
                )
        db.commit()
        time.sleep(max(0, response_at - time.monotonic()))
        return {
            "message": recovery_password.REQUEST_MESSAGE
            if available
            else recovery_password.UNAVAILABLE_MESSAGE
        }

    @app.post("/api/auth/reset-password")
    def reset_password(
        body: RecoveryReset,
        request: Request,
        response: Response,
        tasks: BackgroundTasks,
        db: DB,
    ):
        check_csrf(db, request, settings)
        domain_lock(db)
        ip = request.client.host if request.client else "local"
        ip_ok = recovery_password.throttle(db, "attempt-ip|" + ip, 30)
        token_ok = ip_ok and recovery_password.throttle(
            db, "attempt-token|" + body.token, 10
        )
        db.commit()  # Failed attempts survive subsequent validation rollback.
        if not ip_ok or not token_ok:
            raise DomainError(
                "RATE_LIMITED",
                "Too many recovery attempts. Try again in 20 minutes.",
                429,
            )
        account_id = recovery_password.consume(
            db, body.token, body.password, body.confirmation
        )
        db.commit()
        response.delete_cookie(
            COOKIE,
            path="/api",
            secure=settings.secure_cookie,
            httponly=True,
            samesite="lax",
        )
        tasks.add_task(recovery_password.notify_changed, database, settings, account_id)
        return {"message": "Password changed. Sign in with your new password."}

    @app.post("/api/auth/admin-recovery")
    def admin_recovery(
        body: AdminRecovery, request: Request, tasks: BackgroundTasks, db: DB
    ):
        actor = actor_for(db, request, True)
        domain_lock(db)
        target = db.scalar(select(Account).where(Account.person_id == body.personId))
        recovery_password.authorize_admin(db, actor, target, body.identityConfirmed)
        if not recovery_password.delivery_available(db, settings):
            raise DomainError(
                "DELIVERY_UNAVAILABLE", recovery_password.UNAVAILABLE_MESSAGE, 503
            )
        if not recovery_password.throttle(db, "admin-account|" + target.id, 3):
            db.commit()
            raise DomainError("RATE_LIMITED", "Try again in 20 minutes.", 429)
        token = recovery_password.issue(db, target, actor, "ADMIN_RECOVERY_INITIATED")
        db.commit()
        tasks.add_task(recovery_password.deliver, database, settings, target.id, token)
        return {"message": recovery_password.REQUEST_MESSAGE}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, db: DB):
        session = check_csrf(db, request, settings)
        if session.account_id:
            audit(db, db.get(Account, session.account_id), "LOGOUT", "Authentication")
        db.delete(session)
        db.commit()
        response.delete_cookie(
            COOKIE,
            path="/api",
            secure=settings.secure_cookie,
            httponly=True,
            samesite="lax",
        )
        return {"ok": True}

    @app.post("/api/auth/register")
    def register(
        request: Request,
        response: Response,
        db: DB,
        payload: Annotated[str, Form()],
        photo: Annotated[UploadFile | None, File()] = None,
    ):
        previous = check_csrf(db, request, settings)
        domain_lock(db)
        try:
            data = Registration.model_validate_json(payload)
        except ValidationError as exc:
            raise DomainError(
                "VALIDATION_ERROR", "; ".join(str(e["msg"]) for e in exc.errors()), 422
            ) from exc
        p = Person(
            kind=data.type,
            reference="VIS-" + uuid4().hex[:12].upper()
            if data.type == "VISITOR"
            else data.reference,
            first_name=data.firstName,
            last_name=data.lastName,
            email=data.email,
            phone=data.phone,
            department=data.department,
            status="ACTIVE",
            sync="PENDING",
            purged=False,
            restored=False,
        )
        db.add(p)
        db.flush()
        account = Account(
            person_id=p.id, role="USER", password_hash=PASSWORDS.hash(data.password)
        )
        db.add(account)
        db.flush()
        upload = None
        try:
            if photo:
                content = photo.file.read(settings.upload_limit + 1)
                upload = save_upload(
                    db,
                    settings,
                    p.id,
                    "PHOTO",
                    photo.filename,
                    content,
                    settings.upload_limit,
                )
            row, token = create_session(db, account, settings, previous)
            audit(db, account, "REGISTER", p.id, person_id=p.id)
            db.commit()
        except Exception:
            if upload:
                (settings.upload_dir / upload.stored_name).unlink(missing_ok=True)
            raise
        set_cookie(response, token, settings)
        return identity(db, account)

    @app.get("/api/workspace")
    def workspace(request: Request, db: DB):
        return snapshot(db, actor_for(db, request), settings)

    @app.get("/api/controller/access")
    def controller_access_status(request: Request, db: DB):
        actor = actor_for(db, request)
        controller_access.authorize(db, actor, settings)
        return controller_access.status(db, settings)

    @app.get("/api/controller/unlock")
    def controller_unlock_status(request: Request, db: DB):
        from .hikvision_unlock import application_prepare
        actor = actor_for(db, request)
        require(db, actor, "unlock", settings)
        if settings.controller_mode == "SIMULATED":
            raise DomainError("NOT_CONNECTED", "Real controller access is unavailable in demo mode.", 409)
        db.commit()
        return application_prepare(settings, db)

    @app.post("/api/controller/unlock")
    def controller_unlock(body: ControllerExecution, request: Request, db: DB):
        from .hikvision_unlock import application_command
        actor = actor_for(db, request, True)
        if not body.confirmed:
            raise DomainError("VALIDATION_ERROR", "Confirm one five-second Door 1 release.")
        return application_command(db, settings, actor, request.headers.get("idempotency-key"))

    @app.post("/api/controller/unlock/confirm-physical")
    def controller_unlock_confirm_physical(body: ControllerExecution, request: Request, db: DB):
        from .hikvision_unlock import confirm_physical_unlock
        actor = actor_for(db, request, True)
        if not body.confirmed:
            raise DomainError(
                "VALIDATION_ERROR",
                "Confirm that Door 1 released and returned to its secured state.",
            )
        return confirm_physical_unlock(db, settings, actor)

    @app.post("/api/controller/unlock-duration")
    def controller_unlock_duration(body: ControllerExecution, request: Request, db: DB):
        from .hikvision_unlock import application_command
        actor = actor_for(db, request, True)
        if not body.confirmed:
            raise DomainError("VALIDATION_ERROR", "Confirm the five-second Door 1 release setting.")
        return application_command(db, settings, actor, request.headers.get("idempotency-key"), configure=True)

    @app.post("/api/controller/access/authorize")
    def authorize_controller_cards(body: ControllerAuthorization, request: Request, db: DB):
        actor = actor_for(db, request, True)
        if not body.confirmed or len({c.number for c in body.cards}) != len(body.cards):
            raise DomainError("VALIDATION_ERROR", "Confirm the distinct selected cards.")
        cards = {c.number: {"kind": c.kind, "personIds": c.personIds} for c in body.cards}
        return controller_access.activate(db, settings, actor, cards)

    @app.post("/api/controller/access/close")
    def close_controller_access(request: Request, db: DB):
        return controller_access.close(db, settings, actor_for(db, request, True))

    @app.get("/api/controller/cards/{number}")
    def read_controller_card(number: str, request: Request, db: DB):
        controller_access.authorize(db, actor_for(db, request), settings)
        return controller_access.read_card(db, settings, number)

    @app.post("/api/controller/operations/{id}/execute")
    def execute_controller_operation(id: str, body: ControllerExecution, request: Request, db: DB):
        actor = actor_for(db, request, True)
        if not body.confirmed:
            raise DomainError("VALIDATION_ERROR", "Confirm this card operation.")
        return controller_access.execute(db, settings, actor, id)

    @app.post("/api/commands")
    def commands(body: CommandUnion, request: Request, db: DB):
        actor = actor_for(db, request, True)
        domain_lock(db)
        db.refresh(actor)
        key = request.headers.get("idempotency-key")
        if not key or len(key) > 80:
            raise DomainError("VALIDATION_ERROR", "An Idempotency-Key is required.")
        value = body.model_dump(mode="json", exclude_none=True)
        fingerprint = hashlib.sha256(
            json.dumps(value, sort_keys=True).encode()
        ).hexdigest()
        previous = db.get(Idempotency, (actor.id, key))
        if previous:
            if previous.fingerprint != fingerprint:
                raise DomainError(
                    "CONFLICT", "Idempotency key was used with a different command."
                )
            return operation_result(db, previous.result, settings)
        result = AccessService(db, actor, settings).execute(value)
        entry = Idempotency(
            account_id=actor.id,
            key=key,
            fingerprint=fingerprint,
            result=result,
            at=now(),
        )
        db.add(entry)
        outcome = finish(db, result, body.id if value["type"] == "issue" else None)
        entry.result = outcome
        db.commit()
        return outcome

    @app.post("/api/me/profile")
    def profile(
        request: Request,
        db: DB,
        phone: Annotated[str, Form()],
        photo: Annotated[UploadFile | None, File()] = None,
    ):
        actor = actor_for(db, request, True)
        domain_lock(db)
        if not phone.strip() or len(phone) > 40:
            raise DomainError(
                "VALIDATION_ERROR",
                "Phone is required and must be no longer than 40 characters.",
            )
        p = db.get(Person, actor.person_id)
        p.phone = phone.strip()
        new = None
        if photo:
            new = save_upload(
                db,
                settings,
                p.id,
                "PHOTO",
                photo.filename,
                photo.file.read(settings.upload_limit + 1),
                settings.upload_limit,
            )
        audit(db, actor, "PROFILE_UPDATE", p.id, person_id=p.id)
        db.commit()
        if new:
            for old in list(
                db.scalars(
                    select(Upload).where(
                        Upload.person_id == p.id,
                        Upload.purpose == "PHOTO",
                        Upload.id != new.id,
                    )
                )
            ):
                remove_upload(db, settings, old)
            db.commit()
        return {
            "intendedState": "UPDATED",
            "confirmations": [],
            "result": "CONFIRMED",
            "message": "Profile saved.",
        }

    @app.post("/api/applications/{id}/evidence")
    def evidence(
        id: str, request: Request, db: DB, file: Annotated[UploadFile, File()]
    ):
        actor = actor_for(db, request, True)
        domain_lock(db)
        application = db.get(Application, id)
        if not application or application.person_id != actor.person_id:
            raise DomainError(
                "FORBIDDEN", "Only the applicant may submit evidence.", 403
            )
        key = request.headers.get("idempotency-key")
        if not key or len(key) > 80:
            raise DomainError("VALIDATION_ERROR", "An Idempotency-Key is required.")
        limit = min(get_setting(db, "payment")["uploadLimit"], settings.upload_limit)
        content = file.file.read(limit + 1)
        fingerprint = hashlib.sha256(id.encode() + b"evidence:" + content).hexdigest()
        previous = db.get(Idempotency, (actor.id, key))
        if previous:
            if previous.fingerprint != fingerprint:
                raise DomainError(
                    "CONFLICT", "Idempotency key was used with different evidence."
                )
            return previous.result
        if application.state not in ["DRAFT", "REJECTED"]:
            raise DomainError("CONFLICT", "Evidence has already been submitted.")
        if db.get(Person, actor.person_id).kind == "VISITOR":
            raise DomainError("FORBIDDEN", "Visitors do not pay card fees.", 403)
        upload = save_upload(
            db, settings, actor.person_id, "EVIDENCE", file.filename, content, limit
        )
        old = (
            db.get(Upload, application.evidence_id) if application.evidence_id else None
        )
        application.evidence_id = upload.id
        application.state = "SUBMITTED"
        db.add(
            ReviewHistory(
                application_id=id, actor_id=actor.id, at=now(), action="SUBMITTED"
            )
        )
        audit(db, actor, "EVIDENCE_SUBMITTED", id, person_id=actor.person_id)
        result = {
            "intendedState": "SUBMITTED",
            "confirmations": [],
            "result": "CONFIRMED",
            "message": "Evidence submitted for review; not automatically approved.",
        }
        db.add(
            Idempotency(
                account_id=actor.id,
                key=key,
                fingerprint=fingerprint,
                result=result,
                at=now(),
            )
        )
        db.commit()
        if old:
            remove_upload(db, settings, old)
            db.commit()
        return result

    @app.get("/api/uploads/{id}")
    def upload(id: str, request: Request, db: DB):
        actor = actor_for(db, request)
        row = db.get(Upload, id)
        if not row:
            raise DomainError("VALIDATION_ERROR", "Upload not found.", 404)
        owner = db.get(Person, row.person_id)
        if owner.purged or owner.deleted_at:
            raise DomainError("FORBIDDEN", "Owner details are not available.", 403)
        if row.person_id != actor.person_id:
            cap = "review" if row.purpose == "EVIDENCE" else "people"
            require(db, actor, cap, settings)
        path = (settings.upload_dir / row.stored_name).resolve()
        if path.parent != settings.upload_dir.resolve() or not path.is_file():
            raise DomainError("VALIDATION_ERROR", "Upload not found.", 404)
        return FileResponse(
            path,
            media_type=row.content_type,
            filename=row.name,
            content_disposition_type="inline"
            if row.purpose == "PHOTO"
            else "attachment",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @app.get("/api/records/{family}")
    def records(family: Literal["events", "audits"], request: Request, db: DB):
        try:
            filters = Filters.model_validate(dict(request.query_params))
        except ValidationError as exc:
            raise DomainError(
                "VALIDATION_ERROR", "Invalid record filters.", 422
            ) from exc
        return list_records(db, actor_for(db, request), settings, family, filters)

    @app.post("/api/exports/{family}/{format}")
    def export(
        family: Literal["events", "audits"],
        format: Literal["csv", "pdf"],
        body: Filters,
        request: Request,
        db: DB,
    ):
        actor = actor_for(db, request, True)
        content, mime = export_records(db, actor, settings, family, body, format)
        db.commit()
        return Response(
            content,
            media_type=mime,
            headers={
                "Content-Disposition": f'attachment; filename="ift-{family}.{format}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/recovery")
    def recovery(request: Request, db: DB):
        actor = actor_for(db, request)
        require(db, actor, "recovery", settings)
        return {
            "people": [
                person_view(db, p)
                for p in db.scalars(
                    select(Person).where(
                        Person.deleted_at.is_not(None), Person.purged == False
                    )
                )
            ],
            "events": [
                event_view(e)
                for e in db.scalars(
                    select(AccessEvent).where(AccessEvent.deleted_at.is_not(None))
                )
            ],
            "audits": [
                audit_view(a)
                for a in db.scalars(select(Audit).where(Audit.deleted_at.is_not(None)))
            ],
        }

    def scan_get(db, actor, id):
        require(db, actor, "issue", settings)
        scan = db.get(Enrolment, id)
        if not scan or scan.actor_id != actor.id:
            raise DomainError("FORBIDDEN", "Enrolment session is not yours.", 403)
        if scan.state in ["WAITING", "DETECTED"] and scan.expires_at <= now():
            scan.state = "TIMED_OUT"
        return scan

    def scan_view(scan):
        return {
            "id": scan.id,
            "state": scan.state,
            "expiresAt": iso(scan.expires_at),
            **({"number": scan.number} if scan.number else {}),
        }

    @app.post("/api/enrolment-sessions")
    def scan_start(request: Request, db: DB):
        actor = actor_for(db, request, True)
        require(db, actor, "issue", settings)
        if settings.controller_mode != "SIMULATED":
            domain_lock(db)
            scan = start_real(db, actor, settings)
        else:
            scan = Enrolment(
                actor_id=actor.id,
                state="WAITING",
                expires_at=now() + timedelta(seconds=30),
            )
        db.add(scan)
        db.flush()
        audit(db, actor, "SCAN_STARTED", scan.id)
        db.commit()
        return scan_view(scan)

    @app.get("/api/enrolment-sessions/{id}")
    def scan_poll(id: str, request: Request, db: DB):
        actor = actor_for(db, request)
        scan = scan_get(db, actor, id)
        if settings.controller_mode != "SIMULATED":
            poll_real(db, scan)
        elif scan.state == "WAITING":
            scan.state = "DETECTED"
            scan.number = "00" + str(secrets.randbelow(10**10)).zfill(10)
        db.commit()
        return scan_view(scan)

    @app.post("/api/enrolment-sessions/{id}/{action}")
    def scan_action(
        id: str, action: Literal["confirm", "cancel"], request: Request, db: DB
    ):
        actor = actor_for(db, request, True)
        scan = scan_get(db, actor, id)
        if action == "cancel" and scan.state not in {
            "WAITING",
            "DETECTED",
            "AMBIGUOUS",
        }:
            return {"ok": True}
        if action == "confirm" and settings.controller_mode != "SIMULATED":
            confirm_real(db, scan)
        if action == "confirm" and scan.state != "DETECTED":
            raise DomainError("CONFLICT", "Scan expired, cancelled or not detected.")
        scan.state = "CONFIRMED" if action == "confirm" else "CANCELLED"
        audit(db, actor, "SCAN_" + action.upper(), id)
        db.commit()
        return scan.number if action == "confirm" else {"ok": True}

    return app


app = create_app()
