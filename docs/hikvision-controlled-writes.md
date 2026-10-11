# Controlled card-access writes: preparation and acceptance

The current installation runs **HIKVISION_READ_ONLY**. The controlled executor and native write protocol are implemented and tested with synthetic transports; **no controller write has been enabled or sent**. Scope activation currently refuses the device inventory because the template capacity is not established. No owner-designated spare credential is available. Do not select an existing iVMS card or infer a local person from an unknown swipe.

## Current device evidence

DS-K2802, firmware V2.0.8 build 250625; SDK 6.1.9.4, owner-provided x86_64 Linux package. The matching root `incEn/HCNetSDK.h` supplies every ABI declaration. The card-based guide's “Manage Card Information” and “Configure Access Permission Control Schedule” examples supply the callback and command flows. They contain documentation errors (including holiday-array dimensions and weekly-enable prose); the actual header, checked native output lengths and successful reads are authoritative for layout. No vendor source/header/binary is copied into Git or the image.

| Native structure | Compiled size, bytes |
|---|---:|
| NET_DVR_CARD_CFG_V50 | 2708 |
| NET_DVR_CARD_CFG_COND | 40 |
| NET_DVR_CARD_CFG_SEND_DATA | 52 |
| NET_DVR_WEEK_PLAN_CFG | 920 |
| NET_DVR_HOLIDAY_PLAN_CFG | 160 |
| NET_DVR_HOLIDAY_GROUP_CFG | 136 |
| NET_DVR_PLAN_TEMPLATE | 140 |

Native card callbacks reject short buffers; schedule getters require the actual returned size to match the compiled structure. Callback work is a bounded copy into a fixed queue; overflow is an explicit failure. Permission sessions use their own login without consuming an alarm subscription, independently of the persistent event session.

Observed card capabilities advertise `cardvalid,validtime,cardtype,doorright,leadercard,...,rightplan`, normal/blacklist card types, card identifier length 1–20 and second-level card validity. Week/holiday segments advertise minute precision; week IDs 1–64, holiday IDs 1–256, holiday groups 1–64, eight segments/day, sixteen holidays/group and four groups/template. Card-access template capacity and weekday array origin are absent. Advertised values are not write acceptance.

Read-only enumeration returned four existing cards, each with Door 1 and Door 2 rights linked to template 1. None was imported or changed. Card-access templates 1 “All-Day Authorized” and 2 “All-Day Denied” reference weeks 1 and 2. These enabled resources are protected. Week/group/holiday getters 1–64 succeeded; templates 1–128 succeeded. Template 129–255 reads returned error 11, `NET_DVR_NETWORK_ERRORDATA`; V50 template reads sampled at 1/128 succeeded and 129/255 returned the same error. Error 11 is not treated as “unused” or proof of a capacity limit. The private inventory retains all results and reports `complete:false`. Activation requires a verified template capacity plus complete reads and currently returns `INVENTORY_REQUIRED`.

## Prepared interfaces and safeguards

| Operation | Prepared interface | Device-verified status |
|---|---|---|
| Read cards | StartRemoteConfig GET_CARD_CFG_V50 2178; SendRemoteConfig ENUM_ACS_SEND_DATA; matching data/status callback | Four real records and complete all-card enumeration received |
| Issue/update | SET_CARD_CFG_V50 2179; ordinary card, Door 1 only, explicit validity, one owned template | Not sent; write callback/readback and swipes pending |
| Disable/revoke | Same setter, blacklist type 3 plus zero rights on every door | Not sent; GET must show blacklist plus zero rights, or explicit CARDNO_NOT_EXIST 1903 |
| Card-access week | GET/SET_CARD_RIGHT_WEEK_PLAN 2126/2127 | GET verified; SET/readback/enforcement pending |
| Card-access holiday | GET/SET_CARD_RIGHT_HOLIDAY_PLAN 2130/2131 | GET verified; SET/readback/enforcement pending |
| Card-access holiday group | GET/SET_CARD_RIGHT_HOLIDAY_GROUP 2134/2135 | GET verified; SET/readback/enforcement pending |
| Card-access template | GET/SET_CARD_RIGHT_PLAN_TEMPLATE 2138/2139 | GET verified 1–128; capacity unresolved; SET pending |
| V50 template GET | GetDeviceConfig 2322 with actual PLAN_TEMPLATE_COND | Boundary samples described above; no V50 setter fallback |

These are **card eligibility** plans. No door-state plan setter, remote release, clock write, user/biometric write, reset, network change or upgrade command is exposed. The distinction is also documented in Hikvision's [GetDVRConfig access-control command table](https://open.hikvision.com/hardware/definitions/NET_DVR_GetDVRConfig_ACS.html) and [week-plan structure](https://open.hikvision.com/hardware/structures/NET_DVR_WEEK_PLAN_CFG.html).

`HIKVISION_CONTROLLED` requires an enabled `hikvision:write-scope:<controllerId>` setting created by the authenticated local tool. Scope pins the device identity, exact string card identifiers, permitted people, resource baselines and optional explicitly designated unrestricted card. A Super Admin role alone grants no hardware privilege. Card kinds/assignment ownership and current eligibility are checked again before dispatch. Nonuniform weekly plans require an explicit weekday-order attestation/reference; do not guess it. Missing capacity, unsupported precision, insufficient holiday slots, changed unmanaged resources, unverified clock or identity block execution.

Each activation has a new epoch. Existing operations receive no epoch automatically and remain held, with no attempt increment or physical execution. Individual review re-evaluates the current assignment/version/expiry/revocation/replacement rules; new normal workflow operations receive the active epoch. Out-of-scope targets are refused. Permanently revoked personal cards cannot be re-enabled. Every old personal assignment needs physical disable evidence on every configured controller before replacement resumes. Previous simulation confirmations cannot satisfy that requirement.

The executor holds a dedicated per-controller PostgreSQL advisory lock across reconciliation. Short workflow transactions commit intent and pre-send payload hashes before native calls; no global workflow/event lock is held during native I/O. SDK operations run in synchronous request threads or worker threads, away from FastAPI's event loop. The permission worker is independent of event history/backfill, prioritizes disables, and defers enables when a disable is waiting. Five automatic attempts maximum, with exponential delay capped at 300 seconds. Authentication/ownership/capability failures stop automatic retries and require a reviewed/new intent. Native calls have bounded timeouts; permission reconciliation has a 60-second budget checked between calls, and inventory a 300-second budget (one in-flight request may finish after the budget). There is no transport-acknowledgement or simulated success fallback.

Every retry starts with GET. A timeout leaves the operation UNCERTAIN/FAILED and unconfirmed; matching authoritative readback can reconcile it without replay. Before a plan write, its exact possible hash is durable, allowing a later desired version to recognize a partially applied application plan. Unrecognized changes are protected. The used SDK interfaces provide no controller-side transaction spanning another iVMS client; reserve application test resources operationally and avoid concurrent iVMS edits during acceptance. Successful writes require a subsequent matching GET. Finalization rechecks desired state and scope after I/O. Disables that fail generate critical warnings; visitor returns stay unavailable until every required disable is proven. Pool cards from old simulated confirmations are rejected for real reuse.

Validity is encoded on the controller with explicit Lagos local start/end. Second-level normalization rounds start upward and expiry downward, never extending eligibility. Readback must retain both start-limit and end-limit flags as enabled; matching dates alone are insufficient. Weekly/holiday records persist on the controller; backend-only expiry is insufficient. Holiday capacity overflow blocks the whole policy rather than omitting dates. A separate owned all-week unrestricted template is only usable for the exact locally designated Super Admin credential. Schedule changes invalidate old normal-card confirmation projections; a controlled test plan never marks the global installation schedule synchronized.

## Commands and future activation

Run from the repository root, retaining private `.env`. Keep the SDK excluded by existing Git/Docker ignore rules and mounted read-only. Credentials remain stdin-only to the bridge; application passwords are entered privately with getpass. Do not dump environment or place passwords in commands.

```sh
backend/.venv/bin/python backend/scripts/build_hikvision.py
backend/.venv/bin/python backend/scripts/build_hikvision.py --container
docker compose build backend
# Authorized existing local services, read-only configuration:
docker compose -f compose.yaml -f compose.hikvision.yaml up -d backend
# Existing frontend: http://localhost:5173, normal application login.

env PYTHONPATH=backend backend/.venv/bin/python -m app.hikvision_control inventory \
  --output backend/var/hikvision-inventory-review.json
```

Inventory refuses to overwrite a file and creates mode 600. It contains controller card identifiers; keep it private/ignored. It sends no writes and changes no application records. Runtime SDK and bridge locations remain those in the main capability guide. Container equivalent:

```sh
docker compose -f compose.yaml -f compose.hikvision.yaml exec backend \
  python -m app.hikvision_control inventory --output /tmp/hikvision-inventory-review.json
```

**Do not activate now.** First resolve capacity/read coverage using authoritative SDK/device evidence, establish weekday ordering and designate spare cards through normal approval/payment/person/assignment workflows. Activation rejects the current device inventory even if a manifest is supplied. No bypass option is provided. No card has been selected on the owner's behalf.

After those blockers are resolved, the local-only manifest describes exact test scope. This is a schema example, **not an allocation or permission to use these numbers**; all IDs must be freshly inspected as disabled/unreferenced or match a previous audited application-owned baseline:

```json
{
  "controllerId": "controller-1",
  "cards": {
    "OWNER_SPARE_CARD_NUMBER": {
      "kind": "PERSONAL",
      "personIds": ["EXISTING_TEST_PERSON_ID"]
    }
  },
  "normal": {"week": 3, "template": 3, "group": 3, "holidays": [3, 4]},
  "weekdayOrder": "REQUIRES_VERIFICATION",
  "weekdayEvidence": "Reference or physical/iVMS evidence for SDK weekday ordering"
}
```

Place the completed manifest under ignored `backend/var/`, mode 600. Visitor cards use kind VISITOR and explicit permitted test-person IDs. Optional `unrestrictedCard` must name an explicitly designated active Super Admin card in the card scope, with separate `unrestricted.week/template` resources. Every reservation is fresh-read and authenticated; activation itself sends no SDK write.

```sh
# Future only: change private CONTROLLER_MODE to HIKVISION_CONTROLLED explicitly
# and reload the existing local backend. With no scope, all real writes stay held.
env PYTHONPATH=backend backend/.venv/bin/python -m app.hikvision_control activate \
  --actor-email YOUR_EXISTING_SUPER_ADMIN@futo.edu.ng \
  --manifest backend/var/owner-test-scope.json
# Old backlog: review specific current intent; never bulk-drain it.
env PYTHONPATH=backend backend/.venv/bin/python -m app.hikvision_control review \
  --actor-email YOUR_EXISTING_SUPER_ADMIN@futo.edu.ng --operation OPERATION_ID
# Normal UI approval/issue/revoke/retry actions create durable operations.
# Stop future dispatch locally (this does not revoke already stored permissions):
env PYTHONPATH=backend backend/.venv/bin/python -m app.hikvision_control deactivate \
  --actor-email YOUR_EXISTING_SUPER_ADMIN@futo.edu.ng
```

Reactivation creates a new epoch, keeps the backlog held, and accepts enabled resources only if they match the previous application's audited baselines. Disable test credentials and verify readback before retiring a test scope. Retaining READ_ONLY mode is an additional independent write barrier; switching modes cannot convert old simulation confirmations into real evidence.

## Physical acceptance still required

Normal owner authentication was recorded on the running installation at 17:18:36 Lagos on 2026-10-08. Unauthenticated `/api/workspace` through the frontend proxy returned 401. Role/CSRF and phone/laptop tests use isolated synthetic accounts and normal password/session endpoints; they are separate from the owner's actual account. No account/password reset, impersonation session or unknown-card import was used in this continuation.

The owner selected “Skip for now” for powered Ethernet recovery and declined to choose spare credentials while stressed. All physical write checks therefore remain pending. Prepared sequence, after the blockers above are resolved:

1. Issue a designated paid/approved personal test credential through the application; inspect operation/native callback and complete card/schedule GET; make an ordinary authorized swipe and verify real PostgreSQL/API/UI event.
2. Revoke it through the application; require blacklist/zero-rights or authoritative absence GET; swipe again and verify DENIED. Never reuse a permanently revoked personal number.
3. Use a different designated spare with a short future expiry. Confirm controller validity, stop only the backend, then verify an ordinary swipe is denied after expiry; restart and recover history without duplicates.
4. Verify outside-hours and closed/special-date denial. Verify weekday indices with a deliberately distinct test-only plan before accepting weekly policy. Test unrestricted hours only on the explicitly designated credential.
5. During an Ethernet outage keep the controller powered. Confirm a replacement cannot activate while the old disable is unconfirmed. Reconnect; reconcile old disable before new activation. Verify no false synchronization and no duplicate events.
6. Assign an approved visitor window, confirm expiry without the backend, return and require physical disable before availability. Swipe the returned unassigned card (denied), then assign to the next authorized test visitor. Recover delayed events against the original event-time assignment.
7. Separately unplug only Ethernet, wait for observed disconnected/stale status, swipe during the gap, reconnect and swipe again. Record physical readers, times, card suffix and device outcome. Verify bounded reconnect, resumed live subscription, history coverage and deduplication. A local bridge heartbeat is not device communication; history retries every two minutes and a stale communication projection becomes unavailable after 150 seconds.

No remote-unlock command is part of these checks. The confirmed mapping remains Reader 1 EXIT / Reader 2 ENTRY on Door 1. A grant/swipe does not prove physical passage.

## Files and verification

New implementation: `backend/native/hikvision_permissions.h`, `backend/app/hikvision_writes.py`, `backend/app/hikvision_control.py`; companion bridge/session/config/controller/job/outbox/service/view guards and build manifests extended. Frontend models/banner/dashboard/schedule action distinguish controlled mode, while unlock and fake enrolment stay unavailable. Added isolated PostgreSQL write-policy tests and phone/laptop controlled-mode browser checks. No new migration or credential/database reset is required.

See the current results in [hikvision-capabilities.md](hikvision-capabilities.md). The final refresh found Ethernet DOWN and routing through Wi-Fi; host/container probes timed out and the existing worker honestly reports reconnect wait. Earlier successful native reads remain evidence, while restored connectivity/recovery is pending. The existing API remains its original read-only image; the new preparation is separately built/tested, with no activation or deployment. No commit, push or publication.
