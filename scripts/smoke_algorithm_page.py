import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from app.extensions import db
from app.routes.algorithms import _load_result


def main():
    app = create_app()
    with app.app_context():
        tasks = db.session.execute(text("""
            SELECT DISTINCT ON (t.task_type)
                   t.task_id, t.task_type, t.status, t.parameters,
                   t.started_at, t.finished_at, t.error_message,
                   COALESCE(jsonb_object_agg(
                       m.metric_name || ':' || m.dataset, m.metric_value
                   ) FILTER (WHERE m.metric_id IS NOT NULL), '{}'::jsonb) AS metrics
            FROM ml.model_task t
            LEFT JOIN ml.model_metric m ON m.task_id = t.task_id
            WHERE t.status = 'success'
            GROUP BY t.task_id
            ORDER BY t.task_type, t.finished_at DESC
        """)).mappings().all()
        for task in tasks:
            result = _load_result(task)
            assert result and result["rows"]
            assert len(result["chart"]["labels"]) == len(result["chart"]["values"])
            print(f"{task['task_type']}: task={task['task_id']} rows={len(result['rows'])}")

    with app.test_client() as client:
        with client.session_transaction() as user_session:
            user_session["user_id"] = 1
            user_session["username"] = "smoke"
            user_session["role"] = "admin"
        for task in tasks:
            response = client.get(f"/algorithms?task_id={task['task_id']}")
            assert response.status_code == 200
            assert b"algorithmResultChart" in response.data
            print(
                f"page-{task['task_type']}: status={response.status_code} "
                f"bytes={len(response.data)}"
            )


if __name__ == "__main__":
    main()
