# Frontend service contract (provisional)

Components consume one asynchronous `AccessControlService`, independent of fixtures, storage and transport. All identifiers are strings; timestamps are ISO 8601; displayed operational time is Africa/Lagos. Lists returned by `records` use `{items,total,page,pageSize}`; `snapshot` is a small, role-scoped workspace aggregate rather than an unbounded production listing. Production family listings should adopt the same pagination envelope.

## Authorization

Central source: `frontend/src/permissions.ts`. Apply it to navigation, guards, controls and service operations. The future server must independently enforce role and ownership on every read and write; browser restrictions provide no secure production boundary.

| Capability                                          | SUPER_ADMIN | ADMIN   | MANAGER | MANAGEMENT | USER                 |
| --------------------------------------------------- | ----------- | ------- | ------- | ---------- | -------------------- |
| Dashboard                                           | operate     | operate | view    | no         | no                   |
| Payment review                                      | yes         | yes     | yes     | no         | own status           |
| Ordinary person edits / validity                    | yes         | yes     | no      | no         | own phone/photo only |
| Card issuance / administrative revocation           | yes         | yes     | no      | no         | own missing report   |
| Visitor approval confirmation / issuance / checkout | yes         | yes     | no      | no         | own request          |
| Remote timed unlock                                 | yes         | yes     | no      | no         | no                   |
| Schedules / exceptions                              | yes         | yes     | no      | no         | no                   |
| Access and audit logs                               | yes         | yes     | yes     | yes        | no                   |
| Filtered exports                                    | yes         | yes     | no      | yes        | no                   |
| Controllers, roles, payment, privileged designation | yes         | no      | no      | no         | no                   |
| Soft-delete recovery                                | yes         | no      | no      | no         | no                   |

Every role may manage its own phone/photo and report its own card missing. Person type STUDENT/STAFF/VISITOR is independent of role. Registration always creates USER; visitors cannot upload payment evidence. Privileged-account identity edits require SUPER_ADMIN in the mock.

## Models and state invariants

`src/models.ts` defines Identity, Person, Card, Assignment, Application, Evidence, Visit, Device, Schedule, AccessEvent, Audit, Notice, Confirmation and OperationResult. Person status ACTIVE/DISABLED does not imply physical access. Admin supplies start and exclusive expiry explicitly; expiry must follow start. Institutional email domain must equal futo.edu.ng after trimming and case normalization, without subdomains or lookalikes. Domain validation does not verify ownership.

Applications: DRAFT → SUBMITTED → APPROVED → ISSUANCE_PENDING → ISSUED. Rejection: SUBMITTED → REJECTED → SUBMITTED, retaining dated reviewer/reason history. Evidence upload does not approve; review and confirmed issuance have separate actors/timestamps. Duplicate open submissions and issuance are blocked. The demo accepts JPEG/PNG/PDF up to configurable 5 MiB; preview bytes live only in temporary object URLs. Server implementation must validate content/signatures, reject unsafe files, enforce limits, use safe storage and serve authenticated downloads. Browser MIME checks are not sufficient.

Assignments: PENDING, ACTIVE, REVOKED, EXPIRED, CLOSED. Sync: PENDING, SYNCHRONIZED, FAILED, OUT_OF_SYNC. Every operation tracks confirmations for all target controllers separately. Model arrays support additional doors/controllers; the UI starts with one DS-K2802 and one door with Entry and Exit readers. There is at most one current ACTIVE/PENDING assignment per actual person and physical card. Event records retain their event-time assignment ID and synthetic owner/reference snapshots, never resolving their historical owner from current card ownership.

A card number is a string retaining leading zeros. Manual entry and explicit 30-second enrolment scan sessions support detected, cancelled, expired and confirmed states. Ordinary access swipes never create a card asset. Revoked personal assets cannot be reused. MISSING includes lost and suspected stolen. Replacement revokes locally, attempts old disable everywhere, blocks new activation until confirmed, then attempts new activation. Failed disable is CRITICAL. Own missing reports execute immediately without payment or approval. Access-date edits remain pending until a simulated update is confirmed.

Visitor pool: AVAILABLE_DISABLED → ASSIGNMENT_PENDING → ASSIGNED → RETURN_PENDING_DISABLE → AVAILABLE_DISABLED. Failed removal leaves RETURN_PENDING_DISABLE; missing/damaged cards stay unavailable. Expired unreturned cards are QUARANTINED. Approval is external; Admin records a confirmation reference before assignment. Visitors have individually generated IDs and fee-free requests. Once a temporary account has ended, it cannot submit a new visit to bypass its original retention deadline, even after restoration. Physical return must be recorded; an Exit swipe does not checkout. Expiry ends access even without return. Failed controller removal cannot make a card available.

Normal schedule includes weekdays, working hours and calendar exceptions. Saved changes remain PENDING until simulated controller update succeeds. One separately designated Super Admin personal card has unrestricted 24/7 access including holidays; role elevation does not create one. Privileged schedule designation requires confirmed simulated controller synchronization; failure leaves it unapplied. Revocation can leave no usable designation; replacing it must confirm disable of the previous card before designation/activation.

Timed unlock is five seconds, with explicit confirmation naming the door. Result includes intended state, per-controller confirmation, result CONFIRMED/PENDING/FAILED/UNCERTAIN and a message. Acknowledgement is not sensor proof: sensor remains UNKNOWN. Timeouts are uncertain and never retried automatically. Notification severities INFO/WARNING/CRITICAL include simulated unknown/revoked credentials, disconnect/reconnect, synchronization failures, forced/held opening and reader faults. Actual hardware support is unverified.

## Proposed HTTP mapping

No endpoint below is implemented or final. No Hikvision endpoints are invented. `execute(Command)` is the typed frontend mutation boundary; the HTTP adapter should translate each union variant into a dedicated application endpoint.

| Service operation / command family                     | Proposed FastAPI mapping                                                                                               |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| currentIdentity, login, logout, register               | GET /auth/me; POST /auth/login, /auth/logout, /auth/register, /auth/register/visitor                                   |
| snapshot                                               | GET /workspace (server-scoped aggregate)                                                                               |
| profile, person, dates                                 | PATCH /me/profile; POST/PATCH /people/{id}; PATCH /people/{id}/validity                                                |
| application, submit, review, issue                     | POST /applications; POST /applications/{id}/evidence; POST /applications/{id}/review; POST /applications/{id}/issuance |
| snapshot card/assignment families                      | GET /cards, /cards/{id}/assignments (paginated in production)                                                          |
| scan methods                                           | POST /enrolment-sessions; GET /enrolment-sessions/{id}; POST /{id}/confirm, /{id}/cancel                               |
| revoke, retry-sync                                     | POST /assignments/{id}/revoke; POST /sync-operations/{id}/retry                                                        |
| visit, visitor-approve, visitor-assign, visitor-return | POST /visits; POST /visits/{id}/approval-confirmation, /assignment, /checkout                                          |
| schedule / schedule synchronization                    | PUT /schedules/normal; POST /schedules/normal/synchronize                                                              |
| unlock / device health                                 | POST /doors/{id}/timed-unlock; GET /controllers/health                                                                 |
| records                                                | GET /access-events, /audit-records with filters and pagination                                                         |
| exportRecords                                          | POST /exports/access-events, /exports/audit-records (authenticated CSV/PDF)                                            |
| recovery / restore                                     | GET /recovery/{family}; POST /recovery/{family}/{id}/restore                                                           |
| settings, role, designate, device                      | PATCH /settings; PATCH /accounts/{id}/role; PUT /privileged-card; PATCH /controllers/{id}                              |
| notifications / notice-read                            | GET /notifications; PATCH /notifications/{id}/read                                                                     |

Errors use ServiceError with code, message and optional field: FORBIDDEN, VALIDATION_ERROR, CONFLICT, CONTROLLER_UNAVAILABLE, UNAUTHENTICATED, NOT_CONNECTED. Offline simulated operations return FAILED results carrying per-controller confirmations, so local revocation remains observable. The server should add operation IDs/idempotency keys, transactional uniqueness, device-specific target sets, CSRF/session handling and safe concurrency. HTTP scaffold always rejects as NOT_CONNECTED; it does not fabricate successful responses.

## Logs, export and retention

Access filters: from/through date, person, reference, card, controller, door, reader direction, granted/denied, event type. Audit filters: date range, actor, action, target, result. CSV and bundled PDF use all matching permitted rows independent of displayed pagination and record export in audit history. CSV fields beginning with spreadsheet-formula prefixes are escaped, including whitespace-prefixed formulas. Mock audit history is mutable demonstration data, never tamper-proof storage. Secrets are not written into audit fields.

Access events: soft delete after 90 days, purge after 120 days. Audit: six calendar months (month-end clamped), then 30 days to purge. Visitor details: soft delete 15 days after access ends, purge 45 days after access ends. A restored marker keeps the record visible until its original purge deadline; restoration never restarts retention, revives an assignment or extends validity. Visitor PII copied into event/audit fields is anonymized at the earlier visitor purge deadline, retaining opaque historical references. The demo clock advances these rules in memory. Backend jobs must enforce them durably, including object storage deletion and backups. One-year backup retention is unconnected deployment work.
