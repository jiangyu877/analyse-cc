import json

import numpy as np
from sqlalchemy import text

from app.extensions import db


class AlgorithmError(ValueError):
    pass


def _create_task(task_type, operator_id, parameters=None):
    task_id = db.session.execute(text("""
        INSERT INTO ml.model_task (task_type, parameters, created_by)
        VALUES (:task_type, CAST(:parameters AS jsonb), :created_by)
        RETURNING task_id
    """), {
        "task_type": task_type,
        "parameters": json.dumps(parameters or {}),
        "created_by": operator_id,
    }).scalar_one()
    db.session.commit()
    return task_id


def _finish(task_id, status="success", error=None):
    db.session.execute(text("""
        UPDATE ml.model_task
        SET status = :status, finished_at = now(), error_message = :error
        WHERE task_id = :task_id
    """), {"task_id": task_id, "status": status, "error": error})
    db.session.commit()


def _metric(task_id, name, value, dataset="all"):
    db.session.execute(text("""
        INSERT INTO ml.model_metric (task_id, metric_name, metric_value, dataset)
        VALUES (:task_id, :name, :value, :dataset)
        ON CONFLICT (task_id, metric_name, dataset)
        DO UPDATE SET metric_value = EXCLUDED.metric_value
    """), {"task_id": task_id, "name": name, "value": float(value), "dataset": dataset})


def _latest_rfm_task_id():
    return db.session.execute(text("""
        SELECT task_id
        FROM ml.model_task
        WHERE task_type = 'rfm' AND status = 'success'
        ORDER BY finished_at DESC, task_id DESC
        LIMIT 1
    """)).scalar_one_or_none()


def run_rfm(operator_id):
    task_id = _create_task("rfm", operator_id)
    try:
        db.session.execute(text("""
            WITH raw AS (
                SELECT c.customer_id,
                       GREATEST(0, CURRENT_DATE - COALESCE(
                           (MAX(f.occurred_at) FILTER (WHERE f.flow_type = 'payment'))::date,
                           c.registered_at::date))::int AS recency_days,
                       COUNT(DISTINCT f.order_id) FILTER (WHERE f.flow_type = 'payment')::int AS frequency,
                       COALESCE(SUM(f.net_amount), 0)::numeric(16,2) AS monetary
                FROM biz.customer c
                LEFT JOIN dwd.consumption_flow f ON f.customer_id = c.customer_id
                GROUP BY c.customer_id, c.registered_at
            ), scored AS (
                SELECT raw.*,
                       (6 - ntile(5) OVER (ORDER BY recency_days ASC))::smallint AS r_score,
                       ntile(5) OVER (ORDER BY frequency ASC)::smallint AS f_score,
                       ntile(5) OVER (ORDER BY monetary ASC)::smallint AS m_score
                FROM raw
            ), segmented AS (
                SELECT scored.*,
                       CASE WHEN r_score >= 4 AND f_score >= 4 THEN '高价值客户'
                            WHEN r_score >= 4 THEN '新近客户'
                            WHEN f_score >= 4 AND m_score >= 4 THEN '重要保持客户'
                            WHEN r_score <= 2 THEN '流失预警客户'
                            ELSE '一般客户' END AS segment
                FROM scored
            )
            INSERT INTO ml.rfm_result
                (task_id, customer_id, recency_days, frequency, monetary,
                 r_score, f_score, m_score, segment)
            SELECT :task_id, customer_id, recency_days, frequency, monetary,
                   r_score, f_score, m_score, segment FROM segmented
        """), {"task_id": task_id})
        count = db.session.execute(text(
            "SELECT COUNT(*) FROM ml.rfm_result WHERE task_id = :task_id"
        ), {"task_id": task_id}).scalar_one()
        db.session.execute(text("""
            INSERT INTO ads.customer_rfm
                (customer_id, recency_days, frequency, monetary, r_score, f_score,
                 m_score, segment, task_id, calculated_at)
            SELECT customer_id, recency_days, frequency, monetary, r_score, f_score,
                   m_score, segment, task_id, now()
            FROM ml.rfm_result WHERE task_id = :task_id
            ON CONFLICT (customer_id) DO UPDATE SET
                recency_days = EXCLUDED.recency_days, frequency = EXCLUDED.frequency,
                monetary = EXCLUDED.monetary, r_score = EXCLUDED.r_score,
                f_score = EXCLUDED.f_score, m_score = EXCLUDED.m_score,
                segment = EXCLUDED.segment, task_id = EXCLUDED.task_id,
                calculated_at = EXCLUDED.calculated_at
        """), {"task_id": task_id})
        _metric(task_id, "customer_count", count)
        db.session.commit()
        _finish(task_id)
        return task_id
    except Exception as exc:
        db.session.rollback()
        _finish(task_id, "failed", str(exc)[:2000])
        raise


def run_kmeans(operator_id, clusters=4):
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    clusters = max(2, min(int(clusters), 8))
    rfm_task_id = _latest_rfm_task_id()
    task_id = _create_task(
        "kmeans", operator_id, {"clusters": clusters, "rfm_task_id": rfm_task_id}
    )
    try:
        if rfm_task_id is None:
            raise AlgorithmError("没有可用的 RFM 快照，请先运行 RFM")
        rows = db.session.execute(text("""
            SELECT customer_id, recency_days, frequency, monetary
            FROM ml.rfm_result
            WHERE task_id = :rfm_task_id
            ORDER BY customer_id
        """), {"rfm_task_id": rfm_task_id}).mappings().all()
        if len(rows) < clusters:
            raise AlgorithmError(f"至少需要 {clusters} 条 RFM 结果，请先运行 RFM 或减少分群数")
        matrix = np.array([
            [row["recency_days"], row["frequency"], float(row["monetary"])] for row in rows
        ], dtype=float)
        scaled = StandardScaler().fit_transform(matrix)
        model = KMeans(n_clusters=clusters, random_state=42, n_init=20)
        labels = model.fit_predict(scaled)
        distances = np.min(model.transform(scaled), axis=1)
        db.session.execute(text("""
            INSERT INTO ml.cluster_result (task_id, customer_id, cluster_label, distance)
            VALUES (:task_id, :customer_id, :cluster_label, :distance)
        """), [
            {"task_id": task_id, "customer_id": row["customer_id"],
             "cluster_label": int(label), "distance": float(distance)}
            for row, label, distance in zip(rows, labels, distances)
        ])
        _metric(task_id, "inertia", model.inertia_)
        _metric(task_id, "customer_count", len(rows))
        if len(rows) > clusters:
            _metric(task_id, "silhouette", silhouette_score(scaled, labels))
        db.session.commit()
        _finish(task_id)
        return task_id
    except Exception as exc:
        db.session.rollback()
        _finish(task_id, "failed", str(exc)[:2000])
        raise


def run_churn(operator_id, observation_days=90):
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import f1_score, roc_auc_score
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline

    observation_days = max(30, min(int(observation_days), 180))
    rfm_task_id = _latest_rfm_task_id()
    task_id = _create_task(
        "churn",
        operator_id,
        {"observation_days": observation_days, "rfm_task_id": rfm_task_id},
    )
    try:
        if rfm_task_id is None:
            raise AlgorithmError("没有可用的 RFM 快照，请先运行 RFM")
        training = db.session.execute(text("""
            WITH cutoff AS (SELECT CURRENT_DATE - (:days || ' days')::interval AS dt)
            SELECT c.customer_id,
                   EXTRACT(day FROM (cutoff.dt - COALESCE(MAX(f.occurred_at)
                       FILTER (WHERE f.flow_type = 'payment' AND f.occurred_at < cutoff.dt), c.registered_at)))::float AS recency,
                   COUNT(DISTINCT f.order_id) FILTER
                       (WHERE f.flow_type = 'payment' AND f.occurred_at < cutoff.dt)::float AS frequency,
                   COALESCE(SUM(f.net_amount) FILTER (WHERE f.occurred_at < cutoff.dt), 0)::float AS monetary,
                   (COUNT(f.flow_id) FILTER
                       (WHERE f.flow_type = 'payment' AND f.occurred_at >= cutoff.dt) = 0)::int AS churned
            FROM biz.customer c CROSS JOIN cutoff
            LEFT JOIN dwd.consumption_flow f ON f.customer_id = c.customer_id
            WHERE c.registered_at < cutoff.dt
            GROUP BY c.customer_id, c.registered_at, cutoff.dt
        """), {"days": observation_days}).mappings().all()
        labels = np.array([row["churned"] for row in training], dtype=int)
        if len(training) < 10 or len(np.unique(labels)) < 2:
            raise AlgorithmError("流失模型至少需要 10 个历史客户，且观察期内需同时存在流失与留存标签")
        features = np.array([[row["recency"], row["frequency"], row["monetary"]] for row in training])
        model = make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", random_state=42))
        model.fit(features, labels)
        train_prob = model.predict_proba(features)[:, 1]
        train_pred = train_prob >= 0.5
        _metric(task_id, "auc", roc_auc_score(labels, train_prob), "training")
        _metric(task_id, "f1", f1_score(labels, train_pred), "training")
        _metric(task_id, "sample_count", len(training), "training")

        current = db.session.execute(text("""
            SELECT customer_id, recency_days::float AS recency,
                    frequency::float AS frequency, monetary::float AS monetary
            FROM ml.rfm_result
            WHERE task_id = :rfm_task_id
            ORDER BY customer_id
        """), {"rfm_task_id": rfm_task_id}).mappings().all()
        if not current:
            raise AlgorithmError("没有当前 RFM 特征，请先运行 RFM")
        current_features = np.array([[row["recency"], row["frequency"], row["monetary"]] for row in current])
        probabilities = model.predict_proba(current_features)[:, 1]
        db.session.execute(text("""
            INSERT INTO ml.churn_prediction
                (task_id, customer_id, churn_probability, predicted_label)
            VALUES (:task_id, :customer_id, :probability, :label)
        """), [
            {"task_id": task_id, "customer_id": row["customer_id"],
             "probability": float(probability), "label": bool(probability >= 0.5)}
            for row, probability in zip(current, probabilities)
        ])
        db.session.commit()
        _finish(task_id)
        return task_id
    except Exception as exc:
        db.session.rollback()
        _finish(task_id, "failed", str(exc)[:2000])
        raise
