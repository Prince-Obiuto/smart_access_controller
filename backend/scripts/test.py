"""Run migrations/tests against an isolated PostgreSQL database, never application data."""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.engine import make_url
from app.config import Settings

url = make_url(Settings().database_url)
name = os.environ.get("IFT_TEST_DATABASE", "ift_test")
if not name.startswith("ift_test"):
    raise SystemExit("Refusing non-test database name.")
os.environ["DATABASE_URL"] = url.set(database=name).render_as_string(
    hide_password=False
)
os.environ["JOBS_ENABLED"] = "false"
os.environ["CONTROLLER_MODE"] = "SIMULATED"
# Test processes must not inherit live controller credentials.
os.environ["HIKVISION_USERNAME"] = ""
os.environ["HIKVISION_PASSWORD"] = ""
os.environ["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
from alembic.config import Config
from alembic import command

command.upgrade(
    Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
)
raise SystemExit(
    subprocess.call(
        [
            sys.executable,
            "-m",
            "pytest",
            str(Path(__file__).resolve().parents[1] / "tests"),
            *sys.argv[1:],
        ],
        env=os.environ,
    )
)
