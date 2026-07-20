import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg2
import pytest
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[2]
ROLE_CODES = {
    "super_admin",
    "system_admin",
    "customer_operator",
    "product_operator",
    "order_operator",
    "finance_auditor",
    "data_analyst",
    "model_operator",
    "knowledge_admin",
    "qa_operator",
}
EXPECTED_ROLE_PERMISSIONS = {
    "super_admin": {"system.manage", "customer.read", "refund.approve", "qa.handle"},
    "customer_operator": {"customer.read", "customer.write"},
    "finance_auditor": {"payment.read", "refund.approve"},
    "model_operator": {"model.read", "model.run"},
    "knowledge_admin": {"knowledge.read", "knowledge.write", "knowledge.publish"},
    "qa_operator": {"qa.read", "qa.handle"},
}


def _apply_base(connection):
    with connection.cursor() as cursor:
        cursor.execute((ROOT / "database" / "v2_schema.sql").read_text(encoding="utf-8"))
        cursor.execute((ROOT / "database" / "v2_seed.sql").read_text(encoding="utf-8"))
    connection.commit()


def test_migrations_are_idempotent_and_seed_documented_roles(isolated_database):
    from scripts import init_db

    assert hasattr(init_db, "apply_migrations"), "apply_migrations is required"
    migrations_dir = ROOT / "database" / "migrations"
    assert migrations_dir.exists(), "database/migrations is required"

    _apply_base(isolated_database)
    init_db.apply_migrations(isolated_database, migrations_dir)
    init_db.apply_migrations(isolated_database, migrations_dir)

    with isolated_database.cursor() as cursor:
        cursor.execute("SELECT version, checksum FROM audit.schema_migration ORDER BY version")
        migrations = cursor.fetchall()
        cursor.execute("SELECT role_code FROM auth.role")
        roles = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT r.role_code, p.permission_code
            FROM auth.role r
            JOIN auth.role_permission rp ON rp.role_id = r.role_id
            JOIN auth.permission p ON p.permission_id = rp.permission_id
            WHERE r.role_code = ANY(%s)
            """,
            (list(EXPECTED_ROLE_PERMISSIONS),),
        )
        permission_rows = cursor.fetchall()

    assert [row[0] for row in migrations] == [
        "001_rbac_and_audit.sql",
        "002_inventory_and_refund_workflow.sql",
        "003_release_a_hardening.sql",
        "004_knowledge_and_embeddings.sql",
        "005_qa_and_tickets.sql",
        "006_ads_results.sql",
            "007_model_registry_and_results.sql",
            "008_background_jobs.sql",
            "009_knowledge_publish_guard.sql",
            "010_import_preflight_and_data_maintenance.sql",
        ]
    for version, checksum in migrations:
        assert checksum == hashlib.sha256((migrations_dir / version).read_bytes()).hexdigest()
    assert roles == ROLE_CODES
    actual_permissions = {
        role_code: {
            permission
            for row_role_code, permission in permission_rows
            if row_role_code == role_code
        }
        for role_code in EXPECTED_ROLE_PERMISSIONS
    }
    for role_code, expected in EXPECTED_ROLE_PERMISSIONS.items():
        assert expected <= actual_permissions[role_code]

    with isolated_database.cursor() as cursor:
        cursor.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'ods' AND table_name = 'import_batch'
        """)
        import_batch_columns = {row[0] for row in cursor.fetchall()}
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'ods'
              AND table_name IN ('import_stage_row', 'import_row_issue')
        """)
        import_tables = {row[0] for row in cursor.fetchall()}

    assert {
        "file_sha256",
        "file_size",
        "input_row_count",
        "valid_row_count",
        "invalid_row_count",
        "mapping_json",
        "confirmed_at",
        "created_by",
    } <= import_batch_columns
    assert import_tables == {"import_stage_row", "import_row_issue"}


def test_migration_backfills_every_existing_legacy_role_account(isolated_database):
    from scripts import init_db

    _apply_base(isolated_database)
    with isolated_database.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO auth.account (username, password_hash, full_name, role)
            VALUES (%s, 'legacy-hash', %s, %s)
            """,
            [
                ("alice-admin", "Alice Admin", "admin"),
                ("oliver-operator", "Oliver Operator", "operator"),
                ("amy-analyst", "Amy Analyst", "analyst"),
            ],
        )
    isolated_database.commit()

    init_db.apply_migrations(isolated_database)

    with isolated_database.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.username, array_agg(r.role_code ORDER BY r.role_code)
            FROM auth.account a
            JOIN auth.account_role ar ON ar.account_id = a.account_id
            JOIN auth.role r ON r.role_id = ar.role_id
            WHERE a.username IN ('alice-admin', 'oliver-operator', 'amy-analyst')
            GROUP BY a.username
            """
        )
        assignments = {username: set(roles) for username, roles in cursor.fetchall()}

    assert assignments == {
        "alice-admin": {"system_admin"},
        "oliver-operator": {
            "customer_operator",
            "order_operator",
            "product_operator",
        },
        "amy-analyst": {"data_analyst", "model_operator"},
    }


def test_migration_checksum_change_is_rejected(isolated_database, tmp_path):
    from scripts import init_db

    assert hasattr(init_db, "apply_migrations"), "apply_migrations is required"
    _apply_base(isolated_database)
    migration = tmp_path / "001_probe.sql"
    migration.write_text("CREATE TABLE audit.probe(id integer);", encoding="utf-8")
    init_db.apply_migrations(isolated_database, tmp_path)
    migration.write_text("CREATE TABLE audit.changed_probe(id integer);", encoding="utf-8")

    with pytest.raises(RuntimeError, match="checksum"):
        init_db.apply_migrations(isolated_database, tmp_path)


@pytest.mark.parametrize("mutation", ["delete", "rename"])
def test_applied_migration_file_cannot_disappear_or_be_renamed(
    isolated_database, tmp_path, mutation
):
    from scripts import init_db

    _apply_base(isolated_database)
    migration = tmp_path / "001_initial.sql"
    migration.write_text("CREATE TABLE audit.migration_probe(id integer);", encoding="utf-8")
    init_db.apply_migrations(isolated_database, tmp_path)
    if mutation == "delete":
        migration.unlink()
    else:
        migration.rename(tmp_path / "001_renamed.sql")

    with pytest.raises(RuntimeError, match="missing"):
        init_db.apply_migrations(isolated_database, tmp_path)


def test_new_migration_cannot_sort_before_an_applied_version(isolated_database, tmp_path):
    from scripts import init_db

    _apply_base(isolated_database)
    (tmp_path / "002_second.sql").write_text(
        "CREATE TABLE audit.second_probe(id integer);", encoding="utf-8"
    )
    init_db.apply_migrations(isolated_database, tmp_path)
    (tmp_path / "001_late.sql").write_text(
        "CREATE TABLE audit.late_probe(id integer);", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="out of order"):
        init_db.apply_migrations(isolated_database, tmp_path)


def test_concurrent_migration_runners_are_serialized(isolated_database, tmp_path):
    from scripts import init_db
    from app.config import Config

    _apply_base(isolated_database)
    (tmp_path / "001_slow.sql").write_text(
        "SELECT pg_sleep(0.25); CREATE TABLE audit.concurrent_probe(id integer);",
        encoding="utf-8",
    )

    def run_migrations():
        url = make_url(os.environ.get("TEST_DATABASE_URL") or Config.SQLALCHEMY_DATABASE_URI)
        connection = psycopg2.connect(
            host=url.host,
            port=url.port or 5432,
            dbname=isolated_database.info.dbname,
            user=url.username,
            password=url.password,
        )
        try:
            init_db.apply_migrations(connection, tmp_path)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run_migrations) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)

    with isolated_database.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM audit.schema_migration WHERE version = '001_slow.sql'"
        )
        assert cursor.fetchone()[0] == 1


def test_permission_check_leaves_session_available_for_write_transaction(
    isolated_database, isolated_app
):
    from app.extensions import db
    from app.security.authorization import permission_required
    from scripts.init_db import apply_migrations
    from sqlalchemy import text

    _apply_base(isolated_database)
    apply_migrations(isolated_database)

    @isolated_app.post("/permission-transaction-probe")
    @permission_required("customer.write")
    def permission_transaction_probe():
        with db.session.begin():
            db.session.execute(text("SELECT 1"))
        return "ok"

    with isolated_database.cursor() as cursor:
        cursor.execute("SELECT account_id FROM auth.account WHERE username = 'admin'")
        admin_id = cursor.fetchone()[0]

    client = isolated_app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = admin_id
    response = client.post("/permission-transaction-probe")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "ok"


def _seed_role_account(connection, username, legacy_role, role_code):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO auth.account (username, password_hash, full_name, role)
            VALUES (%s, %s, %s, %s)
            RETURNING account_id
            """,
            (username, "not-used-by-route-tests", username, legacy_role),
        )
        account_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO auth.account_role (account_id, role_id, is_primary)
            SELECT %s, role_id, TRUE FROM auth.role WHERE role_code = %s
            """,
            (account_id, role_code),
        )
    connection.commit()
    return account_id


def test_system_admin_cannot_manage_super_admin_but_can_create_lower_role(
    isolated_database, isolated_app
):
    from scripts.init_db import apply_migrations

    _apply_base(isolated_database)
    apply_migrations(isolated_database)
    system_admin_id = _seed_role_account(
        isolated_database, "system-manager", "admin", "system_admin"
    )

    with isolated_database.cursor() as cursor:
        cursor.execute(
            "SELECT account_id, password_hash, is_active FROM auth.account WHERE username = 'admin'"
        )
        super_admin_id, original_hash, original_active = cursor.fetchone()

    client = isolated_app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = system_admin_id
        flask_session["username"] = "system-manager"
        flask_session["role"] = "admin"

    response = client.post(
        "/admin/accounts",
        data={
            "username": "elevated-account",
            "full_name": "Escalation Probe",
            "password": "SecurePass123",
            "role_codes": "super_admin",
        },
    )
    assert response.status_code == 302

    response = client.post(
        f"/admin/accounts/{super_admin_id}/password",
        data={"password": "ChangedPass123"},
    )
    assert response.status_code == 302
    response = client.post(f"/admin/accounts/{super_admin_id}/toggle")
    assert response.status_code == 302

    response = client.post(
        "/admin/accounts",
        data={
            "username": "customer-agent",
            "full_name": "Customer Agent",
            "password": "SecurePass123",
            "role_codes": "customer_operator",
        },
    )
    assert response.status_code == 302

    with isolated_database.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM auth.account WHERE username = 'elevated-account'"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT password_hash, is_active FROM auth.account WHERE account_id = %s",
            (super_admin_id,),
        )
        assert cursor.fetchone() == (original_hash, original_active)
        cursor.execute(
            """
            SELECT r.role_code
            FROM auth.account a
            JOIN auth.account_role ar ON ar.account_id = a.account_id
            JOIN auth.role r ON r.role_id = ar.role_id
            WHERE a.username = 'customer-agent'
            """
        )
        assert cursor.fetchone()[0] == "customer_operator"


@pytest.mark.parametrize("account_state", ["disabled", "no_role"])
def test_dashboard_denies_existing_sessions_without_active_permissions(
    isolated_database, isolated_app, account_state
):
    from scripts.init_db import apply_migrations

    _apply_base(isolated_database)
    apply_migrations(isolated_database)
    with isolated_database.cursor() as cursor:
        cursor.execute("SELECT account_id FROM auth.account WHERE username = 'analyst'")
        account_id = cursor.fetchone()[0]
        if account_state == "disabled":
            cursor.execute(
                "UPDATE auth.account SET is_active = FALSE WHERE account_id = %s",
                (account_id,),
            )
        else:
            cursor.execute(
                "DELETE FROM auth.account_role WHERE account_id = %s", (account_id,)
            )
    isolated_database.commit()

    client = isolated_app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = account_id
        flask_session["username"] = "analyst"
        flask_session["role"] = "analyst"

    protected_paths = (
        "/", "/customers", "/products", "/orders", "/payments", "/refunds",
        "/algorithms", "/imports", "/sql-lab", "/admin/accounts", "/admin/logs",
    )
    assert {
        path: client.get(path).status_code for path in protected_paths
    } == {path: 403 for path in protected_paths}
    assert client.get("/healthz").status_code == 200


def test_duplicate_account_error_does_not_expose_sql_or_password_hash_field(
    isolated_database, isolated_app
):
    from scripts.init_db import apply_migrations

    _apply_base(isolated_database)
    apply_migrations(isolated_database)
    with isolated_database.cursor() as cursor:
        cursor.execute("SELECT account_id FROM auth.account WHERE username = 'admin'")
        admin_id = cursor.fetchone()[0]

    client = isolated_app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = admin_id
        flask_session["username"] = "admin"
        flask_session["role"] = "admin"

    response = client.post(
        "/admin/accounts",
        data={
            "username": "admin",
            "full_name": "Duplicate",
            "password": "SecurePass123",
            "role_codes": "system_admin",
        },
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "INSERT INTO auth.account" not in body
    assert "password_hash" not in body
