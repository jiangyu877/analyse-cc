import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg2
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Config
from app.utils import check_password, hash_password


ACCOUNT_PASSWORDS = {
    "admin": "ADMIN_PASSWORD",
    "operator": "OPERATOR_PASSWORD",
    "analyst": "ANALYST_PASSWORD",
}
MIGRATION_LOCK_KEY = 746842291003441620


def connect():
    url = make_url(Config.SQLALCHEMY_DATABASE_URI)
    return psycopg2.connect(
        host=url.host,
        port=url.port or 5432,
        dbname=url.database,
        user=url.username,
        password=url.password,
    )


def run_script(cursor, filename):
    path = ROOT / "database" / filename
    cursor.execute(path.read_text(encoding="utf-8"))
    print(f"applied: {filename}")


def apply_migrations(connection, migrations_dir=None):
    migrations_dir = Path(migrations_dir or ROOT / "database" / "migrations")
    migrations_dir.mkdir(parents=True, exist_ok=True)
    paths = sorted(migrations_dir.glob("*.sql"))
    locked = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        connection.commit()
        locked = True

        with connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS audit.schema_migration (
                        version VARCHAR(80) PRIMARY KEY,
                        checksum VARCHAR(64) NOT NULL,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)

        with connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version, checksum FROM audit.schema_migration")
                applied = dict(cursor.fetchall())

        files = {path.name: path for path in paths}
        missing = sorted(set(applied) - set(files))
        if missing:
            raise RuntimeError(f"applied migration files are missing: {', '.join(missing)}")

        for version, checksum in applied.items():
            current = hashlib.sha256(files[version].read_bytes()).hexdigest()
            if current != checksum:
                raise RuntimeError(f"migration checksum changed: {version}")

        pending = [path for path in paths if path.name not in applied]
        if applied and pending:
            last_applied = max(applied)
            out_of_order = [path.name for path in pending if path.name < last_applied]
            if out_of_order:
                raise RuntimeError(
                    "pending migrations are out of order after "
                    f"{last_applied}: {', '.join(out_of_order)}"
                )

        for path in pending:
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute(path.read_text(encoding="utf-8"))
                    cursor.execute(
                        "INSERT INTO audit.schema_migration(version, checksum) VALUES (%s, %s)",
                        (path.name, checksum),
                    )
            print(f"migrated: {path.name}")
    finally:
        if locked:
            connection.rollback()
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
            connection.commit()


def configure_account_passwords(cursor, required=False):
    missing = [variable for variable in ACCOUNT_PASSWORDS.values() if not os.environ.get(variable)]
    if missing and required:
        raise RuntimeError(f"生产初始化缺少账号密码环境变量: {', '.join(missing)}")
    for username, variable in ACCOUNT_PASSWORDS.items():
        password = os.environ.get(variable)
        if not password:
            continue
        if len(password) < 10 or not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
            raise RuntimeError(f"{variable} 至少需要 10 个字符，且同时包含字母和数字")
        cursor.execute("SELECT password_hash FROM auth.account WHERE username = %s", (username,))
        current = cursor.fetchone()
        if current and check_password(password, current[0]):
            continue
        cursor.execute(
            "UPDATE auth.account SET password_hash = %s, updated_at = now() WHERE username = %s",
            (hash_password(password), username),
        )
    if missing and not required:
        print("warning: 未配置的初始账号保持随机密码，设置环境变量后重新运行即可启用")


def main():
    parser = argparse.ArgumentParser(description="Initialize the V2 PostgreSQL database")
    parser.add_argument("--migrate-legacy", action="store_true")
    args = parser.parse_args()

    connection = connect()
    try:
        with connection.cursor() as cursor:
            run_script(cursor, "v2_schema.sql")
            run_script(cursor, "v2_seed.sql")
            run_script(cursor, "demo_commerce_v2.sql")
        connection.commit()
        apply_migrations(connection)
        with connection.cursor() as cursor:
            configure_account_passwords(
                cursor,
                required=os.environ.get("FLASK_ENV", "development").lower() == "production",
            )
            if args.migrate_legacy:
                run_script(cursor, "migrate_v1_to_v2.sql")
        connection.commit()
    finally:
        connection.close()


if __name__ == "__main__":
    main()
