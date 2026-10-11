# Hikvision read-only capability evidence

**Real events now reach PostgreSQL, the API and frontend rendering.** Host/container SDK login, identity, history, real-time subscription and ordinary owner-observed entry/exit swipes succeeded. The current installation remains read-only and pending operations are preserved. Controlled card-access execution is now prepared with additional activation/readback safeguards; no device write has been performed. Normal owner login is recorded; the final Ethernet link is DOWN and host/container probes time out. Physical recovery and write acceptance remain pending.

## Evidence from the prior read-only stage

| Check | Observed result |
|---|---|
| Device response | DS-K2802; firmware raw `33554440` (`0x02000008`), build raw `1639961` (`0x00190619`): packed version V2.0.8, build 250625, matching owner report. |
| Host network | Latest TCP probe VERIFIED; route `10.10.20.8 dev enp0s31f6 src 10.10.20.24`. Earlier Wi-Fi/incorrect gateway routes timed out; owner restored the direct Ethernet route. We changed no network/firewall/controller settings. |
| Backend network | Actual Compose backend container TCP probe VERIFIED. No container network modification was required. |
| Credentials | Existing private credentials used successfully; no values printed or exposed to the frontend. |
| Host SDK runtime | Ubuntu Linux x86_64, glibc 2.43; compiled bridge, initialization, login and cleanup succeeded. |
| Container SDK runtime | Python 3.12 Debian bookworm, x86_64, glibc 2.36; separately compiled bridge, initialization/login/history/subscription/unsubscribe/logout/cleanup succeeded with local read-only mounts. |
| SDK version | Package `EN-HCNetSDKV6.1.9.4_build20220412_linux64`; runtime build `100731140` (`0x06010904`, 6.1.9.4). |
| Login / identification | Synchronous private-protocol Login_V40 and GET_DEVICECFG_V40 VERIFIED in both runtimes. |
| Clock | GET_TIMECFG VERIFIED; reported clock skew about -1 to -2 seconds. No clock write issued. |
| Capabilities | ACS_ABILITY returned XML. `doorNo` text `2`, `cardReaderNo` text `3,4`, `isSupportGetDeviceEvent` options `true,false` without a selected value. Options are not proof of a selected capability; successful history query is the actual support evidence. |
| Historical events | Actual container query completed 53 events in its then-current hour window (sample limit 100). An earlier 20-event sample was correctly PARTIAL. Subsequent bounded collectors persisted additional device records. |
| Live subscription | Legacy client mode returned error 1924 (capacity exhausted). Documented real-time mode `byDeployType=1` succeeds alongside the existing client. Both runtimes received actual callbacks; one monitored host session recorded 5 live deliveries. Final container diagnostic received a live callback and completed a 6-event two-minute history query. |
| Reader mapping | Owner confirmed card ending 3895: Reader 1 / Door 1 at 15:54:13 Lagos was physical EXIT; Reader 2 / Door 1 at 15:55:10 was physical ENTRY. Both GRANTED. Mapping is **1 EXIT / 2 ENTRY**, correcting the original expectation; no wiring/controller change. |
| Events in application (prior read-only stage) | That stage counted 169 real device records, including granted/denied/unknown and owner-observed swipes; original simulated history retained separately. Actual API returned the real rows; phone/laptop rendered actual responses in a read-only snapshot harness. This is distinct from a normal operator login into a continuously running API, which was not deployed. |
| Permission/outbox preservation | Before/after hashes of existing operation and confirmation rows match. This database had zero such rows at baseline; regression tests additionally exercise nonempty pending outboxes. No card permissions synchronized. |


Port 8000 is used for the SDK private protocol; no HTTP/ISAPI endpoint is guessed. The SDK is older than the device firmware. The observed successful reads establish support for the tested operations; unrelated operations remain unknown. Firmware/build decoding follows Hikvision's [packed device-configuration fields](https://open.hikvision.com/hardware/v2/结构体/NET_DVR_DEVICECFG_V40.html); raw values are retained because the package's older English description is less precise.

The owner reports Door 1 magnetic lock, Door 2 reserved, DS-K1107AM Wiegand readers 1 entry / 2 exit, card-only, and working activation/enrolment/offline permissions/revocation/emergency release through iVMS-4200. Those are owner evidence, not application acceptance. Wiegand status in iVMS alone does not establish a reader fault.

## Exact package and ABI

The original package remains under the project root, ignored by Git and Docker build context:

- Matching declarations: `incEn/HCNetSDK.h`. Linux uses the vendor's DWORD/LONG/BOOL and CALLBACK declarations directly; there are no Python ctypes layouts, packing guesses or copied vendor headers.
- Main libraries: `lib/libhcnetsdk.so`, `libHCCore.so`, `libhpr.so`; companion modules in `lib/HCNetSDKCom/`, including `libHCAlarm.so`, `libHCCoreDevCfg.so`, `libHCGeneralCfgMgr.so`.
- Bundled crypto: `lib/libcrypto.so.1.1` and `lib/libssl.so.1.1`, explicitly selected by SDK initialization. No guessed system OpenSSL substitution.
- ELF: 64-bit little-endian x86_64. `readelf` main-library dependencies are `libhpr.so`, `libHCCore.so`, `libstdc++.so.6`, `libm.so.6`, `libgcc_s.so.1`, `libc.so.6`; vendor search paths retain the original package layout. Host inspection does not use `ldd`. The slim container omits readelf; successful native SDK initialization confirms loading the initialization path there, and successful device reads/subscription validate the used access-control modules. Unused modules remain unverified.
- Examples: `consoleDemo/` includes synchronous V40 login and alarm examples. Only the read-only declarations/flows are used; complete vendor demos include writes and must not be run on the controller for this task.
- Documentation: `doc/Device Network SDK (Card-Based Access Control)_Developer Guide_V6.1.5.X_20220311.ZIP`, programming manual CHM and `doc/readme.txt`. Selected documentation text was inspected locally under ignored `backend/var/sdk-doc/`; the full local header is authoritative when the older guide omits newer fields.
- Notices: `doc/Open Source Software Licenses-HCNetSDK.txt` includes LGPL libiconv, zlib/TinyXML, OpenSSL, BSD and MIT notices. No separate vendor redistribution grant was identified. Owner-provided SDK remains local with its notices, mounted read-only; no SDK/header/library is committed, bundled in an image, uploaded or redistributed. Do not infer vendor redistribution rights from dependency notices.

Builds record SHA-256 hashes of the actual local header, library and bridge source in ignored `backend/var/hikvision*/build.json`. Host and Debian bridge outputs are separate: the newer Ubuntu toolchain binary must not be reused inside Debian.

## Setup and verification commands

Run from the repository root. Existing private `.env` already selects `CONTROLLER_MODE=HIKVISION_READ_ONLY`, preserves DB credentials, and sets `HIKVISION_SDK_DIR=EN-HCNetSDKV6.1.9.4_build20220412_linux64`. If controller credentials are absent, the owner runs:

```sh
backend/.venv/bin/python backend/scripts/configure_hikvision.py
```

Both credential inputs are hidden. Never paste them into chat, source, arguments, screenshots or docs. `.env` is ignored/private; environment variables override it. Do not dump the environment or resolved Compose configuration into logs. Preparation/configuration does not start a service.

```sh
# Read-only routing check; the direct Ethernet route is now verified.
ip route get 10.10.20.8

# Host build and bounded preflight/login/history/live diagnostics.
backend/.venv/bin/python backend/scripts/build_hikvision.py
env PYTHONPATH=backend backend/.venv/bin/python -m app.hikvision_diagnostic --tcp-only
env PYTHONPATH=backend backend/.venv/bin/python -m app.hikvision_diagnostic --sample-limit 20 --listen-seconds 60

# Rebuild image and compile for its Debian runtime, with the same vendor header.
docker compose build backend
backend/.venv/bin/python backend/scripts/build_hikvision.py --container
# Original SDK and container bridge are mounted read-only by this override.
docker compose -f compose.yaml -f compose.hikvision.yaml run --rm --no-deps -T backend python -m app.hikvision_diagnostic --tcp-only
docker compose -f compose.yaml -f compose.hikvision.yaml run --rm --no-deps -T backend python -m app.hikvision_diagnostic --sample-limit 20 --listen-seconds 60
```

The container build installs g++ only in a disposable build container; initial package downloads need internet, runtime does not. No API jobs or database ingestion are started by diagnostics. Full diagnostic exits 0 for established login, 2 otherwise; inspect per-stage status because login does not imply event support. `--tcp-only` exits 0 only on TCP success. History samples are bounded 1–100 and live listening 0–60 seconds. Sample-limit truncation is PARTIAL, never a complete checkpoint. Diagnostic output includes local event card identifiers if retrieval succeeds: keep that output private.

Operator startup for the existing local container installation, after its bridge is built: `docker compose -f compose.yaml -f compose.hikvision.yaml up -d backend`. Use this override for normal collection as well as diagnostics; base Compose alone does not mount SDK files. This service-start command was not executed during this task. The existing API remains stopped, and bounded verification sessions are closed.

To verify PostgreSQL ingestion without starting or deploying the API, choose the installation's existing runtime and run:

```sh
# Host database configuration:
backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
env PYTHONPATH=backend backend/.venv/bin/python -m app.hikvision_collect --seconds 120

# Or the container's existing database configuration:
docker compose -f compose.yaml -f compose.hikvision.yaml run --rm --no-deps -T backend alembic upgrade head
docker compose -f compose.yaml -f compose.hikvision.yaml run --rm --no-deps -T backend python -m app.hikvision_collect --seconds 120
```

The additive migration allows unknown original timestamps/door/readers to remain null rather than fabricating fields. The collector permits only read-only mode, persists through the same worker pipeline, summarizes real-event totals/checkpoint/status, and never calls outbox processing or retention. Its duration is bounded 1–300 seconds plus bounded in-flight native work. The additive migration and bounded collector commands were run against application data after connectivity returned. For the first import only, a temporary `HIKVISION_HISTORY_WINDOW_MINUTES=60` override completed the initial hour in one window; normal worker defaults remain 15-minute windows and two-minute scheduling. The normal API worker will use the same integration when the operator starts the existing local runtime; no API service was deployed here. On mode/path changes restart the existing backend; old processes retain prior settings.

## Read-only transport and recovery

The executable implements only SDK path initialization, Init, bounded private-protocol Login_V40, GetDVRConfig (device configuration/time), GetDeviceAbility ACS_ABILITY, alarm/exception callback setup and real-time alarm subscription, Start/GetNext/StopRemoteConfig for command `NET_DVR_GET_ACS_EVENT`, unsubscribe, Logout and Cleanup. No card/person/schedule/clock setter, door control, reset, activation or upgrade API is called.

Blocking SDK work runs in an isolated child process owned by the worker thread, away from FastAPI's event loop. Secrets travel only over stdin; core dumps are disabled. Only prefixed JSON frames enter Python, vendor stdout/error text is not relayed. Connect/receive timeout defaults to 3 seconds, one SDK connect attempt, application deadline 40 seconds. SDK automatic reconnect is disabled; application retry uses 5–300 second backoff. Authentication errors 1/47 and locked account 153 stop automatic login until explicit session/service restart, avoiding repeated bad-password attempts. Missing native libraries/components, unsupported operation (SDK 23), permission failure, timeout and connect failure have distinct safe codes.

The bridge selects real-time subscription (`byDeployType=1`) to coexist with a legacy client channel; it tries client mode only if real-time returns documented unsupported error 23. It never disconnects another client. [Hikvision documents separate real-time channels and no offline replay](https://open.hikvision.com/hardware/structures/NET_DVR_SETUPALARM_PARAM.html), so our history checkpoints recover gaps. A capacity failure remains visible; restart after freeing a channel if needed.

Live callbacks copy scalar event data into a bounded native queue (1024) using a nonblocking mutex attempt. They do no DB work, I/O, allocation of strings or pointer dereference for image/extension data. Truncated callbacks, queue overflow and SDK connection/lost-alarm exceptions surface a fault, stop the session and trigger checkpoint-based recovery. The Python queue and uncommitted live queue are also bounded (4096 each); protocol/overflow failures are surfaced, never silently treated as success.

History queries request all major/minor events with pictures disabled and explicit Lagos wall-time bounds. The worker schedules backfill every 120 seconds, and immediately after reconnect, with two-minute overlap. Default first scan starts 60 minutes back, windows are 15 minutes, maximum 1000 returned events per window; environment knobs are in `.env.example` and Compose. A watermark advances only after the SDK reports FINISH, StopRemoteConfig succeeds, and all corresponding events commit in the same DB transaction. Partial/failed/timed-out reads leave it unchanged. Live rows are acknowledged only after commit; database failure leaves them queued. Live delivery never moves history checkpoints. Simulation has a separate checkpoint namespace.

A dense window over the configured event limit remains PARTIAL and deliberately does not skip ahead: reduce `HIKVISION_HISTORY_WINDOW_MINUTES` or increase `HIKVISION_EVENT_LIMIT` (maximum 10000). Unreachable controller history, exhausted device retention, unsupported history, or a device clock correction can prevent complete gap recovery. These conditions are exposed in status/checkpoints; they are not proof of complete capture. The initial 60-minute lookback is not an import of all device history. Set a larger initial range before first collection when needed (maximum 90 days), while respecting application retention. Never reuse a simulated checkpoint.

## Event semantics and limitations

- Card buffers are decoded as strings, preserving leading zeroes. Binary/unreadable values retain their original hex with a flag instead of conversion to an integer.
- Actual native major/minor, inductive type, serial, door/reader identifiers, original timestamp/type and receive time are retained. Pictures, unrelated PII and callback pointers are not copied into events.
- Only package-defined legal-card/password pass events become GRANTED; defined invalid/no-right/expired/password/anti-passback failure events become DENIED. Other events remain UNKNOWN. The package provides no numeric mapping for nonzero inductive types, so those remain UNKNOWN rather than guessing.
- Local time type 0 is normalized from Africa/Lagos; UTC type 1 stays UTC. Invalid/ambiguous timestamps remain null with original fields/receive time retained. Future time and unverified local device clock are flagged and do not attribute an event to a person. No device clock writes are made. A successful current clock query cannot prove all historical clock settings.
- Deduplication key includes hashed device serial, device event serial, normalized event time, major/minor, card and reader/door; excludes live/history feed. Serial resets on different timestamps remain distinct. The captured live event and both owner-observed swipe identities were also found in controller history with matching source keys; duplicate insertion was rejected. A zero/missing event serial is flagged; identical events within the same timestamp with no unique device serial may collide. Counter reset/wraparound and all firmware event variants still need broader device acceptance.
- Attribution uses assignment creation/start/expiry and immutable end times at the original trustworthy event time, including visitor-card reuse. Unknown cards/readers/times remain visible without inventing an owner. Current application permissions never determine a device outcome, and exit events never check out a visitor.
- Verified site mapping is controller-1 / device Door 1, reader **1 EXIT**, reader **2 ENTRY**. Unknown/unverified identifiers map to UNKNOWN/null. Verification comes only from owner-observed physical swipes matched to stored event IDs; login cannot verify it. The local mapping corrects retained direction labels while preserving original device IDs/times/outcomes. It does not change hardware wiring or configuration. The owner identified the card holder in chat, but no matching local assignment exists: those rows correctly remain Unknown credential rather than fabricating an identity relationship.
- Frontend and exports retain simulation provenance, original device identifiers, unknown results and timestamp uncertainty. Explicit session shutdown is persisted as SESSION_CLOSED after final queued callbacks commit; device communication otherwise expires after 150 seconds; local bridge heartbeats do not count as device responses. History and subscription statuses are displayed separately. A successful connection/read does not synchronize application permissions.

`process_operations` still exits before dispatch, attempts, stale-operation changes or finalization in read-only mode, even if a simulated adapter is injected. Old simulated confirmations remain stored but project as unconfirmed/OUT_OF_SYNC. Pending operations remain durable, unlock/enrolment/simulation endpoints are rejected, and no mock fallback exists. Login/event retrieval does not modify controller permissions or mark any assignment synchronized.

## Verified ordinary swipes and remaining acceptance

Owner-confirmed ordinary swipes for card ending 3895:

| Physical action | Lagos timestamp | Device reader / door | Result | Stored event ID |
|---|---|---|---|---|
| Exit | 2026-10-08 15:54:13 | Reader 1 / Door 1 | GRANTED | `d51c391b-5783-44c1-bb0f-fd6a5bc7092d` |
| Entry | 2026-10-08 15:55:10 | Reader 2 / Door 1 | GRANTED | `5bcb2b34-4062-4bfd-8947-5329521d4fdd` |

After the owner confirmed the physical labels and card, the local mapping was recorded with those two evidence IDs:

```sh
env PYTHONPATH=backend backend/.venv/bin/python -m app.hikvision_mapping --entry-event 5bcb2b34-4062-4bfd-8947-5329521d4fdd --exit-event d51c391b-5783-44c1-bb0f-fd6a5bc7092d --confirmed-physical-readers
```

Do not repeat this command with guessed directions/event IDs. On a new installation, perform ordinary swipes and use its own matching persisted events. The helper validates same-card real events and distinct readers, records owner attestation locally and corrects history labels; it sends no SDK command.

Real API responses were verified using an uncommitted test authentication session rolled back at the end, without changing an account/password or persisting a login. Phone/laptop frontend rendering used those actual responses through a temporary read-only snapshot harness; it did not fabricate event rows or start a persistent API. Private local evidence/snapshots/screenshots remain ignored under `backend/var/`.

Current continuation recorded a normal owner login and ongoing worker ingestion (see the new section below). Remaining checks: confirm owner-observed live UI updates, physical denied-card scenarios, real visitor-card reuse/ownership after registration, safe cable disconnect/reconnect and backlog recovery, clock-error handling, serial wrap/reset and alternate callback layouts on this firmware. Historical denied events and live/history duplicate suppression have been observed, but those do not establish every physical scenario. A swipe/grant is not proof of physical passage. Controlled card/schedule writes are now prepared in the current authorized phase, with activation blocked until the evidence and designated-card requirements below are satisfied.

## Prior read-only stage verification

Current tests use isolated databases and synthetic accounts/events with device credentials removed. They do not establish device support. Native builds and successful actual SDK/device operations are separately recorded above. See README for the full regression commands. The added tests cover UTC/Lagos live-history dedup, leading zeroes, invalid timestamps/unknown readers/results, immutable historical ownership, partial/complete/reconnect checkpoints, failed-commit live retention, callback-gap recovery, native queue overflow, bounded hung-child shutdown, authentication-stop behavior and stale UI communication. Read-only regression browser checks use synthetic device-shaped fixtures. The separate private snapshot harness additionally rendered actual persisted hardware responses, without claiming normal operator authentication or a deployed API. Shutdown tests cover committing final in-transit callbacks without advancing history checkpoints.

The prior stage performed no commit, push, application deployment, controller mutation or pending-operation execution.


| Final validation | Result |
|---|---|
| `backend/.venv/bin/python backend/scripts/test.py -q` | 73 passed, two existing non-failing Starlette/httpx and pypdf deprecation warnings; isolated PostgreSQL only. |
| Frontend `npm test` | 37 passed. |
| Frontend `npm run typecheck` / `npm run build` | Passed; production build includes TypeScript checking. |
| `npm run test:browser` | 6 mock phone/laptop workflows passed. |
| `npm run test:connected` | 2 HTTP/PostgreSQL phone/laptop workflows passed, including API restart. |
| `npm run test:hikvision` | 2 isolated read-only HTTP phone/laptop checks passed, including device-shaped fixtures, unknown timestamps, no unlock UI and server rejection. |
| Private actual-data browser harness | 2 phone/laptop checks passed using actual API responses, including owner-card filtering and exact 15:54 EXIT / 15:55 ENTRY rows with verified device readers. No persistent login session or deployed API. |
| Actual duplicate check | Captured live identity and both owner swipe identities matched actual history; complete query; zero duplicate insertions. |
| Build / consistency | Host and Debian native bridges compiled; final Docker image built; backend F/E9 lint, pip consistency, SDK/secret ignore rules and Git whitespace checks passed. |
| Persistent services | Existing API container remains stopped; PostgreSQL healthy. Verification native sessions/temporary browser servers closed. |

## Files changed in this continuation

Earlier pending frontend/backend/read-only work remains in the working tree. This continuation adds/extends:

- Native transport: `backend/native/hikvision_readonly.cpp`, `backend/app/hikvision.py`, `backend/app/hikvision_diagnostic.py`, `backend/app/hikvision_collect.py`, `backend/app/hikvision_mapping.py`, `backend/scripts/build_hikvision.py`.
- Pipeline/configuration: `backend/app/config.py`, `controllers.py`, `events.py`, `jobs.py`, `models.py`, `records.py`, `retention.py`, `views.py`, and `backend/alembic/versions/50fdd03c8a12_real_event_unknown_fields.py`.
- Runtime/package exclusion: `backend/Dockerfile`, `compose.yaml`, `compose.hikvision.yaml`, `.env.example`, `.gitignore`, `.dockerignore`. Private `.env` SDK path was updated without exposing credentials.
- Frontend: `frontend/src/App.tsx`, `models.ts`, `pages/Dashboard.tsx`, `pages/Records.tsx`, `services/mockService.ts`.
- Checks: `backend/scripts/browser_server.py`, `backend/tests/test_hikvision.py`, `backend/tests/test_hikvision_events.py`, `frontend/tests/http-browser/read-only.spec.ts`.
- Handover: `README.md`, `docs/hikvision-capabilities.md`, `docs/frontend-contract.md`, `docs/implementation-notes.md`.

Generated SDK binaries, build manifests, private snapshots and verification harnesses are local ignored artifacts under the SDK root / `backend/var/`, not repository deliverables. No application data or pending operations were reset; no commit, push or deployment occurred.


## Controlled-write continuation: current status

The existing local Compose backend was started using `compose.hikvision.yaml`; it is healthy in **HIKVISION_READ_ONLY**. The existing HTTP frontend serves port 5173. Normal owner password/session login produced a real `LOGIN` audit at **2026-10-08 17:18:36 Lagos**. Unauthenticated workspace access through that frontend proxy returned 401. No bootstrap, password reset, test-auth session or owner impersonation was used. The owner skipped the physical cable test. Role and CSRF regressions use isolated accounts via normal HTTP login and are separately reported.

A preservation check during this continuation counted **234 real events** (increased from 169), one existing account, and unchanged operation/confirmation hashes. There were zero actual outbox/confirmation rows, as at the previous baseline; nonempty held-outbox behavior is exercised by regressions. The verified reader-map setting remains Reader 1 EXIT / Reader 2 ENTRY. Unknown swiped credentials remain unknown; no person/card import or controller permission synchronization occurred.

Read-only inventory established four existing controller cards and card-access week, holiday, group and template GET interfaces. Existing enabled templates 1/2 (“All-Day Authorized” / “All-Day Denied”) and their plans remain untouched. Card identifiers and full inventory stay in ignored mode-600 local artifacts. The native structures compiled at 2708/40/52 bytes (card/condition/query), 920 bytes (week), 160 (holiday), 136 (group), 140 (template); short callbacks and mismatched getter lengths fail explicitly.

**Activation is currently blocked.** Card-access template GET succeeds at 1–128 but 129–255 returns SDK error 11, not a proven absence result. V50 boundary probes agree. The returned ACS XML omits template capacity; a complete ownership/resource inventory cannot yet be claimed. Nonuniform weekly ordering also lacks authoritative evidence. No owner-designated spare card/person or visitor test credential was supplied. The unknown swiped card was not chosen on the owner's behalf. No setter or AUTHORIZE command was sent to a real native session.

Prepared implementation adds an explicit `HIKVISION_CONTROLLED` mode plus authenticated per-controller/credential/person/resource scopes, epoch-based backlog hold and individual review, serialized durable execution, independent priority disable worker, bounded retries, pre-send hashes, GET-first timeout reconciliation, subsequent readback, current-state revalidation, physical replacement/visitor barriers and separate explicitly designated unrestricted schedule support. Native setter primitives are **implemented but not device-verified**. No remote-unlock, door-state schedule, person/biometric, clock, reset, network or firmware write is exposed. Read-only mode remains an independent gate.

See [controlled-write interfaces, commands, ownership limits and prepared physical sequence](hikvision-controlled-writes.md). The guide distinguishes successful reads from untested writes, and documents the current activation blocker without a bypass. Physical issue/grant, revoke/deny, backend-off expiry, weekly/holiday denial, replacement recovery, returned-visitor denial/reassignment, unrestricted hours and Ethernet recovery remain pending.

| Continuation verification | Result |
|---|---|
| Backend isolated PostgreSQL suite | 88 passed; two existing non-failing deprecation warnings. Synthetic transports, no physical credentials. |
| Frontend unit tests/typecheck/build | 37 passed; typecheck and build passed. |
| Mock browser phone/laptop | 6 passed. |
| Connected HTTP/PostgreSQL browser | 2 passed, including API restart and normal synthetic-account login/roles. |
| Read-only HTTP browser | 2 passed. |
| Controlled-mode HTTP browser | 2 passed; no active scope, no unlock/enrolment, server 409 and honest banner. |
| Native/runtime | Host and Debian bridges compiled against actual headers; backend image built without SDK redistribution. Read-only inventory and current runtime checks recorded separately. |
| Read-only preservation | Operation/confirmation hashes unchanged; account count unchanged; real ingestion continues; no write activation. |

Continuation files: `.env.example`, `README.md`; `backend/app/config.py`, `controllers.py`, `hikvision.py`, `hikvision_writes.py`, `hikvision_control.py`, `jobs.py`, `operations.py`, `service.py`, `main.py`, `views.py`; `backend/native/hikvision_readonly.cpp`, `hikvision_permissions.h`; `backend/scripts/build_hikvision.py`; `backend/tests/test_hikvision_writes.py`; `frontend/src/models.ts`, `App.tsx`, `pages/Dashboard.tsx`, `pages/Administration.tsx`; `frontend/package.json`, `playwright.controlled.config.ts`, `tests/http-browser/controlled.spec.ts`; this report and `docs/hikvision-controlled-writes.md`. No migration or private credential change. Existing SDK exclusion/mounts and event checkpoints remain in place. No commit, push, publication or external deployment.


Final connectivity refresh supersedes the earlier successful route/session checks: **both host and fresh backend-container TCP probes timed out**. Host `enp0s31f6` is DOWN with no configured address, and `ip route get 10.10.20.8` selects `via 10.10.10.1 dev wlp1s0 src 10.10.10.2`. The existing local API and PostgreSQL remain healthy; the worker reports FAILED/RECONNECT_WAIT instead of connected. No firewall, host route, interface or controller configuration was changed. Ethernet restoration and powered reconnect/history recovery remain owner-side acceptance. This is a changed host link/route condition, not evidence that container routing alone failed. The final event count remains 234. The existing running API remains the earlier read-only image; newly prepared code is built/tested separately and has not been activated or deployed.

Two overlapping final test runs collided in the isolated `ift_test` fixtures and were discarded. The reported final backend result is from a subsequent single sequential run; the actual application database preservation check remained unchanged.
