# Development password recovery

Login includes **Forgot password**. `/forgot-password` requests mail; `/reset-password#token=…` accepts a new password and confirmation, then requires normal login. `/contact-admin` provides the configured local contact. No runtime third-party resources are used. Reset links keep tokens in fragments (absent from HTTP/access logs), remove them from browser history on opening, and retain them only in component memory. The HTML referrer meta and API `Referrer-Policy` are `no-referrer`.

Tokens use 32 cryptographically random bytes. PostgreSQL stores only SHA-256 hashes, one account ID, a 20-minute deadline and consumption time. Successful submission serializes with existing authentication/domain mutations, changes the Argon2id password hash, consumes every outstanding token and deletes all of that account's sessions. Opening a link does not consume it. Passwords use registration's 12–128-character policy without trimming. Roles, person status, access dates, assignments and controller permissions are untouched.

Request responses are identical for eligible, missing, disabled and throttled accounts. A 250ms minimum handler budget and delivery after the response separate account lookup from SMTP latency. Persistent request limits: 3 per address and 20 per direct client IP per 20 minutes. Reset attempts: 10 per token and 30 per direct IP per 20 minutes. These counters never lock login accounts; forwarded IP headers are not trusted. CSRF/origin checks apply to every POST. Passwords/tokens/links are never placed in API responses, audit records or fallback logs.

Configure privately in ignored, owner-only `.env`:

```dotenv
APPLICATION_URL=http://localhost:5173
RECOVERY_CONTACT=Contact the IFT E-Library administrator in person.
SMTP_HOST=YOUR_SMTP_HOST
SMTP_PORT=587
SMTP_FROM=YOUR_REGISTERED_SENDER
SMTP_USERNAME=YOUR_PRIVATE_USERNAME
SMTP_PASSWORD=YOUR_PRIVATE_PASSWORD
SMTP_SECURITY=STARTTLS
```

Use `TLS` for implicit TLS, normally port 465. `PLAIN` is restricted to explicit development mode and intended only for a local mail-capture fixture. Outside development, HTTPS application links and encrypted SMTP are required. No credentials are shipped. Links always use `APPLICATION_URL`, never the request Host. Successful password changes send an email notification and an in-app warning, with no password included.

Compose passes the SMTP fields into the backend environment when the container is created. **A plain container restart does not apply changed `.env` values.** After private configuration, and outside a coordinated backend-stopped hardware test, apply the environment to the existing development service without rebuilding or removing its volumes:

```sh
docker compose -f compose.yaml -f compose.hikvision.yaml up -d --no-deps --no-build --force-recreate backend
docker compose -f compose.yaml -f compose.hikvision.yaml exec -T backend python -m app.cli check-recovery-mail
```

These are the running development backend's verified Compose file paths. The local override now mounts external volume `ift-access_controller_evidence`; it is already seeded and checksum-verified on this PC. Preserve or reseed any newer `/data/controller-evidence` files before the first recreation. On a fresh local installation create that volume explicitly, as documented in the README. If additional local override files are introduced later, preserve them when recreating the service. The mail check verifies TLS/authentication and clears a previous sanitized delivery-failure latch; it sends no message and is not proof of inbox delivery. Keep the registered Victor account signed in in a separate browser for the later session-invalidation check. Use Forgot password normally, open the delivered link privately, reset to a privately chosen password, then confirm that the old session is rejected, normal login succeeds and reuse of the link fails. For expiry, request a separate link and wait its actual 20-minute lifetime without changing any clock or database deadline. Never paste either link into chat or an evidence report. Unknown-account generic responses, concurrent token consumption and unchanged physical permissions are covered by isolated tests; they do not prove inbox delivery.

With SMTP absent, recovery is consistently unavailable, for every email. A delivery failure consumes that undelivered token, records only a sanitized failure action and disables email recovery globally until a private operator check succeeds:

```sh
PYTHONPATH=backend backend/.venv/bin/python -m app.cli check-recovery-mail
```

This verifies connection/TLS/authentication, not an inbox. The owner designated Victor's registered email for real delivery. Private SMTP host/sender/authentication fields are now present and settings validate. CONTROLLER_MODE was corrected to HIKVISION_CONTROLLED, preserving the existing mode. Brevo initially returned **525**, its documented [unauthorized-IP error](https://help.brevo.com/hc/en-us/articles/115000188150-Troubleshooting-Issues-with-Brevo-SMTP). After owner authorization, encrypted SMTP authentication succeeded and the validated environment was applied to the existing development backend. Its actual container mail check passed. The new persistent evidence mount retained all 13 original files and their checksum; database/uploads were preserved. At 00:00:27 Lagos on 15 October the normal API accepted Victor's request and created one token expiring at 00:20:27, with no delivery-failure audit/latch afterward. **The owner confirmed the requested recovery flow “works as intended.”** Live audit confirms reset at 00:07:46 Lagos on 15 October, issued-token consumption and normal login at 00:08:16. Victor's access fields match their pre-reset baseline. Owner confirmation covers the overall requested flow; separate expired-link behavior is proven by isolated tests, not a claimed live expiry exercise. No password, token, hash or link was printed or copied into reports. The CLI reports only sanitized authentication codes/TLS failures; all 15 recovery tests passed, including the two new error-sanitization cases. Automated tests use an in-memory mail transport fixture and inspect the actual MIME sender/recipient/body. They send no real email.

Admin/Super Admin may initiate recovery for ordinary USER accounts from **People → Account recovery**, after explicit in-person identity confirmation. Only Super Admin can initiate privileged-account recovery. Server projections hide actions that the actor cannot perform; the endpoint independently checks roles and identity confirmation. Manager/Management have no authority. Administrative recovery sends the same single-use link to the registered email; it does not return a token. Unavailable email directs the operator to the authenticated local command:

```sh
PYTHONPATH=backend backend/.venv/bin/python -m app.cli reset-password \
  --actor-email AUTHORIZED_ADMIN@futo.edu.ng --email TARGET_REGISTERED_EMAIL
```

The operator authenticates privately, confirms identity and chooses the temporary/new password privately. The command uses an internal, hashed single-use recovery credential through the same consumption function; revokes sessions, audits the authenticated actor, and notifies the account. Admin remains limited to USER targets.

For a locked-out Super Admin, the trusted local PC owner can use:

```sh
PYTHONPATH=backend backend/.venv/bin/python -m app.cli operator-recovery \
  --email EXISTING_SUPER_ADMIN@futo.edu.ng
```

This requires an interactive local terminal, ownership of `.env`, and no group/other file permissions, plus explicit identity confirmation. It accepts no universal password and exposes no public endpoint. It operates only on an existing eligible Super Admin, uses the same single-use mechanism, and audits local-operator recovery separately. Anyone with the application's private OS/database credentials already has administrator-level storage access; secure those credentials accordingly.

The additive Alembic migration `76dc91a120ab` was applied to the development database; no reset or truncation. Production configuration and database preparation remain deferred.

Reference: [OWASP Forgot Password Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html).
