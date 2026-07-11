import os
import subprocess
import sys
from pathlib import Path

import psycopg2
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Config


def _enabled(name, default="false"):
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _connect():
    url = make_url(Config.SQLALCHEMY_DATABASE_URI)
    options = {
        "host": url.host,
        "port": url.port or 5432,
        "dbname": url.database,
        "user": url.username,
        "password": url.password,
    }
    options.update(dict(url.query))
    return psycopg2.connect(**options)


def _imported_transactions():
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT count(*)
                FROM dwd.consumption_flow f
                JOIN biz.payment p ON p.payment_id = f.payment_id
                WHERE f.flow_type = 'payment' AND p.payment_no LIKE 'IMP-PAY-%'
            """)
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def main():
    subprocess.run([sys.executable, str(ROOT / "scripts" / "init_db.py")], check=True)
    if not _enabled("IMPORT_DEMO_DATA"):
        print("cloud bootstrap: schema ready; demo import disabled")
        return

    customers = int(os.environ.get("DEMO_CUSTOMERS", "5000"))
    transactions = int(os.environ.get("DEMO_TRANSACTIONS", "50000"))
    if customers < 1 or transactions < 50000:
        raise RuntimeError("DEMO_CUSTOMERS must be positive and DEMO_TRANSACTIONS must be at least 50000")

    current = _imported_transactions()
    if current < transactions:
        subprocess.run([
            sys.executable,
            str(ROOT / "scripts" / "import_demo_data.py"),
            "--customers", str(customers),
            "--transactions", str(transactions),
        ], check=True)
        current = _imported_transactions()
    print(f"cloud bootstrap: schema ready; imported transactions={current}")


if __name__ == "__main__":
    main()
