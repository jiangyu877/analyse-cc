import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.services.algorithms import run_churn, run_kmeans, run_rfm


def main():
    app = create_app()
    with app.app_context():
        rfm_task = run_rfm(None)
        cluster_task = run_kmeans(None, 4)
        churn_task = run_churn(None, 90)
        rows = db.session.execute(text("""
            SELECT t.task_id, t.task_type, t.status,
                   COALESCE(jsonb_object_agg(m.metric_name, m.metric_value)
                       FILTER (WHERE m.metric_id IS NOT NULL), '{}'::jsonb) AS metrics
            FROM ml.model_task t
            LEFT JOIN ml.model_metric m ON m.task_id = t.task_id
            WHERE t.task_id IN (:rfm, :cluster, :churn)
            GROUP BY t.task_id ORDER BY t.task_id
        """), {"rfm": rfm_task, "cluster": cluster_task, "churn": churn_task}).mappings().all()
        for row in rows:
            print(dict(row))


if __name__ == "__main__":
    main()
