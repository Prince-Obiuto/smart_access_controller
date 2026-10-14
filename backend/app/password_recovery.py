"""Recovery changes credentials only; no access-domain/controller operations."""

import secrets
import smtplib
from datetime import timedelta

from sqlalchemy import delete, update

from . import mail
from .common import audit, get_setting, notify, set_setting
from .db import domain_lock
from .models import Account, AuthSession, LoginAttempt, PasswordReset, Person
from .security import PASSWORDS, DomainError, digest, utcnow

REQUEST_MESSAGE = "If the account is eligible, a recovery email will be attempted. Contact the administrator if no email arrives."
UNAVAILABLE_MESSAGE = "Email recovery is unavailable. Contact the administrator for identity-verified recovery."


def delivery_available(db, settings):
    return mail.available(settings) and not get_setting(
        db, "recovery-delivery", {}
    ).get("failed", False)


def throttle(db, key, limit):
    now = utcnow()
    row = db.get(LoginAttempt, digest("recovery|" + key))
    if not row:
        row = LoginAttempt(
            key=digest("recovery|" + key), count=0, until=now + timedelta(minutes=20)
        )
        db.add(row)
    if row.until <= now:
        row.count, row.until = 0, now + timedelta(minutes=20)
    row.count = min(row.count + 1, limit + 1)
    return row.count <= limit


def eligible(db, account):
    p = db.get(Person, account.person_id) if account else None
    return (
        p if p and not p.purged and not p.deleted_at and p.status == "ACTIVE" else None
    )


def issue(db, account, actor=None, action="PASSWORD_RECOVERY_REQUESTED"):
    if not eligible(db, account):
        raise DomainError("FORBIDDEN", "Account is not eligible for recovery.", 403)
    token = secrets.token_urlsafe(32)
    db.add(
        PasswordReset(
            digest=digest(token),
            account_id=account.id,
            expires_at=utcnow() + timedelta(minutes=20),
        )
    )
    audit(db, actor, action, account.person_id, person_id=account.person_id)
    db.flush()
    return token


def deliver(database, settings, account_id, token):
    # Runs after the same public response for known/unknown accounts. Token exists
    # only in memory and SMTP payload; database/audit stores a hash, never a link.
    with database.session() as db:
        account = db.get(Account, account_id)
        person = eligible(db, account)
        if not person:
            return
        link = settings.application_url.rstrip("/") + "/reset-password#token=" + token
        try:
            mail.send(
                settings,
                person.email,
                "Reset your IFT E-Library password",
                f"Use this single-use link within 20 minutes:\n{link}\nIf you did not request recovery, ignore this email.\n{settings.recovery_contact}",
            )
        except (OSError, smtplib.SMTPException, ValueError):
            domain_lock(db)
            row = db.get(PasswordReset, digest(token))
            if row:
                row.consumed_at = utcnow()
            set_setting(db, "recovery-delivery", {"failed": True})
            audit(
                db,
                None,
                "RECOVERY_DELIVERY_FAILED",
                account.person_id,
                result="FAILED",
                person_id=account.person_id,
            )
            db.commit()


def consume(db, token, password, confirmation, actor=None):
    domain_lock(db)
    row = db.get(PasswordReset, digest(token))
    account = db.get(Account, row.account_id) if row else None
    if (
        not row
        or row.consumed_at
        or row.expires_at <= utcnow()
        or not eligible(db, account)
    ):
        raise DomainError(
            "INVALID_RESET",
            "This recovery link is invalid or expired. Request a new link.",
        )
    if not 12 <= len(password) <= 128 or password != confirmation:
        raise DomainError(
            "VALIDATION_ERROR", "Matching passwords of 12–128 characters are required."
        )
    account.password_hash = PASSWORDS.hash(password)
    db.execute(
        update(PasswordReset)
        .where(
            PasswordReset.account_id == account.id, PasswordReset.consumed_at.is_(None)
        )
        .values(consumed_at=utcnow())
    )
    db.execute(delete(AuthSession).where(AuthSession.account_id == account.id))
    audit(db, actor, "PASSWORD_RESET", account.person_id, person_id=account.person_id)
    notify(
        db,
        "Password changed",
        "Your password changed. All previous sessions were revoked. Contact the administrator if this was unexpected.",
        "WARNING",
        account.person_id,
    )
    return account.id


def notify_changed(database, settings, account_id):
    with database.session() as db:
        account = db.get(Account, account_id)
        person = eligible(db, account)
        if not person or not mail.available(settings):
            return
        try:
            mail.send(
                settings,
                person.email,
                "IFT E-Library password changed",
                "Your password changed and all existing sessions were revoked. Sign in normally.\n"
                + settings.recovery_contact,
            )
        except (OSError, smtplib.SMTPException, ValueError):
            set_setting(db, "recovery-delivery", {"failed": True})
            audit(
                db,
                None,
                "PASSWORD_NOTIFICATION_FAILED",
                account.person_id,
                result="FAILED",
                person_id=account.person_id,
            )
            db.commit()


def authorize_admin(db, actor, target, confirmed):
    if (
        not confirmed
        or not actor
        or actor.role not in {"ADMIN", "SUPER_ADMIN"}
        or not target
        or (target.role != "USER" and actor.role != "SUPER_ADMIN")
    ):
        raise DomainError(
            "FORBIDDEN",
            "Identity-confirmed recovery requires an authorized Admin; privileged accounts require Super Admin.",
            403,
        )
    if not eligible(db, target):
        raise DomainError("FORBIDDEN", "Account is not eligible for recovery.", 403)
