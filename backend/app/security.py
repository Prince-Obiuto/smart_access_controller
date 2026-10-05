import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError, VerificationError
from sqlalchemy import select
from .models import AuthSession, Account, Person, Audit, LoginAttempt

PASSWORDS = PasswordHasher()
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(20))
COOKIE = "ift_session"


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc)


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        self.code, self.message, self.status = (
            code,
            message,
            status if status is not None else (409 if code == "CONFLICT" else 400),
        )


def capabilities(settings):
    return json.loads(settings.permissions_path.read_text())


def require(db, account, cap, settings):
    if not account or account.role not in capabilities(settings)[cap]:
        raise DomainError("FORBIDDEN", "Your role cannot perform this action.", 403)


def active_account(db, session):
    if not session or not session.account_id:
        raise DomainError("UNAUTHENTICATED", "Please sign in.", 401)
    account = db.get(Account, session.account_id)
    person = db.get(Person, account.person_id) if account else None
    if not account or not person or person.purged or person.deleted_at:
        raise DomainError("UNAUTHENTICATED", "Account is no longer available.", 401)
    if person.status != "ACTIVE":
        raise DomainError("FORBIDDEN", "Account is disabled.", 403)
    return account


def session_for(db, request):
    token = request.cookies.get(COOKIE)
    row = db.get(AuthSession, digest(token)) if token else None
    return row if row and row.expires_at > utcnow() else None


def create_session(db, account, settings, old=None):
    if old:
        db.delete(old)
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    row = AuthSession(
        digest=digest(token),
        account_id=account.id if account else None,
        csrf=csrf,
        expires_at=utcnow() + timedelta(hours=settings.session_hours),
    )
    db.add(row)
    db.flush()
    return row, token


def set_cookie(response, token, settings):
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.secure_cookie,
        samesite="lax",
        path="/api",
    )


def check_csrf(db, request, settings):
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in settings.origins:
        raise DomainError("FORBIDDEN", "Request origin is not allowed.", 403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise DomainError("FORBIDDEN", "Cross-site write rejected.", 403)
    session = session_for(db, request)
    token = request.headers.get("x-csrf-token", "")
    if not session or not token or not secrets.compare_digest(token, session.csrf):
        raise DomainError(
            "FORBIDDEN", "CSRF validation failed. Reload and try again.", 403
        )
    return session


def password_matches(hash, password):
    try:
        return PASSWORDS.verify(hash, password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


def login_account(db, email, password, request):
    # Persistent throttling by opaque email/IP digest, with a bounded window.
    key = digest(
        email.lower() + "|" + (request.client.host if request.client else "local")
    )
    attempt = db.get(LoginAttempt, key)
    now = utcnow()
    if attempt and attempt.until > now and attempt.count >= 10:
        raise DomainError(
            "FORBIDDEN", "Too many login attempts. Try again in 15 minutes.", 429
        )
    p = db.scalar(
        select(Person).where(
            Person.email == email.strip().lower(),
            Person.purged == False,
            Person.deleted_at.is_(None),
        )
    )
    account = db.scalar(select(Account).where(Account.person_id == p.id)) if p else None
    matched = password_matches(
        account.password_hash if account else DUMMY_HASH, password
    )
    if not account or not matched or p.status != "ACTIVE":
        if not attempt:
            attempt = LoginAttempt(key=key, count=0, until=now + timedelta(minutes=15))
            db.add(attempt)
        if attempt.until <= now:
            attempt.count = 0
            attempt.until = now + timedelta(minutes=15)
        attempt.count += 1
        db.add(
            Audit(
                at=now,
                actor="Unauthenticated",
                action="LOGIN_FAILED",
                target="Authentication",
                result="DENIED",
            )
        )
        db.commit()  # Preserve throttle and audit even though login is rejected.
        raise DomainError("UNAUTHENTICATED", "Invalid email or password.", 401)
    if attempt:
        db.delete(attempt)
    if PASSWORDS.check_needs_rehash(account.password_hash):
        account.password_hash = PASSWORDS.hash(password)
    return account
