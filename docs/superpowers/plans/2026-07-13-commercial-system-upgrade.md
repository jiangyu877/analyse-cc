# Consumer Analysis Commercial System Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the current Flask/PostgreSQL retail prototype into a document-aligned system with consistent V2 data contracts, resource-level RBAC, complete transaction controls, traceable analytics and prediction, knowledge-base RAG, human support tickets, and production acceptance evidence.

**Architecture:** Flask remains the only public web entry and owns authentication, authorization, business pages, knowledge management, customer-service pages, and audit boundaries. PostgreSQL remains the source of truth; versioned SQL migrations extend the current V2 schema, while Gradio is reduced to an internal analysis workbench that calls the same service layer instead of querying legacy tables. Long-running model and vector jobs run through a PostgreSQL-backed worker so HTTP requests stay bounded without introducing Redis.

**Tech Stack:** Python 3.12, Flask 3, SQLAlchemy 2, PostgreSQL 18, pgvector, psycopg2, scikit-learn, pandas, Gradio 5, Waitress, pytest, Playwright, Docker Compose, Render.

---

## Execution Record (2026-07-13)

**Release A status:** Complete. Tasks 1-3 meet their behavioral exit gates.

**Release B lightweight status:** Complete. At the user's direction, pgvector and external AI providers were removed from this release. Tasks 4-5 are delivered as a simple PostgreSQL keyword QA system with cited source chunks, feedback, low-match fallback, and human tickets.

**Verification evidence:**

- Full suite with real temporary PostgreSQL databases: `83 passed in 38.59s`.
- Local database: migrations 001-003 applied; a second initialization applied no migrations.
- Local database facts: 10 roles, 28 permissions, 2 refund/order composite foreign keys, and 0 fabricated demo refund items.
- Runtime smoke: administrator login, all 11 protected business/administration pages, `/healthz`, and Gradio returned HTTP 200.
- Independent final review: no remaining Critical or Important findings.
- Backups: `.cache/release-a-pre-migration.dump` and `.cache/release-a-pre-003.dump`.

**Review-driven improvements beyond the original Task 1-3 wording:**

- Added append-only `003_release_a_hardening.sql` instead of editing already-recorded migrations 001/002.
- Serialized migration runners with a PostgreSQL advisory lock and rejected missing, renamed, checksum-changed, and out-of-order migration files.
- Made migration 003 require a no-traffic maintenance window with `ACCESS EXCLUSIVE NOWAIT`; lock conflicts roll back and are safe to retry.
- Reconciled any migration-002 fabricated refund inventory return with a traceable negative manual-adjustment log before deleting the fabricated item mapping.
- Enforced refund-to-order-item ownership with composite foreign keys and repeated quantity, ownership, and amount validation at approval time.
- Fixed both Flask and Gradio KMeans/churn result views to read the RFM snapshot used by the selected task.

**Repository state:** Release A/B was committed as `b580ad7`. Release C changes remain uncommitted on the explicitly authorized current `main` worktree; no source change was reverted or discarded.

**Release B lightweight evidence:**

- Migrations 004-005 add knowledge bases, documents, chunks, keyword indexes, QA sessions/messages, retrieval evidence, feedback, and tickets.
- Supported local document formats are TXT, Markdown, and DOCX; files are stored outside static assets with generated names.
- Retrieval uses a GIN-indexed Chinese bigram/trigram and English keyword array. Answers always reference stored chunks; insufficient matches create one human ticket.
- Resolving a ticket writes the human reply back into the original QA conversation.
- Full suite with real temporary PostgreSQL databases: `89 passed in 37.06s`.
- Runtime smoke: `/knowledge`, `/qa`, `/qa/tickets`, `/healthz`, and Gradio returned HTTP 200.
- Backup before applying migrations 004-005: `.cache/release-b-pre-migration.dump`.

**Release C status:** Complete. Tasks 6-8 meet their behavioral exit gates.

**Release C evidence:**

- Append-only migrations `006_ads_results.sql` and `007_model_registry_and_results.sql` were applied locally; a second initialization applied no migrations.
- ADS refresh task `49` produced the current traceable dashboard snapshot, with daily, product, category, customer-profile, and RFM results linked to the refresh task.
- RFM, KMeans, churn, 30-day customer amount, product sales forecast, and product recommendation runs are model-versioned and task-scoped.
- The home page is a permission-shaped operational workspace with finance, model, knowledge, QA, and administration actions plus a compact mobile layout.
- Full suite with real temporary PostgreSQL databases: `101 passed, 1 skipped in 38.94s`; the skipped legacy demo test requires an explicit `TEST_DATABASE_URL` instead of the local-test opt-in.
- Runtime smoke: `/healthz`, `/login`, and Gradio returned HTTP 200; application URL registration includes reports and all six model families.
- Local browser screenshot automation was blocked by browser policy for `127.0.0.1`; responsive behavior is covered by focused template and role-workspace contracts.

---

## Priority And Dependency Order

| Order | Priority | Milestone | Depends on | Exit gate |
|---:|---|---|---|---|
| 1 | P0 | Freeze the V2 runtime contract | None | No registered module or worker references legacy `users`, `roles`, `spending_record`, or `budgets` tables |
| 2 | P0 | Add migrations and resource-level RBAC | 1 | Ten documented roles are seeded and route permissions are tested |
| 3 | P0 | Complete transaction and refund controls | 2 | Refund approval, item quantities, inventory return, and audit records are atomic |
| 4 | P0 | Deliver knowledge-base and RAG support | 2 | Answers cite stored chunks and low-confidence questions create tickets |
| 5 | P1 | Complete analytics result tables | 1, 3 | Dashboard and reports read traceable ADS results with reproducible refresh jobs |
| 6 | P1 | Complete model registry and prediction persistence | 1, 5 | Every model result has a task, model version, parameters, metrics, and result rows |
| 7 | P1 | Replace the display-first home with role workspaces | 2, 3, 4, 5, 6 | Each role sees its alerts, actions, and authorized modules above the fold |
| 8 | P2 | Add background jobs and production operations | 4, 5, 6 | Slow work is queued; health, readiness, backup, and error observability are verified |
| 9 | P2 | Expand integration, security, performance, and E2E tests | 1-8 | CI proves empty-database setup and the four business loops |
| 10 | P3 | Produce release and acceptance evidence | 1-9 | A clean environment can be deployed, tested, backed up, and restored from the docs |

Do not start a later milestone while an earlier exit gate is red. The first product-scope release is reached after Tasks 4-5; Tasks 6-11 raise completeness and commercial reliability.

## Target File Boundaries

- `app/security/authorization.py`: permission lookup and decorators only.
- `app/repositories/auth.py`: accounts, roles, permissions, and audit reads.
- `app/services/commerce.py`: order, payment, refund, and inventory transactions.
- `app/services/knowledge.py`: document validation, parsing, chunking, and vector jobs.
- `app/services/rag.py`: retrieval, answer generation, citation validation, feedback, and fallback.
- `app/services/analytics.py`: ADS refresh jobs and reproducible report queries.
- `app/services/prediction.py`: model registration, training tasks, metrics, and result persistence.
- `app/services/jobs.py`: PostgreSQL job enqueue, claim, retry, and completion.
- `app/routes/admin.py`, `knowledge.py`, `qa.py`, `reports.py`: thin HTTP adapters.
- `database/migrations/*.sql`: ordered, append-only upgrades for deployed databases.
- `tests/integration/`: real PostgreSQL tests; unit tests stay in `tests/`.

---

### Task 1 [P0-1]: Freeze The Runtime Contract And Remove V1 Ambiguity

**Files:**
- Create: `tests/test_runtime_v2_contract.py`
- Modify: `app/__init__.py`
- Modify: `gradio_app.py`
- Remove after replacement: `app/routes/analysis.py`, `app/routes/budgets.py`, `app/routes/forecast.py`, `app/routes/logs.py`, `app/routes/query.py`, `app/routes/users.py`
- Remove after replacement: `app/templates/analysis.html`, `app/templates/budgets.html`, `app/templates/forecast.html`, `app/templates/logs.html`, `app/templates/query.html`, `app/templates/users.html`
- Remove after replacement: `analysis_workbench.py`

- [ ] **Step 1: Write the failing runtime contract test**

Create a test that builds the Flask URL map and scans every imported runtime module:

```python
from pathlib import Path

from app import create_app


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("FROM users", "JOIN users", "FROM roles", "spending_record", "FROM budgets")


def test_registered_runtime_is_v2_only():
    app = create_app()
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    assert "main.dashboard" in endpoints
    assert "customers.index" in endpoints
    assert "algorithms.index" in endpoints

    runtime_files = [ROOT / "gradio_app.py", *sorted((ROOT / "app").rglob("*.py"))]
    violations = []
    for path in runtime_files:
        source = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN:
            if marker.lower() in source.lower():
                violations.append(f"{path.relative_to(ROOT)}: {marker}")
    assert violations == []
```

- [ ] **Step 2: Run the test and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_runtime_v2_contract.py -q`

Expected: FAIL with legacy references from `gradio_app.py` and the unregistered V1 route modules.

- [ ] **Step 3: Replace Gradio database access with V2 service calls**

Keep only the current RFM, KMeans, and churn tabs. Import `run_rfm`, `run_kmeans`, and `run_churn` from `app.services.algorithms`; remove functions that query V1 tables and remove the unsupported Prophet/LSTM, amount, sales, recommendation, old query, and old save tabs until Task 6 adds V2 implementations.

- [ ] **Step 4: Remove unregistered V1 modules**

Delete the listed route and template files only after `rg -n -i "users|roles|spending_record|budgets" app gradio_app.py` shows that no registered feature imports them. Preserve user-facing capabilities by rebuilding account management in Task 2 and reports in Task 5.

- [ ] **Step 5: Verify GREEN and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_runtime_v2_contract.py tests\test_gradio_contract.py -q
rg -n -i "FROM users|JOIN users|FROM roles|spending_record|FROM budgets" app gradio_app.py
```

Expected: tests pass and `rg` returns no matches. Commit: `refactor: enforce the V2 runtime contract`.

---

### Task 2 [P0-2]: Add Ordered Migrations And Resource-Level RBAC

**Files:**
- Create: `database/migrations/001_rbac_and_audit.sql`
- Create: `app/security/__init__.py`
- Create: `app/security/authorization.py`
- Create: `app/repositories/auth.py`
- Create: `app/routes/admin.py`
- Create: `app/templates/admin/users.html`
- Create: `app/templates/admin/logs.html`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_rbac_integration.py`
- Modify: `scripts/init_db.py`
- Modify: `app/__init__.py`
- Modify: `app/utils.py`
- Modify: `.env.example`

- [ ] **Step 1: Write migration-runner and RBAC integration tests**

The test must initialize an empty PostgreSQL database twice, assert each migration is recorded once, and assert the following role-to-permission examples:

```python
EXPECTED = {
    "super_admin": {"system.manage", "customer.read", "refund.approve", "qa.handle"},
    "customer_operator": {"customer.read", "customer.write"},
    "finance_auditor": {"payment.read", "refund.approve"},
    "model_operator": {"model.read", "model.run"},
    "knowledge_admin": {"knowledge.read", "knowledge.write", "knowledge.publish"},
    "qa_operator": {"qa.read", "qa.handle"},
}
```

- [ ] **Step 2: Add append-only migration tracking**

Add `audit.schema_migration(version varchar(80) primary key, checksum varchar(64), applied_at timestamptz)` and update `scripts/init_db.py` to sort `database/migrations/*.sql`, calculate SHA-256, reject checksum changes, execute unapplied files in one transaction, and insert the version record after success.

- [ ] **Step 3: Create the RBAC tables and seed ten roles**

`001_rbac_and_audit.sql` creates `auth.role`, `auth.permission`, `auth.account_role`, `auth.role_permission`, `audit.data_change_log`, `audit.import_log`, and `audit.interface_log`. Keep `auth.account.account_id` as the existing BIGINT key; do not rewrite production identities to UUID merely to match the example document.

Seed these role codes exactly: `super_admin`, `system_admin`, `customer_operator`, `product_operator`, `order_operator`, `finance_auditor`, `data_analyst`, `model_operator`, `knowledge_admin`, `qa_operator`. Backfill current `admin`, `operator`, and `analyst` accounts into the nearest roles without dropping the legacy `role` column in this release.

- [ ] **Step 4: Replace coarse role checks**

Implement and use this interface:

```python
def permission_required(permission_code):
    """Require an authenticated account with an enabled role and permission."""


def account_permissions(account_id):
    """Return a frozenset of permission codes for the request account."""
```

Replace existing decorators such as `@role_required("admin", "operator")` and `@role_required("admin", "analyst")` on registered routes with permissions such as `customer.write`, `product.write`, `order.write`, `payment.write`, `refund.request`, `refund.approve`, `model.run`, `import.read`, and `sql.execute`.

- [ ] **Step 5: Restore V2-compatible user and audit administration**

Register `admin_bp` under `/admin`. Implement account list/create/disable/password-reset against `auth.account` and role assignment against `auth.account_role`. Implement read-only login and operation log pages; never return password hashes or connection strings.

- [ ] **Step 6: Verify and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests\integration\test_rbac_integration.py tests\test_bs_contract.py -q`

Expected: migrations are idempotent, all ten roles exist, unauthorized requests return 403, and authorized requests succeed. Commit: `feat: add versioned RBAC and audit administration`.

---

### Task 3 [P0-3]: Make Refunds And Inventory A Complete Transaction

**Files:**
- Create: `database/migrations/002_inventory_and_refund_workflow.sql`
- Create: `tests/integration/test_refund_workflow.py`
- Modify: `app/repositories/retail.py`
- Modify: `app/services/commerce.py`
- Modify: `app/routes/refunds.py`
- Modify: `app/templates/refunds.html`
- Modify: `app/templates/orders.html`

- [ ] **Step 1: Write failing transaction tests**

Cover request, reject, approve, duplicate approve, partial item return, and rollback. The core assertion is:

```python
def test_approved_refund_restores_exact_item_quantity(connection, services):
    before = services.product_stock("SKU-F001")
    refund_id = services.request_refund(payment_id=1, items=[{"order_item_id": 1, "quantity": 2}], reason="质量问题")
    services.approve_refund(refund_id, approver_id=1)
    assert services.product_stock("SKU-F001") == before + 2
    assert services.inventory_delta(refund_id) == 2
    assert services.refund_flow_count(refund_id) == 1
```

- [ ] **Step 2: Extend the schema**

Create `biz.inventory_log` and `biz.refund_item`. Add `requested_by`, `reviewed_by`, `reviewed_at`, and `review_note` to `biz.refund`. Expand the refund status constraint to `pending`, `approved`, `rejected`, `success`, and `failed` while preserving existing `success` rows.

- [ ] **Step 3: Split refund request from approval**

Expose these service methods:

```python
RefundService.request(payment_id, items, reason, requester_id) -> int
RefundService.approve(refund_id, approver_id, review_note="") -> int
RefundService.reject(refund_id, approver_id, review_note) -> int
```

`approve` locks the refund, order, payment, affected order items, and products in deterministic ID order; validates remaining refundable quantities; restores stock; writes inventory logs and the negative consumption flow; updates order totals; writes the audit record; and commits once.

- [ ] **Step 4: Add separate UI actions and permissions**

Business users can submit requests with order-item quantities. Only accounts with `refund.approve` see approve/reject controls. Display status history and reviewer information.

- [ ] **Step 5: Verify and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_commerce.py tests\integration\test_refund_workflow.py -q`

Expected: all refund paths pass and a repeated approval produces no duplicate stock or flow. Commit: `feat: add audited refund approval and inventory return`.

---

### Task 4 [P0-4]: Build Knowledge Management And Vector Ingestion

**Files:**
- Create: `database/migrations/003_knowledge_and_vector.sql`
- Create: `app/repositories/knowledge.py`
- Create: `app/services/knowledge.py`
- Create: `app/services/ai_provider.py`
- Create: `app/routes/knowledge.py`
- Create: `app/templates/knowledge/index.html`
- Create: `app/templates/knowledge/detail.html`
- Create: `tests/test_knowledge_service.py`
- Create: `tests/integration/test_vector_retrieval.py`
- Modify: `app/__init__.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `requirements.txt`

- [ ] **Step 1: Write failing upload and retrieval tests**

Test accepted extensions `.pdf`, `.docx`, `.txt`, `.md`; reject executable content, path traversal, empty documents, and files over `MAX_UPLOAD_MB`. Use a fake embedding provider that returns deterministic 768-dimensional vectors.

- [ ] **Step 2: Create knowledge tables and HNSW index**

Enable `vector`; create `kb.knowledge_base`, `kb.document`, `kb.document_chunk`, and `kb.document_embedding`; enforce unique `(document_id, chunk_no)` and one current embedding per chunk/model. Create an HNSW cosine index on `embedding vector_cosine_ops`.

- [ ] **Step 3: Implement the provider boundary**

Use this contract so tests and providers remain replaceable:

```python
class AIProvider:
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def answer(self, question: str, contexts: list[dict]) -> str:
        raise NotImplementedError
```

The production adapter reads `AI_API_BASE`, `AI_API_KEY`, `AI_CHAT_MODEL`, `AI_EMBEDDING_MODEL`, and `EMBEDDING_DIM`. Add `pgvector>=0.3,<1`, `httpx>=0.28,<1`, `pypdf>=5,<6`, and `python-docx>=1.1,<2` to `requirements.txt`. Validate every returned vector dimension before insertion.

- [ ] **Step 4: Implement safe ingestion**

Store uploads outside `app/static`; generate server-side filenames; parse text; normalize whitespace; split into 400-700 Chinese-character chunks with 80-character overlap; calculate content hashes; write chunks; enqueue embeddings; publish only when parsed chunk count equals embedded chunk count.

- [ ] **Step 5: Implement administrator pages**

Add list/create/upload/parse/publish/disable actions protected by `knowledge.read`, `knowledge.write`, and `knowledge.publish`. Display document version, parse status, chunk count, embedding count, and last failure.

- [ ] **Step 6: Verify and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_knowledge_service.py tests\integration\test_vector_retrieval.py -q`

Expected: malicious uploads are rejected; a published document returns the expected nearest chunk. Commit: `feat: add secure knowledge ingestion and pgvector retrieval`.

---

### Task 5 [P0-5]: Deliver RAG Answers, Citations, Feedback, And Human Tickets

**Files:**
- Create: `database/migrations/004_qa_and_tickets.sql`
- Create: `app/repositories/qa.py`
- Create: `app/services/rag.py`
- Create: `app/routes/qa.py`
- Create: `app/templates/qa/chat.html`
- Create: `app/templates/qa/tickets.html`
- Create: `tests/test_rag_service.py`
- Create: `tests/integration/test_qa_traceability.py`
- Modify: `app/__init__.py`
- Modify: `app/templates/dashboard.html`

- [ ] **Step 1: Write failing grounded-answer tests**

Cover successful answer, missing retrieval, low confidence, provider failure, feedback, ticket assignment, and ticket resolution. Assert that no successful answer can be stored without at least one retrieval log.

- [ ] **Step 2: Create the five QA tables**

Create `qa.qa_session`, `qa.qa_message`, `qa.qa_retrieval_log`, `qa.qa_feedback`, and `qa.qa_ticket`. Store prompt version, provider model, confidence, latency, token counts, and error code on the message so every answer is reproducible.

- [ ] **Step 3: Implement retrieval and fallback**

`RagService.ask(session_id, question, account_id)` embeds the question, filters only published documents in authorized knowledge bases, retrieves `RAG_TOP_K`, and compares the best distance with `RAG_DISTANCE_THRESHOLD`. Seed `RAG_TOP_K=5` and `RAG_DISTANCE_THRESHOLD=0.35` as the initial cosine-distance settings, then keep the values configurable for evaluation. When retrieval is empty or below threshold, store a refusal message and open a pending ticket instead of calling the answer provider.

- [ ] **Step 4: Enforce citations**

The answer response must contain structured citations:

```python
{
    "answer": "退款申请需在支付后7日内提交。",
    "citations": [{"document_id": 12, "chunk_id": 98, "title": "退款规则", "rank": 1}],
    "confidence": 0.91,
    "ticket_id": None,
}
```

Reject provider output that cites an un-retrieved chunk. Show citation title and source excerpt in the chat UI.

- [ ] **Step 5: Add the support workspace**

`qa_operator` sees pending tickets, source question, retrieval evidence, customer history allowed by permission, assignment, reply, and resolution. Every state transition writes an operation log.

- [ ] **Step 6: Verify and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_rag_service.py tests\integration\test_qa_traceability.py -q`

Expected: grounded answers have citations, low-confidence questions create one ticket, and no answer fabricates a source. Commit: `feat: add traceable RAG and human support tickets`.

---

### Task 6 [P1-1]: Add Traceable ADS Refreshes And Reports

**Files:**
- Create: `database/migrations/005_ads_results.sql`
- Create: `app/services/analytics.py`
- Create: `app/routes/reports.py`
- Create: `app/templates/reports/index.html`
- Create: `tests/integration/test_analytics_refresh.py`
- Modify: `app/repositories/retail.py`
- Modify: `app/routes/main.py`
- Modify: `app/__init__.py`

- [ ] **Step 1: Write failing reconciliation tests**

Seed payments and refunds, refresh ADS, and assert daily, product, category, RFM, and customer-profile totals reconcile to `dwd.consumption_flow` and order items.

- [ ] **Step 2: Add result tables**

Create `ads.daily_sales`, `ads.product_sales`, `ads.category_sales`, and `ads.customer_profile`, each with `snapshot_date`, `refresh_task_id`, unique business keys, and indexes for dashboard filters.

- [ ] **Step 3: Implement one transactional refresh service**

`AnalyticsService.refresh(snapshot_date, operator_id)` creates an audit task, upserts all ADS tables from the same transaction snapshot, records row counts and elapsed time, and marks failure with an error message after rollback.

- [ ] **Step 4: Add filtered reports and CSV export**

Reports accept bounded date ranges and explicit dimensions. CSV output streams UTF-8 with BOM, applies the same permission and filter rules as the page, and records an export audit event.

- [ ] **Step 5: Verify and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests\integration\test_analytics_refresh.py -q`

Expected: all report totals equal source facts and repeated refreshes are idempotent. Commit: `feat: add traceable analytics refreshes and reports`.

---

### Task 7 [P1-2]: Complete Model Registry And Prediction Persistence

**Files:**
- Create: `database/migrations/006_model_registry_and_results.sql`
- Create: `app/services/prediction.py`
- Create: `tests/test_prediction_service.py`
- Create: `tests/integration/test_prediction_persistence.py`
- Modify: `app/services/algorithms.py`
- Modify: `app/routes/algorithms.py`
- Modify: `app/templates/algorithms.html`
- Modify: `gradio_app.py`

- [ ] **Step 1: Write failing lifecycle tests**

Assert every RFM, cluster, churn, amount, sales, and recommendation run creates a model task, links a model version, stores parameters and metrics, writes result rows, and preserves earlier task results.

- [ ] **Step 2: Add the missing model tables**

Create `ml.model_registry`, `ml.customer_amount_prediction`, `ml.product_sales_forecast`, and `ml.product_recommendation`. Add `model_id`, `dataset_snapshot`, `code_version`, and `finished_at` links to `ml.model_task` without deleting historical tasks.

- [ ] **Step 3: Implement supported baseline models**

Use scikit-learn Ridge for 30-day customer amount, lagged rolling averages for product sales, and cosine similarity for product recommendations. Store the exact feature list, training window, random seed, and evaluation metric in the task record.

- [ ] **Step 4: Rebuild Gradio as a service consumer**

Gradio calls the same prediction service functions as Flask and only displays task-scoped results. It contains no raw SQL and remains bound to `127.0.0.1` unless deployed behind authenticated access.

- [ ] **Step 5: Verify and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_prediction_service.py tests\integration\test_prediction_persistence.py tests\test_gradio_contract.py -q`

Expected: all six result families are task-scoped and previous runs remain queryable. Commit: `feat: persist versioned prediction results`.

---

### Task 8 [P1-3]: Convert The Home Page Into Role Workspaces

**Files:**
- Modify: `app/templates/dashboard.html`
- Modify: `app/templates/base.html`
- Modify: `app/routes/main.py`
- Modify: `app/repositories/retail.py`
- Modify: `tests/test_dashboard_responsive.py`

- [ ] **Step 1: Write failing role-workspace tests**

Render the dashboard for finance, knowledge, QA, model, and administrator roles. Assert each sees only authorized navigation plus its own pending count, alert list, and primary action.

- [ ] **Step 2: Reduce display-only density**

Limit the hero to a compact identity band; put role KPIs, pending work, low-stock/refund/model/knowledge alerts, and primary actions in the first viewport. On screens below 600px, render modules as compact icon rows rather than full-height promotional cards.

- [ ] **Step 3: Add complete navigation**

Expose account administration, audit, reports, model tasks, knowledge management, customer service, and tickets according to permission codes. Keep normal anchors and visible focus states when JavaScript is unavailable.

- [ ] **Step 4: Verify and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_dashboard_responsive.py -q`

Expected: all role and responsive contracts pass. Capture desktop and mobile screenshots for administrator and QA roles. Commit: `feat: add role-focused operational workspaces`.

---

### Task 9 [P2-1]: Add PostgreSQL-Backed Jobs And Operational Controls

**Files:**
- Create: `database/migrations/007_background_jobs.sql`
- Create: `app/services/jobs.py`
- Create: `worker.py`
- Create: `tests/integration/test_job_worker.py`
- Modify: `app/routes/system.py`
- Modify: `render.yaml`
- Modify: `docker-compose.yml`
- Modify: `README.md`

- [ ] **Step 1: Write failing claim/retry tests**

Two worker connections must never claim the same job. Failed jobs retry at most three times with recorded error text; dead jobs remain inspectable.

- [ ] **Step 2: Create and implement the job queue**

Create `audit.background_job` with type, payload, status, attempts, available time, lock owner, timestamps, and error. Claim one job with `FOR UPDATE SKIP LOCKED`; commit the claim before execution; update success or retry in a separate transaction.

- [ ] **Step 3: Queue slow work**

Knowledge parsing/vectorization, ADS refresh, and model tasks return a job ID immediately. UI pages poll bounded status endpoints protected by the same permission as the submitted action.

- [ ] **Step 4: Add readiness and deployment processes**

Keep `/healthz` as process liveness and add `/readyz` for a bounded database query and migration-version check. Add a worker process to Docker Compose and Render; keep Gradio internal and do not expose PostgreSQL publicly.

- [ ] **Step 5: Verify and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests\integration\test_job_worker.py tests\test_deploy_contract.py -q`

Expected: concurrent claim tests pass and deployment descriptors include web, worker, health, readiness, and secrets. Commit: `feat: add durable background jobs and readiness checks`.

---

### Task 10 [P2-2]: Establish Commercial Acceptance Tests

**Files:**
- Modify: `tests/integration/conftest.py`
- Create: `tests/e2e/test_critical_flows.py`
- Create: `tests/security/test_upload_and_access.py`
- Create: `tests/performance/test_query_budgets.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `requirements.txt`

- [ ] **Step 1: Add a real PostgreSQL 18 plus pgvector CI service**

Initialize an isolated database per integration module, apply the base schema and every migration, seed deterministic fixtures, and drop the database after the module.

- [ ] **Step 2: Add four-loop E2E coverage**

Automate: login and role denial; order-payment-refund-inventory; analytics-model-task-result; document-publish-question-citation-ticket. Tests must assert database facts after each browser action.

- [ ] **Step 3: Add security checks**

Cover CSRF, open redirect, disabled accounts, permission escalation, SQL lab write attempts, upload extension and content mismatch, path traversal, oversized files, prompt injection in documents, and citation spoofing.

- [ ] **Step 4: Add measurable performance budgets**

With 5,000 customers, 50,000 transactions, and 5,000 chunks, enforce local CI budgets of P95 under 1 second for indexed list/report queries, under 800 ms for TopK retrieval excluding provider latency, and under 5 seconds for a fake-provider end-to-end QA request.

- [ ] **Step 5: Verify and commit**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: the complete suite passes with no skipped database tests in CI. Commit: `test: cover commercial critical flows and budgets`.

---

### Task 11 [P3-1]: Add Backup, Restore, Monitoring, And Release Evidence

**Files:**
- Create: `scripts/backup_db.ps1`
- Create: `scripts/restore_db.ps1`
- Create: `docs/deployment.md`
- Create: `docs/user-guide.md`
- Create: `docs/acceptance-checklist.md`
- Create: `docs/data-retention.md`
- Modify: `README.md`
- Modify: `.env.example`

- [ ] **Step 1: Document measurable operating targets**

Set prototype targets to RPO 24 hours, RTO 4 hours, 99.5% monthly availability, 30-day application logs, 180-day audit logs, and a quarterly restore drill. State that higher targets require a paid database and multi-instance deployment.

- [ ] **Step 2: Add safe backup and restore scripts**

Backups use `pg_dump --format=custom`, timestamped filenames, checksum files, retention pruning, and nonzero exit handling. Restore always targets an explicitly named empty database and refuses the configured production database name.

- [ ] **Step 3: Complete operator and user documentation**

Document environment variables, initialization, migrations, web and worker startup, role assignment, knowledge publishing, ticket handling, algorithm interpretation, backup, restore, rollback, and common failures.

- [ ] **Step 4: Run a clean-environment acceptance rehearsal**

From a new database: initialize, create accounts, execute all four loops, run the full suite, make a backup, restore to a second database, and compare row counts and migration checksums.

- [ ] **Step 5: Final verification and release commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
& 'C:\Users\jiang\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe' diff --check
```

Expected: all tests pass, no whitespace errors, no secrets are tracked, and `docs/acceptance-checklist.md` contains evidence for every requirement. Commit: `docs: add commercial operations and acceptance evidence`.

---

## Release Gates

### Release A: Stable V2 Core

Tasks 1-3 complete. The system has one runtime data contract, resource-level permissions, and transactionally correct refunds and inventory.

### Release B: Named Product Scope Complete

Tasks 4-5 complete. The deployed product can legitimately include “智能客服” in its name because knowledge ingestion, grounded answers, citations, feedback, and human tickets are demonstrable.

### Release C: Analysis And Prediction Complete

Tasks 6-8 complete. Dashboard, reports, models, and role workspaces are traceable and operationally efficient.

### Release D: Commercial Prototype Acceptance

Tasks 9-11 complete. CI, job execution, deployment, backup/restore, security checks, performance budgets, and delivery documents are reproducible.

## Estimated Effort

| Release | Solo full-time estimate | Main risk |
|---|---:|---|
| A | 8-12 working days | Migrating accounts and refund data without breaking the deployed database |
| B | 10-15 working days | AI provider configuration, vector quality, and grounded-answer evaluation |
| C | 8-12 working days | Making model outputs reproducible and reconciling ADS metrics |
| D | 8-12 working days | CI runtime, Render worker cost, and reliable restore rehearsal |

Total: approximately 34-51 focused working days for one engineer. A two-person team can parallelize Tasks 4-5 and Tasks 6-7 only after Tasks 1-3 are green.
