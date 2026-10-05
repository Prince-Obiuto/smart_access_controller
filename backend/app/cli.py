"""Local, explicit bootstrap and audited password reset. No default credentials."""

import argparse
from getpass import getpass
from sqlalchemy import select, delete
from .config import Settings
from .db import Database, domain_lock
from .models import Account, Person, AuthSession
from .security import PASSWORDS, password_matches, DomainError
from .schemas import Registration
from .common import initialize, audit, notify
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from .retention import run_retention


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
    target.password_hash = PASSWORDS.hash(password)
    db.execute(delete(AuthSession).where(AuthSession.account_id == target.id))
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


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap_parser = sub.add_parser("bootstrap")
    for field in ["email", "reference", "first-name", "last-name"]:
        bootstrap_parser.add_argument("--" + field, required=True)
    reset = sub.add_parser("reset-password")
    reset.add_argument("--email", required=True)
    reset.add_argument("--actor-email", required=True)
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
        elif args.command == "reset-password":
            actor_password = getpass("Local operator password: ")
            reason = input("Local reset reason (not a secret): ")
            password = new_password()
            reset_password(
                db,
                settings,
                args.email,
                args.actor_email,
                actor_password,
                password,
                reason,
            )
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
