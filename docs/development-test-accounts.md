# Fresh development test accounts

## Current acceptance state — 2026-10-14

This section supersedes the dated re-registration snapshot below; its historical IDs and reset evidence remain retained. **Victor Amaobi** (`20211260402`) completed genuine proof/payment review and normal issuance of fresh card **1053990519**. Application `1e68f236-405e-4ecc-aa53-6d5bc085a92f` is ISSUED and assignment `b4216342-ef7a-435d-8e25-96261bfbbb2a` ACTIVE/SYNCHRONIZED, with authorized 13 October–31 December 2026 access, existing TEMPLATE 3 and owner-confirmed granted swipe/UI. The mistaken Super Admin application remains unissued.

Visitor01/02/03/05 visits have ended; do not renew them or erase their historical ownership. Visitor03/05 were physically returned and checked out, with individually confirmed controller disable. Visitor04's genuinely approved 14 October **13:05–18:45 Lagos** visit remains available for its separate reviewed V assignment. V is **1052815031**; A **1051305927** and B **1052625607** are permanently revoked and must never be reactivated. No duplicate test registrations or password changes are needed. A fresh approved visit is required for the still-pending backend-stopped expiry test.

The current frontend is `http://localhost:5173`, proxying the READ_ONLY Docker API on 8000 with current code mounted read-only. The temporary API is stopped. Original upload storage and private acceptance backups are preserved. See [development-acceptance.md](development-acceptance.md) for current automated, device-readback and owner-observed results.

## Re-registration verified — 2026-10-13

The owner registered **Victor Amaobi**, STUDENT/USER, person `bb0bfc7f-1689-4be5-bdc3-83b1e4309384`, reference `20211260402`, matching the intended institutional email. The owner-entered name order is preserved. His new FIRST application `1e68f236-405e-4ecc-aa53-6d5bc085a92f` is **APPROVED**, has proof and a recorded Super Admin reviewer, and has no assignment. Administrative access dates remain unset. The separate Super Admin application was identified by the owner as a mistake; it remains unissued and is excluded from acceptance.

The actual new VISITOR/USER registrations replace the earlier proposed John/Jane identities:

| Identity | Reference | Person ID | Visit/assignment |
|---|---|---|---|
| Visitor 01 | `VIS-7C61A2CE1134` | `404d6e18-eaab-4222-bd90-6675e06349e4` | None |
| Visitor 02 | `VIS-9B9B5EE23AA4` | `d789c73f-cb08-4312-8e43-3d3e3e6dd3be` | None |

Do not register duplicate John/Jane accounts. Both actual visitor requests received owner-recorded `EXTERNAL_APPROVAL_CONFIRMED` with reason “visit genuine”: Visitor 01 at 18:17:38 Lagos, Visitor 02 at 18:17:49. Visitor 01's approved 18:13–18:18 window expired before assignment; normal retention ended the visit/account access at 18:18. Do not renew or reuse that ended identity to manufacture acceptance. Visitor 02's approved window is 18:20–18:45. Both remain unassigned at this snapshot.

The familiar frontend at `http://localhost:5173` now proxies the READ_ONLY/jobs-disabled acceptance API on 8001 and supports Admin request-on-behalf and real reader capture; the extra frontend on 5174 was stopped. Original collector/backfill remains on 8000. Existing proof files are privately mirrored for review, originals unchanged. Before any additional proof/profile upload, restore the frontend proxy to the original API (omit `VITE_API_PROXY_TARGET` when starting Vite); this avoids writing new files to the temporary mirror. No scope or write is active yet. The next fresh-personal-card scan must be bounded and owner-confirmed before issuance/scope designation. Cancelled sessions and their earlier swipes are excluded.

The registration table below records the original requested fields, not instructions to create further accounts now.

On 2026-10-13, at the owner's explicit request, the old Amaobi Victor, John Doe and Jane Doe accounts and their registration/person records were removed. Amaobi's unused FIRST draft `a3dc0cc3-d381-4550-b001-4c4b855a5966` was deleted. The sole Super Admin account, password and sessions are unchanged. Historical registration/application audits retain their original actor names/targets; four audit person links were detached so the removed registrations can be recreated without deleting that evidence. No card/event/operation/schedule records or controller permissions were removed.

Register each account through **Create account** at `http://localhost:5173`. They must receive the ordinary **USER** role; do not create another Super Admin. Choose passwords privately, at least 12 characters, and save them in your password manager. Passwords are not supplied or stored in this document. Log out between registrations, then sign back in as the existing Super Admin for administration.

| Field | Personal test user | Visitor 1 | Visitor 2 |
|---|---|---|---|
| First name | Amaobi | John | Jane |
| Last name | Victor | Doe | Doe |
| Type | STUDENT | VISITOR | VISITOR |
| Reference | `20211260402` | Generated by registration | Generated by registration |
| Email | `amaobivictor.20211260402@futo.edu.ng` | `john.doe.ift-test@example.invalid` | `jane.doe.ift-test@example.invalid` |
| Phone | `009132421301` | `TEST-NO-PHONE` | `TEST-NO-PHONE` |
| Department | Information Technology | IFT acceptance test | IFT acceptance test |

The visitor contact fields are explicitly fictional test values previously approved by the owner. Amaobi's phone remains a string with its leading zeros. Reusing these emails/reference is now possible because the previous registration records were removed; the new accounts will have new IDs. Record the new visitor references rather than reusing the old `VIS-...` identifiers.

After registration, Amaobi must create a new FIRST application, submit the required genuine proof, receive authorized payment review and have administrative access dates set. Issuance still requires a fresh personal card captured from the reader and inspected for local/device conflicts. **Never use A `1051305927` or B `1052625607`: both remain permanently revoked/blacklisted with zero rights.** V `1052815031` remains the unassigned, quarantined visitor-pool test asset; do not register it as a new personal card or mark it available manually.

Visitors require normal dated requests and genuine external approval recorded by an authorized administrator. No visit, approval, assignment or password reset was created by this cleanup. Previously proposed 2026-10-13 visit windows and identity-bound manifests are superseded; refresh them after the owner registers the new accounts. Physical expiry, closing-boundary and visitor reuse acceptance remain pending. Production and the completed-baseline milestone remain deferred.

Private recovery/evidence: ignored `backend/var/acceptance/account-reset-20261013/database-before-reset-piped.dump`, `result.json` and `file-cleanup.json`. The database backup is mode 600. The first direct-file Docker backup attempt produced an empty file; the successful backup used piped transport. No successful backup or original controller evidence was discarded.
