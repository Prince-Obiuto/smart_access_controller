"""Isolated synthetic-data API supervisor used only by connected browser tests."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings, ROOT
from sqlalchemy.engine import make_url

settings = Settings()
url = make_url(settings.database_url).set(database="ift_test_browser")
os.environ.update(
    DATABASE_URL=url.render_as_string(hide_password=False),
    JOBS_ENABLED="false",
    UPLOAD_DIR=str(ROOT / "backend/var/browser-uploads"),
    ALLOWED_ORIGINS="http://127.0.0.1:5175",
    SIMULATION_CONTROLS_ENABLED="true",
    CONTROLLER_MODE=os.environ.get("IFT_BROWSER_CONTROLLER_MODE", "SIMULATED"),
    HIKVISION_USERNAME="",
    HIKVISION_PASSWORD="",
)
from alembic.config import Config
from alembic import command

command.upgrade(Config(str(ROOT / "backend/alembic.ini")), "head")
from app.db import Database
from app.models import Base, Person, Account
from app.security import PASSWORDS
from app.common import initialize
from sqlalchemy import text

settings = Settings()
dbase = Database(settings)
with dbase.engine.begin() as c:
    c.execute(
        text(
            "TRUNCATE "
            + ",".join('"' + t.name + '"' for t in Base.metadata.sorted_tables)
            + " CASCADE"
        )
    )
with dbase.session() as db:
    initialize(db, settings)
    for role in ["SUPER_ADMIN", "ADMIN", "MANAGER", "MANAGEMENT"]:
        p = Person(
            kind="STAFF",
            reference="BROWSER-" + role,
            first_name=role,
            last_name="Browser Tester",
            email=role.lower() + "@futo.edu.ng",
            phone="0800",
            department="IFT",
            status="ACTIVE",
            sync="PENDING",
        )
        db.add(p)
        db.flush()
        db.add(
            Account(
                person_id=p.id,
                role=role,
                password_hash=PASSWORDS.hash("Synthetic-browser-password!"),
            )
        )
    if settings.controller_mode == "HIKVISION_READ_ONLY":
        # Parser-to-PostgreSQL-to-browser fixtures only. Never a live device
        # acceptance claim; the test process has no credentials and no jobs.
        from app.hikvision import parse_event
        from app.events import store_real_event
        from app.common import now

        at = now().replace(microsecond=0)
        for serial, reader, outcome, stamp in [
            (1, 1, "GRANTED", list(at.timetuple()[:6])),
            (2, 99, "UNKNOWN", [0] * 6),
        ]:
            row = parse_event(
                {
                    "major": 5,
                    "minor": 1,
                    "serial": serial,
                    "door": 1,
                    "reader": reader,
                    "time": stamp,
                    "timeType": 1,
                    "outcome": outcome,
                    "cardHex": (b"00000123" + bytes(24)).hex(),
                },
                "synthetic-browser-fixture",
                clock_verified=True,
            )
            store_real_event(db, row, "controller-1")
    db.commit()
dbase.engine.dispose()
(ROOT / "backend/var").mkdir(parents=True, exist_ok=True)
(ROOT / "backend/var/browser-api.pid").write_text(str(os.getpid()))
from PIL import Image

Image.new("RGB", (12, 12), "green").save(ROOT / "backend/var/browser-photo.png")
worker = None
restart = False
stopping = False


def handle_restart(*args):
    global restart
    restart = True


def stop(*args):
    global stopping
    stopping = True


signal.signal(signal.SIGUSR1, handle_restart)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


def start():
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8001",
            "--no-access-log",
        ],
        cwd=ROOT / "backend",
        env=os.environ,
    )


try:
    worker = start()
    while not stopping:
        if restart:
            worker.terminate()
            worker.wait(timeout=15)
            worker = start()
            restart = False
        if worker.poll() is not None:
            raise SystemExit("Browser test API stopped unexpectedly.")
        time.sleep(0.2)
finally:
    if worker and worker.poll() is None:
        worker.terminate()
        worker.wait(timeout=15)
    (ROOT / "backend/var/browser-api.pid").unlink(missing_ok=True)
