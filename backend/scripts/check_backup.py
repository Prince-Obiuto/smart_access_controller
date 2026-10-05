"""Actual backup/restore acceptance using newly created isolated PostgreSQL databases."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.engine import make_url
from sqlalchemy import select
from app.config import Settings, ROOT
from app.db import Database
from app.models import Person, Upload, AccessEvent, Operation, AuthSession
from app.common import initialize, now
from app.events import store_event
from app.uploads import save_upload
from PIL import Image
import io
from alembic import command
from alembic.config import Config
from backup import backup, restore

base = Settings()
url = make_url(base.database_url)
source = "ift_test_backup_" + uuid4().hex[:8]
target = "ift_test_restore_" + uuid4().hex[:8]
for name in [source, target]:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "createdb", "-U", "ift", name],
        cwd=ROOT,
        check=True,
    )
with tempfile.TemporaryDirectory(prefix="ift-backup-test-", dir="/tmp") as root:
    root = Path(root)
    source_settings = base.model_copy(
        update={
            "database_url": url.set(database=source).render_as_string(
                hide_password=False
            ),
            "upload_dir": root / "source-uploads",
            "jobs_enabled": False,
        }
    )
    os.environ["DATABASE_URL"] = source_settings.database_url
    command.upgrade(Config(str(ROOT / "backend/alembic.ini")), "head")
    database = Database(source_settings)
    with database.session() as db:
        initialize(db, source_settings)
        normal = Person(
            kind="STAFF",
            reference="BACKUP",
            first_name="Backup",
            last_name="Test",
            email="backup@futo.edu.ng",
            phone="0800",
            department="IFT",
            status="ACTIVE",
            sync="PENDING",
        )
        expired = Person(
            kind="VISITOR",
            reference="VIS-BACKUP",
            first_name="Expired",
            last_name="Visitor",
            email="expired@example.org",
            phone="0800",
            department="External",
            status="ACTIVE",
            sync="PENDING",
            access_ended_at=now() - timedelta(days=46),
        )
        db.add_all([normal, expired])
        db.flush()
        out = io.BytesIO()
        Image.new("RGB", (12, 12), "green").save(out, format="PNG")
        kept = save_upload(
            db, source_settings, normal.id, "PHOTO", "photo.png", out.getvalue(), 10000
        )
        save_upload(
            db, source_settings, expired.id, "PHOTO", "old.png", out.getvalue(), 10000
        )
        eid = store_event(db, "old-record", now() - timedelta(days=121), "00009999")
        op = Operation(
            controller_id="controller-1",
            target_type="DOOR",
            target_id="door-1",
            desired="TIMED_RELEASE",
            version=1,
            status="PENDING",
            created_at=now(),
            data={"seconds": 5},
        )
        db.add(op)
        db.add(
            AuthSession(
                digest="test-digest",
                csrf="synthetic-only",
                expires_at=now() + timedelta(hours=1),
            )
        )
        db.commit()
        kept_name = kept.stored_name
        expired_id = expired.id
        op_id = op.id
    database.engine.dispose()
    backup(source_settings, root / "backup")
    destination = base.model_copy(
        update={
            "database_url": url.set(database=target).render_as_string(
                hide_password=False
            ),
            "upload_dir": root / "restored-uploads",
            "jobs_enabled": False,
        }
    )
    restore(destination, root / "backup")
    checked = Database(destination)
    with checked.session() as db:
        assert db.get(Person, expired_id).purged
        assert not db.get(AccessEvent, eid)
        assert db.get(Operation, op_id).status == "UNCERTAIN"
        assert not list(db.scalars(select(AuthSession)))
        assert len(list(db.scalars(select(Upload)))) == 1
        assert (destination.upload_dir / kept_name).is_file()
    try:
        restore(destination, root / "backup")
    except SystemExit:
        pass
    else:
        raise AssertionError("Restore must refuse an occupied target.")
    checked.engine.dispose()
print(
    "Backup/restore acceptance passed: DB+uploads, expiry/privacy reapplication, session invalidation, unlock quarantine, refusal to overwrite."
)
print("Isolated test databases retained: " + source + " and " + target)
