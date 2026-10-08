# IFT E-Library Access Control

Phone-first React/TypeScript application connected to a durable FastAPI/PostgreSQL backend. Controller modes are explicit: `SIMULATED` for demonstrations and `HIKVISION_READ_ONLY` for native SDK reads with all physical writes blocked. The local official Linux SDK now initializes in both runtimes, and native login/history/live transport and PostgreSQL ingestion are implemented. **Real events now reach PostgreSQL and the frontend.** Host/container TCP, SDK login, device identity, history and real-time subscription are verified. Owner-confirmed swipes established Reader 1 EXIT and Reader 2 ENTRY on Door 1. Runtime assets, authentication, uploads, exports and jobs operate locally; internet access is needed for initial dependency/image installation.

## Start locally with Docker Compose

Requirements: Docker with Compose, Node 22.12+ and npm. Commands below run from the repository root unless a `cd` is shown.

```sh
# Generates private local DB credentials; refuses to overwrite an existing .env.
python3 backend/scripts/configure_local.py
# Alternatively copy .env.example and replace every password placeholder yourself.
docker compose up -d --build
# API startup applies Alembic migrations before accepting requests.
docker compose ps
```

The API is at `http://127.0.0.1:8000/api/health`; PostgreSQL binds only to `127.0.0.1:55432`. Database and uploads use separate named volumes. Keep `.env` private and retain both volumes; `docker compose down -v` deletes application data.

Create the first Super Admin explicitly, using your actual staff identity at exactly `futo.edu.ng`. Passwords are entered privately, never supplied as command arguments:

```sh
docker compose exec backend python -m app.cli bootstrap \
  --email YOUR_EMAIL@futo.edu.ng --reference YOUR_STAFF_ID \
  --first-name YOUR_FIRST_NAME --last-name YOUR_LAST_NAME
```

Bootstrap refuses a second first administrator. No production administrator credentials or sample personal records are seeded. Registration always creates a USER and gives application access immediately, without physical access or email-ownership verification. Assign other roles through the Super Admin settings screen. Configure actual payment instructions there; the initial instruction explicitly asks the operator to configure them. Add visitor pool assets in that screen; availability requires simulated disable confirmation.

```sh
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. HTTP mode is the default, and Vite proxies `/api` to the local backend. Login uses the backend session. For isolated development only, run `VITE_SERVICE_MODE=mock npm run dev`; mock data resets on reload and uses the separate development identity selector.

## Native backend development

Python 3.12 is used with the exact dependency specification in `backend/requirements.lock`:

```sh
docker compose up -d db
python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.lock
backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
cd backend
.venv/bin/python -m app.cli bootstrap --email YOUR_EMAIL@futo.edu.ng \
  --reference YOUR_STAFF_ID --first-name YOUR_FIRST_NAME --last-name YOUR_LAST_NAME
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Stop the Compose backend before running the native API on port 8000. Native uploads default to `backend/var/uploads`, separate from the Docker upload volume. Choose one storage deployment for an installation; switching storage paths does not copy evidence automatically.

For an explicit manual migration in the container: `docker compose exec backend alembic upgrade head`. For an audited local password reset:

```sh
docker compose exec backend python -m app.cli reset-password \
  --actor-email AUTHORIZED_OPERATOR@futo.edu.ng --email TARGET@futo.edu.ng
```

The authenticated local operator must be an active Admin/Super Admin; Admin can reset only USER accounts. The command requests both passwords privately, audits the operator/target and invalidates the target's sessions. The operator keeps any verification evidence for the human reset process outside the application.

## Configuration

`.env.example` contains placeholders only. `configure_local.py` creates ignored, mode-600 development credentials and explicitly enables development HTTP cookies and simulation controls. Review these settings for your installation:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLAlchemy `postgresql+psycopg://` URL for the native backend; URL-encode custom credential characters |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT` | Compose PostgreSQL configuration; generated passwords are URL-safe hex |
| `ENVIRONMENT`, `ALLOW_INSECURE_DEV_COOKIES` | HTTP cookies require explicit `development` plus `true`; elsewhere HTTPS and Secure cookies are required |
| `ALLOWED_ORIGINS` | Exact frontend origins allowed for CSRF-protected writes; include a chosen LAN development origin explicitly |
| `UPLOAD_DIR`, `UPLOAD_LIMIT` | Private native upload directory and maximum bytes, default 5 MiB; relative directories resolve from the repository root |
| `SESSION_HOURS` | Session lifetime, default 12 hours |
| `CONTROLLER_MODE` | `SIMULATED` or `HIKVISION_READ_ONLY`; read-only mode never dispatches physical writes |
| `HIKVISION_*` | Private device credentials, SDK/bridge paths and bounded reads; see the capability guide |
| `SIMULATION_CONTROLS_ENABLED` | Explicitly enables Super Admin-only failure/event controls |
| `JOBS_ENABLED` | Native worker switch; Compose runs expiry/retention and durable operations jobs |

Vite variables: `VITE_SERVICE_MODE=http` (default), `VITE_API_BASE_URL=/api`, `VITE_API_PROXY_TARGET=http://127.0.0.1:8000`. Use the same-origin arrangement for cookies. An eventual local production server must serve the compiled frontend and proxy `/api` under the same HTTPS origin; this task does not publish or install such a server. The application uses local fonts/assets and has no runtime CDN dependency. Set `VITE_DEPARTMENT_LOGO` to a local supplied image if needed. Colours are configurable in `frontend/src/styles.css` and are not claimed institutional standards.

## Verification

Create the isolated test databases once:

```sh
docker compose exec -T db createdb -U ift ift_test
docker compose exec -T db createdb -U ift ift_test_browser
backend/.venv/bin/python backend/scripts/test.py -q
backend/.venv/bin/python backend/scripts/check_backup.py
cd frontend
npm run typecheck
npm test
npm run build
# Install Chromium once if absent:
npx playwright install chromium
npm run test:browser
npm run test:connected
npm run test:hikvision
```

For a custom PostgreSQL user, substitute that username in `createdb`. The backend test runner derives credentials privately and refuses a non-`ift_test` database name. Tests truncate only their isolated test database, never `ift_access`. Connected browser tests use `ift_test_browser`, API port 8001 and Vite port 5175, create synthetic test accounts, and restart the actual API process. Run these test suites sequentially. No test credentials are loaded into the application database. Backup acceptance creates fresh `ift_test_backup_*`/`ift_test_restore_*` databases and reports their names for later operator cleanup.

See [implementation and verification notes](docs/implementation-notes.md), [HTTP/service contract](docs/frontend-contract.md), and [backup/restore instructions](docs/backup-restore.md).

## Hikvision read-only integration

The device response confirms DS-K2802, firmware V2.0.8 build 250625. Ordinary swipes confirmed **device Reader 1 is Door 1 EXIT and Reader 2 is Door 1 ENTRY**, correcting the initial expected direction mapping. The extracted `EN-HCNetSDKV6.1.9.4_build20220412_linux64/` package supplies the matching headers, libraries and access-control documentation. Our C++ bridge compiles against its actual declarations; Python declares no vendor ABI structures. SDK 6.1.9.4 initialization and cleanup succeeded on Ubuntu and in the Debian backend image. After the owner restored Ethernet, host and actual backend-container TCP/login/history checks succeeded at `10.10.20.8:8000`; the host route is directly through `enp0s31f6`, source `10.10.20.24`. Real-time subscription works alongside the existing legacy alarm client; the legacy channel initially returned capacity error 1924. No network or controller settings were changed.

From the project root, using the existing private `.env` and backend virtual environment:

```sh
# Only if local credentials are missing: owner enters them in a private terminal.
backend/.venv/bin/python backend/scripts/configure_hikvision.py
# Set HIKVISION_SDK_DIR in private .env to the extracted package root above.
# Build our host bridge; SDK files remain local and ignored.
backend/.venv/bin/python backend/scripts/build_hikvision.py
env PYTHONPATH=backend backend/.venv/bin/python -m app.hikvision_diagnostic --tcp-only
env PYTHONPATH=backend backend/.venv/bin/python -m app.hikvision_diagnostic --sample-limit 20 --listen-seconds 60

# Container bridge must be compiled for Debian, not copied from Ubuntu.
docker compose build backend
backend/.venv/bin/python backend/scripts/build_hikvision.py --container
docker compose -f compose.yaml -f compose.hikvision.yaml run --rm --no-deps -T backend python -m app.hikvision_diagnostic --tcp-only
docker compose -f compose.yaml -f compose.hikvision.yaml run --rm --no-deps -T backend python -m app.hikvision_diagnostic --sample-limit 20 --listen-seconds 60
```

`compose.hikvision.yaml` mounts the original SDK and locally built bridge read-only. Vendor files never enter Git or the image. Initial container bridge compilation downloads a compiler into a disposable container; subsequent runtime operation stays local. Keep upstream notices; no redistribution permission is inferred from the included open-source notices.

When the operator chooses to start the existing local container installation, use `docker compose -f compose.yaml -f compose.hikvision.yaml up -d backend` so the SDK mounts are present. This startup command was not executed here; the existing API remains stopped. The frontend continues to use HTTP mode and its normal local `/api` proxy. Native development uses the host bridge with the existing native startup command above.

Diagnostics do not store events. The normal backend worker uses the native session, commits real events and complete historical checkpoints atomically, backfills every two minutes with overlap, and deduplicates live/history delivery. To verify ingestion locally without starting the API or dispatching any outbox operations, first apply the additive migration and then run the bounded collector against the existing application database:

```sh
backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
env PYTHONPATH=backend backend/.venv/bin/python -m app.hikvision_collect --seconds 120
# Container equivalent, choosing the existing container database/upload configuration:
docker compose -f compose.yaml -f compose.hikvision.yaml run --rm --no-deps -T backend alembic upgrade head
docker compose -f compose.yaml -f compose.hikvision.yaml run --rm --no-deps -T backend python -m app.hikvision_collect --seconds 120
```

Choose the installation's existing backend runtime. The additive migration and bounded collectors were run against the existing application database and real events were persisted. The API service was not deployed or started persistently. Existing pending operations remain preserved and blocked. Login and event reads cannot synchronize card permissions. Unlock, enrolment, person/card/schedule/clock writes, reset and upgrade are unavailable. Existing simulated history remains labelled and there is no mock fallback.

See [SDK evidence, setup, recovery limits and physical acceptance](docs/hikvision-capabilities.md). Owner-confirmed exit at 15:54:13 and entry at 15:55:10 Africa/Lagos are stored with verified reader directions. Actual API responses containing real rows rendered successfully on phone and laptop using a temporary read-only browser snapshot harness; ordinary operator login into a running installation remains a separate check. The physical user's card has no local assignment, so the application correctly retains Unknown credential rather than inferring an owner from chat. A grant or swipe does not prove physical passage. No commit, push or deployment was performed.
