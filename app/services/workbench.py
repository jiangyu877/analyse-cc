from sqlalchemy import text

from app.extensions import db


def load_rfm_result(task_id):
    return db.session.execute(text("""
        SELECT r.customer_id, c.customer_no, c.name,
               r.recency_days, r.frequency, r.monetary::float AS monetary,
               r.r_score, r.f_score, r.m_score, r.segment
        FROM ml.rfm_result r
        JOIN biz.customer c ON c.customer_id = r.customer_id
        WHERE r.task_id = :task_id
        ORDER BY r.monetary DESC, r.customer_id
    """), {"task_id": task_id}).mappings().all()


def load_cluster_result(task_id):
    return db.session.execute(text("""
        SELECT cr.customer_id, c.customer_no, c.name, cr.cluster_label,
               cr.distance::float AS distance,
               r.recency_days, r.frequency, r.monetary::float AS monetary
        FROM ml.cluster_result cr
        JOIN ml.model_task task ON task.task_id = cr.task_id
        JOIN biz.customer c ON c.customer_id = cr.customer_id
        JOIN ml.rfm_result r ON r.customer_id = cr.customer_id
          AND r.task_id = COALESCE(
              NULLIF(task.parameters->>'rfm_task_id', '')::bigint,
              (
                  SELECT source.task_id
                  FROM ml.model_task source
                  WHERE source.task_type = 'rfm' AND source.status = 'success'
                    AND source.finished_at <= task.started_at
                  ORDER BY source.finished_at DESC, source.task_id DESC
                  LIMIT 1
              )
          )
        WHERE cr.task_id = :task_id
        ORDER BY cr.cluster_label, r.monetary DESC, cr.customer_id
    """), {"task_id": task_id}).mappings().all()


def load_churn_result(task_id):
    rows = db.session.execute(text("""
        SELECT p.customer_id, c.customer_no, c.name,
               p.churn_probability::float AS churn_probability,
               p.predicted_label, r.recency_days, r.frequency,
               r.monetary::float AS monetary
        FROM ml.churn_prediction p
        JOIN ml.model_task task ON task.task_id = p.task_id
        JOIN biz.customer c ON c.customer_id = p.customer_id
        LEFT JOIN ml.rfm_result r ON r.customer_id = p.customer_id
          AND r.task_id = COALESCE(
              NULLIF(task.parameters->>'rfm_task_id', '')::bigint,
              (
                  SELECT source.task_id
                  FROM ml.model_task source
                  WHERE source.task_type = 'rfm' AND source.status = 'success'
                    AND source.finished_at <= task.started_at
                  ORDER BY source.finished_at DESC, source.task_id DESC
                  LIMIT 1
              )
          )
        WHERE p.task_id = :task_id
        ORDER BY p.churn_probability DESC, p.customer_id
    """), {"task_id": task_id}).mappings().all()
    metric_rows = db.session.execute(text("""
        SELECT metric_name, dataset, metric_value::float AS metric_value
        FROM ml.model_metric
        WHERE task_id = :task_id
        ORDER BY dataset, metric_name
    """), {"task_id": task_id}).mappings().all()
    metrics = {
        f"{row['metric_name']}:{row['dataset']}": row["metric_value"]
        for row in metric_rows
    }
    return rows, metrics
