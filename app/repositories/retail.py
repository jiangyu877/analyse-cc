from sqlalchemy import text

from app.extensions import db


class CustomerRepository:
    @staticmethod
    def list(search=""):
        return db.session.execute(text("""
            SELECT c.customer_id, c.customer_no, c.name, c.phone, c.city, c.status,
                   COALESCE(v.order_count, 0) AS order_count,
                   COALESCE(v.net_amount, 0) AS net_amount
            FROM biz.customer c
            LEFT JOIN ads.customer_value_summary v ON v.customer_id = c.customer_id
            WHERE (:search = '' OR c.customer_no ILIKE :pattern OR c.name ILIKE :pattern
                   OR COALESCE(c.phone, '') ILIKE :pattern)
            ORDER BY c.created_at DESC
            LIMIT 200
        """), {"search": search, "pattern": f"%{search}%"}).mappings().all()

    @staticmethod
    def get(customer_id):
        return db.session.execute(text("""
            SELECT c.*, COALESCE(v.order_count, 0) AS order_count,
                   COALESCE(v.net_amount, 0) AS net_amount, v.last_purchase_at,
                   r.recency_days, r.frequency, r.monetary, r.segment,
                   cp.churn_probability, cp.predicted_label
            FROM biz.customer c
            LEFT JOIN ads.customer_value_summary v ON v.customer_id = c.customer_id
            LEFT JOIN ads.customer_rfm r ON r.customer_id = c.customer_id
            LEFT JOIN LATERAL (
                SELECT p.churn_probability, p.predicted_label
                FROM ml.churn_prediction p
                JOIN ml.model_task t ON t.task_id = p.task_id
                WHERE p.customer_id = c.customer_id AND t.status = 'success'
                ORDER BY t.finished_at DESC LIMIT 1
            ) cp ON TRUE
            WHERE c.customer_id = :id
        """), {"id": customer_id}).mappings().first()

    @staticmethod
    def orders(customer_id):
        return db.session.execute(text("""
            SELECT order_id, order_no, status, total_amount, paid_amount,
                   refunded_amount, ordered_at
            FROM biz.sales_order WHERE customer_id = :id
            ORDER BY ordered_at DESC LIMIT 50
        """), {"id": customer_id}).mappings().all()

    @staticmethod
    def create(data):
        return db.session.execute(text("""
            INSERT INTO biz.customer
                (customer_no, name, gender, phone, email, province, city)
            VALUES (:customer_no, :name, :gender, :phone, :email, :province, :city)
            RETURNING customer_id
        """), data).scalar_one()


class ProductRepository:
    @staticmethod
    def list(active_only=False):
        return db.session.execute(text("""
            SELECT p.product_id, p.sku, p.product_name, c.category_name,
                   p.unit_price, p.stock_qty, p.status
            FROM biz.product p
            LEFT JOIN biz.product_category c ON c.category_id = p.category_id
            WHERE (:active_only = FALSE OR p.status = 'active')
            ORDER BY p.product_id DESC LIMIT 300
        """), {"active_only": active_only}).mappings().all()

    @staticmethod
    def categories():
        return db.session.execute(text(
            "SELECT category_id, category_name FROM biz.product_category ORDER BY category_name"
        )).mappings().all()

    @staticmethod
    def create(data):
        return db.session.execute(text("""
            INSERT INTO biz.product
                (sku, product_name, category_id, unit_price, stock_qty)
            VALUES (:sku, :product_name, :category_id, :unit_price, :stock_qty)
            RETURNING product_id
        """), data).scalar_one()

    @staticmethod
    def lock(product_id):
        return db.session.execute(text("""
            SELECT product_id, sku, product_name, unit_price, stock_qty, status
            FROM biz.product WHERE product_id = :id FOR UPDATE
        """), {"id": product_id}).mappings().first()

    @staticmethod
    def deduct_stock(product_id, quantity):
        return db.session.execute(text("""
            UPDATE biz.product SET stock_qty = stock_qty - :quantity, updated_at = now()
            WHERE product_id = :id AND stock_qty >= :quantity
            RETURNING stock_qty + :quantity AS before_qty, stock_qty AS after_qty
        """), {"id": product_id, "quantity": quantity}).mappings().one_or_none()

    @staticmethod
    def return_stock(product_id, quantity):
        return db.session.execute(text("""
            UPDATE biz.product SET stock_qty = stock_qty + :quantity, updated_at = now()
            WHERE product_id = :id
            RETURNING stock_qty - :quantity AS before_qty, stock_qty AS after_qty
        """), {"id": product_id, "quantity": quantity}).mappings().one()


class InventoryRepository:
    @staticmethod
    def record(
        *, product_id, order_id, refund_id, refund_item_id, change_type,
        quantity_delta, before_qty, after_qty, operator_id, remark=None,
    ):
        return db.session.execute(text("""
            INSERT INTO biz.inventory_log
                (product_id, order_id, refund_id, refund_item_id, change_type,
                 quantity_delta, before_qty, after_qty, operator_id, remark)
            VALUES
                (:product_id, :order_id, :refund_id, :refund_item_id, :change_type,
                 :quantity_delta, :before_qty, :after_qty, :operator_id, :remark)
            RETURNING inventory_log_id
        """), locals()).scalar_one()


class OrderRepository:
    @staticmethod
    def list():
        return db.session.execute(text("""
            SELECT o.order_id, o.order_no, c.name AS customer_name, o.status,
                   o.total_amount, o.paid_amount, o.refunded_amount, o.ordered_at
            FROM biz.sales_order o
            JOIN biz.customer c ON c.customer_id = o.customer_id
            ORDER BY o.ordered_at DESC LIMIT 200
        """)).mappings().all()

    @staticmethod
    def create(order_no, customer_id, total_amount, remark, created_by):
        return db.session.execute(text("""
            INSERT INTO biz.sales_order
                (order_no, customer_id, total_amount, remark, created_by)
            VALUES (:order_no, :customer_id, :total_amount, :remark, :created_by)
            RETURNING order_id
        """), locals()).scalar_one()

    @staticmethod
    def add_item(order_id, product_id, quantity, unit_price, line_amount):
        db.session.execute(text("""
            INSERT INTO biz.order_item
                (order_id, product_id, quantity, unit_price, line_amount)
            VALUES (:order_id, :product_id, :quantity, :unit_price, :line_amount)
        """), locals())

    @staticmethod
    def lock(order_id):
        return db.session.execute(text("""
            SELECT * FROM biz.sales_order WHERE order_id = :id FOR UPDATE
        """), {"id": order_id}).mappings().first()


class PaymentRepository:
    @staticmethod
    def list():
        return db.session.execute(text("""
            SELECT p.payment_id, p.payment_no, o.order_no, c.name AS customer_name,
                   p.method, p.amount, p.status, p.paid_at
            FROM biz.payment p
            JOIN biz.sales_order o ON o.order_id = p.order_id
            JOIN biz.customer c ON c.customer_id = o.customer_id
            ORDER BY p.paid_at DESC LIMIT 200
        """)).mappings().all()

    @staticmethod
    def create(payment_no, order_id, method, amount, transaction_ref, created_by):
        return db.session.execute(text("""
            INSERT INTO biz.payment
                (payment_no, order_id, method, amount, transaction_ref, created_by)
            VALUES (:payment_no, :order_id, :method, :amount, :transaction_ref, :created_by)
            RETURNING payment_id, paid_at
        """), locals()).mappings().one()


class RefundRepository:
    @staticmethod
    def list():
        return db.session.execute(text("""
            SELECT r.refund_id, r.refund_no, o.order_no, c.name AS customer_name,
                   r.amount, r.reason, r.status, r.refunded_at, r.created_at,
                   r.reviewed_at, r.review_note, reviewer.username AS reviewer_name,
                   COALESCE(SUM(ri.quantity), 0)::int AS requested_quantity,
                   COALESCE(SUM(ri.returned_qty), 0)::int AS returned_quantity,
                   COUNT(ri.refund_item_id) > 0 AS has_item_details
            FROM biz.refund r
            JOIN biz.sales_order o ON o.order_id = r.order_id
            JOIN biz.customer c ON c.customer_id = o.customer_id
            LEFT JOIN biz.refund_item ri ON ri.refund_id = r.refund_id
            LEFT JOIN auth.account reviewer ON reviewer.account_id = r.reviewed_by
            GROUP BY r.refund_id, o.order_no, c.name, reviewer.username
            ORDER BY r.created_at DESC LIMIT 200
        """)).mappings().all()

    @staticmethod
    def refundable_items():
        return db.session.execute(text("""
            SELECT p.payment_id, p.payment_no, o.order_no, c.name AS customer_name,
                   oi.order_item_id, pr.product_name, oi.unit_price,
                   oi.quantity - COALESCE(SUM(ri.quantity) FILTER (
                       WHERE r.status IN ('pending', 'approved', 'success')
                   ), 0)::int AS remaining_quantity
            FROM biz.payment p
            JOIN biz.sales_order o ON o.order_id = p.order_id
            JOIN biz.customer c ON c.customer_id = o.customer_id
            JOIN biz.order_item oi ON oi.order_id = o.order_id
            JOIN biz.product pr ON pr.product_id = oi.product_id
            LEFT JOIN biz.refund r ON r.payment_id = p.payment_id
            LEFT JOIN biz.refund_item ri
              ON ri.refund_id = r.refund_id AND ri.order_item_id = oi.order_item_id
            WHERE p.status = 'success'
              AND NOT EXISTS (
                  SELECT 1
                  FROM biz.refund legacy_refund
                  WHERE legacy_refund.payment_id = p.payment_id
                    AND legacy_refund.status IN ('pending', 'approved', 'success')
                    AND NOT EXISTS (
                        SELECT 1 FROM biz.refund_item legacy_item
                        WHERE legacy_item.refund_id = legacy_refund.refund_id
                    )
              )
            GROUP BY p.payment_id, p.payment_no, o.order_no, c.name,
                     oi.order_item_id, pr.product_name, oi.unit_price, oi.quantity
            HAVING oi.quantity - COALESCE(SUM(ri.quantity) FILTER (
                WHERE r.status IN ('pending', 'approved', 'success')
            ), 0)::int > 0
            ORDER BY p.paid_at DESC, oi.order_item_id
            LIMIT 200
        """)).mappings().all()

    @staticmethod
    def successful_total(payment_id):
        return db.session.execute(text("""
            SELECT COALESCE(SUM(amount), 0) FROM biz.refund
            WHERE payment_id = :id AND status = 'success'
        """), {"id": payment_id}).scalar_one()

    @staticmethod
    def lock_payment(payment_id):
        return db.session.execute(text("""
            SELECT p.payment_id, p.order_id, p.amount, p.status,
                   o.customer_id, o.status AS order_status, o.refunded_amount
            FROM biz.payment p
            JOIN biz.sales_order o ON o.order_id = p.order_id
            WHERE p.payment_id = :payment_id
            FOR UPDATE OF p, o
        """), {"payment_id": payment_id}).mappings().first()

    @staticmethod
    def lock_order_items(order_id, item_ids):
        return db.session.execute(text("""
            SELECT oi.order_item_id, oi.product_id, oi.quantity, oi.unit_price,
                   oi.line_amount, p.product_name
            FROM biz.order_item oi
            JOIN biz.product p ON p.product_id = oi.product_id
            WHERE oi.order_id = :order_id AND oi.order_item_id = ANY(:item_ids)
            ORDER BY oi.product_id, oi.order_item_id
            FOR UPDATE OF oi, p
        """), {"order_id": order_id, "item_ids": list(item_ids)}).mappings().all()

    @staticmethod
    def reserved_quantities(payment_id, item_ids, exclude_refund_id=None):
        rows = db.session.execute(text("""
            SELECT ri.order_item_id, COALESCE(SUM(ri.quantity), 0)::int AS quantity
            FROM biz.refund_item ri
            JOIN biz.refund r ON r.refund_id = ri.refund_id
            WHERE r.payment_id = :payment_id
              AND r.status IN ('pending', 'approved', 'success')
              AND (:exclude_refund_id IS NULL OR r.refund_id <> :exclude_refund_id)
              AND ri.order_item_id = ANY(:item_ids)
            GROUP BY ri.order_item_id
        """), {
            "payment_id": payment_id,
            "item_ids": list(item_ids),
            "exclude_refund_id": exclude_refund_id,
        }).mappings().all()
        return {row["order_item_id"]: row["quantity"] for row in rows}

    @staticmethod
    def has_unallocated_active_refund(payment_id, exclude_refund_id=None):
        return bool(db.session.execute(text("""
            SELECT EXISTS (
                SELECT 1
                FROM biz.refund r
                WHERE r.payment_id = :payment_id
                  AND r.status IN ('pending', 'approved', 'success')
                  AND (:exclude_refund_id IS NULL OR r.refund_id <> :exclude_refund_id)
                  AND NOT EXISTS (
                      SELECT 1 FROM biz.refund_item ri WHERE ri.refund_id = r.refund_id
                  )
            )
        """), {
            "payment_id": payment_id,
            "exclude_refund_id": exclude_refund_id,
        }).scalar_one())

    @staticmethod
    def create_request(refund_no, payment_id, order_id, amount, reason, requested_by):
        return db.session.execute(text("""
            INSERT INTO biz.refund
                (refund_no, payment_id, order_id, amount, reason, status,
                 refunded_at, created_by, requested_by)
            VALUES (:refund_no, :payment_id, :order_id, :amount, :reason, 'pending',
                    NULL, :requested_by, :requested_by)
            RETURNING refund_id
        """), locals()).scalar_one()

    @staticmethod
    def add_items(refund_id, order_id, items):
        db.session.execute(text("""
            INSERT INTO biz.refund_item
                (refund_id, order_id, order_item_id, quantity, refund_amount)
            VALUES (:refund_id, :order_id, :order_item_id, :quantity, :refund_amount)
        """), [
            {"refund_id": refund_id, "order_id": order_id, **item}
            for item in items
        ])

    @staticmethod
    def lock(refund_id):
        return db.session.execute(text("""
            SELECT r.*, p.amount AS payment_amount, p.status AS payment_status,
                   o.customer_id, o.refunded_amount
            FROM biz.refund r
            JOIN biz.payment p ON p.payment_id = r.payment_id
            JOIN biz.sales_order o ON o.order_id = r.order_id
            WHERE r.refund_id = :refund_id
            FOR UPDATE OF r, p, o
        """), {"refund_id": refund_id}).mappings().first()

    @staticmethod
    def lock_items(refund_id):
        return db.session.execute(text("""
            SELECT ri.refund_item_id, ri.order_item_id, ri.quantity, ri.refund_amount,
                   ri.returned_qty, ri.order_id AS refund_item_order_id,
                   oi.order_id, oi.product_id, oi.quantity AS sold_quantity,
                   oi.unit_price, p.product_name
            FROM biz.refund_item ri
            JOIN biz.order_item oi ON oi.order_item_id = ri.order_item_id
            JOIN biz.product p ON p.product_id = oi.product_id
            WHERE ri.refund_id = :refund_id
            ORDER BY oi.product_id, ri.refund_item_id
            FOR UPDATE OF ri, oi, p
        """), {"refund_id": refund_id}).mappings().all()

    @staticmethod
    def mark_item_returned(refund_item_id, quantity):
        db.session.execute(text("""
            UPDATE biz.refund_item SET returned_qty = :quantity
            WHERE refund_item_id = :refund_item_id
        """), {"refund_item_id": refund_item_id, "quantity": quantity})

    @staticmethod
    def approve(refund_id, reviewer_id, review_note):
        return db.session.execute(text("""
            UPDATE biz.refund
            SET status = 'success', reviewed_by = :reviewer_id,
                reviewed_at = now(), review_note = :review_note, refunded_at = now()
            WHERE refund_id = :refund_id AND status = 'pending'
            RETURNING refunded_at
        """), locals()).scalar_one()

    @staticmethod
    def reject(refund_id, reviewer_id, review_note):
        return db.session.execute(text("""
            UPDATE biz.refund
            SET status = 'rejected', reviewed_by = :reviewer_id,
                reviewed_at = now(), review_note = :review_note
            WHERE refund_id = :refund_id AND status = 'pending'
        """), locals()).rowcount


class DashboardRepository:
    @staticmethod
    def current_snapshot():
        return db.session.execute(text("""
            SELECT task_id,
                   (parameters->>'snapshot_date')::date AS snapshot_date,
                   finished_at
            FROM ml.model_task
            WHERE task_type = 'analytics_refresh'
              AND status = 'success'
              AND parameters ? 'snapshot_date'
            ORDER BY (parameters->>'snapshot_date')::date DESC,
                     finished_at DESC, task_id DESC
            LIMIT 1
        """)).mappings().first()

    @staticmethod
    def summary(snapshot=None):
        snapshot = snapshot or DashboardRepository.current_snapshot()
        parameters = {
            "snapshot_date": snapshot["snapshot_date"] if snapshot else None,
            "task_id": snapshot["task_id"] if snapshot else None,
        }
        return db.session.execute(text("""
            SELECT
              (SELECT COUNT(*) FROM biz.customer WHERE status = 'active') AS customer_count,
              (SELECT COALESCE(SUM(order_count), 0) FROM ads.daily_sales
               WHERE snapshot_date = :snapshot_date
                 AND refresh_task_id = :task_id
                 AND sales_date >= date_trunc('month', CAST(:snapshot_date AS date))
              ) AS month_orders,
              (SELECT COALESCE(SUM(net_amount), 0) FROM ads.daily_sales
               WHERE snapshot_date = :snapshot_date
                 AND refresh_task_id = :task_id
                 AND sales_date >= date_trunc('month', CAST(:snapshot_date AS date))
              ) AS month_net_amount,
              (SELECT COUNT(*) FROM biz.product WHERE stock_qty < 10 AND status = 'active') AS low_stock_count
        """), parameters).mappings().one()

    @staticmethod
    def trend(snapshot=None):
        snapshot = snapshot or DashboardRepository.current_snapshot()
        if snapshot is None:
            return []
        return db.session.execute(text("""
            SELECT to_char(date_trunc('month', sales_date), 'YYYY-MM') AS month,
                   SUM(net_amount) AS net_amount
            FROM ads.daily_sales
            WHERE snapshot_date = :snapshot_date
              AND refresh_task_id = :task_id
              AND sales_date >= date_trunc('month', CAST(:snapshot_date AS date))
                               - interval '11 months'
            GROUP BY date_trunc('month', sales_date)
            ORDER BY date_trunc('month', sales_date)
        """), {
            "snapshot_date": snapshot["snapshot_date"],
            "task_id": snapshot["task_id"],
        }).mappings().all()

    @staticmethod
    def recent_orders():
        return OrderRepository.list()[:10]

    @staticmethod
    def low_stock_attention():
        items = db.session.execute(text("""
            SELECT product_id, sku, product_name, stock_qty
            FROM biz.product
            WHERE status = 'active' AND stock_qty < 10
            ORDER BY stock_qty, product_id
            LIMIT 5
        """)).mappings().all()
        count = db.session.execute(text("""
            SELECT COUNT(*)::int FROM biz.product
            WHERE status = 'active' AND stock_qty < 10
        """)).scalar_one()
        return {"count": count, "items": items}

    @staticmethod
    def refund_attention():
        items = db.session.execute(text("""
            SELECT refund_id, refund_no, amount, created_at
            FROM biz.refund
            WHERE status = 'pending'
            ORDER BY created_at, refund_id
            LIMIT 5
        """)).mappings().all()
        count = db.session.execute(text("""
            SELECT COUNT(*)::int FROM biz.refund WHERE status = 'pending'
        """)).scalar_one()
        return {"count": count, "items": items}

    @staticmethod
    def model_attention():
        items = db.session.execute(text("""
            SELECT task_id, task_type, status, started_at, error_message
            FROM ml.model_task
            WHERE task_type <> 'analytics_refresh'
              AND status IN ('pending', 'running', 'failed')
            ORDER BY CASE status WHEN 'failed' THEN 1 WHEN 'running' THEN 2 ELSE 3 END,
                     started_at DESC, task_id DESC
            LIMIT 5
        """)).mappings().all()
        count = db.session.execute(text("""
            SELECT COUNT(*)::int FROM ml.model_task
            WHERE task_type <> 'analytics_refresh'
              AND status IN ('pending', 'running', 'failed')
        """)).scalar_one()
        return {"count": count, "items": items}

    @staticmethod
    def knowledge_attention():
        items = db.session.execute(text("""
            SELECT document_id, title, version, status, is_published
            FROM kb.document
            WHERE status = 'failed' OR (status = 'ready' AND NOT is_published)
            ORDER BY CASE status WHEN 'failed' THEN 1 ELSE 2 END,
                     updated_at DESC, document_id DESC
            LIMIT 5
        """)).mappings().all()
        count = db.session.execute(text("""
            SELECT COUNT(*)::int FROM kb.document
            WHERE status = 'failed' OR (status = 'ready' AND NOT is_published)
        """)).scalar_one()
        return {"count": count, "items": items}

    @staticmethod
    def ticket_attention():
        items = db.session.execute(text("""
            SELECT ticket.ticket_id, ticket.status, source.content AS question
            FROM qa.qa_ticket ticket
            JOIN qa.qa_message source ON source.message_id = ticket.source_message_id
            WHERE ticket.status IN ('pending', 'assigned')
            ORDER BY CASE ticket.status WHEN 'pending' THEN 1 ELSE 2 END,
                     ticket.created_at, ticket.ticket_id
            LIMIT 5
        """)).mappings().all()
        count = db.session.execute(text("""
            SELECT COUNT(*)::int FROM qa.qa_ticket
            WHERE status IN ('pending', 'assigned')
        """)).scalar_one()
        return {"count": count, "items": items}

    @staticmethod
    def workspace(permissions):
        permissions = frozenset(permissions)
        workspace = {
            "snapshot": None,
            "summary": {},
            "trend_labels": [],
            "trend_values": [],
            "recent_orders": [],
            "alerts": {},
        }

        if "analysis.read" in permissions:
            snapshot = DashboardRepository.current_snapshot()
            trend = DashboardRepository.trend(snapshot)
            workspace.update(
                snapshot=snapshot,
                summary=DashboardRepository.summary(snapshot),
                trend_labels=[row["month"] for row in trend],
                trend_values=[float(row["net_amount"]) for row in trend],
            )
        if "order.read" in permissions:
            workspace["recent_orders"] = DashboardRepository.recent_orders()
        if "product.read" in permissions:
            workspace["alerts"]["low_stock"] = DashboardRepository.low_stock_attention()
        if "refund.read" in permissions:
            workspace["alerts"]["refunds"] = DashboardRepository.refund_attention()
        if "model.read" in permissions:
            workspace["alerts"]["models"] = DashboardRepository.model_attention()
        if "knowledge.read" in permissions:
            workspace["alerts"]["knowledge"] = DashboardRepository.knowledge_attention()
        if "qa.handle" in permissions:
            workspace["alerts"]["tickets"] = DashboardRepository.ticket_attention()

        workspace["pending_total"] = sum(
            alert["count"] for alert in workspace["alerts"].values()
        )
        return workspace
