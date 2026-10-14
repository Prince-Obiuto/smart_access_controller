"""Local, explicit bootstrap and audited password reset. No default credentials."""

import argparse
from getpass import getpass

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .common import audit, initialize, notify
from .config import Settings
from .db import Database, domain_lock
from .models import Account, Person
from .retention import run_retention
from .schemas import Registration
from .security import PASSWORDS, DomainError, password_matches


def new_password():
    password = getpass("New password (12+ characters): ")
    if len(password) < 12 or len(password) > 128:
        raise SystemExit("Password must contain 12–128 characters.")
    if getpass("Repeat new password: ") != password:
        raise SystemExit("Passwords differ.")
    return password


def bootstrap(db, settings, data):
    domain_lock(db)
    initialize(db, settings)
    if db.scalar(select(Account).where(Account.role == "SUPER_ADMIN")):
        raise DomainError(
            "CONFLICT",
            "First Super Admin already exists. Use authorized role management.",
        )
    validated = Registration.model_validate(data)
    p = Person(
        kind="STAFF",
        reference=validated.reference,
        first_name=validated.firstName,
        last_name=validated.lastName,
        email=validated.email,
        phone=validated.phone,
        department=validated.department,
        status="ACTIVE",
        sync="PENDING",
        purged=False,
        restored=False,
    )
    db.add(p)
    db.flush()
    account = Account(
        person_id=p.id,
        role="SUPER_ADMIN",
        password_hash=PASSWORDS.hash(validated.password),
    )
    db.add(account)
    db.flush()
    audit(db, None, "BOOTSTRAP_SUPER_ADMIN", p.id, person_id=p.id)
    db.commit()
    return account


def reset_password(db, settings, email, actor_email, actor_password, password, reason):
    domain_lock(db)
    p = db.scalar(
        select(Person).where(
            Person.email == actor_email.strip().lower(), Person.purged == False
        )
    )
    actor = db.scalar(select(Account).where(Account.person_id == p.id)) if p else None
    if (
        not actor
        or p.status != "ACTIVE"
        or p.deleted_at
        or actor.role not in ["SUPER_ADMIN", "ADMIN"]
        or not password_matches(actor.password_hash, actor_password)
    ):
        raise DomainError(
            "FORBIDDEN", "Authorized local operator authentication failed.", 403
        )
    target_p = db.scalar(
        select(Person).where(
            Person.email == email.strip().lower(), Person.purged == False
        )
    )
    target = (
        db.scalar(select(Account).where(Account.person_id == target_p.id))
        if target_p
        else None
    )
    if not target or (target.role != "USER" and actor.role != "SUPER_ADMIN"):
        raise DomainError("FORBIDDEN", "Target account is not permitted.", 403)
    if not 12 <= len(password) <= 128 or not reason.strip():
        raise DomainError(
            "VALIDATION_ERROR", "Strong password and a reset reason are required."
        )
    from .password_recovery import consume, issue

    token = issue(db, target, actor, "LOCAL_RECOVERY_INITIATED")
    consume(db, token, password, password, actor)
    audit(
        db, actor, "LOCAL_PASSWORD_RESET", target.person_id, person_id=target.person_id
    )
    notify(
        db,
        "Local password reset",
        "A local administrator reset your password and revoked existing sessions.",
        "WARNING",
        target.person_id,
    )
    db.commit()
    # The human reason is not copied into audit free text to avoid personal/secret
    # spill; audit records the explicit local reset and its authorized actor.
    return target.id


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap_parser = sub.add_parser("bootstrap")
    for field in ["email", "reference", "first-name", "last-name"]:
        bootstrap_parser.add_argument("--" + field, required=True)
    reset = sub.add_parser("reset-password")
    reset.add_argument("--email", required=True)
    reset.add_argument("--actor-email", required=True)
    operator = sub.add_parser("operator-recovery")
    operator.add_argument("--email", required=True)
    sub.add_parser("check-recovery-mail")
    sub.add_parser("retention")
    args = parser.parse_args()
    settings = Settings()
    database = Database(settings)
    with database.session() as db:
        if args.command == "bootstrap":
            bootstrap(
                db,
                settings,
                {
                    "type": "STAFF",
                    "reference": args.reference,
                    "firstName": args.first_name,
                    "lastName": args.last_name,
                    "email": args.email,
                    "phone": input("Phone: ").strip(),
                    "department": input("Department: ").strip(),
                    "password": new_password(),
                },
            )
            print(
                "First Super Admin created. No physical card privilege has been assigned."
            )
        elif args.command == "check-recovery-mail":
            import smtplib
            import ssl

            from . import mail
            from .common import set_setting

            if not mail.available(settings):
                raise SystemExit("Configure SMTP_HOST and SMTP_FROM privately first.")
            transport = (
                smtplib.SMTP_SSL if settings.smtp_security == "TLS" else smtplib.SMTP
            )
            kwargs = (
                {"context": ssl.create_default_context()}
                if settings.smtp_security == "TLS"
                else {}
            )
            try:
                with transport(
                    settings.smtp_host, settings.smtp_port, timeout=8, **kwargs
                ) as client:
                    if settings.smtp_security == "STARTTLS":
                        client.starttls(context=ssl.create_default_context())
                    if settings.smtp_username.get_secret_value():
                        client.login(
                            settings.smtp_username.get_secret_value(),
                            settings.smtp_password.get_secret_value(),
                        )
                    client.noop()
            except (OSError, smtplib.SMTPException):
                raise SystemExit(
                    "SMTP connection/authentication failed; no message sent or credentials printed."
                ) from None
            domain_lock(db)
            set_setting(db, "recovery-delivery", {"failed": False})
            db.commit()
            print(
                "SMTP connection verified; recovery enabled. Real inbox delivery remains unverified."
            )
        elif args.command == "operator-recovery":
            import os
            import sys

            from .config import ROOT
            from .password_recovery import consume, issue, notify_changed

            # Physical OS ownership of private configuration is the trust boundary,
            # not a public endpoint or a universal credential. Never accept pipes.
            if (
                not sys.stdin.isatty()
                or os.stat(ROOT / ".env").st_uid != os.getuid()
                or os.stat(ROOT / ".env").st_mode & 0o077
            ):
                raise SystemExit(
                    "Use the local owner terminal with owner-only .env permissions."
                )
            p = db.scalar(
                select(Person).where(Person.email == args.email.strip().lower())
            )
            target = (
                db.scalar(select(Account).where(Account.person_id == p.id))
                if p
                else None
            )
            if not target or target.role != "SUPER_ADMIN":
                raise SystemExit(
                    "Local operator recovery is restricted to Super Admin."
                )
            if (
                input(
                    "Confirm the Super Admin identity in person; type IDENTITY VERIFIED: "
                )
                != "IDENTITY VERIFIED"
            ):
                raise SystemExit("Identity confirmation required.")
            domain_lock(db)
            token = issue(db, target, None, "LOCAL_OPERATOR_RECOVERY")
            password = new_password()
            consume(db, token, password, password)
            db.commit()
            notify_changed(database, settings, target.id)
            print("Super Admin recovery audited; sessions revoked. Sign in normally.")
        elif args.command == "reset-password":
            actor_password = getpass("Local operator password: ")
            if (
                input("Type IDENTITY VERIFIED after confirming the target in person: ")
                != "IDENTITY VERIFIED"
            ):
                raise SystemExit("Identity confirmation required.")
            reason = input("Local reset reason (not a secret): ")
            password = new_password()
            target_id = reset_password(
                db,
                settings,
                args.email,
                args.actor_email,
                actor_password,
                password,
                reason,
            )
            from .password_recovery import notify_changed

            notify_changed(database, settings, target_id)
            print("Password reset audited; existing sessions revoked.")
        else:
            domain_lock(db)
            run_retention(db, settings)
            db.commit()
            print(
                "Retention and expiry rules applied before exposing restored records."
            )


if __name__ == "__main__":
    try:
        main()
    except DomainError as exc:
        raise SystemExit(exc.message) from None
    except ValidationError as exc:
        raise SystemExit(
            "Invalid configuration/identity: "
            + "; ".join(str(error["msg"]) for error in exc.errors())
        ) from None
    except SQLAlchemyError:
        raise SystemExit(
            "Database operation failed. Check configuration/migrations or duplicate identity; no credentials are printed."
        ) from None
