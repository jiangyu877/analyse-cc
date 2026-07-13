import json
from pathlib import Path

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]


def _initialize(connection):
    from scripts.init_db import apply_migrations

    with connection.cursor() as cursor:
        cursor.execute((ROOT / "database" / "v2_schema.sql").read_text(encoding="utf-8"))
        cursor.execute((ROOT / "database" / "v2_seed.sql").read_text(encoding="utf-8"))
    connection.commit()
    apply_migrations(connection)


def test_old_cluster_and_churn_results_keep_their_rfm_snapshot(
    isolated_database, isolated_app
):
    from app.extensions import db
    from app.routes.algorithms import _churn_result, _kmeans_result
    from app.services.workbench import load_churn_result, load_cluster_result

    _initialize(isolated_database)
    with isolated_app.app_context():
        customer_id = db.session.execute(text(
            "SELECT customer_id FROM biz.customer ORDER BY customer_id LIMIT 1"
        )).scalar_one()
        source_task_id = db.session.execute(text("""
            INSERT INTO ml.model_task (task_type, status, finished_at)
            VALUES ('rfm', 'success', now() - interval '2 hours')
            RETURNING task_id
        """)).scalar_one()
        db.session.execute(text("""
            INSERT INTO ml.rfm_result
                (task_id, customer_id, recency_days, frequency, monetary,
                 r_score, f_score, m_score, segment)
            VALUES (:task_id, :customer_id, 10, 2, 100.00, 4, 3, 3, 'snapshot')
        """), {"task_id": source_task_id, "customer_id": customer_id})
        cluster_task_id = db.session.execute(text("""
            INSERT INTO ml.model_task (task_type, status, parameters, finished_at)
            VALUES ('kmeans', 'success', CAST(:parameters AS jsonb), now() - interval '1 hour')
            RETURNING task_id
        """), {
            "parameters": json.dumps({"clusters": 2, "rfm_task_id": source_task_id})
        }).scalar_one()
        churn_task_id = db.session.execute(text("""
            INSERT INTO ml.model_task (task_type, status, parameters, finished_at)
            VALUES ('churn', 'success', CAST(:parameters AS jsonb), now() - interval '1 hour')
            RETURNING task_id
        """), {
            "parameters": json.dumps({"observation_days": 90, "rfm_task_id": source_task_id})
        }).scalar_one()
        db.session.execute(text("""
            INSERT INTO ml.cluster_result (task_id, customer_id, cluster_label, distance)
            VALUES (:task_id, :customer_id, 1, 0.25)
        """), {"task_id": cluster_task_id, "customer_id": customer_id})
        db.session.execute(text("""
            INSERT INTO ml.churn_prediction
                (task_id, customer_id, churn_probability, predicted_label)
            VALUES (:task_id, :customer_id, 0.75, TRUE)
        """), {"task_id": churn_task_id, "customer_id": customer_id})

        latest_task_id = db.session.execute(text("""
            INSERT INTO ml.model_task (task_type, status, finished_at)
            VALUES ('rfm', 'success', now()) RETURNING task_id
        """)).scalar_one()
        db.session.execute(text("""
            INSERT INTO ml.rfm_result
                (task_id, customer_id, recency_days, frequency, monetary,
                 r_score, f_score, m_score, segment)
            VALUES (:task_id, :customer_id, 999, 99, 9999.00, 1, 1, 1, 'latest')
        """), {"task_id": latest_task_id, "customer_id": customer_id})
        db.session.execute(text("""
            INSERT INTO ads.customer_rfm
                (customer_id, recency_days, frequency, monetary,
                 r_score, f_score, m_score, segment, task_id)
            VALUES (:customer_id, 999, 99, 9999.00, 1, 1, 1, 'latest', :task_id)
        """), {"customer_id": customer_id, "task_id": latest_task_id})
        db.session.commit()

        cluster_row = load_cluster_result(cluster_task_id)[0]
        churn_row = load_churn_result(churn_task_id)[0][0]
        flask_cluster_row = _kmeans_result(cluster_task_id, {})["rows"][0]
        flask_churn_row = _churn_result(churn_task_id, {})["top_customers"][0]

    assert (
        cluster_row["recency_days"],
        cluster_row["frequency"],
        cluster_row["monetary"],
    ) == (10, 2, 100.0)
    assert (
        churn_row["recency_days"],
        churn_row["frequency"],
        churn_row["monetary"],
    ) == (10, 2, 100.0)
    assert (
        flask_cluster_row["avg_recency"],
        flask_cluster_row["avg_frequency"],
        flask_cluster_row["avg_monetary"],
    ) == (10.0, 2.0, 100.0)
    assert (
        flask_churn_row["recency_days"],
        flask_churn_row["frequency"],
        flask_churn_row["monetary"],
    ) == (10, 2, 100.0)
