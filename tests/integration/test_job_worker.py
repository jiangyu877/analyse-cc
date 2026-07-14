from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from sqlalchemy import text


def _make_available(job_id):
    from app.extensions import db

    db.session.execute(
        text("""
            UPDATE audit.background_job
            SET available_at = now() - INTERVAL '1 second'
            WHERE job_id = :job_id
        """),
        {"job_id": job_id},
    )
    db.session.commit()


@pytest.fixture
def created_by(initialized_database):
    with initialized_database.cursor() as cursor:
        cursor.execute("SELECT account_id FROM auth.account WHERE username = 'admin'")
        account_id = cursor.fetchone()[0]
    initialized_database.rollback()
    return account_id


def _make_stale(job_id):
    from app.extensions import db

    db.session.execute(
        text("""
            UPDATE audit.background_job
            SET locked_at = now() - INTERVAL '16 minutes'
            WHERE job_id = :job_id
        """),
        {"job_id": job_id},
    )
    db.session.commit()


def test_enqueue_normalizes_every_supported_job_and_derives_permission(
    initialized_app, created_by
):
    from app.services.jobs import JOB_SPECS, JobService

    cases = {
        "analytics_refresh": ({"snapshot_date": date(2026, 7, 14)}, "analysis.run"),
        "model_rfm": ({}, "model.run"),
        "model_kmeans": ({"clusters": "2"}, "model.run"),
        "model_churn": ({"observation_days": "180"}, "model.run"),
        "model_customer_amount": (
            {"horizon_days": "90", "training_days": "60"},
            "model.run",
        ),
        "model_product_sales_forecast": (
            {"horizon_days": "1", "training_days": "730"},
            "model.run",
        ),
        "model_product_recommendation": (
            {"top_k": "20", "training_days": "30"},
            "model.run",
        ),
    }
    expected_payloads = {
        "analytics_refresh": {"snapshot_date": "2026-07-14"},
        "model_rfm": {},
        "model_kmeans": {"clusters": 2},
        "model_churn": {"observation_days": 180},
        "model_customer_amount": {"horizon_days": 90, "training_days": 60},
        "model_product_sales_forecast": {"horizon_days": 1, "training_days": 730},
        "model_product_recommendation": {"top_k": 20, "training_days": 30},
    }

    assert set(JOB_SPECS) == set(cases)
    with initialized_app.app_context():
        for job_type, (payload, permission_code) in cases.items():
            assert JOB_SPECS[job_type]["permission"] == permission_code
            job_id = JobService.enqueue(job_type, payload, created_by)
            job = JobService.get(job_id)

            assert isinstance(job_id, int)
            assert job["job_type"] == job_type
            assert job["payload"] == {
                **expected_payloads[job_type],
                "operator_id": created_by,
            }
            assert job["permission_code"] == permission_code
            assert job["created_by"] == created_by
            assert job["status"] == "queued"
            assert job["attempts"] == 0
        assert JobService.recover_stale(stale_after_seconds=900) == 0


def test_enqueue_rejects_unknown_missing_extra_and_out_of_range_payload(
    initialized_app, created_by
):
    from app.services.jobs import JobError, JobService

    invalid_jobs = [
        ("not_a_job", {}),
        ("model_kmeans", {}),
        ("model_rfm", {"unexpected": 1}),
        ("model_churn", {"observation_days": 29}),
        ("model_customer_amount", {"horizon_days": 91, "training_days": 60}),
        ("model_product_sales_forecast", {"horizon_days": 1, "training_days": 27}),
        ("model_product_recommendation", {"top_k": 0, "training_days": 30}),
        ("analytics_refresh", {"snapshot_date": "2026-02-30"}),
        ("model_rfm", []),
    ]

    with initialized_app.app_context():
        for job_type, payload in invalid_jobs:
            with pytest.raises(JobError):
                JobService.enqueue(job_type, payload, created_by)


def test_two_workers_cannot_claim_the_same_job(initialized_app, created_by):
    from app.services.jobs import JobService

    with initialized_app.app_context():
        job_id = JobService.enqueue("model_rfm", {}, created_by)

    barrier = Barrier(2)

    def claim(worker_id):
        with initialized_app.app_context():
            barrier.wait(timeout=5)
            return JobService.claim_next(worker_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(claim, ("worker-a", "worker-b")))

    winners = [job for job in claimed if job is not None]
    assert len(winners) == 1
    assert winners[0]["job_id"] == job_id
    assert winners[0]["status"] == "running"
    assert winners[0]["attempts"] == 1
    assert winners[0]["locked_by"] in {"worker-a", "worker-b"}


def test_fail_requeues_twice_then_marks_dead_and_redacts_credentials(
    initialized_app, created_by
):
    from app.services.jobs import JobService

    errors = [
        RuntimeError("temporary password=plain-secret failure"),
        "cannot open postgresql://alice:url-secret@db.local/app",
        "final password=last-secret at https://bob:web-secret@example.test/path "
        + "x" * 2100,
    ]
    secrets = {"plain-secret", "url-secret", "last-secret", "web-secret"}

    with initialized_app.app_context():
        job_id = JobService.enqueue("model_rfm", {}, created_by)
        for attempt, error in enumerate(errors, start=1):
            job = JobService.claim_next("retry-worker")
            assert job["job_id"] == job_id
            assert job["attempts"] == attempt

            failed = JobService.fail(job_id, "retry-worker", error)
            assert failed["status"] == ("dead" if attempt == 3 else "queued")
            assert failed["last_error"]
            assert len(failed["last_error"]) <= 2000
            assert not any(secret in failed["last_error"] for secret in secrets)
            if attempt == 1:
                assert "RuntimeError" in failed["last_error"]

            if attempt < 3:
                assert JobService.claim_next("too-early-worker") is None
                _make_available(job_id)

        final = JobService.get(job_id)
        assert final["attempts"] == 3
        assert final["finished_at"] is not None
        assert final["locked_by"] is None
        assert final["locked_at"] is None


def test_stale_running_job_is_recovered_and_claimed_by_a_new_worker(
    initialized_app, created_by
):
    from app.services.jobs import JobService

    with initialized_app.app_context():
        job_id = JobService.enqueue("model_kmeans", {"clusters": 4}, created_by)
        first_claim = JobService.claim_next("stale-worker")
        assert first_claim["attempts"] == 1
        _make_stale(job_id)

        recovered = JobService.claim_next(
            "replacement-worker", stale_after_seconds=900
        )

        assert recovered["job_id"] == job_id
        assert recovered["status"] == "running"
        assert recovered["attempts"] == 2
        assert recovered["locked_by"] == "replacement-worker"


def test_non_integral_stale_threshold_does_not_change_active_job_owner(
    initialized_app, created_by
):
    from app.services.jobs import JobError, JobService

    with initialized_app.app_context():
        job_id = JobService.enqueue("model_rfm", {}, created_by)
        claimed = JobService.claim_next("active-worker")

        with pytest.raises(JobError, match="non-negative integer"):
            JobService.claim_next("replacement-worker", stale_after_seconds=0.5)

        current = JobService.get(job_id)
        assert current["status"] == "running"
        assert current["locked_by"] == "active-worker"
        assert current["locked_at"] == claimed["locked_at"]
        assert current["attempts"] == 1


def test_complete_requires_owner_and_stores_an_object_result(initialized_app, created_by):
    from app.services.jobs import JobError, JobService

    result = {"task_id": 42, "metrics": {"rows": 12}}
    with initialized_app.app_context():
        job_id = JobService.enqueue("model_rfm", {}, created_by)
        JobService.claim_next("result-worker")

        with pytest.raises(JobError):
            JobService.complete(job_id, "other-worker", result)
        with pytest.raises(JobError):
            JobService.fail(job_id, "other-worker", "must not update")
        with pytest.raises(JobError):
            JobService.complete(job_id, "result-worker", ["not", "an", "object"])

        completed = JobService.complete(job_id, "result-worker", result)

        assert completed["status"] == "succeeded"
        assert completed["result"] == result
        assert completed["finished_at"] is not None
        assert completed["locked_by"] is None
        assert completed["locked_at"] is None
        assert JobService.get(job_id) == completed
        assert JobService.get(999999999) is None


def test_worker_success_persists_task_id_and_removes_session(
    initialized_app, created_by, monkeypatch
):
    import worker
    from app.extensions import db
    from app.services.jobs import JOB_SPECS, JobService

    handled_payloads = []
    removed_sessions = []
    original_remove = db.session.remove

    def handle(payload):
        handled_payloads.append(payload)
        return 731

    def remove_session():
        removed_sessions.append(True)
        original_remove()

    monkeypatch.setitem(worker.HANDLERS, "model_rfm", handle)
    monkeypatch.setattr(db.session, "remove", remove_session)

    with initialized_app.app_context():
        assert set(worker.HANDLERS) == set(JOB_SPECS)
        job_id = JobService.enqueue("model_rfm", {}, created_by)

        assert worker.run_once("success-worker") is True
        job = JobService.get(job_id)

        assert handled_payloads == [{"operator_id": created_by}]
        assert removed_sessions == [True]
        assert job["status"] == "succeeded"
        assert job["result"] == {"task_id": 731}


def test_worker_failure_is_safely_requeued(initialized_app, created_by, monkeypatch):
    import worker
    from app.services.jobs import JobService

    def fail(_payload):
        raise RuntimeError("temporary password=worker-secret failure")

    monkeypatch.setitem(worker.HANDLERS, "model_rfm", fail)

    with initialized_app.app_context():
        job_id = JobService.enqueue("model_rfm", {}, created_by)

        assert worker.run_once("failure-worker") is True
        job = JobService.get(job_id)

        assert job["status"] == "queued"
        assert job["attempts"] == 1
        assert job["result"] is None
        assert "RuntimeError" in job["last_error"]
        assert "worker-secret" not in job["last_error"]


def test_worker_rolls_back_aborted_handler_transaction_before_fail(
    initialized_app, created_by, monkeypatch
):
    import worker
    from app.extensions import db
    from app.services.jobs import JobService

    def aborting_handler(_payload):
        db.session.execute(text("SELECT 1/0"))

    monkeypatch.setitem(worker.HANDLERS, "model_rfm", aborting_handler)

    with initialized_app.app_context():
        job_id = JobService.enqueue("model_rfm", {}, created_by)

        assert worker.run_once("aborted-transaction-worker") is True
        job = JobService.get(job_id)

        assert job["status"] == "queued"
        assert job["attempts"] == 1
        assert job["last_error"]
        assert job["locked_by"] is None


def test_worker_unknown_type_fails_without_invoking_dynamic_code(
    initialized_app, created_by
):
    import worker
    from app.extensions import db
    from app.services.jobs import JobService

    with initialized_app.app_context():
        job_id = db.session.execute(
            text("""
                INSERT INTO audit.background_job
                    (job_type, payload, created_by, permission_code)
                VALUES
                    ('unregistered_handler', CAST(:payload AS jsonb),
                     :created_by, 'model.run')
                RETURNING job_id
            """),
            {"payload": '{"operator_id": 1}', "created_by": created_by},
        ).scalar_one()
        db.session.commit()

        assert worker.run_once("unknown-worker") is True
        job = JobService.get(job_id)

        assert job["status"] == "queued"
        assert job["attempts"] == 1
        assert "unknown job type" in job["last_error"].lower()


def test_worker_returns_false_when_no_job_is_available(initialized_app):
    import worker

    with initialized_app.app_context():
        assert worker.run_once("idle-worker") is False
