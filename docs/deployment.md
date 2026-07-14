# Deployment

Requirements: Python 3.12, PostgreSQL 18, and `PG_BIN_DIR` containing `pg_dump.exe`, `pg_restore.exe`, and `psql.exe`. Copy `.env.example` to `.env`, set `DATABASE_URL` and secrets, then run `python scripts/init_db.py` (migrations 008 and 009 are required).

Run web and worker locally with `python serve.py` and `python worker.py`. Docker Compose starts both; the worker has no public port. `GET /healthz` is process liveness, while `/readyz` verifies PostgreSQL and migration 008.

Schedule `scripts/backup_db.ps1` daily. It writes a custom dump, `.sha256` sidecar, and removes expired pairs. Restore only to a uniquely named empty database with `scripts/restore_db.ps1`; it verifies checksum and refuses the production database. For rollback, stop writes, restore, validate counts and migration checksums, then switch the connection string.

Common failures: `PG_BIN_DIR must point to an existing directory`, `checksum mismatch`, `target unavailable or not empty`, and `production target refused`. Keep backup ACLs restricted and store encrypted off-host copies.

## Lightweight QA agent

The QA agent is optional and requires no database migration, vector database, Redis, or worker. Configure `QA_AGENT_ENABLED=true`, `AI_BASE_URL`, `AI_API_KEY`, and `AI_MODEL` on the Web service. Requests time out after `AI_TIMEOUT_SECONDS`; any provider failure automatically falls back to the built-in keyword QA. Set `QA_AGENT_ENABLED=false` to disable the external model without redeploying code.

Render rollout:

1. In the `analyse-cc` Web Service, add `AI_BASE_URL`, `AI_API_KEY`, and `AI_MODEL` under Environment. The endpoint must support OpenAI-compatible `POST /chat/completions` tool calls.
2. Keep `AI_TIMEOUT_SECONDS=8`, `AI_MAX_TOOL_CALLS=2`, and `AI_MAX_RESPONSE_CHARS=800`; then set `QA_AGENT_ENABLED=true`.
3. Deploy the latest commit. No database migration or worker restart is required.
4. Sign in with a `qa_operator` account and verify one knowledge question, one known order/refund number, one unknown number, and “转人工客服”.
5. To roll back immediately, set `QA_AGENT_ENABLED=false`. Keyword retrieval and human tickets remain available, and `/healthz` does not depend on the model provider.

This release is for authenticated internal operators. It does not bind a login account to a retail customer, so do not expose order lookup as a public customer-facing chat until customer identity verification is added.
