import os
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path

import psycopg2
import pytest
from flask import session
from sqlalchemy import text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[2]


def _initialize(connection):
    from scripts.init_db import apply_migrations

    with connection.cursor() as cursor:
        cursor.execute((ROOT / "database" / "v2_schema.sql").read_text(encoding="utf-8"))
        cursor.execute((ROOT / "database" / "v2_seed.sql").read_text(encoding="utf-8"))
    connection.commit()
    apply_migrations(connection)


def _seed_legacy_refund(connection, suffix, status, quantity):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT account_id FROM auth.account WHERE username = 'admin'"
        )
        account_id = cursor.fetchone()[0]
        cursor.execute(
            "SELECT customer_id FROM biz.customer ORDER BY customer_id LIMIT 1"
        )
        customer_id = cursor.fetchone()[0]
        cursor.execute(
            "SELECT product_id, unit_price FROM biz.product ORDER BY product_id LIMIT 1"
        )
        product_id, unit_price = cursor.fetchone()
        amount = unit_price * quantity
        order_status = "refunded" if status == "success" else "paid"
        refunded_amount = amount if status == "success" else Decimal("0.00")
        cursor.execute(
            """
            INSERT INTO biz.sales_order
                (order_no, customer_id, status, total_amount, paid_amount,
                 refunded_amount, paid_at, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, now(), %s)
            RETURNING order_id
            """,
            (
                f"LEGACY-ORDER-{suffix}",
                customer_id,
                order_status,
                amount,
                amount,
                refunded_amount,
                account_id,
            ),
        )
        order_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO biz.order_item
                (order_id, product_id, quantity, unit_price, line_amount)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING order_item_id
            """,
            (order_id, product_id, quantity, unit_price, amount),
        )
        order_item_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO biz.payment
                (payment_no, order_id, method, amount, status, created_by)
            VALUES (%s, %s, 'wechat', %s, 'success', %s)
            RETURNING payment_id
            """,
            (f"LEGACY-PAY-{suffix}", order_id, amount, account_id),
        )
        payment_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO biz.refund
                (refund_no, payment_id, order_id, amount, reason, status,
                 refunded_at, created_by)
            VALUES (%s, %s, %s, %s, 'legacy refund', %s, now(), %s)
            RETURNING refund_id
            """,
            (
                f"LEGACY-REFUND-{suffix}",
                payment_id,
                order_id,
                amount,
                status,
                account_id,
            ),
        )
        refund_id = cursor.fetchone()[0]
    connection.commit()
    return {
        "refund_id": refund_id,
        "payment_id": payment_id,
        "order_item_id": order_item_id,
    }


def test_migration_does_not_fabricate_historical_refund_items(
    isolated_database, isolated_app
):
    from app.repositories.retail import RefundRepository
    from scripts.init_db import apply_migrations

    with isolated_database.cursor() as cursor:
        cursor.execute((ROOT / "database" / "v2_schema.sql").read_text(encoding="utf-8"))
        cursor.execute((ROOT / "database" / "v2_seed.sql").read_text(encoding="utf-8"))
    isolated_database.commit()
    legacy = {
        "success": _seed_legacy_refund(isolated_database, "SUCCESS", "success", 3),
        "pending": _seed_legacy_refund(isolated_database, "PENDING", "pending", 2),
        "failed": _seed_legacy_refund(isolated_database, "FAILED", "failed", 2),
    }

    apply_migrations(isolated_database)

    with isolated_database.cursor() as cursor:
        cursor.execute(
            """
            SELECT r.refund_no, COUNT(ri.refund_item_id)
            FROM biz.refund r
            LEFT JOIN biz.refund_item ri ON ri.refund_id = r.refund_id
            WHERE r.refund_no LIKE 'LEGACY-REFUND-%'
            GROUP BY r.refund_no
            """
        )
        assert dict(cursor.fetchall()) == {
            "LEGACY-REFUND-SUCCESS": 0,
            "LEGACY-REFUND-PENDING": 0,
            "LEGACY-REFUND-FAILED": 0,
        }

    with isolated_app.app_context():
        available_payment_ids = {
            row["payment_id"] for row in RefundRepository.refundable_items()
        }

    assert legacy["success"]["payment_id"] not in available_payment_ids
    assert legacy["pending"]["payment_id"] not in available_payment_ids
    assert legacy["failed"]["payment_id"] in available_payment_ids


def test_full_seed_and_migration_sequence_is_idempotent(isolated_database):
    from scripts.init_db import apply_migrations

    for _run in range(2):
        with isolated_database.cursor() as cursor:
            for filename in ("v2_schema.sql", "v2_seed.sql", "demo_commerce_v2.sql"):
                cursor.execute((ROOT / "database" / filename).read_text(encoding="utf-8"))
        isolated_database.commit()
        apply_migrations(isolated_database)

    with isolated_database.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM audit.schema_migration")
        assert cursor.fetchone()[0] == 8
        cursor.execute("""
            SELECT COUNT(*)
            FROM biz.refund_item ri
            JOIN biz.refund r ON r.refund_id = ri.refund_id
            WHERE r.refund_no LIKE 'DEMO2-REF-%'
        """)
        assert cursor.fetchone()[0] == 0


def test_hardening_migration_reconciles_an_approved_fabricated_item(
    isolated_database, tmp_path
):
    from scripts.init_db import apply_migrations

    with isolated_database.cursor() as cursor:
        cursor.execute((ROOT / "database" / "v2_schema.sql").read_text(encoding="utf-8"))
        cursor.execute((ROOT / "database" / "v2_seed.sql").read_text(encoding="utf-8"))
    isolated_database.commit()
    legacy = _seed_legacy_refund(isolated_database, "PHASED", "pending", 2)

    migrations = ROOT / "database" / "migrations"
    for filename in ("001_rbac_and_audit.sql", "002_inventory_and_refund_workflow.sql"):
        (tmp_path / filename).write_bytes((migrations / filename).read_bytes())
    apply_migrations(isolated_database, tmp_path)

    with isolated_database.cursor() as cursor:
        cursor.execute("""
            SELECT ri.refund_item_id, ri.quantity, oi.product_id, r.order_id,
                   p.stock_qty, o.customer_id, r.amount, r.payment_id
            FROM biz.refund_item ri
            JOIN biz.refund r ON r.refund_id = ri.refund_id
            JOIN biz.order_item oi ON oi.order_item_id = ri.order_item_id
            JOIN biz.product p ON p.product_id = oi.product_id
            JOIN biz.sales_order o ON o.order_id = r.order_id
            WHERE r.refund_id = %s
        """, (legacy["refund_id"],))
        (
            refund_item_id,
            quantity,
            product_id,
            order_id,
            stock_before_wrong_approval,
            customer_id,
            refund_amount,
            payment_id,
        ) = cursor.fetchone()
        cursor.execute(
            "UPDATE biz.product SET stock_qty = stock_qty + %s WHERE product_id = %s",
            (quantity, product_id),
        )
        cursor.execute("""
            INSERT INTO biz.inventory_log
                (product_id, order_id, refund_id, refund_item_id, change_type,
                 quantity_delta, before_qty, after_qty, remark)
            VALUES (%s, %s, %s, %s, 'refund_return', %s, %s, %s, 'pre-003 approval')
        """, (
            product_id,
            order_id,
            legacy["refund_id"],
            refund_item_id,
            quantity,
            stock_before_wrong_approval,
            stock_before_wrong_approval + quantity,
        ))
        cursor.execute(
            "UPDATE biz.refund_item SET returned_qty = quantity WHERE refund_item_id = %s",
            (refund_item_id,),
        )
        cursor.execute("""
            UPDATE biz.refund
            SET status = 'success', refunded_at = now(), reviewed_at = now()
            WHERE refund_id = %s
        """, (legacy["refund_id"],))
        cursor.execute("""
            INSERT INTO dwd.consumption_flow
                (customer_id, order_id, payment_id, refund_id, flow_type,
                 gross_amount, net_amount, occurred_at)
            VALUES (%s, %s, %s, %s, 'refund', %s, -%s, now())
        """, (
            customer_id,
            order_id,
            payment_id,
            legacy["refund_id"],
            refund_amount,
            refund_amount,
        ))
    isolated_database.commit()

    hardening = "003_release_a_hardening.sql"
    (tmp_path / hardening).write_bytes((migrations / hardening).read_bytes())
    apply_migrations(isolated_database, tmp_path)

    with isolated_database.cursor() as cursor:
        cursor.execute(
            "SELECT stock_qty FROM biz.product WHERE product_id = %s", (product_id,)
        )
        assert cursor.fetchone()[0] == stock_before_wrong_approval
        cursor.execute(
            "SELECT COUNT(*) FROM biz.refund_item WHERE refund_id = %s",
            (legacy["refund_id"],),
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute("""
            SELECT COALESCE(SUM(quantity_delta), 0),
                   COUNT(*) FILTER (WHERE change_type = 'manual_adjustment')
            FROM biz.inventory_log WHERE refund_id = %s
        """, (legacy["refund_id"],))
        assert cursor.fetchone() == (0, 1)
        cursor.execute(
            "SELECT COUNT(*) FROM dwd.consumption_flow WHERE refund_id = %s",
            (legacy["refund_id"],),
        )
        assert cursor.fetchone()[0] == 1


def test_hardening_migration_refuses_concurrent_business_traffic(
    isolated_database, tmp_path
):
    from app.config import Config
    from scripts.init_db import apply_migrations

    with isolated_database.cursor() as cursor:
        cursor.execute((ROOT / "database" / "v2_schema.sql").read_text(encoding="utf-8"))
        cursor.execute((ROOT / "database" / "v2_seed.sql").read_text(encoding="utf-8"))
    isolated_database.commit()

    migrations = ROOT / "database" / "migrations"
    for filename in ("001_rbac_and_audit.sql", "002_inventory_and_refund_workflow.sql"):
        (tmp_path / filename).write_bytes((migrations / filename).read_bytes())
    apply_migrations(isolated_database, tmp_path)
    hardening = "003_release_a_hardening.sql"
    (tmp_path / hardening).write_bytes((migrations / hardening).read_bytes())

    with isolated_database.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM biz.payment")

    url = make_url(os.environ.get("TEST_DATABASE_URL") or Config.SQLALCHEMY_DATABASE_URI)
    migration_connection = psycopg2.connect(
        host=url.host,
        port=url.port or 5432,
        dbname=isolated_database.info.dbname,
        user=url.username,
        password=url.password,
    )
    try:
        with pytest.raises(psycopg2.errors.LockNotAvailable):
            apply_migrations(migration_connection, tmp_path)
        isolated_database.rollback()
        apply_migrations(migration_connection, tmp_path)
        with migration_connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM audit.schema_migration WHERE version = %s",
                (hardening,),
            )
            assert cursor.fetchone()[0] == 1
    finally:
        migration_connection.close()


def test_approved_refund_restores_exact_item_quantity_once(isolated_database, isolated_app):
    from app.extensions import db
    from app.services.commerce import BusinessError, OrderService, PaymentService, RefundService

    assert hasattr(RefundService, "request"), "refund request workflow is required"
    assert hasattr(RefundService, "approve"), "refund approval workflow is required"
    _initialize(isolated_database)

    with isolated_app.test_request_context("/refunds"):
        account_id = db.session.execute(text(
            "SELECT account_id FROM auth.account WHERE username = 'admin'"
        )).scalar_one()
        customer_id = db.session.execute(text(
            "SELECT customer_id FROM biz.customer WHERE customer_no = 'C20260001'"
        )).scalar_one()
        product = db.session.execute(text("""
            SELECT product_id, stock_qty FROM biz.product WHERE sku = 'SKU-F001'
        """)).mappings().one()
        db.session.commit()
        session["user_id"] = account_id

        order_id = OrderService.create(
            customer_id,
            [{"product_id": product["product_id"], "quantity": 2}],
            "refund integration",
            account_id,
        )
        payment_id = PaymentService.pay(order_id, "wechat", account_id, "TEST-PAY-001")
        order_item_id = db.session.execute(text("""
            SELECT order_item_id FROM biz.order_item WHERE order_id = :order_id
        """), {"order_id": order_id}).scalar_one()
        stock_after_sale = db.session.execute(text("""
            SELECT stock_qty FROM biz.product WHERE product_id = :product_id
        """), {"product_id": product["product_id"]}).scalar_one()
        db.session.commit()

        refund_id = RefundService.request(
            payment_id,
            [{"order_item_id": order_item_id, "quantity": 1}],
            "商品破损",
            account_id,
        )
        pending = db.session.execute(text("""
            SELECT status, amount FROM biz.refund WHERE refund_id = :refund_id
        """), {"refund_id": refund_id}).one()
        db.session.commit()
        assert pending == ("pending", Decimal("59.90"))

        RefundService.approve(refund_id, account_id, "审核通过")
        stock_after_refund = db.session.execute(text("""
            SELECT stock_qty FROM biz.product WHERE product_id = :product_id
        """), {"product_id": product["product_id"]}).scalar_one()
        refund_state = db.session.execute(text("""
            SELECT status, reviewed_by, review_note FROM biz.refund WHERE refund_id = :refund_id
        """), {"refund_id": refund_id}).one()
        counts = db.session.execute(text("""
            SELECT
              (SELECT COUNT(*) FROM biz.inventory_log WHERE refund_id = :refund_id),
              (SELECT COUNT(*) FROM dwd.consumption_flow
               WHERE refund_id = :refund_id AND flow_type = 'refund')
        """), {"refund_id": refund_id}).one()
        db.session.commit()

        assert stock_after_sale == product["stock_qty"] - 2
        assert stock_after_refund == stock_after_sale + 1
        assert refund_state == ("success", account_id, "审核通过")
        assert counts == (1, 1)

        with pytest.raises(BusinessError, match="待审核"):
            RefundService.approve(refund_id, account_id, "重复审核")
        db.session.rollback()

        final_counts = db.session.execute(text("""
            SELECT
              (SELECT COUNT(*) FROM biz.inventory_log WHERE refund_id = :refund_id),
              (SELECT COUNT(*) FROM dwd.consumption_flow
               WHERE refund_id = :refund_id AND flow_type = 'refund')
        """), {"refund_id": refund_id}).one()
        assert final_counts == (1, 1)


def test_rejected_refund_does_not_change_stock(isolated_database, isolated_app):
    from app.extensions import db
    from app.services.commerce import OrderService, PaymentService, RefundService

    assert hasattr(RefundService, "reject"), "refund rejection workflow is required"
    _initialize(isolated_database)

    with isolated_app.test_request_context("/refunds"):
        account_id = db.session.execute(text(
            "SELECT account_id FROM auth.account WHERE username = 'admin'"
        )).scalar_one()
        customer_id = db.session.execute(text(
            "SELECT customer_id FROM biz.customer WHERE customer_no = 'C20260002'"
        )).scalar_one()
        product_id = db.session.execute(text(
            "SELECT product_id FROM biz.product WHERE sku = 'SKU-F002'"
        )).scalar_one()
        db.session.commit()
        session["user_id"] = account_id

        order_id = OrderService.create(
            customer_id, [{"product_id": product_id, "quantity": 1}], "", account_id
        )
        payment_id = PaymentService.pay(order_id, "alipay", account_id, "TEST-PAY-002")
        order_item_id = db.session.execute(text(
            "SELECT order_item_id FROM biz.order_item WHERE order_id = :order_id"
        ), {"order_id": order_id}).scalar_one()
        stock_before = db.session.execute(text(
            "SELECT stock_qty FROM biz.product WHERE product_id = :product_id"
        ), {"product_id": product_id}).scalar_one()
        db.session.commit()

        refund_id = RefundService.request(
            payment_id, [{"order_item_id": order_item_id, "quantity": 1}], "不再需要", account_id
        )
        RefundService.reject(refund_id, account_id, "不符合退货规则")
        stock_after = db.session.execute(text(
            "SELECT stock_qty FROM biz.product WHERE product_id = :product_id"
        ), {"product_id": product_id}).scalar_one()
        state = db.session.execute(text(
            "SELECT status, review_note FROM biz.refund WHERE refund_id = :refund_id"
        ), {"refund_id": refund_id}).one()

        assert stock_after == stock_before
        assert state == ("rejected", "不符合退货规则")


def test_approval_revalidates_tampered_refund_quantity(
    isolated_database, isolated_app
):
    from app.extensions import db
    from app.services.commerce import BusinessError, OrderService, PaymentService, RefundService

    _initialize(isolated_database)
    with isolated_app.test_request_context("/refunds"):
        account_id = db.session.execute(text(
            "SELECT account_id FROM auth.account WHERE username = 'admin'"
        )).scalar_one()
        customer_id = db.session.execute(text(
            "SELECT customer_id FROM biz.customer WHERE customer_no = 'C20260001'"
        )).scalar_one()
        product = db.session.execute(text("""
            SELECT product_id, stock_qty FROM biz.product WHERE sku = 'SKU-F001'
        """)).mappings().one()
        db.session.commit()
        session["user_id"] = account_id

        order_id = OrderService.create(
            customer_id,
            [{"product_id": product["product_id"], "quantity": 2}],
            "approval revalidation",
            account_id,
        )
        payment_id = PaymentService.pay(order_id, "wechat", account_id, "TEST-TAMPER-PAY")
        order_item_id = db.session.execute(text(
            "SELECT order_item_id FROM biz.order_item WHERE order_id = :order_id"
        ), {"order_id": order_id}).scalar_one()
        stock_after_sale = db.session.execute(text(
            "SELECT stock_qty FROM biz.product WHERE product_id = :product_id"
        ), {"product_id": product["product_id"]}).scalar_one()
        db.session.commit()

        refund_id = RefundService.request(
            payment_id,
            [{"order_item_id": order_item_id, "quantity": 1}],
            "tamper probe",
            account_id,
        )
        db.session.execute(text("""
            UPDATE biz.refund_item SET quantity = 3
            WHERE refund_id = :refund_id
        """), {"refund_id": refund_id})
        db.session.commit()

        with pytest.raises(BusinessError, match="refundable|amount|quantity|可退|数量|金额"):
            RefundService.approve(refund_id, account_id, "must reject tampering")
        db.session.rollback()

        state = db.session.execute(text("""
            SELECT r.status, ri.returned_qty,
                   (SELECT stock_qty FROM biz.product WHERE product_id = :product_id),
                   (SELECT COUNT(*) FROM biz.inventory_log WHERE refund_id = :refund_id),
                   (SELECT COUNT(*) FROM dwd.consumption_flow WHERE refund_id = :refund_id)
            FROM biz.refund r
            JOIN biz.refund_item ri ON ri.refund_id = r.refund_id
            WHERE r.refund_id = :refund_id
        """), {
            "refund_id": refund_id,
            "product_id": product["product_id"],
        }).one()
        assert state == ("pending", 0, stock_after_sale, 0, 0)


def test_refund_http_flow_accepts_multiple_order_items(
    isolated_database, isolated_app
):
    from app.extensions import db
    from app.services.commerce import OrderService, PaymentService

    _initialize(isolated_database)
    with isolated_app.test_request_context("/refunds"):
        account_id = db.session.execute(text(
            "SELECT account_id FROM auth.account WHERE username = 'admin'"
        )).scalar_one()
        customer_id = db.session.execute(text(
            "SELECT customer_id FROM biz.customer WHERE customer_no = 'C20260001'"
        )).scalar_one()
        products = db.session.execute(text("""
            SELECT product_id, stock_qty
            FROM biz.product WHERE sku IN ('SKU-F001', 'SKU-F002')
            ORDER BY product_id
        """)).mappings().all()
        db.session.commit()
        session["user_id"] = account_id
        order_id = OrderService.create(
            customer_id,
            [
                {"product_id": product["product_id"], "quantity": 2}
                for product in products
            ],
            "multi-item route",
            account_id,
        )
        payment_id = PaymentService.pay(order_id, "wechat", account_id, "TEST-MULTI-PAY")
        order_items = db.session.execute(text("""
            SELECT order_item_id, product_id
            FROM biz.order_item WHERE order_id = :order_id ORDER BY order_item_id
        """), {"order_id": order_id}).mappings().all()
        stock_after_sale = {
            row["product_id"]: row["stock_qty"]
            for row in db.session.execute(text("""
                SELECT product_id, stock_qty FROM biz.product
                WHERE product_id = ANY(:product_ids)
            """), {"product_ids": [row["product_id"] for row in products]}).mappings()
        }
        db.session.commit()

    client = isolated_app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = account_id
        flask_session["username"] = "admin"
        flask_session["role"] = "admin"

    item_ids = [row["order_item_id"] for row in order_items]
    response = client.post(
        "/refunds",
        data={
            "payment_id": str(payment_id),
            "order_item_id": [str(item_id) for item_id in item_ids],
            f"quantity_{item_ids[0]}": "1",
            f"quantity_{item_ids[1]}": "1",
            "reason": "multi-item refund",
        },
    )
    assert response.status_code == 302

    with isolated_app.app_context():
        refund_id = db.session.execute(text("""
            SELECT refund_id FROM biz.refund WHERE payment_id = :payment_id
        """), {"payment_id": payment_id}).scalar_one()
        assert db.session.execute(text("""
            SELECT COUNT(*) FROM biz.refund_item WHERE refund_id = :refund_id
        """), {"refund_id": refund_id}).scalar_one() == 2
        db.session.commit()

    response = client.post(
        f"/refunds/{refund_id}/approve", data={"review_note": "approved"}
    )
    assert response.status_code == 302

    with isolated_app.app_context():
        final_stocks = {
            row["product_id"]: row["stock_qty"]
            for row in db.session.execute(text("""
                SELECT product_id, stock_qty FROM biz.product
                WHERE product_id = ANY(:product_ids)
            """), {"product_ids": list(stock_after_sale)}).mappings()
        }
        counts = db.session.execute(text("""
            SELECT
              (SELECT COUNT(*) FROM biz.inventory_log WHERE refund_id = :refund_id),
              (SELECT COUNT(*) FROM dwd.consumption_flow
               WHERE refund_id = :refund_id AND flow_type = 'refund')
        """), {"refund_id": refund_id}).one()

    assert final_stocks == {
        product_id: quantity + 1
        for product_id, quantity in stock_after_sale.items()
    }
    assert counts == (2, 1)


def test_approval_failure_after_stock_update_rolls_back_everything(
    isolated_database, isolated_app, monkeypatch
):
    from app.extensions import db
    from app.services import commerce
    from app.services.commerce import OrderService, PaymentService, RefundService

    _initialize(isolated_database)
    with isolated_app.test_request_context("/refunds"):
        account_id = db.session.execute(text(
            "SELECT account_id FROM auth.account WHERE username = 'admin'"
        )).scalar_one()
        customer_id = db.session.execute(text(
            "SELECT customer_id FROM biz.customer WHERE customer_no = 'C20260001'"
        )).scalar_one()
        product_id = db.session.execute(text(
            "SELECT product_id FROM biz.product WHERE sku = 'SKU-F001'"
        )).scalar_one()
        db.session.commit()
        session["user_id"] = account_id

        order_id = OrderService.create(
            customer_id, [{"product_id": product_id, "quantity": 2}], "rollback", account_id
        )
        payment_id = PaymentService.pay(order_id, "wechat", account_id, "TEST-ROLLBACK-PAY")
        order_item_id = db.session.execute(text(
            "SELECT order_item_id FROM biz.order_item WHERE order_id = :order_id"
        ), {"order_id": order_id}).scalar_one()
        stock_before = db.session.execute(text(
            "SELECT stock_qty FROM biz.product WHERE product_id = :product_id"
        ), {"product_id": product_id}).scalar_one()
        db.session.commit()
        refund_id = RefundService.request(
            payment_id,
            [{"order_item_id": order_item_id, "quantity": 1}],
            "rollback probe",
            account_id,
        )

        def fail_inventory_log(**_values):
            raise RuntimeError("injected inventory log failure")

        monkeypatch.setattr(commerce.InventoryRepository, "record", fail_inventory_log)
        with pytest.raises(RuntimeError, match="injected inventory log failure"):
            RefundService.approve(refund_id, account_id, "must rollback")
        db.session.rollback()

        state = db.session.execute(text("""
            SELECT r.status, ri.returned_qty,
                   (SELECT stock_qty FROM biz.product WHERE product_id = :product_id),
                   (SELECT COUNT(*) FROM biz.inventory_log WHERE refund_id = :refund_id),
                   (SELECT COUNT(*) FROM dwd.consumption_flow WHERE refund_id = :refund_id)
            FROM biz.refund r
            JOIN biz.refund_item ri ON ri.refund_id = r.refund_id
            WHERE r.refund_id = :refund_id
        """), {"refund_id": refund_id, "product_id": product_id}).one()
        assert state == ("pending", 0, stock_before, 0, 0)


def test_concurrent_approval_changes_stock_and_flow_only_once(
    isolated_database, isolated_app
):
    from app.extensions import db
    from app.services.commerce import OrderService, PaymentService, RefundService

    _initialize(isolated_database)
    with isolated_app.test_request_context("/refunds"):
        account_id = db.session.execute(text(
            "SELECT account_id FROM auth.account WHERE username = 'admin'"
        )).scalar_one()
        customer_id = db.session.execute(text(
            "SELECT customer_id FROM biz.customer WHERE customer_no = 'C20260001'"
        )).scalar_one()
        product_id = db.session.execute(text(
            "SELECT product_id FROM biz.product WHERE sku = 'SKU-F001'"
        )).scalar_one()
        db.session.commit()
        session["user_id"] = account_id
        order_id = OrderService.create(
            customer_id, [{"product_id": product_id, "quantity": 1}], "concurrency", account_id
        )
        payment_id = PaymentService.pay(order_id, "wechat", account_id, "TEST-CONCURRENT-PAY")
        order_item_id = db.session.execute(text(
            "SELECT order_item_id FROM biz.order_item WHERE order_id = :order_id"
        ), {"order_id": order_id}).scalar_one()
        stock_before = db.session.execute(text(
            "SELECT stock_qty FROM biz.product WHERE product_id = :product_id"
        ), {"product_id": product_id}).scalar_one()
        db.session.commit()
        refund_id = RefundService.request(
            payment_id,
            [{"order_item_id": order_item_id, "quantity": 1}],
            "concurrent approval",
            account_id,
        )

    barrier = Barrier(2)

    def approve_once():
        client = isolated_app.test_client()
        with client.session_transaction() as flask_session:
            flask_session["user_id"] = account_id
            flask_session["username"] = "admin"
            flask_session["role"] = "admin"
        barrier.wait(timeout=5)
        return client.post(
            f"/refunds/{refund_id}/approve", data={"review_note": "concurrent"}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _index: approve_once(), range(2)))
    assert statuses == [302, 302]

    with isolated_app.app_context():
        state = db.session.execute(text("""
            SELECT r.status,
                   (SELECT stock_qty FROM biz.product WHERE product_id = :product_id),
                   (SELECT COUNT(*) FROM biz.inventory_log WHERE refund_id = :refund_id),
                   (SELECT COUNT(*) FROM dwd.consumption_flow WHERE refund_id = :refund_id)
            FROM biz.refund r WHERE r.refund_id = :refund_id
        """), {"refund_id": refund_id, "product_id": product_id}).one()
    assert state == ("success", stock_before + 1, 1, 1)


def test_refund_routes_separate_request_and_approval_permissions(
    isolated_database, isolated_app
):
    _initialize(isolated_database)
    finance_id = _seed_finance_account(isolated_database)
    with isolated_database.cursor() as cursor:
        cursor.execute("SELECT account_id FROM auth.account WHERE username = 'operator'")
        operator_id = cursor.fetchone()[0]

    operator_client = isolated_app.test_client()
    with operator_client.session_transaction() as flask_session:
        flask_session["user_id"] = operator_id
    assert operator_client.post("/refunds/999/approve").status_code == 403
    assert operator_client.post(
        "/refunds", data={"reason": "missing fields"}
    ).status_code == 302

    finance_client = isolated_app.test_client()
    with finance_client.session_transaction() as flask_session:
        flask_session["user_id"] = finance_id
    assert finance_client.post("/refunds", data={"reason": "forbidden"}).status_code == 403
    assert finance_client.post("/refunds/999/approve").status_code == 302


def _seed_finance_account(connection):
    with connection.cursor() as cursor:
        cursor.execute("""
            INSERT INTO auth.account (username, password_hash, full_name, role)
            VALUES ('finance-reviewer', 'unused', 'Finance Reviewer', 'operator')
            RETURNING account_id
        """)
        account_id = cursor.fetchone()[0]
        cursor.execute("""
            INSERT INTO auth.account_role (account_id, role_id, is_primary)
            SELECT %s, role_id, TRUE FROM auth.role WHERE role_code = 'finance_auditor'
        """, (account_id,))
    connection.commit()
    return account_id
