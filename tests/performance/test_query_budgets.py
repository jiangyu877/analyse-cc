import io
import time

from flask import session
from sqlalchemy import text

from app.services.knowledge import KnowledgeService
from app.utils import hash_password


def _admin_client(initialized_app, initialized_database):
    with initialized_database.cursor() as cursor:
        cursor.execute(
            "SELECT username, account_id FROM auth.account WHERE role = 'admin' LIMIT 1"
        )
        username, account_id = cursor.fetchone()
        cursor.execute(
            "UPDATE auth.account SET password_hash = %s, is_active = true WHERE account_id = %s",
            (hash_password("Admin12345"), account_id),
        )
    initialized_database.commit()
    client = initialized_app.test_client()
    response = client.post(
        "/login",
        data={"username": username, "password": "Admin12345"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    return client, account_id


def _prepare_qa(initialized_app, initialized_database, tmp_path, account_id):
    initialized_app.config["KNOWLEDGE_UPLOAD_DIR"] = str(tmp_path / "knowledge")
    with initialized_app.test_request_context("/knowledge"):
        session["user_id"] = account_id
        base_id = KnowledgeService.create_base(
            "perfqa", "Performance QA", "", account_id
        )
        document_id = KnowledgeService.ingest(
            base_id,
            "Refund policy",
            "perf-qa.txt",
            b"Refunds are processed within seven days.",
            account_id,
        )
        KnowledgeService.publish(document_id, account_id)
    return "Refunds are processed within seven days."


def test_sql_lab_row_and_timeout_budgets(initialized_app, initialized_database):
    client, _account_id = _admin_client(initialized_app, initialized_database)
    initialized_app.config.update(SQL_QUERY_MAX_ROWS=20, SQL_QUERY_TIMEOUT_MS=200)
    response = client.post(
        "/sql-lab/execute", json={"sql": "SELECT generate_series(1,100) AS n"}
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["row_count"] == 20
    assert payload["truncated"] is True

    started = time.perf_counter()
    response = client.post("/sql-lab/execute", json={"sql": "SELECT pg_sleep(1)"})
    assert time.perf_counter() - started < 1.5
    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_report_pages_respond_within_budget(initialized_app, initialized_database):
    client, _account_id = _admin_client(initialized_app, initialized_database)
    for path in ("/reports", "/customers"):
        client.get(path)
        durations = []
        for _ in range(3):
            started = time.perf_counter()
            response = client.get(path)
            durations.append(time.perf_counter() - started)
            assert response.status_code == 200
        assert max(durations) < 2


def test_keyword_qa_request_stays_within_budget(
    initialized_app, initialized_database, tmp_path
):
    client, account_id = _admin_client(initialized_app, initialized_database)
    question = _prepare_qa(initialized_app, initialized_database, tmp_path, account_id)

    warmup = client.post("/qa/ask", data={"question": question}, follow_redirects=False)
    assert warmup.status_code == 302
    durations = []
    for _ in range(3):
        started = time.perf_counter()
        response = client.post("/qa/ask", data={"question": question}, follow_redirects=False)
        durations.append(time.perf_counter() - started)
        assert response.status_code == 302
        assert "/qa?session_id=" in response.headers["Location"]

    with initialized_database.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM qa.qa_retrieval_log retrieval "
            "JOIN qa.qa_message message ON message.message_id = retrieval.message_id "
            "WHERE message.message_role = 'assistant'"
        )
        assert cursor.fetchone()[0] >= 1
    assert max(durations) < 2
