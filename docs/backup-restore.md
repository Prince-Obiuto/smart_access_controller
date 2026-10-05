# Local database and upload backup/restore

Backups contain private database records, password hashes and uploads. Keep them in restricted storage and encrypt off-machine copies using the operator's established tools. `.env`, database passwords and controller secrets are not included. No year of historical backups is claimed.

The scripts use the running Compose PostgreSQL container and its local socket. They do not require PostgreSQL credentials in arguments. Run from the repository root using the configured native Python environment. A consistent upload/database snapshot requires **all API and CLI writers stopped**; `--quiesced` explicitly acknowledges this. Leave PostgreSQL running. Do not run bootstrap, reset or migration commands during a backup/restore.

## Native API storage

Stop the native Uvicorn process, then:

```sh
backend/.venv/bin/python backend/scripts/backup.py backup \
  backend/var/backups/2026-10-04T120000Z --quiesced
# Restart the API after successful backup.
```

Choose a new timestamp directory for every backup. The tool refuses overwrite, writes a custom PostgreSQL dump, upload archive and SHA-256 manifest, and sets private permissions. A partial set without a valid manifest is not restorable.

## Docker upload storage

Stop the backend first; the named upload volume persists. Copy its private files into a new private staging directory:

```sh
docker compose stop backend
mkdir -m 700 backend/var/backup-staging-20261004
# Docker cp also works with a stopped container.
docker compose cp backend:/data/uploads/. backend/var/backup-staging-20261004/
UPLOAD_DIR=backend/var/backup-staging-20261004 \
  backend/.venv/bin/python backend/scripts/backup.py backup \
  backend/var/backups/2026-10-04T120000Z --quiesced
docker compose start backend
```

Use a new empty staging directory each time and remove it securely after verifying the backup, according to local storage policy. These scripts do not silently select a different upload volume.

## Restore into a fresh target

Never overwrite an occupied installation. Stop writers. Create a new empty PostgreSQL database and a new empty upload directory. Configure a private recovery environment file with `DATABASE_URL` pointing to the new database; keep the original `.env` and volumes intact. Supply that URL through the environment without printing or placing the password in a command argument (for example, export it from the private file in a restricted operator shell). Set `UPLOAD_DIR` to the new directory.

```sh
docker compose exec -T db createdb -U ift ift_recovered
# After configuring DATABASE_URL and UPLOAD_DIR privately for the fresh target:
backend/.venv/bin/python backend/scripts/backup.py restore \
  backend/var/backups/2026-10-04T120000Z --quiesced
```

Restore verifies hashes/archive paths and refuses any public tables or existing uploads. It invalidates sessions, marks backup-era pending timed unlocks UNCERTAIN and reapplies current retention/expiry rules before completion. A failed restore target must remain offline; use another empty target for a clean retry. Run Alembic `upgrade head` before starting a newer backend. Run `python -m app.cli retention` again after a migration or additional offline waiting time, before exposing the restored application.

For Docker operation, create a **new** upload volume/container for recovery, keeping the original volume untouched. Point `POSTGRES_DB` to the new database in a separate Compose project/environment, copy the retained restored upload files into its `/data/uploads` directory, set ownership to container user 10001, and then start the backend. Do not copy unprocessed old archive files after the retention pass; use the cleaned restore directory. Verify authorized file downloads and scoped records before switching the frontend to the recovered installation. Native recovery uses the restored directory directly.

## One-year retention

Run periodically over the dedicated backup root:

```sh
backend/.venv/bin/python backend/scripts/backup.py prune backend/var/backups
```

This removes only complete sets created by this tool whose manifest timestamp is at least one calendar year old. Unknown files, symlinks and incomplete sets are preserved for operator inspection. Schedule backups/pruning using the installation's normal local scheduler; no automatic system cron installation or existing year-long history is claimed.

## Acceptance check

`backend/.venv/bin/python backend/scripts/check_backup.py` creates fresh isolated PostgreSQL source/restore databases and private temporary upload directories. It verifies both database and uploads, preservation of a retained photo, overdue event/visitor cleanup, invalidated sessions, quarantined unlocks and refusal to overwrite. It leaves the isolated test databases intact and reports their names. It does not test a production disaster recovery, storage failure or year of backup history.
