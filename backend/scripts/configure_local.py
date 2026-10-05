"""Generate local-only secrets without displaying them or overwriting existing work."""

from pathlib import Path
import secrets
import os

root = Path(__file__).resolve().parents[2]
path = root / ".env"
if path.exists():
    raise SystemExit("Existing .env preserved. Edit it explicitly if needed.")
password = secrets.token_hex(24)
with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
    out.write(
        f"POSTGRES_USER=ift\nPOSTGRES_PASSWORD={password}\nPOSTGRES_DB=ift_access\nPOSTGRES_PORT=55432\nDATABASE_URL=postgresql+psycopg://ift:{password}@127.0.0.1:55432/ift_access\nENVIRONMENT=development\nALLOW_INSECURE_DEV_COOKIES=true\nALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:5175,http://127.0.0.1:5175\nCONTROLLER_MODE=SIMULATED\nSIMULATION_CONTROLS_ENABLED=true\nJOBS_ENABLED=true\n"
    )
path.chmod(0o600)
print(
    "Created ignored .env with generated local database credentials. No administrator account exists yet."
)
