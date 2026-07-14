from sqlalchemy import text
from app.utils import hash_password
import io


def _login(client, username="admin", password="admin"):
    return client.post("/login", data={"username": username, "password": password}, follow_redirects=False)


def test_login_rejects_external_next_and_disabled_account(initialized_app, initialized_database):
    client = initialized_app.test_client()
    with initialized_database.cursor() as cur:
        cur.execute("SELECT username, account_id FROM auth.account WHERE role='admin' LIMIT 1")
        username, account_id = cur.fetchone()
        cur.execute("UPDATE auth.account SET password_hash=%s WHERE account_id=%s", (hash_password("Admin12345"), account_id))
    initialized_database.commit()
    response = client.post("/login?next=//evil.example", data={"username": username, "password": "Admin12345"})
    assert "evil.example" not in response.headers.get("Location", "")
    with initialized_database.cursor() as cur:
        cur.execute("UPDATE auth.account SET is_active=false WHERE account_id=%s", (account_id,))
    initialized_database.commit()
    client.post("/logout")
    disabled = client.post("/login", data={"username": username, "password": "Admin12345"})
    assert disabled.status_code == 403
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_csrf_and_upload_limits(initialized_app):
    initialized_app.config.update(WTF_CSRF_ENABLED=True)
    response = initialized_app.test_client().post("/payments", data={})
    assert response.status_code == 400


def test_sql_lab_blocks_mutations_and_limits(initialized_app, initialized_database):
    with initialized_database.cursor() as cur:
        cur.execute("SELECT username, account_id FROM auth.account WHERE role='admin' LIMIT 1")
        username, account_id = cur.fetchone()
        cur.execute("UPDATE auth.account SET password_hash=%s WHERE account_id=%s", (hash_password("Admin12345"), account_id))
    initialized_database.commit()
    client = initialized_app.test_client()
    assert _login(client, username, "Admin12345").status_code == 302
    for query in ("INSERT INTO biz.customer (name) VALUES ('x')", "UPDATE biz.customer SET name='x'", "DELETE FROM biz.customer", "SELECT 1; SELECT 2"):
        response = client.post("/sql-lab/execute", json={"sql": query})
        assert response.status_code == 200
        assert response.get_json()["success"] is False


def test_multipart_upload_rejects_unsafe_files(initialized_app, initialized_database):
    client = initialized_app.test_client()
    with initialized_database.cursor() as cur:
        cur.execute("SELECT username, account_id FROM auth.account WHERE role='admin' LIMIT 1")
        username, account_id = cur.fetchone()
        cur.execute("UPDATE auth.account SET password_hash=%s WHERE account_id=%s", (hash_password("Admin12345"), account_id))
    initialized_database.commit()
    assert _login(client, username, "Admin12345").status_code == 302
    client.post("/knowledge/bases", data={"base_code":"SEC", "name":"Security"})
    with initialized_database.cursor() as cur:
        cur.execute("SELECT knowledge_base_id FROM kb.knowledge_base ORDER BY knowledge_base_id DESC LIMIT 1")
        base_id = cur.fetchone()[0]
    bad = [("../escape.txt", b"safe"), ("bad.exe", b"MZ"), ("note.txt", b"MZ fake"), ("bad.docx", b"not zip")]
    for filename, content in bad:
        response = client.post("/knowledge/documents", data={"knowledge_base_id": str(base_id), "title": filename, "document": (io.BytesIO(content), filename)}, content_type="multipart/form-data")
        assert response.status_code == 302
    oversized = b"x" * (initialized_app.config["MAX_UPLOAD_MB"] * 1024 * 1024 + 1)
    response = client.post("/knowledge/documents", data={"knowledge_base_id": str(base_id), "title": "big.txt", "document": (io.BytesIO(oversized), "big.txt")}, content_type="multipart/form-data")
    assert response.status_code in (400, 413, 302)
    with initialized_database.cursor() as cur:
        cur.execute("SELECT count(*) FROM kb.document WHERE knowledge_base_id=%s AND status='ready'", (base_id,))
        assert cur.fetchone()[0] == 0
