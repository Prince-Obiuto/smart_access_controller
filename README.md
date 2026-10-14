# IFT E-Library Access Control

Current development acceptance: [evidence table](docs/development-acceptance.md). [Forgot password and private recovery setup](docs/password-recovery.md) are implemented; SMTP inbox verification remains pending. Victor’s normal paid card issuance has real SDK/readback and owner-granted evidence. Production remains deferred until the remaining acceptance passes.

The owner's 2026-10-13 cleanup removed the three old test registrations and Victor's unused draft, preserving the sole Super Admin, card/event evidence and blocked backlog. Fresh normal registrations now exist for Victor Amaobi (reference `20211260402`), Visitor 01 and Visitor 02. Victor's new FIRST application has proof and an authorized APPROVED review; it is unissued with no assignment or access dates. The mistaken Super Admin application remains unissued and excluded. See the [current registration and acceptance state](docs/development-test-accounts.md). A/B remain permanently revoked; V remains quarantined. The normal frontend at `http://localhost:5173` now proxies the temporary READ_ONLY/jobs-disabled acceptance API on 8001; the original container collector continues uninterrupted on 8000. The extra frontend on 5174 was stopped. No new controller write or test scope activation occurred. Production remains deferred.

Phone-first React/TypeScript application connected to a durable FastAPI/PostgreSQL backend. Controller modes are explicit: `SIMULATED` for demonstrations, `HIKVISION_READ_ONLY` for native SDK reads with ordinary outbox writes blocked, and `HIKVISION_CONTROLLED` for separately scoped permissions. The collector/outbox remain read-only. On 2026-10-12 a separately authenticated A/B diagnostic scope began real card-write acceptance against the owner's existing TEMPLATE 3; schedule editing and normal paid/visitor issuance remain gated independently. Earlier host/container TCP/SDK, powered Ethernet recovery and duplicate-free outage backfill are verified. **Reader 1 ENTRY / Reader 2 EXIT on Door 1** is owner-confirmed. Runtime operation remains local/offline; internet is needed for initial dependency/image installation. See the dated reports for actual write/readback/physical results and remaining evidence gaps.

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
| `CONTROLLER_MODE` | `SIMULATED`, `HIKVISION_READ_ONLY`, or `HIKVISION_CONTROLLED`; controlled mode also requires authenticated per-controller test activation |
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
# Isolated phone/laptop polling, burst and flicker regression; no backend/hardware:
npm run test:refresh
npm run test:connected
npm run test:hikvision
```

For a custom PostgreSQL user, substitute that username in `createdb`. The backend test runner derives credentials privately and refuses a non-`ift_test` database name. Tests truncate only their isolated test database, never `ift_access`. Connected browser tests use `ift_test_browser`, API port 8001 and Vite port 5175, create synthetic test accounts, and restart the actual API process. Run these test suites sequentially. No test credentials are loaded into the application database. Backup acceptance creates fresh `ift_test_backup_*`/`ift_test_restore_*` databases and reports their names for later operator cleanup.

See [implementation and verification notes](docs/implementation-notes.md), [HTTP/service contract](docs/frontend-contract.md), and [backup/restore instructions](docs/backup-restore.md).

## Hikvision read-only integration

The device response confirms DS-K2802, firmware V2.0.8 build 250625. Owner-reconfirmed ordinary swipes on 2026-10-12 establish **device Reader 1 is Door 1 ENTRY and Reader 2 is Door 1 EXIT**, superseding the earlier opposite attestation; only local event classification changed. The extracted `EN-HCNetSDKV6.1.9.4_build20220412_linux64/` package supplies the matching headers, libraries and access-control documentation. Our C++ bridge compiles against its actual declarations; Python declares no vendor ABI structures. SDK 6.1.9.4 initialization and cleanup succeeded on Ubuntu and in the Debian backend image. After the owner restored Ethernet, host and actual backend-container TCP/login/history checks succeeded at `10.10.20.8:8000`; the host route is directly through `enp0s31f6`, source `10.10.20.24`. Real-time subscription works alongside the existing legacy alarm client; the legacy channel initially returned capacity error 1924. No network or controller settings were changed.

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

When the operator chooses to start the existing local container installation, use `docker compose -f compose.yaml -f compose.hikvision.yaml up -d backend` so the SDK mounts are present. The existing local backend was started with this override during the controlled-write preparation phase. It is healthy in `HIKVISION_READ_ONLY`; the existing HTTP frontend remains on port 5173. The frontend continues to use HTTP mode and its normal local `/api` proxy. Native development uses the host bridge with the existing native startup command above.

Diagnostics do not store events. The normal backend worker uses the native session, commits real events and complete historical checkpoints atomically, backfills every two minutes with overlap, and deduplicates live/history delivery. To verify ingestion locally without starting the API or dispatching any outbox operations, first apply the additive migration and then run the bounded collector against the existing application database:

```sh
backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
env PYTHONPATH=backend backend/.venv/bin/python -m app.hikvision_collect --seconds 120
# Container equivalent, choosing the existing container database/upload configuration:
docker compose -f compose.yaml -f compose.hikvision.yaml run --rm --no-deps -T backend alembic upgrade head
docker compose -f compose.yaml -f compose.hikvision.yaml run --rm --no-deps -T backend python -m app.hikvision_collect --seconds 120
```

Choose the installation's existing backend runtime. The additive migration and bounded collectors were run against the existing application database and real events were persisted. The existing local API is now running in read-only mode. Existing pending operations remain preserved and blocked. Login and event reads cannot synchronize card permissions. Unlock, enrolment, person/card/schedule/clock writes, reset and upgrade are unavailable. Existing simulated history remains labelled and there is no mock fallback.

See [SDK evidence, setup, recovery limits and physical acceptance](docs/hikvision-capabilities.md). The current owner-confirmed entry at 12:06:42, exit at 12:06:53 and post-recovery entry at 12:21:28 Africa/Lagos on 2026-10-12 are persisted with corrected local reader directions. Prior dated physical labels remain documented as historical attestations. Actual API responses containing real rows rendered successfully on phone and laptop using a temporary read-only browser snapshot harness; ordinary operator login into the running local installation was subsequently recorded through the normal authentication endpoint. Role/CSRF/browser regression checks remain separately identified as isolated tests. The physical user's card has no local assignment, so the application correctly retains Unknown credential rather than inferring an owner from chat. A grant or swipe does not prove physical passage. No commit, push or deployment was performed.


## Controlled card-access write preparation

[Controlled-write setup and test sequence](docs/hikvision-controlled-writes.md) records actual card-only diagnostic acceptance as well as earlier preparation. Unknown total template capacity and weekday ordering still block schedule allocation/editing; they do not prevent a narrowly scoped card operation referencing freshly verified existing TEMPLATE 3. A separate authenticated diagnostic CLI preserves its complete linked schedule baseline and every unmanaged card, executes only the exact designated A/B operation and retains durable readback/physical evidence. It does not drain the ordinary backlog, issue a paid assignment, approve visitors or edit schedules. Capacity-query retries and WEEK 4 calibration are deferred.

The owner designated A `1051305927`, B `1052625607` and V `1052815031`. Keep `CONTROLLER_MODE=HIKVISION_READ_ONLY` for the running collector/outbox. A date-window grant/denials, A revocation, B replacement grant and B cleanup denial are owner-confirmed and stored as real events. A/B retain blacklist type 3/zero-rights readback and permanent local revocation; the temporary diagnostic scope is closed. The SDK responses return zero validity flags despite observed date enforcement; the original strict issuance mismatches remain preserved. Victor's new FIRST is APPROVED but unissued; V's last exact GET found it absent, and it remains quarantined locally pending normal approval. Existing “Active Hours” schedules and Agwuoma's permissions remain protected. Schedule editing, backend-stopped expiry, the closing-hours boundary, visitor acceptance and production work remain deferred.

```sh
# Read-only inventory; output is a new ignored, mode-600 local file.
env PYTHONPATH=backend backend/.venv/bin/python -m app.hikvision_control inventory \
  --output backend/var/hikvision-inventory-review.json
# Currently prints complete:false. Do not bypass the activation guard.
# Additional isolated phone/laptop guard regression:
cd frontend
npm run test:controlled
```

Latest retry (2026-10-12): Ethernet `enp0s31f6` is UP at `10.10.20.24/27`, with a direct controller route; host/container TCP and SDK checks pass. The powered outage froze the checkpoint and ingestion, then the worker automatically reconnected without restart; a targeted history query recovered 18 missing rows from 22 events and repetition inserted zero duplicates. At 12:24:40 Lagos there were 318 real PostgreSQL events. At 12:28:46 Lagos there were 322 real events; the owner confirms recovered-event and post-recovery entrance rendering. Full multi-day background catch-up remains incomplete. No network settings or controller permissions were changed. The existing API continues its original read-only image; prepared write code remains unactivated and was not rebuilt/deployed. Full current evidence: [capability report](docs/hikvision-capabilities.md#acceptance-retry--2026-10-12) and [controlled-write report](docs/hikvision-controlled-writes.md#acceptance-retry--2026-10-12).
