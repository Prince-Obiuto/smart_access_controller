# IFT E-Library Access Control

Phone-first React/TypeScript application connected to a durable FastAPI/PostgreSQL backend. **Controller integration is simulated.** This application has not been validated against a real DS-K2802. Runtime assets, authentication, uploads, exports and jobs operate locally; internet access is needed for initial dependency/image installation.

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
| `CONTROLLER_MODE` | Only `SIMULATED` is supported; real mode is rejected |
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
```

For a custom PostgreSQL user, substitute that username in `createdb`. The backend test runner derives credentials privately and refuses a non-`ift_test` database name. Tests truncate only their isolated test database, never `ift_access`. Connected browser tests use `ift_test_browser`, API port 8001 and Vite port 5175, create synthetic test accounts, and restart the actual API process. Run these test suites sequentially. No test credentials are loaded into the application database. Backup acceptance creates fresh `ift_test_backup_*`/`ift_test_restore_*` databases and reports their names for later operator cleanup.

See [implementation and verification notes](docs/implementation-notes.md), [HTTP/service contract](docs/frontend-contract.md), and [backup/restore instructions](docs/backup-restore.md).

## Hardware boundary

The reset DS-K2802 has not been reactivated. The adapter boundary and durable operation queue are implemented, but no Hikvision endpoints, SDK compatibility, offline controller schedules or physical confirmations are invented. Reactivation, documented transport selection and physical acceptance remain required. Simulated acknowledgements are never proof of credential removal or door opening; door sensor state remains UNKNOWN. Uncertain timed releases are never automatically replayed.

Existing Git history, remote and local files were preserved. This work is uncommitted; nothing was pushed, published or deployed.
