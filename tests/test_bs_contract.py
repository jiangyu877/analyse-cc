from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from flask import Blueprint, Flask


ROOT = Path(__file__).resolve().parents[1]


def test_production_server_and_health_route_exist():
    server = (ROOT / "serve.py").read_text(encoding="utf-8")
    system_route = (ROOT / "app" / "routes" / "system.py").read_text(encoding="utf-8")
    assert "waitress" in server
    assert '"/healthz"' in system_route


def test_browser_never_receives_database_credentials():
    templates = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "app" / "templates").glob("*.html")
    ).lower()
    assert "database_url" not in templates
    assert "db_password" not in templates
    assert "postgresql://" not in templates


def test_no_hardcoded_legacy_database_password():
    compatibility_db = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
    assert '"password"' not in compatibility_db


def _app_for_blueprint(blueprint):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test-secret", TESTING=True)
    auth_bp = Blueprint("auth", __name__)
    auth_bp.add_url_rule("/login", "login", lambda: "login")
    app.register_blueprint(auth_bp)
    app.register_blueprint(blueprint)
    return app


def test_health_and_readiness_have_separate_database_contracts(monkeypatch):
    from app.routes import system

    class FakeResult:
        def scalar_one(self):
            return True

    class ReadySession:
        def __init__(self, failure=None):
            self.calls = []
            self.failure = failure
            self.rollbacks = 0

        def execute(self, statement, parameters=None):
            self.calls.append((str(statement), parameters))
            if self.failure is not None:
                raise self.failure
            return FakeResult()

        def rollback(self):
            self.rollbacks += 1

    app = _app_for_blueprint(system.system_bp)
    client = app.test_client()
    unavailable = ReadySession(
        RuntimeError("postgresql://admin:database-secret@database/app is down")
    )
    monkeypatch.setattr(system, "db", SimpleNamespace(session=unavailable))

    health = client.get("/healthz")
    readiness = client.get("/readyz")

    assert health.status_code == 200
    assert health.get_json() == {"status": "ok"}
    assert len(unavailable.calls) == 1
    assert readiness.status_code == 503
    assert "database-secret" not in readiness.get_data(as_text=True)
    assert unavailable.rollbacks == 1

    ready = ReadySession()
    monkeypatch.setattr(system, "db", SimpleNamespace(session=ready))
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ready"}
    statements = [statement for statement, _parameters in ready.calls]
    assert any("statement_timeout" in statement and "1000ms" in statement for statement in statements)
    assert any(statement.strip() == "SELECT 1" for statement in statements)
    migration_calls = [
        (statement, parameters)
        for statement, parameters in ready.calls
        if "audit.schema_migration" in statement
    ]
    assert len(migration_calls) == 1
    assert migration_calls[0][1] == {"version": "009_knowledge_publish_guard.sql"}


def test_job_status_enforces_saved_permission_and_exposes_only_safe_fields(monkeypatch):
    from app.routes import system

    now = datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc)
    jobs = {
        41: {
            "job_id": 41,
            "job_type": "model_rfm",
            "payload": {"operator_id": 7},
            "status": "succeeded",
            "attempts": 1,
            "permission_code": "model.run",
            "created_by": 7,
            "created_at": now,
            "started_at": now,
            "finished_at": now,
            "updated_at": now,
            "result": {"task_id": 88},
            "last_error": "must stay private unless dead",
            "locked_by": None,
        }
    }
    permissions = set()

    monkeypatch.setattr(
        system,
        "JobService",
        SimpleNamespace(get=lambda job_id: jobs.get(job_id)),
        raising=False,
    )
    monkeypatch.setattr(
        system,
        "account_permissions",
        lambda _account_id: frozenset(permissions),
        raising=False,
    )

    app = _app_for_blueprint(system.system_bp)
    client = app.test_client()

    assert client.get("/jobs/41").status_code == 302
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = 7

    assert client.get("/jobs/999").status_code == 404
    assert client.get("/jobs/41").status_code == 403

    permissions.add("model.run")
    response = client.get("/jobs/41")
    assert response.status_code == 200
    body = response.get_json()
    assert set(body) == {
        "job_id",
        "job_type",
        "status",
        "attempts",
        "created_at",
        "started_at",
        "finished_at",
        "result",
    }
    assert body["result"] == {"task_id": 88}

    jobs[41] = {**jobs[41], "status": "dead", "result": None, "last_error": "safe error"}
    dead = client.get("/jobs/41").get_json()
    assert set(dead) == set(body) | {"last_error"}
    assert dead["last_error"] == "safe error"


def test_six_algorithm_posts_only_enqueue_and_redirect(monkeypatch):
    from app.routes import algorithms
    from app.security import authorization
    from app.services.jobs import JobService
    from app.services.prediction import PredictionService

    calls = []

    def enqueue(job_type, payload, created_by):
        calls.append((job_type, payload, created_by))
        return 700 + len(calls)

    def synchronous_call(*_args, **_kwargs):
        raise AssertionError("algorithm services must not run inside the request")

    monkeypatch.setattr(JobService, "enqueue", staticmethod(enqueue))
    monkeypatch.setattr(authorization, "account_permissions", lambda _account_id: {"model.run"})
    for name in ("run_rfm", "run_kmeans", "run_churn"):
        monkeypatch.setattr(algorithms, name, synchronous_call, raising=False)
    for name in (
        "run_customer_amount",
        "run_product_sales_forecast",
        "run_product_recommendation",
    ):
        monkeypatch.setattr(PredictionService, name, staticmethod(synchronous_call))

    app = _app_for_blueprint(algorithms.algorithms_bp)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = 17

    cases = (
        ("rfm", "model_rfm", {}),
        ("kmeans", "model_kmeans", {"clusters": 4}),
        ("churn", "model_churn", {"observation_days": 90}),
        (
            "customer_amount",
            "model_customer_amount",
            {"horizon_days": 30, "training_days": 180},
        ),
        (
            "product_sales_forecast",
            "model_product_sales_forecast",
            {"horizon_days": 30, "training_days": 90},
        ),
        (
            "product_recommendation",
            "model_product_recommendation",
            {"top_k": 5, "training_days": 180},
        ),
    )
    for index, (route_type, _job_type, _payload) in enumerate(cases, start=1):
        response = client.post(f"/algorithms/run/{route_type}")
        assert response.status_code == 302
        assert f"job_id={700 + index}" in response.headers["Location"]

    assert calls == [
        (job_type, payload, 17) for _route_type, job_type, payload in cases
    ]


def test_report_refresh_only_enqueues_and_defaults_to_today(monkeypatch):
    from app.routes import reports
    from app.security import authorization
    from app.services.analytics import AnalyticsService
    from app.services.jobs import JobService

    calls = []

    def enqueue(job_type, payload, created_by):
        calls.append((job_type, payload, created_by))
        return 801 + len(calls)

    def synchronous_call(*_args, **_kwargs):
        raise AssertionError("analytics refresh must not run inside the request")

    monkeypatch.setattr(JobService, "enqueue", staticmethod(enqueue))
    monkeypatch.setattr(AnalyticsService, "refresh", staticmethod(synchronous_call))
    monkeypatch.setattr(
        authorization, "account_permissions", lambda _account_id: {"analysis.run"}
    )

    app = _app_for_blueprint(reports.reports_bp)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = 19

    explicit = client.post("/reports/refresh", data={"snapshot_date": "2026-07-01"})
    defaulted = client.post("/reports/refresh")

    assert explicit.status_code == 302
    assert "job_id=802" in explicit.headers["Location"]
    assert defaulted.status_code == 302
    assert "job_id=803" in defaulted.headers["Location"]
    assert calls == [
        ("analytics_refresh", {"snapshot_date": "2026-07-01"}, 19),
        ("analytics_refresh", {"snapshot_date": date.today().isoformat()}, 19),
    ]


def test_background_action_templates_use_bounded_same_origin_polling():
    algorithms = (ROOT / "app" / "routes" / "algorithms.py").read_text(encoding="utf-8")
    reports = (ROOT / "app" / "routes" / "reports.py").read_text(encoding="utf-8")

    assert "JobService.enqueue(" in algorithms
    assert "run_rfm(" not in algorithms
    assert "run_kmeans(" not in algorithms
    assert "run_churn(" not in algorithms
    assert "PredictionService.run_" not in algorithms
    assert '@reports_bp.post("/refresh")' in reports
    assert 'permission_required("analysis.run")' in reports
    assert "JobService.enqueue(" in reports

    for relative in (
        Path("app/templates/algorithms.html"),
        Path("app/templates/reports/index.html"),
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "{% if job_id %}" in source
        assert "data-job-monitor" in source
        assert "data-status-url" in source
        assert "data-success-url" in source
        assert "{task_id}" in source
        assert "min-height" in source
        assert "js/job-status.js" in source

    poller = (ROOT / "app" / "static" / "js" / "job-status.js").read_text(
        encoding="utf-8"
    )
    assert "POLL_INTERVAL_MS = 2000" in poller
    assert "MAX_POLLS = 150" in poller
    assert "querySelector('[data-job-monitor]')" in poller
    assert "querySelectorAll('[data-job-monitor]')" not in poller
    assert "window.location.origin" in poller
    assert "same-origin" in poller
    for state in ("queued", "running", "succeeded", "dead"):
        assert state in poller
    assert "last_error" in poller
    assert "textContent" in poller
    assert "innerHTML" not in poller
