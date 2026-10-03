# IFT E-Library Access Control

Phone-first React/TypeScript frontend with a typed asynchronous mock service. All hardware operations and live events are simulated. No backend or real controller is connected.

## Start locally

Node 22.12+ (Node 24 used during development) and npm are required for development. Internet is needed to install dependencies once; the built application bundles its runtime assets locally.

```sh
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. Use **Development demo entry** to choose an identity. Login with email/password is for accounts registered in the current in-memory demo session; seeded identities use the separate demo entry and have no password.

```sh
npm run typecheck
npm test
npm run build
npm run preview
# Browser verification (download Chromium once):
npx playwright install chromium
npm run test:browser
```

The production preview deliberately removes development identity switching and simulation controls. Register a fictional student/staff or visitor account to use that preview. Frontend authentication and mock permissions are demonstrations, not production security.

## Demo identities

| Identity       | Email                  | Application role |
| -------------- | ---------------------- | ---------------- |
| Chinedu Okafor | super@futo.edu.ng      | SUPER_ADMIN      |
| Adaeze Nwosu   | admin@futo.edu.ng      | ADMIN            |
| Ibrahim Bello  | manager@futo.edu.ng    | MANAGER          |
| Ngozi Eze      | management@futo.edu.ng | MANAGEMENT       |
| Amara Okeke    | amara@futo.edu.ng      | USER / STUDENT   |
| Tunde Adeyemi  | tunde@example.test     | USER / VISITOR   |

Switch using the development identity selector. State is deliberately memory-only: **reload resets everything**, including new accounts, receipts and photos. Alternatively expand **Demo simulation controls** and select **Reset all demo records and sign out**. Never use real personal information, receipts or passwords in the demo. No localStorage, tokens, controller credentials or real receipts are persisted. Object URLs are revoked on replacement, purge or reset and disappear with page unload.

The demo clock starts at 3 October 2026, 11:00 Africa/Lagos. The normal schedule is Monday–Friday, 08:00–18:00; the initial Saturday denies normal swipes. Advance the clock to a weekday to demonstrate granted normal access. Dashboard events update every 12 seconds in the service, clearly simulated. This timer does not implement the future two-minute controller collection/backfill job.

Try delayed activation, failed disable, offline controllers and an uncertain unlock from simulation controls. Disable the failure switch and use the relevant **Retry simulated sync** action. A replacement never activates while old-credential disable is unconfirmed. A visitor pool card becomes available only with physical return and confirmed disable.

## Service adapter

Copy `frontend/.env.example` to `frontend/.env.local`, then restart Vite:

```ini
VITE_SERVICE_MODE=mock
# Switch to http to inspect the disconnected adapter state:
# VITE_SERVICE_MODE=http
VITE_API_BASE_URL=/api
```

`src/services/accessControlService.ts` is the component-facing interface. `MockService` enforces workflow rules and scoped reads. `httpService.ts` intentionally returns a structured NOT_CONNECTED error: no existing FastAPI or controller endpoint is assumed. Implement its transport against the agreed server contract before enabling HTTP mode. See [frontend contract](docs/frontend-contract.md).

## Appearance and logo

Edit `frontend/src/styles.css` colour variables. The selected green/yellow shades are practical choices, not claimed official institutional values. No remotely hosted font, image or CDN is used. To add a department logo, put it in `frontend/public/department-logo.png` and set `VITE_DEPARTMENT_LOGO=/department-logo.png`; the local book mark is the fallback.

## Scope and handover

See [implementation and verification notes](docs/implementation-notes.md) for files, verification evidence and limitations. No remote was created and nothing was pushed, published or deployed. Next work: FastAPI/PostgreSQL authorization and persistence, authenticated evidence storage, backend retention/collection jobs, and verified integration with the reactivated DS-K2802. Ubuntu SDK compatibility and hardware capability must be tested before choosing a controller transport. One-year backup retention remains a backend/deployment requirement; no backups exist in this demonstration.
