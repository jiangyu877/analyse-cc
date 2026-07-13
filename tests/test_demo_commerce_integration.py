import os
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest
from psycopg2 import sql
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
SQL_PATHS = (
    ROOT / "database" / "v2_schema.sql",
    ROOT / "database" / "v2_seed.sql",
    ROOT / "database" / "demo_commerce_v2.sql",
)
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BLOCKED_TARGET_QUERY_KEYS = frozenset(
    {"dbname", "database", "host", "hostaddr", "port", "user", "password", "service"}
)


def _connection_kwargs(url, database_name=None):
    blocked_keys = BLOCKED_TARGET_QUERY_KEYS.intersection(url.query)
    if blocked_keys:
        raise ValueError(
            "TEST_DATABASE_URL query parameters cannot override connection target: "
            + ", ".join(sorted(blocked_keys))
        )

    kwargs = {
        key: value[-1] if isinstance(value, tuple) else value
        for key, value in url.query.items()
        if key not in BLOCKED_TARGET_QUERY_KEYS
    }
    kwargs.update(
        {
            "host": url.host,
            "port": url.port,
            "user": url.username,
            "password": url.password,
            "dbname": database_name or url.database or "postgres",
        }
    )
    return {key: value for key, value in kwargs.items() if value is not None}


@pytest.mark.parametrize(
    "blocked_key",
    ("dbname", "database", "host", "hostaddr", "port", "user", "password", "service"),
)
def test_connection_kwargs_rejects_target_query_parameters(blocked_key):
    url = make_url(
        "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/postgres"
        f"?{blocked_key}=override"
    )

    with pytest.raises(ValueError, match=blocked_key):
        _connection_kwargs(url, "isolated_database")


def _run_seed_sequence(connection):
    with connection.cursor() as cursor:
        for path in SQL_PATHS:
            cursor.execute(path.read_text(encoding="utf-8"))


def _assert_pending_order_state(connection, paid_order_numbers=frozenset()):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                o.order_no,
                o.status,
                o.total_amount,
                count(oi.order_item_id),
                min(oi.line_amount)
            FROM biz.sales_order o
            LEFT JOIN biz.order_item oi ON oi.order_id = o.order_id
            WHERE o.order_no LIKE 'DEMO2-PEND-SO-%'
            GROUP BY o.order_id, o.order_no, o.status, o.total_amount
            ORDER BY o.order_no
            """
        )
        rows = cursor.fetchall()

    expected_order_numbers = [
        f"DEMO2-PEND-SO-{number:03d}" for number in range(1, 21)
    ]
    assert [row[0] for row in rows] == expected_order_numbers
    for order_no, status, total_amount, item_count, line_amount in rows:
        expected_status = (
            "paid" if order_no in paid_order_numbers else "awaiting_payment"
        )
        assert status == expected_status
        assert item_count == 1
        assert line_amount == total_amount


def _assert_demo_seed_state(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (SELECT count(*) FROM biz.product
                 WHERE sku LIKE 'DEMO2-SKU-%'),
                (SELECT count(*) FROM biz.sales_order
                 WHERE order_no LIKE 'DEMO2-PEND-SO-%'),
                (SELECT count(*) FROM biz.sales_order
                 WHERE order_no LIKE 'DEMO2-PAID-SO-%'),
                (SELECT count(*) FROM biz.payment
                 WHERE payment_no LIKE 'DEMO2-PAY-%'),
                (SELECT count(*) FROM biz.refund
                 WHERE refund_no LIKE 'DEMO2-REF-%'),
                (SELECT count(*)
                 FROM dwd.consumption_flow f
                 JOIN biz.payment p ON p.payment_id = f.payment_id
                 WHERE p.payment_no LIKE 'DEMO2-PAY-%'
                   AND f.flow_type = 'payment'
                   AND f.net_amount > 0),
                (SELECT count(*)
                 FROM dwd.consumption_flow f
                 JOIN biz.refund r ON r.refund_id = f.refund_id
                 WHERE r.refund_no LIKE 'DEMO2-REF-%'
                   AND f.flow_type = 'refund'
                   AND f.net_amount < 0),
                (SELECT count(*) FROM biz.product
                 WHERE sku LIKE 'DEMO2-SKU-%' AND stock_qty < 10)
            """
        )
        assert cursor.fetchone() == (20, 20, 10, 10, 10, 10, 10, 3)

        cursor.execute(
            """
            SELECT status, count(*)
            FROM biz.sales_order
            WHERE order_no LIKE 'DEMO2-PAID-SO-%'
            GROUP BY status
            """
        )
        assert dict(cursor.fetchall()) == {
            "partially_refunded": 6,
            "refunded": 4,
        }

        cursor.execute(
            """
            SELECT
                o.order_no,
                oi.line_amount,
                o.total_amount,
                p.amount,
                o.paid_amount,
                o.refunded_amount,
                r.amount,
                r.order_id,
                p.order_id,
                payment_flow.net_amount,
                refund_flow.net_amount,
                o.ordered_at,
                p.paid_at,
                r.refunded_at,
                CURRENT_TIMESTAMP
            FROM biz.sales_order o
            JOIN biz.order_item oi ON oi.order_id = o.order_id
            JOIN biz.payment p ON p.order_id = o.order_id
                                 AND p.status = 'success'
            JOIN biz.refund r ON r.payment_id = p.payment_id
                              AND r.status = 'success'
            JOIN dwd.consumption_flow payment_flow
              ON payment_flow.payment_id = p.payment_id
             AND payment_flow.flow_type = 'payment'
            JOIN dwd.consumption_flow refund_flow
              ON refund_flow.refund_id = r.refund_id
             AND refund_flow.flow_type = 'refund'
            WHERE o.order_no LIKE 'DEMO2-PAID-SO-%'
            ORDER BY o.order_no
            """
        )
        rows = cursor.fetchall()

    assert [row[0] for row in rows] == [
        f"DEMO2-PAID-SO-{number:03d}" for number in range(1, 11)
    ]
    for row in rows:
        (
            _,
            line_amount,
            total_amount,
            payment_amount,
            paid_amount,
            refunded_amount,
            refund_amount,
            refund_order_id,
            payment_order_id,
            payment_flow_amount,
            refund_flow_amount,
            ordered_at,
            paid_at,
            refunded_at,
            checked_at,
        ) = row
        assert line_amount == total_amount
        assert payment_amount == paid_amount == total_amount
        assert refunded_amount == refund_amount <= payment_amount
        assert refund_order_id == payment_order_id
        assert payment_flow_amount == payment_amount
        assert refund_flow_amount == -refund_amount
        assert ordered_at <= paid_at <= refunded_at <= checked_at


def test_demo_commerce_seed_is_consistent_idempotent_and_preserves_operator_changes():
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL is not set; PostgreSQL integration test requires an admin database URL"
        )

    url = make_url(TEST_DATABASE_URL)
    database_name = f"test_demo_commerce_{uuid4().hex}"
    admin_connection = None
    database_connection = None
    database_created = False

    try:
        admin_connection = psycopg2.connect(**_connection_kwargs(url))
        admin_connection.autocommit = True
        with admin_connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )
        database_created = True

        database_connection = psycopg2.connect(
            **_connection_kwargs(url, database_name)
        )
        with database_connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            assert cursor.fetchone() == (database_name,)
        _run_seed_sequence(database_connection)
        _assert_demo_seed_state(database_connection)
        _assert_pending_order_state(database_connection)

        with database_connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE biz.product
                SET stock_qty = 321
                WHERE sku = 'DEMO2-SKU-F003'
                """
            )
            assert cursor.rowcount == 1
            cursor.execute(
                """
                UPDATE biz.sales_order
                SET status = 'paid',
                    paid_amount = total_amount,
                    paid_at = now()
                WHERE order_no = 'DEMO2-PEND-SO-001'
                """
            )
            assert cursor.rowcount == 1
        database_connection.commit()

        _run_seed_sequence(database_connection)
        _assert_demo_seed_state(database_connection)
        _assert_pending_order_state(
            database_connection, {"DEMO2-PEND-SO-001"}
        )

        with database_connection.cursor() as cursor:
            cursor.execute(
                "SELECT stock_qty FROM biz.product WHERE sku = 'DEMO2-SKU-F003'"
            )
            assert cursor.fetchone() == (321,)
            cursor.execute(
                """
                SELECT status, paid_amount = total_amount
                FROM biz.sales_order
                WHERE order_no = 'DEMO2-PEND-SO-001'
                """
            )
            assert cursor.fetchone() == ("paid", True)
    finally:
        if database_connection is not None:
            database_connection.close()
        if admin_connection is not None:
            try:
                if database_created:
                    with admin_connection.cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT pg_terminate_backend(pid)
                            FROM pg_stat_activity
                            WHERE datname = %s AND pid <> pg_backend_pid()
                            """,
                            (database_name,),
                        )
                        cursor.execute(
                            sql.SQL("DROP DATABASE {}").format(
                                sql.Identifier(database_name)
                            )
                        )
            finally:
                admin_connection.close()
