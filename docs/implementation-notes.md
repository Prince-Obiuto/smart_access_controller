# Implementation and verification notes

## Delivered files

Starting state: no application files and no usable Git repository; the workspace supplied an empty read-only `.git` directory. Git was initialized at the root with the approved external command. Existing `.agents`, `.aws`, `.codex` and workspace metadata were preserved. No commit, remote, push, publication or deployment was performed.

- Root: `AGENTS.md`, `.gitignore`, `README.md`.
- `frontend/`: npm manifest and lockfile, TypeScript/Vite/Playwright configuration, HTML entry, `.env.example`, local-asset placeholder.
- `frontend/src/`: responsive CSS, entry and router shell, shared context, typed models, central permissions, reusable accessible controls/dialogs, and all requested route modules.
- `frontend/src/services/`: typed asynchronous interface, synthetic fixtures, permission-enforcing mock workflows, explicit disconnected HTTP adapter and adapter selection.
- `frontend/tests/`: focused service-rule tests and browser workflows at 360 × 800 and 1440 × 1000.
- `docs/`: provisional frontend contract and these handover notes.

The sidebar becomes a native modal mobile drawer. Native dialogs provide focus containment, Escape handling and restoration. Forms use explicit accessible names and described hints; asynchronous controls show loading/error states. Tables scroll within their own panels; record cards and form grids stack on phones. Status labels accompany every status colour. Colour variables are editable and do not claim official institutional hex values. Runtime assets are local, with an optional owner-supplied department logo.

## Verification

| Check | Result |
|---|---|
| `npm run typecheck` | Passed |
| `npm test` | 37 focused service tests passed |
| `npm run build` | Passed; locally bundled production output in `frontend/dist/` |
| `npm run test:browser` | Six workflows passed across 360 × 800 phone and 1440 × 1000 laptop |
| Final visitor browser rerun after retention hardening | Two viewport checks passed |
| Dependency audit during patched installation | Zero reported vulnerabilities |
| Screenshot inspection | Phone and laptop dashboard inspected; uploads and visitor cards captured in browser checks |
| Git / source hygiene | No remote; no whitespace errors; source remains uncommitted |

Browser screenshots/traces are generated under ignored `frontend/test-results/`; these are local verification artifacts rather than application source. Earlier browser failures were corrected and rerun: the asynchronous demo checkbox needed optimistic state, form hints needed separate accessible descriptions, and test navigation needed to wait for authentication/role changes.

Service tests cover exact university domain, immediate USER registration, Manager restrictions, own-record scope, review/issuance separation, preserved rejection history, duplicate submissions and issuance, card uniqueness, leading zeros, failed old-card disable, delayed activation, immediate missing reports, date validity, scan cancellation/timeout, visitor approval/return, unavailable expired cards, filtered CSV/PDF exports, formula escaping, uncertain unlock, privileged replacement, immutable historical visitor ownership and original retention deadlines. HTTP adapter tests assert NOT_CONNECTED rather than fabricated backend success.

Browser workflows cover dashboard, responsive drawer navigation, forbidden routes, Manager controls/export restrictions, log filtering, registration/domain error, image upload, evidence preview/download, rejection/resubmission, enrolment scan confirmation, issuance, physical visitor return validation, failed disable and confirmed return recovery.

## Deliberate limits and next phase

- Everything is synthetic and in memory. Refresh resets accounts, state, evidence and photo previews. Registered-account passwords exist only in process memory; no browser persistent storage is used.
- Development role selection and simulation switches are absent from production builds. The production mock preview permits newly registered synthetic USER accounts; it is not a production authentication system.
- HTTP transport is a switchable but disconnected scaffold. Endpoints in the contract are proposed application endpoints. No FastAPI server exists in this phase.
- Mock authorization is independently checked in the service but is not secure server authorization. Add server sessions, role/ownership checks, transactions, idempotent commands and database uniqueness constraints.
- Browser file checks are provisional MIME/size checks. Implement server signature/content validation, safe storage, authenticated download, malicious-file handling and durable deletion.
- The model supports arrays of devices/doors and per-controller confirmations. The initial UI configures one door and one DS-K2802 with Entry/Exit readers; actual multi-controller routing needs the backend.
- Dashboard timers and simulation controls produce demonstration events only. Implement backend collection/backfill and live UI transport separately; browser timers never contact hardware.
- Door sensor is UNKNOWN. Command acknowledgement is not physical opening evidence. Verify forced-opening, held-open and reader-fault support against the reactivated controller.
- Ended temporary visitor accounts cannot renew access through restored records; their original personal-data purge deadlines are preserved. New visitor-account lifecycle/renewal policy should be finalized with the backend.
- Retention and recovery run against a configurable demo clock in the service. Implement durable backend jobs, PII redaction across copied fields and attachment storage, and immutable/tamper-resistant production audit storage.
- One-year backup retention, backup/restore and deployment acceptance remain unconnected backend work. No backup exists in this frontend.
- Controller protocol, any Hikvision SDK, Ubuntu compatibility, real hardware disable/activation and physical acceptance remain unverified. Select transport only after inspecting and testing the reactivated DS-K2802.

Final source remains uncommitted for review. A successful frontend build does not establish full-stack or production readiness.
