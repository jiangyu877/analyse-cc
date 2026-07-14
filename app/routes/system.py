from flask import Blueprint, abort, jsonify, redirect, request, session, url_for
from sqlalchemy import text

from app.extensions import db
from app.security.authorization import account_permissions
from app.services.jobs import JobService

system_bp = Blueprint("system", __name__)


@system_bp.get("/healthz")
def health():
    return jsonify(status="ok"), 200


@system_bp.get("/readyz")
def ready():
    try:
        db.session.execute(text("SET LOCAL statement_timeout = '1000ms'"))
        db.session.execute(text("SELECT 1")).scalar_one()
        migrated = db.session.execute(
            text("""
                SELECT EXISTS (
                    SELECT 1
                    FROM audit.schema_migration
                    WHERE version = :version
                )
            """),
            {"version": "008_background_jobs.sql"},
        ).scalar_one()
        db.session.rollback()
        if not migrated:
            return jsonify(status="not_ready"), 503
        return jsonify(status="ready"), 200
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify(status="not_ready"), 503


@system_bp.get("/jobs/<int:job_id>")
def job_status(job_id):
    account_id = session.get("user_id")
    if account_id is None:
        return redirect(url_for("auth.login", next=request.path))

    job = JobService.get(job_id)
    if job is None:
        abort(404)
    if job["permission_code"] not in account_permissions(account_id):
        abort(403)

    response = {
        "job_id": job["job_id"],
        "job_type": job["job_type"],
        "status": job["status"],
        "attempts": job["attempts"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "result": job["result"],
    }
    if job["status"] == "dead":
        response["last_error"] = str(job.get("last_error") or "")[:2000]
    return jsonify(response), 200
