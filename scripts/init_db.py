import argparse
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
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            run_script(cursor, "v2_schema.sql")
            run_script(cursor, "v2_seed.sql")
            run_script(cursor, "demo_commerce_v2.sql")
            configure_account_passwords(
                cursor,
                required=os.environ.get("FLASK_ENV", "development").lower() == "production",
            )
            if args.migrate_legacy:
                run_script(cursor, "migrate_v1_to_v2.sql")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
