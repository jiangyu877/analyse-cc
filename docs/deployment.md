# Deployment

Requirements: Python 3.12, PostgreSQL 18, and `PG_BIN_DIR` containing `pg_dump.exe`, `pg_restore.exe`, and `psql.exe`. Copy `.env.example` to `.env`, set `DATABASE_URL` and secrets, then run `python scripts/init_db.py` (migration 008 is required).

Run web and worker locally with `python serve.py` and `python worker.py`. Docker Compose starts both; the worker has no public port. `GET /healthz` is process liveness, while `/readyz` verifies PostgreSQL and migration 008.

Schedule `scripts/backup_db.ps1` daily. It writes a custom dump, `.sha256` sidecar, and removes expired pairs. Restore only to a uniquely named empty database with `scripts/restore_db.ps1`; it verifies checksum and refuses the production database. For rollback, stop writes, restore, validate counts and migration checksums, then switch the connection string.

Common failures: `PG_BIN_DIR must point to an existing directory`, `checksum mismatch`, `target unavailable or not empty`, and `production target refused`. Keep backup ACLs restricted and store encrypted off-host copies.
