# Implementation and verification notes

## Current implementation

The existing React screens were preserved and connected through their typed service boundary. HTTP is the default; explicit mock mode remains memory-only. FastAPI/PostgreSQL stores accounts/sessions, people, applications/reviews/uploads, card assets/historical assignments, visitors/pool, controllers/doors/readers, normal schedules/exceptions, events, audits, notifications, confirmations/settings and a durable operation outbox. PostgreSQL migrations create the schema and use bigint outbox versions. Local domain mutations use a transaction advisory lock plus partial unique constraints for concurrency protection.

Authentication uses Argon2id and opaque server-side cookie sessions with CSRF/origin checks. Public registration cannot set a role. Strict university-domain validation does not establish email ownership. Server services enforce the shared capability matrix and ownership. Explicit local CLI commands bootstrap the first Super Admin and audit password resets; there are no default production accounts.

Card approval and issuance are distinct, with reviewer/issuer history and private validated upload storage. Missing reports persist promptly. Replacement activation waits for all previous controller removals. Per-controller desired/last-confirmed states and failures remain visible. Returned pool cards require physical return and confirmed removal; unreturned expired, missing or damaged cards cannot be reassigned. Historical event attribution uses assignment creation/end/validity times and is immutable after reuse.

Schedules are saved pending and explicitly synchronized. Privileged designation is a separate action for one synchronized Super Admin-owned personal card; designation changes first disable previous credentials, and role demotion revokes a designated assignment. Timed unlock commits an UNCERTAIN marker before sending, so a process crash or timeout cannot cause automatic replay. The sensor remains UNKNOWN.

A single backend worker runs expiry/retention and pending operations every two seconds, with simulated event backfill every two minutes. Source keys deduplicate overlap. The existing frontend subscription contract polls every four seconds while visible; both workspace and filtered records update. This is deliberately a small modular monolith, intended to run one API process, without Redis/Celery or unrelated infrastructure.

Retention is based on original times, with six calendar months for audit records. Restored records remain visible until original purge deadlines without activating access. Visitor purge removes uploaded files/accounts/sessions and redacts copied details in events/raw data, notices, audits and visit records. Backup/restore scripts protect private files, refuse occupied targets and reapply expired-record rules before exposure. One-year pruning is a separate operator task.

## Changed files

- `backend/app/`: configuration, database/models, validation, session security, upload parser/storage, independent business services, simulated adapter, durable operations, events/jobs, records/exports, retention/views, HTTP routes and local CLI.
- `backend/alembic/`, `backend/alembic.ini`: initial PostgreSQL schema and bigint version migration.
- `backend/requirements.in`, `backend/requirements.lock`, `backend/Dockerfile`: dependency bounds, exact resolved specification and non-root container operation.
- `backend/scripts/`: non-overwriting local configuration, isolated PostgreSQL test runner, connected browser supervisor, private backup/restore/pruning and real isolated restore acceptance check.
- `backend/tests/`: PostgreSQL HTTP/service/concurrency/retention tests.
- Root `compose.yaml`, `.dockerignore`, `.env.example`, `.gitignore`, `shared/permissions.json`.
- `frontend/src/services/httpService.ts`, adapter selection, central permissions/models, shell/auth/admin/access screens, Vite proxy and browser configurations/tests; npm scripts add `test:connected`.
- `README.md`, `docs/frontend-contract.md`, `docs/backup-restore.md`, these notes.

Existing local history/remote and workspace metadata were preserved. Starting state on this backend run was clean `main...origin/main` at `959670d`. The implementation remains uncommitted; no push, remote creation, publication or deployment was performed.

## Verification performed on 4 October 2026

| Check | Result |
|---|---|
| Alembic migrations against PostgreSQL 17 | Passed for application and isolated test schemas |
| Backend integration suite | 42 tests passed against PostgreSQL; includes raw concurrent DB writers and concurrent authenticated API issuance |
| Backend `ruff` undefined/unused checks and `pip check` | Passed |
| Frontend typecheck | Passed |
| Frontend service tests | 37 tests passed |
| Frontend production build | Passed; all runtime assets bundled locally |
| Existing mock browser workflows | Six passed across 360×800 phone and 1440×1000 laptop |
| Connected HTTP browser workflows | Two passed across the same viewports, including actual API child-process restart |
| Backup/restore acceptance | Passed using newly created isolated PostgreSQL databases and private temporary uploads |
| Docker backend image and Compose startup | Image built; PostgreSQL and backend health checks healthy |
| Source whitespace / secret hygiene | Checked before handover; `.env`, uploads, virtual environment, browser outputs and backups are ignored |

The backend suite verifies exact-domain and role injection rejection; sessions, CSRF, logout and reset invalidation; ownership and every restricted capability; Manager denial of issuance/export/person edits/unlock; content-based uploads/protected downloads; rejection reasons and approval before issuance; idempotency; leading-zero identifiers; concurrent uniqueness; two-controller failed old removal; immediate own revocation; delayed activation/restart/expiry; uncertain and crash-interrupted unlocks; visitor approval/physical return/failed removal/expiry; immutable reuse and late-event attribution; source-key deduplication; matching CSV/PDF filters/audit/formula escaping; restored original retention deadlines and visitor privacy copies; schedule exceptions and separate privileged designation, including repeated failed previous-card removal.

Connected browser checks register and upload through the actual API, persist application/session/photo/evidence through reload and restart, confirm role selection is unavailable, review as Manager, reject direct unauthorized issuance, hide forbidden controls, and receive stored events without manual refresh. Browser tests use synthetic records only, in `ift_test_browser`, with trace recording disabled to avoid retaining cookies/tokens. Screenshots under ignored `frontend/test-results/` were inspected in phone/laptop layouts.

Verification found and corrected stale queued date/schedule operations, visitor revocation retention timing, integer overflow in operation versions, permission checks before record lookup, and a mock enrolment UI race: activation is now disabled until the confirmed card number arrives. Docker snap required piped binary backup transport rather than inherited file descriptors. All affected checks were rerun. The test stack reports two deprecation warnings (Starlette TestClient/httpx and pypdf's test-only JavaScript builder); these did not fail tests.

## Limits and required real DS-K2802 work

**Controller integration is simulated.** No physical controller was connected, reactivated, queried or commanded. Simulated confirmation, communication timestamps, swipes, failures and scan numbers establish only application behavior. Simulated backfill emits stable unknown-credential buckets and is not a device-history recovery implementation. Door opening, physical credential disable/expiry, reader faults and offline schedules remain unverified.

For real integration:

1. Reactivate the reset controller through the manufacturer's supported procedure. Record exact model, firmware, credentials provisioning, clock/timezone, configured doors/readers and network settings.
2. Obtain authoritative documentation for the interface supported by that firmware. Verify DS-K2802 support, Linux/Ubuntu architecture and any SDK licensing/runtime requirements before choosing SDK or documented transport. No invented Hikvision HTTP routes exist here.
3. Implement `AccessControllerAdapter` using that verified interface, including per-controller idempotent permission updates/removal, authoritative readback/acknowledgement semantics, offline weekly/special-date and validity programming, the separate designated 24/7 card, timed release, real enrolment and event collection. Provision secrets outside the frontend/audit logs.
4. Preserve controller event IDs, event/receipt timestamps, entry/exit reader mapping and stored assignment references. Implement device-specific live/backfill collection/checkpoints and deduplication. Add real door/contact/fault indications only when supported and verified; acknowledgement alone is not opening evidence.
5. Test loss/reconnect and uncertain transport outcomes on hardware. Prove every relevant controller removes old/revoked permissions before replacement activation, that expiry and schedules continue offline, and that uncertain unlocks are never automatically replayed. Verify visitor return/reuse, missing/unreturned cards and historical ownership.
6. Perform physical acceptance with cards/readers/lock/sensors, local-network offline operation, HTTPS/session settings, private storage permissions and production backup recovery. Select actual installation routing, controller targeting and an operational recovery policy before exposure.

Other practical limits: private uploads have structural checks but no malware-scanning service; the small-installation workspace aggregate and export/retention scans need pagination/batching for substantially larger datasets; audit tables are persistent but are not a tamper-proof external audit ledger; no email verification/delivery or external visitor approval system is integrated; no cron, firewall, HTTPS proxy or production installation was performed; a year of backup history is not present. The owner's first Super Admin still requires the documented explicit bootstrap command with their chosen credentials.
