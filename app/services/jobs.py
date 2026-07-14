import json
import re
from collections.abc import Mapping
from datetime import date, datetime

from sqlalchemy import text

from app.extensions import db


class JobError(RuntimeError):
    pass


JOB_SPECS = {
    "analytics_refresh": {
        "permission": "analysis.run",
        "fields": {"snapshot_date": ("date", None, None)},
    },
    "model_rfm": {
        "permission": "model.run",
        "fields": {},
    },
    "model_kmeans": {
        "permission": "model.run",
        "fields": {"clusters": ("int", 2, 8)},
    },
    "model_churn": {
        "permission": "model.run",
        "fields": {"observation_days": ("int", 30, 180)},
    },
    "model_customer_amount": {
        "permission": "model.run",
        "fields": {
            "horizon_days": ("int", 1, 90),
            "training_days": ("int", 60, 730),
        },
    },
    "model_product_sales_forecast": {
        "permission": "model.run",
        "fields": {
            "horizon_days": ("int", 1, 90),
            "training_days": ("int", 28, 730),
        },
    },
    "model_product_recommendation": {
        "permission": "model.run",
        "fields": {
            "top_k": ("int", 1, 20),
            "training_days": ("int", 30, 730),
        },
    },
}


_URL_CREDENTIAL_RE = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<username>[^\s/:@]+):(?P<password>[^\s/@]+)@",
    re.IGNORECASE,
)
_QUOTED_PASSWORD_RE = re.compile(
    r"(?P<label>password\s*=\s*)(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE,
)
_PASSWORD_RE = re.compile(
    r"(?P<label>password\s*=\s*)[^\s&;,]+",
    re.IGNORECASE,
)


def _canonical_integer(field_name, value, minimum, maximum):
    if isinstance(value, bool):
        raise JobError(f"{field_name} must be an integer")
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped or not re.fullmatch(r"[+-]?\d+", stripped):
            raise JobError(f"{field_name} must be an integer")
        normalized = int(stripped)
    else:
        raise JobError(f"{field_name} must be an integer")
    if not minimum <= normalized <= maximum:
        raise JobError(f"{field_name} must be between {minimum} and {maximum}")
    return normalized


def _canonical_date(field_name, value):
    if isinstance(value, datetime):
        normalized = value.date()
    elif isinstance(value, date):
        normalized = value
    elif isinstance(value, str):
        try:
            normalized = date.fromisoformat(value.strip())
        except ValueError as error:
            raise JobError(f"{field_name} must be an ISO date") from error
    else:
        raise JobError(f"{field_name} must be an ISO date")
    return normalized.isoformat()


def _normalized_payload(job_type, payload, created_by):
    spec = JOB_SPECS.get(job_type)
    if spec is None:
        raise JobError(f"unknown job type: {job_type}")
    if not isinstance(payload, Mapping):
        raise JobError("payload must be an object")

    fields = spec["fields"]
    supplied = set(payload)
    expected = set(fields)
    missing = sorted(expected - supplied)
    extra = sorted(supplied - expected)
    if missing:
        raise JobError(f"missing payload fields: {', '.join(missing)}")
    if extra:
        raise JobError(f"unexpected payload fields: {', '.join(extra)}")

    normalized = {}
    for field_name, (field_type, minimum, maximum) in fields.items():
        value = payload[field_name]
        if field_type == "int":
            normalized[field_name] = _canonical_integer(
                field_name, value, minimum, maximum
            )
        else:
            normalized[field_name] = _canonical_date(field_name, value)

    if isinstance(created_by, bool):
        raise JobError("created_by must be an integer")
    try:
        operator_id = int(created_by)
    except (TypeError, ValueError) as error:
        raise JobError("created_by must be an integer") from error
    if operator_id <= 0:
        raise JobError("created_by must be positive")
    normalized["operator_id"] = operator_id

    serialized = json.dumps(
        normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    if len(serialized.encode("utf-8")) > 4096:
        raise JobError("payload exceeds 4096 bytes")
    return spec, serialized


def _worker_id(value):
    if not isinstance(value, str) or not value.strip():
        raise JobError("worker_id is required")
    normalized = value.strip()
    if len(normalized) > 128:
        raise JobError("worker_id exceeds 128 characters")
    return normalized


def _job_id(value):
    if isinstance(value, bool):
        raise JobError("job_id must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise JobError("job_id must be an integer") from error
    if normalized <= 0:
        raise JobError("job_id must be positive")
    return normalized


def _safe_error(error):
    if isinstance(error, BaseException):
        message = f"{type(error).__name__}: {error}"
    else:
        message = str(error)
    message = _URL_CREDENTIAL_RE.sub(
        lambda match: (
            f"{match.group('scheme')}{match.group('username')}:[REDACTED]@"
        ),
        message,
    )
    message = _QUOTED_PASSWORD_RE.sub(
        lambda match: f"{match.group('label')}[REDACTED]",
        message,
    )
    message = _PASSWORD_RE.sub(
        lambda match: f"{match.group('label')}[REDACTED]",
        message,
    )
    return message[:2000]


def _as_dict(row):
    return dict(row) if row is not None else None


class JobService:
    @staticmethod
    def enqueue(job_type, payload, created_by):
        """Validate a registered job and return its integer job_id."""
        spec, serialized = _normalized_payload(job_type, payload, created_by)
        operator_id = json.loads(serialized)["operator_id"]
        try:
            row = db.session.execute(
                text("""
                    INSERT INTO audit.background_job
                        (job_type, payload, created_by, permission_code)
                    VALUES
                        (:job_type, CAST(:payload AS jsonb), :created_by, :permission_code)
                    RETURNING job_id
                """),
                {
                    "job_type": job_type,
                    "payload": serialized,
                    "created_by": operator_id,
                    "permission_code": spec["permission"],
                },
            ).one()
            db.session.commit()
            return int(row.job_id)
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def recover_stale(stale_after_seconds=900):
        """Requeue or kill abandoned running jobs and return the affected count."""
        if isinstance(stale_after_seconds, bool):
            raise JobError("stale_after_seconds must be a non-negative integer")
        if isinstance(stale_after_seconds, int):
            normalized_stale_after_seconds = stale_after_seconds
        elif isinstance(stale_after_seconds, str) and re.fullmatch(
            r"[+-]?\d+", stale_after_seconds.strip()
        ):
            normalized_stale_after_seconds = int(stale_after_seconds.strip())
        else:
            raise JobError("stale_after_seconds must be a non-negative integer")
        stale_after_seconds = normalized_stale_after_seconds
        if stale_after_seconds < 0:
            raise JobError("stale_after_seconds must be a non-negative integer")

        try:
            result = db.session.execute(
                text("""
                    UPDATE audit.background_job
                    SET status = CASE WHEN attempts >= 3 THEN 'dead' ELSE 'queued' END,
                        available_at = CASE
                            WHEN attempts < 3 THEN now()
                            ELSE available_at
                        END,
                        locked_by = NULL,
                        locked_at = NULL,
                        finished_at = CASE
                            WHEN attempts >= 3 THEN now()
                            ELSE NULL
                        END,
                        updated_at = now()
                    WHERE status = 'running'
                      AND locked_at <= now()
                          - CAST(:stale_after_seconds AS integer) * INTERVAL '1 second'
                """),
                {"stale_after_seconds": stale_after_seconds},
            )
            recovered = result.rowcount
            db.session.commit()
            return recovered
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def claim_next(worker_id, stale_after_seconds=900):
        """Recover stale work, claim one job, commit, and return a dict or None."""
        worker_id = _worker_id(worker_id)
        JobService.recover_stale(stale_after_seconds)
        try:
            row = db.session.execute(
                text("""
                    WITH candidate AS (
                        SELECT job_id
                        FROM audit.background_job
                        WHERE status = 'queued'
                          AND attempts < 3
                          AND available_at <= now()
                        ORDER BY available_at, job_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE audit.background_job AS job
                    SET status = 'running',
                        attempts = job.attempts + 1,
                        locked_by = :worker_id,
                        locked_at = now(),
                        started_at = COALESCE(job.started_at, now()),
                        finished_at = NULL,
                        updated_at = now()
                    FROM candidate
                    WHERE job.job_id = candidate.job_id
                    RETURNING job.*
                """),
                {"worker_id": worker_id},
            ).mappings().one_or_none()
            job = _as_dict(row)
            db.session.commit()
            return job
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def complete(job_id, worker_id, result):
        """Mark the worker-owned running job succeeded and return the stored dict."""
        job_id = _job_id(job_id)
        worker_id = _worker_id(worker_id)
        if not isinstance(result, Mapping):
            raise JobError("result must be an object")
        try:
            serialized = json.dumps(
                dict(result), ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
        except (TypeError, ValueError) as error:
            raise JobError("result must be JSON serializable") from error

        try:
            row = db.session.execute(
                text("""
                    UPDATE audit.background_job
                    SET result = CAST(:result AS jsonb),
                        status = 'succeeded',
                        last_error = NULL,
                        locked_by = NULL,
                        locked_at = NULL,
                        finished_at = now(),
                        updated_at = now()
                    WHERE job_id = :job_id
                      AND status = 'running'
                      AND locked_by = :worker_id
                    RETURNING *
                """),
                {
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "result": serialized,
                },
            ).mappings().one_or_none()
            if row is None:
                db.session.rollback()
                raise JobError("job is not running for this worker")
            job = _as_dict(row)
            db.session.commit()
            return job
        except JobError:
            raise
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def fail(job_id, worker_id, error):
        """Requeue attempts one/two or preserve attempt three as dead."""
        job_id = _job_id(job_id)
        worker_id = _worker_id(worker_id)
        last_error = _safe_error(error)
        try:
            row = db.session.execute(
                text("""
                    UPDATE audit.background_job
                    SET status = CASE WHEN attempts >= 3 THEN 'dead' ELSE 'queued' END,
                        available_at = CASE
                            WHEN attempts < 3 THEN now() + INTERVAL '5 seconds'
                            ELSE available_at
                        END,
                        last_error = :last_error,
                        locked_by = NULL,
                        locked_at = NULL,
                        finished_at = CASE
                            WHEN attempts >= 3 THEN now()
                            ELSE NULL
                        END,
                        updated_at = now()
                    WHERE job_id = :job_id
                      AND status = 'running'
                      AND locked_by = :worker_id
                    RETURNING *
                """),
                {
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "last_error": last_error,
                },
            ).mappings().one_or_none()
            if row is None:
                db.session.rollback()
                raise JobError("job is not running for this worker")
            job = _as_dict(row)
            db.session.commit()
            return job
        except JobError:
            raise
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def get(job_id):
        """Return one job dict or None without changing it."""
        job_id = _job_id(job_id)
        row = db.session.execute(
            text("SELECT * FROM audit.background_job WHERE job_id = :job_id"),
            {"job_id": job_id},
        ).mappings().one_or_none()
        return _as_dict(row)
