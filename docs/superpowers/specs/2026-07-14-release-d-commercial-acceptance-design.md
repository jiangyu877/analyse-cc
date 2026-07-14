# Release D Commercial Prototype Acceptance Design

**Date:** 2026-07-14  
**Status:** Approved for implementation  
**Depends on:** Release C commit `634d5ba`

## Goal

Complete the commercial-prototype acceptance layer around the existing Flask and PostgreSQL application. Release D must provide durable background execution, reproducible acceptance checks, safe database backup and restore, and operator-facing delivery documentation without reintroducing pgvector, external AI providers, or an actual Render deployment.

## Scope Decisions

Release D uses an acceptance-focused implementation:

- Queue ADS refresh and the six model task families through PostgreSQL.
- Keep lightweight TXT, Markdown, and DOCX knowledge ingestion synchronous and bounded by the existing 8 MB upload limit.
- Keep the internal Gradio workbench synchronous.
- Use PostgreSQL 18 in Docker Compose and CI, but do not add pgvector.
- Add a Docker Compose worker and document a generic worker command, but do not create, enable, or deploy a paid Render worker.
- Reuse existing integration coverage instead of duplicating every service-level test in browser automation.
- Use Flask's test client for HTTP acceptance flows; do not add Playwright or a browser runtime.
- Keep large 5,000-customer and 50,000-transaction rehearsal as a documented release check rather than importing that dataset on every pull request.

## Architecture

Flask remains the only public entry point. A permitted HTTP action validates its parameters, inserts an allowlisted job into PostgreSQL, and immediately redirects to a page carrying the job identifier. The page polls a same-origin, permission-protected status endpoint. A separate Python worker atomically claims one available job with `FOR UPDATE SKIP LOCKED`, commits the claim before executing it, invokes a fixed handler, and persists success or retry state in a separate transaction.

PostgreSQL remains the source of truth for queue state and model results. No Redis, message broker, object store, vector extension, or provider API is introduced.

## Background Job Data Model

Append-only migration `database/migrations/008_background_jobs.sql` creates `audit.background_job`. The number is `008` because Release C already applied `007_model_registry_and_results.sql`; existing migration files must not be renamed or edited.

Each job stores:

- identity: `job_id`, `job_type`, `payload`, and optional `result`;
- authorization: `created_by` and the permission code required to inspect the job;
- lifecycle: `queued`, `running`, `succeeded`, or `dead`;
- retry state: `attempts`, `available_at`, and `last_error`;
- ownership: `locked_by` and `locked_at`;
- timestamps: created, started, finished, and updated times.

The migration constrains status and attempts, references the creating account, and adds a partial `(available_at, job_id)` index for queued work. Attempts increment when a worker claims a job. Failed attempts one and two return to `queued` with short bounded backoff; the third failure becomes `dead`. A running job whose lock is older than 15 minutes is recovered before the next claim: it is requeued when attempts remain and marked dead after the third attempt.

## Job Service And Worker

`app/services/jobs.py` owns the queue contract:

- `enqueue()` accepts only registered job types and JSON-safe payloads;
- `claim_next()` uses `FOR UPDATE SKIP LOCKED` and commits ownership before returning;
- `complete()` stores a bounded JSON result and clears the lock;
- `fail()` stores a redacted exception type/message, retries when allowed, and preserves dead jobs;
- `recover_stale()` handles abandoned running jobs;
- `get()` returns one job for the protected status endpoint.

`worker.py` contains a fixed handler map for:

- `analytics_refresh`;
- `model_rfm`;
- `model_kmeans`;
- `model_churn`;
- `model_customer_amount`;
- `model_product_sales_forecast`;
- `model_product_recommendation`.

Payloads contain only documented scalar parameters and the requesting account identifier. They can never name a Python module, callable, SQL statement, or filesystem path. Successful handlers return `{"task_id": <id>}`. The worker removes the SQLAlchemy session after every attempt, emits structured job identifiers and elapsed time to stdout, polls with a bounded idle interval, and exits cleanly on termination.

## HTTP And UI Flow

The algorithm POST route validates the same existing form limits, enqueues the corresponding model job, flashes the queue identifier, and redirects immediately. The reports page gains an `analysis.run`-protected ADS refresh POST using the same flow.

`GET /jobs/<job_id>` requires an authenticated account holding the permission saved with the job. It returns only the identifier, type, status, attempt count, timestamps, bounded result, and a safe dead-job error summary. The algorithm and reports pages poll only when a job identifier is present, stop on terminal state, and redirect to the task-scoped result when `task_id` becomes available. Normal navigation remains functional without JavaScript.

## Health And Readiness

`GET /healthz` is process liveness and always returns HTTP 200 while Flask can serve requests; it performs no database work.

`GET /readyz` runs a database query with a one-second statement timeout and verifies that `008_background_jobs.sql` is recorded in `audit.schema_migration`. It returns HTTP 200 only when the database and migration level are ready. Errors return a generic HTTP 503 response without SQL or credentials.

Docker Compose uses `/readyz` for the web health check and starts a no-port worker from the same image after the initialized web service is ready. The Docker image explicitly includes `worker.py`. Render is not operated in Release D and no paid worker service is added to the blueprint.

## Commercial Acceptance Coverage

CI uses Python 3.12, PostgreSQL 18, one test job, and one full `pytest -q` invocation with a 15-minute timeout. Database tests continue to use randomly named, function-scoped isolated databases. The shared fixture moves to `tests/conftest.py` so integration, E2E, security, and performance directories use the same safe database lifecycle.

Acceptance coverage adds only gaps not already proven:

- critical HTTP flows: real login and denial, commerce state transitions, queued analytics/model completion, and lightweight document-to-answer/citation/ticket behavior;
- security: CSRF rejection, external redirect rejection, disabled-account login, SQL Lab write/multi-statement rejection, upload path/extension/content/size validation, and job-status authorization;
- performance smoke budgets after warm-up: indexed list/report queries under two seconds, lightweight keyword QA under two seconds, and enforcement of the configured SQL timeout and row limit;
- queue behavior: mutually exclusive concurrent claims, committed claims, retry/dead transitions, stale recovery, permission checks, and persisted task identifiers.

Prompt-injection and provider-latency tests are excluded because the approved QA implementation has no model prompt or external provider. Server-generated citations must still reference published stored chunks. The 50,000-transaction rehearsal remains in the acceptance checklist for release candidates rather than every CI run.

## Backup And Restore

`scripts/backup_db.ps1` resolves PostgreSQL tools from `PG_BIN_DIR` or `PATH`, reads database settings without printing credentials, creates a timestamped custom-format dump through a `.partial` file, writes a SHA-256 checksum, exits nonzero on tool failure, and prunes only matching expired files directly inside the configured backup directory.

`scripts/restore_db.ps1` requires an explicit dump and target database. It verifies the checksum and `pg_restore --list`, refuses the database named by the configured connection or `PRODUCTION_DB_NAME`, confirms that the target contains no user tables, and restores with `--exit-on-error`, `--single-transaction`, `--no-owner`, and `--no-privileges`. It never creates, drops, cleans, or overwrites a database.

The scripts fail fast with a clear `PG_BIN_DIR` instruction when PostgreSQL client tools are unavailable. Backup files contain sensitive business and account data; documentation requires restricted filesystem permissions and an encrypted off-host copy.

## Documentation And Operating Targets

Release D adds:

- `docs/deployment.md`: local and Docker setup, environment variables, initialization, migrations, web/worker commands, health/readiness, rollback, and common failures;
- `docs/user-guide.md`: accounts and roles, commerce workflow, knowledge publishing, QA tickets, reports, and six model result families;
- `docs/acceptance-checklist.md`: commit/environment metadata and evidence for tests, four business loops, security, performance, backup/restore, row counts, and migration checksums;
- `docs/data-retention.md`: prototype operating targets and data ownership;
- README links and concise Release D commands;
- `.env.example` settings for PostgreSQL tools, backup directory, retention, and production database protection, with secrets left empty.

Prototype targets are RPO 24 hours, RTO 4 hours, 99.5% monthly availability objective, 30-day application logs, at least 180-day audit logs, 30-day backups, permanently retained migration history, and quarterly restore drills. These are prototype objectives, not a guaranteed SLA; stronger targets require paid managed database and multi-instance infrastructure.

## Error Handling And Security Boundaries

- Queue handlers are an allowlist and never execute user-supplied code or SQL.
- Job payloads and status responses are size-bounded.
- Database claims and job execution use separate transactions.
- Dead jobs remain inspectable; raw tracebacks, credentials, and connection strings never reach HTTP responses.
- Readiness uses a bounded query and generic failure response.
- Restore refuses production and nonempty targets before invoking `pg_restore`.
- Backup retention deletion is nonrecursive and restricted to generated filename patterns in the resolved backup directory.
- Existing RBAC, CSRF, upload, and SQL Lab protections remain authoritative and gain acceptance tests rather than duplicate production implementations.

## File Boundaries

Primary new files:

- `database/migrations/008_background_jobs.sql`
- `app/services/jobs.py`
- `worker.py`
- `tests/integration/test_job_worker.py`
- `tests/e2e/test_critical_flows.py`
- `tests/security/test_upload_and_access.py`
- `tests/performance/test_query_budgets.py`
- `scripts/backup_db.ps1`
- `scripts/restore_db.ps1`
- four Release D documents under `docs/`

Existing route, template, Docker, CI, configuration, migration-contract, README, and test-fixture files receive focused changes only. No Release A-C migration or unrelated UI component is rewritten.

## Release Gate

Release D is complete only when fresh evidence shows:

1. the full PostgreSQL-backed suite passes with only explicitly documented legacy skips;
2. queue concurrency, retries, stale recovery, and HTTP permissions pass;
3. CI and Docker descriptors validate PostgreSQL 18, web readiness, and the worker process;
4. backup and restore succeed against an isolated empty database, with matching selected row counts and migration checksums;
5. security and performance smoke budgets pass;
6. `git diff --check`, Python compilation, secret scans, and the acceptance checklist are clean;
7. no Render deployment or paid service creation is performed.
