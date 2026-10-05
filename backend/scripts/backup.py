"""Offline operator backups. Keep application writers stopped during backup/restore."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text, select, delete
from sqlalchemy.engine import make_url
from app.config import Settings, ROOT
from app.db import Database, domain_lock
from app.models import Operation, AuthSession
from app.retention import run_retention, months


def pg(command, database, *, input=None, output=None):
    # Compose-local socket authentication: never put passwords in command arguments.
    process = subprocess.Popen(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            *command,
            "-U",
            os.environ.get("POSTGRES_USER", make_url(Settings().database_url).username),
            "-d",
            database,
        ],
        cwd=ROOT,
        stdin=subprocess.PIPE if input else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
    )
    if input:
        shutil.copyfileobj(input, process.stdin)
        process.stdin.close()
    if output:
        shutil.copyfileobj(process.stdout, output)
    else:
        # pg_restore is quiet unless explicitly asked for verbose output.
        for _ in iter(lambda: process.stdout.read(65536), b""):
            pass
    process.stdout.close()
    if process.wait():
        raise SystemExit(
            "PostgreSQL backup/restore command failed; leave application writers stopped."
        )


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def backup(settings, destination):
    if destination.exists():
        raise SystemExit("Backup destination already exists; refusing overwrite.")
    destination.mkdir(parents=True, mode=0o700)
    database = make_url(settings.database_url).database
    try:
        with (destination / "database.dump").open("xb") as out:
            pg(["pg_dump", "-Fc", "--no-owner", "--no-acl"], database, output=out)
        with tarfile.open(destination / "uploads.tar", "w") as archive:
            if settings.upload_dir.exists():
                for path in sorted(settings.upload_dir.iterdir()):
                    if (
                        not path.is_file()
                        or path.is_symlink()
                        or not re.fullmatch(r"[a-f0-9]{32}\.(png|jpg|pdf)", path.name)
                    ):
                        raise SystemExit(
                            "Unexpected upload storage entry; backup stopped."
                        )
                    archive.add(path, arcname=path.name)
        manifest = {
            "format": "ift-backup-v1",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "schema": "alembic",
            "files": {
                name: digest(destination / name)
                for name in ["database.dump", "uploads.tar"]
            },
        }
        (destination / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n"
        )
        for path in destination.iterdir():
            path.chmod(0o600)
    except BaseException:
        # Keep partial files for diagnosis; they cannot pass manifest validation.
        raise
    print(
        "Database and uploads backed up. Contains private personal data; protect the backup directory."
    )


def restore(settings, source):
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest.get("format") != "ift-backup-v1" or set(manifest.get("files", {})) != {
        "database.dump",
        "uploads.tar",
    }:
        raise SystemExit("Invalid backup manifest.")
    for name, value in manifest["files"].items():
        if digest(source / name) != value:
            raise SystemExit("Backup checksum mismatch.")
    database = Database(settings)
    with database.engine.connect() as connection:
        if connection.scalar(
            text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
        ):
            raise SystemExit(
                "Restore target database is not empty; refusing overwrite."
            )
    if settings.upload_dir.exists() and any(settings.upload_dir.iterdir()):
        raise SystemExit("Restore upload directory is not empty; refusing overwrite.")
    with tarfile.open(source / "uploads.tar", "r") as archive:
        members = archive.getmembers()
        if len({m.name for m in members}) != len(members):
            raise SystemExit("Duplicate archive entries.")
        for member in members:
            if (
                not member.isfile()
                or not re.fullmatch(r"[a-f0-9]{32}\.(png|jpg|pdf)", member.name)
                or member.size > settings.upload_limit * 5
            ):
                raise SystemExit("Unsafe archive entry.")
        settings.upload_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        archive.extractall(settings.upload_dir, members=members, filter="data")
    for path in settings.upload_dir.iterdir():
        path.chmod(0o600)
    with (source / "database.dump").open("rb") as data:
        pg(
            ["pg_restore", "--no-owner", "--no-acl", "--exit-on-error"],
            make_url(settings.database_url).database,
            input=data,
        )
    with database.session() as db:
        domain_lock(db)
        # Backup-era timed releases are obsolete; never replay them on restoration.
        for op in db.scalars(
            select(Operation).where(
                Operation.target_type == "DOOR", Operation.status == "PENDING"
            )
        ):
            op.status = "UNCERTAIN"
        db.execute(delete(AuthSession))
        run_retention(db, settings)
        db.commit()
    database.engine.dispose()
    print(
        "Restored into empty target; sessions invalidated, expired-record rules reapplied, pending unlocks quarantined. Start the backend only after this succeeds."
    )


def prune(root):
    at = datetime.now(timezone.utc)
    for directory in root.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        manifest = directory / "manifest.json"
        if not manifest.is_file():
            continue
        data = json.loads(manifest.read_text())
        if data.get("format") != "ift-backup-v1":
            continue
        if months(datetime.fromisoformat(data["createdAt"]), 12) <= at:
            # Delete only this tool's complete, private backup sets under the selected root.
            if set(p.name for p in directory.iterdir()) != {
                "manifest.json",
                "database.dump",
                "uploads.tar",
            }:
                continue
            shutil.rmtree(directory)
    print(
        "Completed backup sets older than one calendar year pruned; no year of history is implied."
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["backup", "restore", "prune"])
    p.add_argument("path", type=Path)
    p.add_argument(
        "--quiesced",
        action="store_true",
        help="Confirm all API/CLI writers are stopped; required for consistent uploads/database snapshots.",
    )
    args = p.parse_args()
    if args.action != "prune" and not args.quiesced:
        p.error("Stop all application writers first, then pass --quiesced.")
    settings = Settings()
    if args.action == "backup":
        backup(settings, args.path)
    elif args.action == "restore":
        restore(settings, args.path)
    else:
        prune(args.path)


if __name__ == "__main__":
    main()
