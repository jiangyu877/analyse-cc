import io
import re

from sqlalchemy import text

from app.utils import hash_password


def _login(client, initialized_database):
    with initialized_database.cursor() as cur:
        cur.execute("UPDATE auth.account SET password_hash=%s WHERE username='admin'", (hash_password("Admin12345"),))
    initialized_database.commit()
    response = client.post("/login", data={"username": "admin", "password": "Admin12345"}, follow_redirects=False)
    assert response.status_code in (302, 303), response.get_data(as_text=True)


def test_order_payment_refund_inventory_http_loop(initialized_app, initialized_database):
    client = initialized_app.test_client()
    _login(client, initialized_database)
    with initialized_database.cursor() as cur:
        cur.execute("SELECT customer_id FROM biz.customer ORDER BY customer_id LIMIT 1")
        customer_id = cur.fetchone()[0]
        cur.execute("SELECT product_id, stock_qty FROM biz.product WHERE status='active' ORDER BY product_id LIMIT 1")
        product_id, stock_before = cur.fetchone()
    response = client.post("/orders", data={"customer_id": customer_id, "product_id": product_id, "quantity": 1}, follow_redirects=False)
    assert response.status_code in (302, 303)
    with initialized_database.cursor() as cur:
        cur.execute("SELECT order_id, total_amount FROM biz.sales_order WHERE customer_id=%s ORDER BY order_id DESC LIMIT 1", (customer_id,))
        order_id, amount = cur.fetchone()
        cur.execute("SELECT order_item_id FROM biz.order_item WHERE order_id=%s", (order_id,))
        item_id = cur.fetchone()[0]
    assert client.post("/payments", data={"order_id": order_id, "method": "cash"}, follow_redirects=False).status_code in (302, 303)
    with initialized_database.cursor() as cur:
        cur.execute("SELECT payment_id, status, amount FROM biz.payment WHERE order_id=%s", (order_id,))
        payment_id, payment_status, paid_amount = cur.fetchone()
    assert payment_status == "success" and paid_amount == amount
    assert client.post("/refunds", data={"payment_id": payment_id, "order_item_id": item_id, f"quantity_{item_id}": 1, "reason": "customer request"}, follow_redirects=False).status_code in (302, 303)
    with initialized_database.cursor() as cur:
        cur.execute("SELECT refund_id FROM biz.refund WHERE payment_id=%s ORDER BY refund_id DESC LIMIT 1", (payment_id,))
        refund_id = cur.fetchone()[0]
    assert client.post(f"/refunds/{refund_id}/approve", data={"review_note": "ok"}, follow_redirects=False).status_code in (302, 303)
    with initialized_database.cursor() as cur:
        cur.execute("SELECT status FROM biz.sales_order WHERE order_id=%s", (order_id,)); assert cur.fetchone()[0] == "refunded"
        cur.execute("SELECT status FROM biz.refund WHERE refund_id=%s", (refund_id,)); assert cur.fetchone()[0] == "success"
        cur.execute("SELECT quantity FROM biz.refund_item WHERE refund_id=%s", (refund_id,)); assert cur.fetchone()[0] == 1
        cur.execute("SELECT stock_qty FROM biz.product WHERE product_id=%s", (product_id,)); stock_after = cur.fetchone()[0]
        assert stock_after == stock_before
        cur.execute("SELECT flow_type FROM dwd.consumption_flow WHERE order_id=%s", (order_id,)); events = {r[0] for r in cur.fetchall()}
        assert {"payment", "refund"} <= events


def test_queued_model_and_lightweight_qa_http_loops(initialized_app, initialized_database, tmp_path):
    import worker
    from app.extensions import db
    from app.services.jobs import JobService
    client = initialized_app.test_client(); _login(client, initialized_database)
    response = client.post("/algorithms/run/rfm", follow_redirects=False)
    assert response.status_code in (302, 303)
    job_id = int(re.search(r"job_id=(\d+)", response.headers["Location"]).group(1))
    with initialized_app.app_context():
        assert worker.run_once("e2e-worker") is True
        assert client.get(f"/jobs/{job_id}").json["status"] == "succeeded"
        row = db.session.execute(text("SELECT status, result->>'task_id' FROM audit.background_job WHERE job_id=:id"), {"id": job_id}).one(); assert row[0] == "succeeded" and row[1]
        assert db.session.execute(text("SELECT COUNT(*) FROM ml.model_task WHERE status='success'" )).scalar_one() >= 1
        assert db.session.execute(text("SELECT COUNT(*) FROM ml.rfm_result")).scalar_one() >= 1
    assert client.post("/knowledge/bases", data={"base_code":"e2e","name":"E2E KB"}, follow_redirects=False).status_code in (302,303)
    with initialized_database.cursor() as cur:
        cur.execute("SELECT knowledge_base_id FROM kb.knowledge_base WHERE base_code='e2e'"); base_id=cur.fetchone()[0]
    assert client.post("/knowledge/documents", data={"knowledge_base_id":base_id,"title":"Refund policy","document":(io.BytesIO("Refunds are allowed within seven days.".encode()),"policy.txt")}, content_type="multipart/form-data", follow_redirects=False).status_code in (302,303)
    with initialized_database.cursor() as cur:
        cur.execute("SELECT document_id FROM kb.document WHERE knowledge_base_id=%s ORDER BY document_id DESC LIMIT 1",(base_id,)); doc_id=cur.fetchone()[0]
    assert client.post(f"/knowledge/documents/{doc_id}/publish", follow_redirects=False).status_code in (302,303)
    assert client.post("/qa/ask", data={"question":"Refunds are allowed within seven days."}, follow_redirects=False).status_code in (302,303)
    with initialized_database.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM qa.qa_message WHERE message_role='user' AND content ILIKE 'Refunds are allowed within seven days.'"); assert cur.fetchone()[0] >= 1
        cur.execute("SELECT COUNT(*) FROM qa.qa_retrieval_log rl JOIN kb.document_chunk dc ON dc.chunk_id=rl.chunk_id WHERE dc.document_id=%s", (doc_id,)); assert cur.fetchone()[0] >= 1
    assert client.post("/qa/ask", data={"question":"Unrelated quantum spaceship fuel?"}, follow_redirects=False).status_code in (302,303)
    with initialized_database.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM qa.qa_ticket t JOIN qa.qa_message m ON m.message_id=t.source_message_id WHERE t.reason_code IS NOT NULL AND t.status='pending'"); assert cur.fetchone()[0] >= 1
