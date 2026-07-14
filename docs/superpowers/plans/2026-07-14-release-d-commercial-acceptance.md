# Release D Commercial Prototype Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a reproducible Release D with PostgreSQL-backed background jobs, bounded health/readiness checks, commercial acceptance coverage, safe backup/restore tooling, and complete operating evidence.

**Architecture:** Flask validates and enqueues ADS/model work into `audit.background_job`; a separate allowlisted worker claims jobs with `FOR UPDATE SKIP LOCKED` and writes terminal results. PostgreSQL remains the only shared state, while CI, PowerShell operations scripts, and delivery documents prove the prototype without pgvector, Playwright, or an actual Render deployment.

**Tech Stack:** Python 3.12, Flask 3, SQLAlchemy 2, PostgreSQL 18, psycopg2, pytest, PowerShell, Docker Compose, GitHub Actions.

---

## Fixed Scope And File Map

- Do not add pgvector, Redis, external AI providers, Playwright, or a Render worker service.
- Keep knowledge ingestion and Gradio synchronous.
- Queue only ADS refresh and the six Flask model families.
- Use append-only migration `008_background_jobs.sql`; do not edit migrations 001-007.
- Keep production code boundaries focused:
  - `app/services/jobs.py`: validation and durable queue state only.
  - `worker.py`: job handler allowlist and worker loop only.
  - `app/routes/system.py`: liveness, readiness, and authorized job status.
  - `app/routes/algorithms.py` and `app/routes/reports.py`: thin enqueue adapters.
  - `app/static/js/job-status.js`: shared bounded polling behavior.
  - `scripts/backup_db.ps1` and `scripts/restore_db.ps1`: database operations only.
- If the environment cannot write `.git`, complete and verify the task, then have the user run the exact commit command shown for that task.

## Task 1: Durable PostgreSQL Queue Core

**Files:**
- Create: `database/migrations/008_background_jobs.sql`
- Create: `app/services/jobs.py`
- Create: `tests/conftest.py`
- Create: `tests/integration/test_job_worker.py`
- Delete: `tests/integration/conftest.py`
- Modify: `tests/integration/test_rbac_integration.py`
- Modify: `tests/integration/test_refund_workflow.py`

- [ ] **Step 1: Promote and extend the isolated database fixtures**

Move `tests/integration/conftest.py` to `tests/conftest.py` without changing the safe URL parsing, random database creation, backend termination, or drop cleanup. Rename generated databases to `consumer_release_d_<uuid>` and add initialized fixtures:

```python
@pytest.fixture
def initialized_database(isolated_database):
    from scripts.init_db import ROOT, apply_migrations

    with isolated_database.cursor() as cursor:
        for filename in ("v2_schema.sql", "v2_seed.sql", "demo_commerce_v2.sql"):
            cursor.execute((ROOT / "database" / filename).read_text(encoding="utf-8"))
    isolated_database.commit()
    apply_migrations(isolated_database)
    return isolated_database


@pytest.fixture
def initialized_app(initialized_database):
    from app import create_app
    from app.config import TestConfig

    initialized_database.rollback()
    with initialized_database.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        database_name = cursor.fetchone()[0]
    url = make_url(_test_database_url()).set(database=database_name)

    class IntegrationConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = url.render_as_string(hide_password=False)
        SECRET_KEY = "integration-test-secret"

    app = create_app(IntegrationConfig)
    yield app
    with app.app_context():
        from app.extensions import db
        db.session.remove()
```

Keep the existing `isolated_app` fixture for tests that intentionally control schema initialization themselves.

- [ ] **Step 2: Write failing queue lifecycle tests**

Add these focused tests to `tests/integration/test_job_worker.py` using `isolated_app` and the same schema/seed/migration initialization pattern as the other integration modules:

```python
def test_two_workers_never_claim_the_same_job(initialized_app):
    job_id = enqueue_for_test(initialized_app, "model_rfm", {})
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(
            lambda worker: claim_in_app(initialized_app, worker),
            ("worker-a", "worker-b"),
        ))
    claimed = [job for job in claims if job is not None]
    assert [job["job_id"] for job in claimed] == [job_id]
    assert claimed[0]["attempts"] == 1


def test_failure_retries_twice_then_preserves_dead_job(initialized_app):
    job_id = enqueue_for_test(initialized_app, "model_rfm", {})
    for attempt in (1, 2, 3):
        job = claim_in_app(initialized_app, f"worker-{attempt}")
        fail_in_app(initialized_app, job_id, f"worker-{attempt}", RuntimeError("boom"))
        state = load_in_app(initialized_app, job_id)
        assert state["attempts"] == attempt
        assert state["status"] == ("dead" if attempt == 3 else "queued")
        assert "RuntimeError" in state["last_error"]
        if attempt < 3:
            make_available_in_database(initialized_app, job_id)


def test_stale_running_job_is_recovered(initialized_app):
    job_id = enqueue_for_test(initialized_app, "model_rfm", {})
    claim_in_app(initialized_app, "worker-old")
    age_lock_in_database(initialized_app, job_id, minutes=16)
    recovered = claim_in_app(initialized_app, "worker-new")
    assert recovered["job_id"] == job_id
    assert recovered["locked_by"] == "worker-new"
    assert recovered["attempts"] == 2
```

Define `enqueue_for_test`, `claim_in_app`, `fail_in_app`, and `load_in_app` in this test module by entering `initialized_app.app_context()` and calling the matching public `JobService` method. Define `age_lock_in_database` and `make_available_in_database` in the same module; these two setup helpers may issue direct SQL to set `locked_at = now() - make_interval(mins => :minutes)` and `available_at = now()` respectively. Resolve the creating account with `SELECT account_id FROM auth.account WHERE username = 'admin'`; do not assume a numeric account ID.

- [ ] **Step 3: Run the tests and verify the RED state**

Run:

```powershell
$env:ALLOW_LOCAL_DB_TESTS = 'true'
.\.venv\Scripts\python.exe -m pytest tests\integration\test_job_worker.py -q --basetemp .pytest_cache\release-d-red
```

Expected: collection or import fails because `app.services.jobs` and migration 008 do not exist. A database connection failure is not an acceptable RED result.

- [ ] **Step 4: Add migration 008**

Create `database/migrations/008_background_jobs.sql` with this schema contract:

```sql
CREATE TABLE IF NOT EXISTS audit.background_job (
    job_id           BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    job_type         VARCHAR(64) NOT NULL,
    payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
    result           JSONB,
    status           VARCHAR(16) NOT NULL DEFAULT 'queued',
    attempts         SMALLINT NOT NULL DEFAULT 0,
    available_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by        VARCHAR(128),
    locked_at        TIMESTAMPTZ,
    created_by       BIGINT REFERENCES auth.account(account_id) ON DELETE SET NULL,
    permission_code  VARCHAR(100) NOT NULL,
    last_error       VARCHAR(2000),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_background_job_status
        CHECK (status IN ('queued', 'running', 'succeeded', 'dead')),
    CONSTRAINT ck_background_job_attempts CHECK (attempts BETWEEN 0 AND 3),
    CONSTRAINT ck_background_job_payload CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT ck_background_job_result
        CHECK (result IS NULL OR jsonb_typeof(result) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_background_job_available
    ON audit.background_job (available_at, job_id)
    WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS ix_background_job_creator
    ON audit.background_job (created_by, created_at DESC);
```

- [ ] **Step 5: Implement queue validation and state transitions**

Create `app/services/jobs.py` with a `JobError` exception, `JobService`, and this exact public contract:

```python
class JobService:
    @staticmethod
    def enqueue(job_type, payload, created_by):
        """Validate a registered job and return its integer job_id."""

    @staticmethod
    def claim_next(worker_id, stale_after_seconds=900):
        """Recover stale work, atomically claim one job, commit, and return a dict or None."""

    @staticmethod
    def complete(job_id, worker_id, result):
        """Mark the worker-owned running job succeeded and return the stored dict."""

    @staticmethod
    def fail(job_id, worker_id, error):
        """Requeue attempts one/two or preserve attempt three as dead."""

    @staticmethod
    def recover_stale(stale_after_seconds=900):
        """Requeue or kill abandoned running jobs and return the affected count."""

    @staticmethod
    def get(job_id):
        """Return one job dict or None without changing it."""
```

Define all accepted jobs and parameter bounds in one mapping:

```python
JOB_SPECS = {
    "analytics_refresh": {"permission": "analysis.run", "fields": {"snapshot_date": ("date", None, None)}},
    "model_rfm": {"permission": "model.run", "fields": {}},
    "model_kmeans": {"permission": "model.run", "fields": {"clusters": ("int", 2, 8)}},
    "model_churn": {"permission": "model.run", "fields": {"observation_days": ("int", 30, 180)}},
    "model_customer_amount": {"permission": "model.run", "fields": {
        "horizon_days": ("int", 1, 90), "training_days": ("int", 60, 730),
    }},
    "model_product_sales_forecast": {"permission": "model.run", "fields": {
        "horizon_days": ("int", 1, 90), "training_days": ("int", 28, 730),
    }},
    "model_product_recommendation": {"permission": "model.run", "fields": {
        "top_k": ("int", 1, 20), "training_days": ("int", 30, 730),
    }},
}
```

`enqueue()` must reject unknown, missing, and extra fields, inject `operator_id` from `created_by`, cap serialized payloads at 4096 bytes, and derive `permission_code` from `JOB_SPECS`. `claim_next()` must use one CTE-backed `UPDATE` whose candidate `SELECT` uses `FOR UPDATE SKIP LOCKED LIMIT 1`, then commit before returning. `complete()` and `fail()` must require matching `locked_by`. Redact URL passwords and `password=` values from error text before truncating it to 2000 characters.

- [ ] **Step 6: Update migration contracts**

Append `008_background_jobs.sql` to the expected migration sequence in `tests/integration/test_rbac_integration.py`. Change the exact expected migration count from seven to eight in `tests/integration/test_refund_workflow.py`. Do not change the expected names or checksums for migrations 001-007.

- [ ] **Step 7: Run queue and migration tests**

Run:

```powershell
$env:ALLOW_LOCAL_DB_TESTS = 'true'
.\.venv\Scripts\python.exe -m pytest tests\integration\test_job_worker.py tests\integration\test_rbac_integration.py tests\integration\test_refund_workflow.py -q --basetemp .pytest_cache\release-d-task1
```

Expected: all selected tests pass; no database test is skipped.

- [ ] **Step 8: Commit Task 1**

```powershell
& $git add database/migrations/008_background_jobs.sql app/services/jobs.py tests/conftest.py tests/integration/test_job_worker.py tests/integration/test_rbac_integration.py tests/integration/test_refund_workflow.py
& $git rm tests/integration/conftest.py
& $git commit -m 'feat: add durable PostgreSQL job queue'
```

## Task 2: Worker, HTTP Status, Readiness, And UI Polling

**Files:**
- Create: `worker.py`
- Create: `app/static/js/job-status.js`
- Modify: `app/routes/system.py`
- Modify: `app/routes/algorithms.py`
- Modify: `app/routes/reports.py`
- Modify: `app/templates/algorithms.html`
- Modify: `app/templates/reports/index.html`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `tests/integration/test_job_worker.py`
- Modify: `tests/test_bs_contract.py`
- Modify: `tests/test_deploy_contract.py`

- [ ] **Step 1: Write failing worker and route tests**

Add tests that prove handler persistence and HTTP boundaries:

```python
def test_worker_success_persists_model_task_id(initialized_app):
    job_id = enqueue_for_test(initialized_app, "model_rfm", {})
    with initialized_app.app_context():
        assert run_once("test-worker") is True
        job = JobService.get(job_id)
    assert job["status"] == "succeeded"
    assert isinstance(job["result"]["task_id"], int)


def test_job_status_requires_saved_permission(initialized_app):
    job_id = enqueue_for_test(initialized_app, "model_rfm", {})
    client = initialized_app.test_client()
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 302
    login_session(client, account_with_permissions(initialized_app, set()))
    assert client.get(f"/jobs/{job_id}").status_code == 403


def test_health_is_live_when_database_is_unavailable(initialized_app, monkeypatch):
    monkeypatch.setattr("app.routes.system.db.session.execute", Mock(side_effect=RuntimeError("down")))
    assert initialized_app.test_client().get("/healthz").status_code == 200
    assert initialized_app.test_client().get("/readyz").status_code == 503
```

Add source-contract assertions that the algorithms and reports POST routes call `JobService.enqueue`, both templates reference `job-status.js`, Docker copies `worker.py`, Compose declares a no-port `worker`, and the web health check uses `/readyz`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
$env:ALLOW_LOCAL_DB_TESTS = 'true'
.\.venv\Scripts\python.exe -m pytest tests\integration\test_job_worker.py tests\test_bs_contract.py tests\test_deploy_contract.py -q --basetemp .pytest_cache\release-d-task2-red
```

Expected: failures identify missing `worker.py`, `/readyz`, job status, enqueue routes, polling asset, and Compose worker.

- [ ] **Step 3: Implement the allowlisted worker**

Create `worker.py` with `HANDLERS`, `run_once(worker_id)`, and `main()`. The handlers must call exactly:

```python
HANDLERS = {
    "analytics_refresh": lambda payload: AnalyticsService.refresh(payload["snapshot_date"], payload["operator_id"]),
    "model_rfm": lambda payload: run_rfm(payload["operator_id"]),
    "model_kmeans": lambda payload: run_kmeans(payload["operator_id"], payload["clusters"]),
    "model_churn": lambda payload: run_churn(payload["operator_id"], payload["observation_days"]),
    "model_customer_amount": lambda payload: PredictionService.run_customer_amount(
        payload["operator_id"], payload["horizon_days"], payload["training_days"]
    ),
    "model_product_sales_forecast": lambda payload: PredictionService.run_product_sales_forecast(
        payload["operator_id"], payload["horizon_days"], payload["training_days"]
    ),
    "model_product_recommendation": lambda payload: PredictionService.run_product_recommendation(
        payload["operator_id"], payload["top_k"], payload["training_days"]
    ),
}
```

`run_once()` uses the active application context, claims one job, invokes only this mapping, completes with `{"task_id": task_id}`, calls `fail()` on exceptions, removes `db.session` in `finally`, and returns whether a job was claimed. `main()` supports `--once`, `--poll-seconds` (default 2), and `--worker-id`; it creates one Flask app/context for the process and installs SIGINT/SIGTERM handlers that stop the bounded loop.

- [ ] **Step 4: Split liveness and readiness and add job status**

Change `/healthz` to return `{"status": "ok"}` without touching `db`. Add `/readyz` with `SET LOCAL statement_timeout = '1000ms'`, `SELECT 1`, and an exact migration check for `008_background_jobs.sql`.

Add `GET /jobs/<int:job_id>` to `app/routes/system.py`. Redirect anonymous users to login, return 404 for missing jobs, compare the saved `permission_code` with `account_permissions(session["user_id"])`, and return 403 on mismatch. JSON must contain only `job_id`, `job_type`, `status`, `attempts`, `created_at`, `started_at`, `finished_at`, `result`, and `last_error` when status is dead.

- [ ] **Step 5: Convert Flask ADS/model actions to enqueue**

Map the six algorithm task types to the seven registered queue fields, use current form defaults, and call `JobService.enqueue(queue_type, payload, session["user_id"])`. Redirect to `algorithms.index(job_id=job_id)` without calling the model service in the request.

Add `POST /reports/refresh` protected by `analysis.run`. Validate `snapshot_date` with `date.fromisoformat`, enqueue `analytics_refresh`, and redirect to `reports.index(job_id=job_id)`. Pass `job_id` to both templates.

- [ ] **Step 6: Add bounded shared polling**

Create `app/static/js/job-status.js`. It must find one `[data-job-monitor]`, fetch its same-origin `data-status-url`, retry every two seconds only for `queued` or `running`, and stop after 150 polls. On `succeeded`, replace `{task_id}` in `data-success-url` and navigate. On `dead`, render the server's bounded error. Network errors retry without resizing the status container.

Add a stable-height status band to algorithms and reports only when `job_id` is present. Use existing buttons/icons and include the asset through the templates' script blocks. Do not add explanatory marketing copy or alter unrelated layouts.

- [ ] **Step 7: Add the local worker deployment process**

Copy `worker.py` in the Dockerfile. Add a Compose `worker` service using the same build and database environment as web, no ports, command `python worker.py`, and dependency on healthy web. Change the web health check to `/readyz`. Do not add a Render worker service or perform a Render action.

- [ ] **Step 8: Run focused and regression tests**

```powershell
$env:ALLOW_LOCAL_DB_TESTS = 'true'
.\.venv\Scripts\python.exe -m pytest tests\integration\test_job_worker.py tests\integration\test_analytics_refresh.py tests\integration\test_prediction_persistence.py tests\test_bs_contract.py tests\test_deploy_contract.py tests\test_authorization.py -q --basetemp .pytest_cache\release-d-task2
```

Expected: all selected tests pass and database tests are not skipped.

- [ ] **Step 9: Commit Task 2**

```powershell
& $git add worker.py app/routes/system.py app/routes/algorithms.py app/routes/reports.py app/templates/algorithms.html app/templates/reports/index.html app/static/js/job-status.js Dockerfile docker-compose.yml tests/integration/test_job_worker.py tests/test_bs_contract.py tests/test_deploy_contract.py
& $git commit -m 'feat: run analytics through background worker'
```

## Task 3: CI, Critical Flows, Security, And Performance Budgets

**Files:**
- Create: `tests/e2e/test_critical_flows.py`
- Create: `tests/security/test_upload_and_access.py`
- Create: `tests/performance/test_query_budgets.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_deploy_contract.py`

- [ ] **Step 1: Write critical HTTP acceptance tests**

Create `tests/e2e/test_critical_flows.py` with these database-backed flows:

```python
def test_real_login_and_permission_denial(initialized_app):
    set_test_password(initialized_app, "analyst", "AnalystPass123")
    client = initialized_app.test_client()
    assert client.post("/login", data={"username": "analyst", "password": "AnalystPass123"}).status_code == 302
    assert client.get("/admin/users").status_code == 403


def test_order_payment_refund_inventory_http_loop(initialized_app):
    client = logged_in_admin(initialized_app)
    product_id, stock_before, customer_id = commerce_fixture(initialized_app)
    client.post("/orders", data={"customer_id": customer_id, "product_id": [product_id], "quantity": ["2"]})
    order_id = latest_order_id(initialized_app)
    client.post("/payments", data={"order_id": order_id, "method": "card"})
    payment_id, item_id = paid_order_facts(initialized_app, order_id)
    client.post("/refunds", data={"payment_id": payment_id, "order_item_id": [item_id], f"quantity_{item_id}": "1", "reason": "acceptance"})
    refund_id = latest_refund_id(initialized_app)
    client.post(f"/refunds/{refund_id}/approve", data={"review_note": "accepted"})
    assert_commerce_facts(initialized_app, order_id, refund_id, product_id, stock_before - 1)


def test_queued_model_and_lightweight_qa_http_loops(initialized_app):
    client = logged_in_admin(initialized_app)
    model_response = client.post("/algorithms/run/rfm")
    job_id = job_id_from_redirect(model_response)
    run_worker_until_terminal(initialized_app, job_id)
    assert client.get(f"/jobs/{job_id}").get_json()["status"] == "succeeded"
    document_id = upload_and_publish_document(client, "Returns are accepted in seven days.")
    answer = client.post("/qa/ask", data={"question": "When are returns accepted?"})
    assert answer.status_code == 302
    assert_answer_has_published_citation(initialized_app, document_id)
```

Helpers must query exact database facts; they may not assert only flash text or HTTP status.

- [ ] **Step 2: Write security characterization tests**

Create `tests/security/test_upload_and_access.py` covering:

- CSRF-enabled POST without a token returns 400;
- `next=https://example.com` and `next=//example.com` redirect to the local dashboard;
- a disabled account receives 403 at real login;
- SQL Lab rejects INSERT, UPDATE, DELETE, and `SELECT 1; SELECT 2`;
- uploads reject `../name.txt`, `.exe`, executable bytes renamed `.txt`, invalid DOCX bytes, and `MAX_UPLOAD_MB + 1` bytes;
- job status returns 302/403/200 for anonymous, unauthorized, and authorized accounts;
- every returned QA citation references a published stored chunk.

These are characterization tests for existing controls. They may start GREEN; if one fails, fix the smallest production defect and rerun the exact test before proceeding.

- [ ] **Step 3: Write deterministic performance smoke budgets**

Create `tests/performance/test_query_budgets.py`. Warm each operation once, then time three runs with `time.perf_counter()`. Assert the maximum report/list request is under 2.0 seconds and the maximum local keyword QA request is under 2.0 seconds. Configure SQL Lab with `SQL_QUERY_MAX_ROWS=20` and `SQL_QUERY_TIMEOUT_MS=200`; assert `SELECT * FROM generate_series(1, 100)` returns 20 rows with `truncated=true`, and `SELECT pg_sleep(1)` fails in under 1.5 seconds.

- [ ] **Step 4: Verify acceptance RED/GREEN behavior**

Run the new suites before changing CI:

```powershell
$env:ALLOW_LOCAL_DB_TESTS = 'true'
.\.venv\Scripts\python.exe -m pytest tests\e2e tests\security tests\performance -q --basetemp .pytest_cache\release-d-acceptance
```

Expected: missing queue/fixture behavior fails initially; after Tasks 1-2 and the shared fixture are complete, all new tests pass. Any test skipped for a missing database is a failure of this gate.

- [ ] **Step 5: Align CI with the accepted runtime**

Change the service image from `postgres:16` to `postgres:18`, add `timeout-minutes: 15` to the test job, and keep one Python 3.12 job with one `pytest -q` command. Do not install pgvector, browsers, coverage services, or a build matrix. Update the deployment contract test to assert PostgreSQL 18 and the timeout.

- [ ] **Step 6: Run the complete acceptance slice**

```powershell
$env:ALLOW_LOCAL_DB_TESTS = 'true'
.\.venv\Scripts\python.exe -m pytest tests\e2e tests\security tests\performance tests\integration -q --basetemp .pytest_cache\release-d-task3
```

Expected: all selected tests pass with only the explicitly documented legacy `TEST_DATABASE_URL` test outside this selection.

- [ ] **Step 7: Commit Task 3**

```powershell
& $git add tests/e2e tests/security tests/performance .github/workflows/ci.yml tests/test_deploy_contract.py
& $git commit -m 'test: add commercial acceptance gates'
```

## Task 4: Safe Backup And Restore Tooling

**Files:**
- Create: `scripts/backup_db.ps1`
- Create: `scripts/restore_db.ps1`
- Create: `tests/test_backup_restore_contract.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing operations contract tests**

Create `tests/test_backup_restore_contract.py` and assert both scripts exist. The backup source must contain `pg_dump`, `--format=custom`, `.partial`, `Get-FileHash`, `BACKUP_RETENTION_DAYS`, and nonrecursive `Get-ChildItem`. The restore source must contain `pg_restore`, `--list`, `--single-transaction`, `--exit-on-error`, `--no-owner`, `--no-privileges`, a production-name comparison, and a query that rejects user tables. Assert neither source contains `--clean`, `--create`, `DROP DATABASE`, or recursive deletion.

Also invoke each script with a deliberately nonexistent `PG_BIN_DIR` when PowerShell is available and assert a nonzero exit plus a message naming `PG_BIN_DIR`.

- [ ] **Step 2: Run contract tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_backup_restore_contract.py -q
```

Expected: failure because both scripts are missing.

- [ ] **Step 3: Implement `backup_db.ps1`**

Use parameters `DatabaseUrl`, `EnvFile`, `PgBinDir`, `BackupDir`, and `RetentionDays`. Defaults come from `DATABASE_URL` or DB component variables, `.env`, `PG_BIN_DIR`, `BACKUP_DIR`, and `BACKUP_RETENTION_DAYS`. Normalize `postgresql+psycopg2://` before parsing. Put the password only in the child process environment (`PGPASSWORD`), never in printed text or command arguments.

Resolve the backup directory, create `<database>-yyyyMMdd-HHmmss.dump.partial`, run `pg_dump --format=custom --file=<partial> <database>`, rename only after exit code zero, write `<dump>.sha256`, and prune only files matching the generated `<database>-*.dump` and checksum names whose direct parent equals the resolved backup directory.

- [ ] **Step 4: Implement `restore_db.ps1`**

Require `-BackupFile` and `-TargetDatabase`; accept the same connection/tool settings. Verify the adjacent SHA-256 file and `pg_restore --list`. Refuse a target equal to the database parsed from the configured URL or `PRODUCTION_DB_NAME`. Use `psql` to assert that the explicit target exists and has zero user tables. Restore only with:

```text
pg_restore --exit-on-error --single-transaction --no-owner --no-privileges --dbname <target> <dump>
```

Never create, drop, clean, or overwrite a database. Restore the caller's original `PGPASSWORD` value in `finally` in both scripts.

- [ ] **Step 5: Update safe environment examples**

Leave `SECRET_KEY`, account passwords, and `DB_PASSWORD` empty in `.env.example`. Add:

```dotenv
PG_BIN_DIR=
BACKUP_DIR=.cache/backups
BACKUP_RETENTION_DAYS=30
PRODUCTION_DB_NAME=consumer_analysis
```

- [ ] **Step 6: Run operations tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_backup_restore_contract.py tests\test_deploy_contract.py -q
```

Expected: all contract tests pass and no secret value appears in output.

- [ ] **Step 7: Commit Task 4**

```powershell
& $git add scripts/backup_db.ps1 scripts/restore_db.ps1 tests/test_backup_restore_contract.py .env.example
& $git commit -m 'feat: add safe database backup and restore'
```

## Task 5: Delivery Documentation, Rehearsal, And Final Gate

**Files:**
- Create: `docs/deployment.md`
- Create: `docs/user-guide.md`
- Create: `docs/acceptance-checklist.md`
- Create: `docs/data-retention.md`
- Create: `tests/test_release_d_docs.py`
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-07-13-commercial-system-upgrade.md`

- [ ] **Step 1: Write failing documentation contracts**

Create `tests/test_release_d_docs.py`. Assert all four documents exist and contain the exact operating targets `RPO 24 hours`, `RTO 4 hours`, `99.5%`, `30 days`, `180 days`, and `quarterly`. Assert deployment documents name `worker.py`, `/healthz`, `/readyz`, migration 008, backup, restore, rollback, and `PG_BIN_DIR`. Assert the user guide covers roles, order/payment/refund, knowledge publishing, citations/tickets, reports, and all six model families. Assert README links every document and does not claim a Render worker was deployed.

- [ ] **Step 2: Run documentation tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_release_d_docs.py -q
```

Expected: failure because the delivery documents do not exist.

- [ ] **Step 3: Write concise operator and user documentation**

Write:

- `docs/deployment.md`: prerequisites, `.env`, initialization/migrations, local web and worker commands, Docker Compose, liveness/readiness, daily backup scheduling, restore-to-new-database rollback, and concrete common failure messages.
- `docs/user-guide.md`: login/role assignment, commerce workflow, knowledge upload/publish, QA citations/feedback/tickets, reports/export/ADS refresh, and interpretation of RFM, KMeans, churn, amount, sales, and recommendation results.
- `docs/data-retention.md`: RPO 24 hours, RTO 4 hours, 99.5% monthly objective, 30-day application logs/backups, 180-day audit logs, permanent migration history, quarterly restore drills, restricted backup ACLs, and encrypted off-host copies.
- `docs/acceptance-checklist.md`: commit/date/environment/executor fields and evidence rows for the full test command, four HTTP loops, queue concurrency/retry, security, performance, backup checksum, restored row counts, and migration checksum equality. Leave evidence unchecked until the corresponding command runs.

Keep README as the quick-start index and link to the detailed documents instead of duplicating them.

- [ ] **Step 4: Run the full PostgreSQL suite**

```powershell
$env:ALLOW_LOCAL_DB_TESTS = 'true'
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest_cache\release-d-full
```

Expected: every Release D test passes. The only allowed skip is the pre-existing legacy demo test that explicitly requires `TEST_DATABASE_URL`; record its name and reason in the checklist.

- [ ] **Step 5: Rehearse backup and isolated restore**

Resolve PostgreSQL client tools from `PG_BIN_DIR` or an installed PostgreSQL 18 `bin` directory. Run `scripts/backup_db.ps1`, create a uniquely named empty database such as `consumer_release_d_restore_<timestamp>`, and run `scripts/restore_db.ps1 -TargetDatabase <name>`. Compare selected row counts for `auth.account`, `biz.sales_order`, `dwd.consumption_flow`, `ml.model_task`, and `audit.background_job`, then compare every `audit.schema_migration(version, checksum)` row. Drop only the uniquely named rehearsal database after recording evidence.

If tools are genuinely unavailable, do not mark this gate complete: record the exact missing executable and keep the checklist item open.

- [ ] **Step 6: Run final static and secret checks**

```powershell
.\.venv\Scripts\python.exe -m py_compile app\services\jobs.py app\routes\system.py app\routes\algorithms.py app\routes\reports.py worker.py
& $git diff --check
& $git grep -n -E 'postgres(ql)?://[^:[:space:]]+:[^@[:space:]]+@|SECRET_KEY=[^[:space:]]+|DB_PASSWORD=[^[:space:]]+'
```

Expected: compilation and diff checks exit zero. The secret scan has no real credential matches; empty example values and test-only local `postgres:postgres` fixtures must be reviewed and documented rather than silently ignored.

- [ ] **Step 7: Update release evidence and run docs tests**

After verification, fill only evidence actually observed in `docs/acceptance-checklist.md`. Add a Release D execution record to `docs/superpowers/plans/2026-07-13-commercial-system-upgrade.md` with test counts, backup filename/checksum, restore database, migration 008, and any explicit residual limitation. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_release_d_docs.py tests\test_backup_restore_contract.py tests\test_deploy_contract.py -q
```

Expected: all documentation and deployment contracts pass.

- [ ] **Step 8: Commit Release D evidence**

```powershell
& $git add README.md docs/deployment.md docs/user-guide.md docs/acceptance-checklist.md docs/data-retention.md docs/superpowers/plans/2026-07-13-commercial-system-upgrade.md tests/test_release_d_docs.py
& $git commit -m 'docs: add Release D operations and acceptance evidence'
```

Release D execution record: Tasks 1-3 completed with 51 tests; Task 4 contract tests 10 passed. Backup/restore rehearsal remains open until PostgreSQL client tools and an isolated database are available.

## Final Review Checklist

- [ ] Every approved design section maps to at least one task above.
- [ ] Migration 008 is append-only and migrations 001-007 are unchanged.
- [ ] Knowledge ingestion and Gradio remain synchronous.
- [ ] No pgvector, Redis, external provider, Playwright, or paid Render worker appears.
- [ ] HTTP actions return a job identifier before model/ADS execution.
- [ ] Claims commit before handler execution; retries and stale jobs terminate predictably.
- [ ] Job status enforces the permission stored with the job.
- [ ] CI and Compose use PostgreSQL 18 and readiness semantics.
- [ ] Restore refuses production and nonempty targets.
- [ ] Full tests, backup/restore evidence, migration checksums, static checks, and secret review are recorded before completion is claimed.
