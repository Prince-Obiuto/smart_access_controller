# Authenticated controller management

The normal web workflow no longer needs a diagnostic manifest. This is development implementation evidence, not full production or physical acceptance.

## Card workflow

1. Save the genuine approved paid issuance, replacement, validity change, revocation, or approved visitor assignment/return normally. Application roles and physical access rights are separate. Active Super Admin accounts are eligible for ordinary personal issuance.
2. In **Administration → Controller**, prepare a current assignment operation if changed dates have superseded the previous operation. This queues only that assignment and sends no device command.
3. **Read and reconcile controller card** accepts the full string number independently of a write scope, including a revoked or iVMS-created record. Compare local ownership/dates with actual type, all door rights, four plan references per door, validity flags and timestamps.
4. Select **Compare this operation**, choose a successfully readable existing template and prepare a fresh comparison. External or changed permissions require an explicit reconciliation reason. **Adopt observed baseline only** records the fresh external baseline and its reason; it does not assign ownership, invent payment/visitor approval, change permissions or claim synchronization.
5. Review the operation ID, before/proposed permissions and affected cards. Confirm that exact change once. The server rechecks account permissions, current assignment version, real approval, expiry, irreversible revocation and previous-card disable. It compares a fresh complete current-card/reference graph and Door 1/2 configuration with the saved preview.
6. SDK acknowledgement alone cannot synchronize an assignment. The final exact identity/type/leader/rights/plan/validity readback and unaffected-resource comparison must pass. A timeout, missing response or mismatch remains `REQUIRES_RECONCILIATION`; inspect fresh state and prepare an explicitly reviewed comparison. An already-applied matching card is confirmed by GET without another SET. Original failed attempts remain in history.

Ordinary enable/update currently reviews Door 1 access. A card with existing rights on another door requires a separate review and cannot silently lose those rights. Disable previews disclose every currently affected door. Cards A `1051305927` and B `1052625607` remain permanently revoked. Role membership never creates an unrestricted entitlement. Privileged designation still requires the normal separate Super Admin designation and a verified all-day week/template without restrictive holiday references; old privileged-card disable and expiry/revocation remain enforced.

## Live schedules

**Administration → Schedules → Live controller schedules** reads a specific WEEK, HOLIDAY, GROUP or TEMPLATE using the installed HCNetSDK card-access definitions. The local schedule form below is a draft, not global hardware synchronization.

- Refresh retrieves a complete typed baseline and revision. Edits use read-modify-write and retain names, reserved bytes, segment door/verification fields, and the fields outside the requested change.
- Weekly plans have up to eight intervals per day; 24:00 is an explicit end-of-day boundary. Holiday plans include first/last dates and intervals. Groups link holiday plans; templates link one week and advertised holiday-group counts. Specific linked slots must be successfully read.
- Nonuniform named-weekday writes require independently labelled controller-retrieved iVMS evidence. The evidence form requires seven distinct matching daily patterns and a view reference, establishing the full native-index permutation. Uniform/repeating weeks cannot establish a complete order. Uniform seven-day edits can be reviewed without inventing weekday labels.
- Preview lists current card/door references, reads linked resources and captures both door configurations. Any current Door 2 reference blocks the schedule change in this Door 1 workflow. An empty impact list means **no current authorized card reference**, not proof that a slot is globally unused. Dormant/unreferenced template inventory and total template capacity remain unknown.
- Each SET is followed by full GET comparison. **Preview exact preimage restoration** prepares a separately authorized rollback using the complete saved native preimage, including the original enable byte and reserved fields. It compares against the new current revision and verifies the entire restored native record. Clearing enabled intervals alone is not restoration.
- Specific supported-slot updates do not require an invented global template range. Firmware-rejected GET/SET operations remain unsupported/unverified. No bulk allocation or namespace scan is performed.

The actual DS-K2802 currently advertises weeks 1–64/eight intervals, holidays 1–256/eight intervals, groups 1–64/16 plans, and four holiday groups per template. The header contains larger arrays in some structures; those are not device capacities. Total template capacity and complete weekday ordering remain unresolved. The exact manufacturer question in [hikvision-controlled-writes.md](hikvision-controlled-writes.md) remains applicable.

## Durability and authorization

FastAPI synchronous handlers keep native calls off the event loop. The same controller advisory lock used by remote unlocking serializes physical writes. Domain locks and current-version checks prevent a local revoke from being ignored between review and dispatch; native setters compare the immediate typed preimage too. A preview expires after ten minutes. The native channel uses bounded requests, complete-buffer checks and explicit callback-gap errors.

Previews and attempts are durable `controller_operations` records. Raw preimages are internal; public views contain parsed permissions. Preview request keys are bound to actor and payload. Confirmation names one preview ID; duplicate confirmed execution returns its result, uncertain attempts are not replayed. No write epoch is added to unrelated queue entries. Legacy operator scopes must be closed before this web workflow is used; legacy diagnostic commands remain for existing evidence/compatibility, not normal administration.

Admin may review ordinary card operations according to configured permissions. Super Admin alone manages device schedules, baseline adoption, controller configuration and privileged cards. Manager, Management and User cannot use these administrative paths. Role permission and CSRF checks apply on the server, independently of UI visibility.

No production configuration/database, SDK redistribution, remote unlock replay, bulk synchronization, database reset, push or deployment is part of this change.

## Observed validity response profile

The owner-authorized Super Admin creation exposed the same zero start/end-flag response retained in the earlier timing evidence. A fresh GET-only compiler ABI probe of this card found complete, identical legacy/V50 validity bytes and exact dates. The global strict matcher is unchanged. The web path has one explicit exception for ordinary Door 1/TEMPLATE 3 on serial DS-K280220250625V020008ENGS6372431 / firmware V2.0.8 build 250625, pinned to the probed SDK header/library hashes and ABI offsets. Its proof requires the retained owner-confirmed pre-start denial, in-window grant and post-expiry denial events; every other parsed permission and the complete validity byte slice must match exactly. No flag or actual record is synthesized. Other firmware, SDK files, templates, privileged cards, date/rights differences, partial flags or additional raw validity differences remain unconfirmed.

The application exposes the actual zero flags, profile caveat and GET-only confirmation. **Verify actual state without resending** cannot issue a SET, including for an uncertain/expired preview; it still requires the current approved assignment/version, exact operation/affected-card IDs and protected-resource checks. Old superseded source commands cannot be revived. Reconciliation preserves the original mismatch history. The [official flag definition](https://open.hikvision.com/hardware/structures/NET_DVR_VALID_PERIOD_CFG.html) gives normative limit meanings; this observation profile does not settle why these SDK getters return zero or prove backend-stopped expiry. That physical check and manufacturer clarification remain pending.
