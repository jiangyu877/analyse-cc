from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]


def _initialize(connection):
    from scripts.init_db import apply_migrations

    with connection.cursor() as cursor:
        cursor.execute((ROOT / "database" / "v2_schema.sql").read_text(encoding="utf-8"))
        cursor.execute((ROOT / "database" / "v2_seed.sql").read_text(encoding="utf-8"))
    connection.commit()
    apply_migrations(connection)


def _seed_paid_order(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT account_id FROM auth.account WHERE username = 'admin'")
        account_id = cursor.fetchone()[0]
        cursor.execute("SELECT customer_id FROM biz.customer ORDER BY customer_id LIMIT 1")
        customer_id = cursor.fetchone()[0]
        cursor.execute("SELECT product_id, category_id, unit_price FROM biz.product ORDER BY product_id LIMIT 1")
        product_id, category_id, unit_price = cursor.fetchone()
        ordered_at = date.today() - timedelta(days=2)
        total = (unit_price * 2).quantize(Decimal("0.01"))
        cursor.execute(
            """
            INSERT INTO biz.sales_order
                (order_no, customer_id, status, total_amount, paid_amount,
                 ordered_at, paid_at, created_by)
            VALUES ('ANALYTICS-ORDER-1', %s, 'partially_refunded', %s, %s,
                    %s, %s, %s)
            RETURNING order_id
            """,
            (customer_id, total, total, ordered_at, ordered_at, account_id),
        )
        order_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO biz.order_item
                (order_id, product_id, quantity, unit_price, line_amount)
            VALUES (%s, %s, 2, %s, %s)
            RETURNING order_item_id
            """,
            (order_id, product_id, unit_price, total),
        )
        order_item_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO biz.payment
                (payment_no, order_id, method, amount, status, paid_at, created_by)
            VALUES ('ANALYTICS-PAY-1', %s, 'wechat', %s, 'success', %s, %s)
            RETURNING payment_id
            """,
            (order_id, total, ordered_at, account_id),
        )
        payment_id = cursor.fetchone()[0]
        refund_amount = (unit_price).quantize(Decimal("0.01"))
        cursor.execute(
            """
            INSERT INTO biz.refund
                (refund_no, payment_id, order_id, amount, reason, status,
                 refunded_at, created_by)
            VALUES ('ANALYTICS-REF-1', %s, %s, %s, 'test refund', 'success', %s, %s)
            RETURNING refund_id
            """,
            (payment_id, order_id, refund_amount, ordered_at, account_id),
        )
        refund_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO biz.refund_item
                (refund_id, order_id, order_item_id, quantity, refund_amount, returned_qty)
            VALUES (%s, %s, %s, 1, %s, 1)
            """,
            (refund_id, order_id, order_item_id, refund_amount),
        )
        cursor.execute(
            """
            INSERT INTO dwd.consumption_flow
                (customer_id, order_id, payment_id, flow_type,
                 gross_amount, net_amount, occurred_at)
            VALUES (%s, %s, %s, 'payment', %s, %s, %s)
            """,
            (customer_id, order_id, payment_id, total, total, ordered_at),
        )
        cursor.execute(
            """
            INSERT INTO dwd.consumption_flow
                (customer_id, order_id, payment_id, refund_id, flow_type,
                 gross_amount, net_amount, occurred_at)
            VALUES (%s, %s, %s, %s, 'refund', %s, -%s, %s)
            """,
            (customer_id, order_id, payment_id, refund_id, refund_amount, refund_amount, ordered_at),
        )
    connection.commit()
    return account_id, customer_id, product_id, category_id, refund_amount


def test_refresh_reconciles_ads_and_is_idempotent(isolated_database, isolated_app):
    from app.extensions import db
    from app.services.analytics import AnalyticsService

    _initialize(isolated_database)
    account_id, customer_id, product_id, category_id, expected_net = _seed_paid_order(
        isolated_database
    )
    snapshot_date = date.today()

    with isolated_app.app_context():
        first_task = AnalyticsService.refresh(snapshot_date, account_id)
        source_total = db.session.execute(text(
            "SELECT COALESCE(SUM(net_amount), 0) FROM dwd.consumption_flow"
        )).scalar_one()
        totals = db.session.execute(text("""
            SELECT
              (SELECT COALESCE(SUM(net_amount), 0) FROM ads.daily_sales
               WHERE snapshot_date = :snapshot_date),
              (SELECT COALESCE(SUM(net_amount), 0) FROM ads.product_sales
               WHERE snapshot_date = :snapshot_date),
              (SELECT COALESCE(SUM(net_amount), 0) FROM ads.category_sales
               WHERE snapshot_date = :snapshot_date),
              (SELECT COALESCE(SUM(monetary), 0) FROM ads.customer_profile
               WHERE snapshot_date = :snapshot_date),
              (SELECT COALESCE(SUM(monetary), 0) FROM ads.customer_rfm
               WHERE refresh_task_id = :task_id)
        """), {"snapshot_date": snapshot_date, "task_id": first_task}).one()
        assert totals == (source_total, source_total, source_total, source_total, source_total)

        product_row = db.session.execute(text("""
            SELECT product_id, category_id, quantity, net_amount
            FROM ads.product_sales
            WHERE snapshot_date = :snapshot_date AND product_id = :product_id
        """), {"snapshot_date": snapshot_date, "product_id": product_id}).one()
        assert product_row[:2] == (product_id, category_id)
        assert product_row[2] == 1
        assert product_row[3] == expected_net

        profile = db.session.execute(text("""
            SELECT customer_id, frequency, monetary
            FROM ads.customer_profile
            WHERE snapshot_date = :snapshot_date AND customer_id = :customer_id
        """), {"snapshot_date": snapshot_date, "customer_id": customer_id}).one()
        assert profile == (customer_id, 1, expected_net)

        second_task = AnalyticsService.refresh(snapshot_date, account_id)
        assert second_task != first_task
        assert db.session.execute(text("""
            SELECT COUNT(*) FROM ads.daily_sales WHERE snapshot_date = :snapshot_date
        """), {"snapshot_date": snapshot_date}).scalar_one() == 3
        assert db.session.execute(text("""
            SELECT COUNT(DISTINCT refresh_task_id) FROM ads.daily_sales
            WHERE snapshot_date = :snapshot_date
        """), {"snapshot_date": snapshot_date}).scalar_one() == 1


def test_reports_apply_same_filters_to_html_and_csv(isolated_database, isolated_app):
    from app.extensions import db
    from app.services.analytics import AnalyticsService

    _initialize(isolated_database)
    account_id, *_ = _seed_paid_order(isolated_database)
    with isolated_app.app_context():
        AnalyticsService.refresh(date.today(), account_id)
        db.session.commit()

    client = isolated_app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = account_id
        flask_session["username"] = "admin"
        flask_session["role"] = "admin"

    html = client.get(f"/reports?dimension=product&start={date.today() - timedelta(days=3)}&end={date.today()}")
    assert html.status_code == 200
    assert "ANALYTICS" not in html.get_data(as_text=True)

    csv = client.get(f"/reports/export.csv?dimension=product&start={date.today() - timedelta(days=3)}&end={date.today()}")
    assert csv.status_code == 200
    assert csv.data.startswith("\ufeff".encode("utf-8"))
    assert "product_id" in csv.get_data(as_text=True)
