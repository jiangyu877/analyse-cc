from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy import text

from app.extensions import db
from app.services.algorithms import AlgorithmError, run_churn, run_kmeans, run_rfm
from app.utils import login_required, role_required

algorithms_bp = Blueprint("algorithms", __name__, url_prefix="/algorithms")


@algorithms_bp.get("")
@login_required
def index():
    tasks = db.session.execute(text("""
        SELECT t.task_id, t.task_type, t.status, t.parameters, t.started_at,
               t.finished_at, t.error_message,
               COALESCE(jsonb_object_agg(m.metric_name || ':' || m.dataset, m.metric_value)
                   FILTER (WHERE m.metric_id IS NOT NULL), '{}'::jsonb) AS metrics
        FROM ml.model_task t
        LEFT JOIN ml.model_metric m ON m.task_id = t.task_id
        GROUP BY t.task_id ORDER BY t.started_at DESC LIMIT 100
    """)).mappings().all()
    return render_template(
        "algorithms.html",
        tasks=tasks,
        gradio_public_url=current_app.config["GRADIO_PUBLIC_URL"],
    )


@algorithms_bp.post("/run/<task_type>")
@role_required("admin", "analyst")
def run(task_type):
    try:
        if task_type == "rfm":
            task_id = run_rfm(session["user_id"])
        elif task_type == "kmeans":
            task_id = run_kmeans(session["user_id"], request.form.get("clusters", 4))
        elif task_type == "churn":
            task_id = run_churn(session["user_id"], request.form.get("observation_days", 90))
        else:
            raise AlgorithmError("未知算法类型")
        flash(f"算法任务 {task_id} 已完成", "success")
    except (AlgorithmError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("algorithms.index"))
