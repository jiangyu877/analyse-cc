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
        db.session.execute(text("""
            UPDATE biz.product SET stock_qty = stock_qty - :quantity, updated_at = now()
            WHERE product_id = :id
        """), {"id": product_id, "quantity": quantity})


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
                   r.amount, r.reason, r.status, r.refunded_at
            FROM biz.refund r
            JOIN biz.sales_order o ON o.order_id = r.order_id
            JOIN biz.customer c ON c.customer_id = o.customer_id
            ORDER BY r.refunded_at DESC LIMIT 200
        """)).mappings().all()

    @staticmethod
    def successful_total(payment_id):
        return db.session.execute(text("""
            SELECT COALESCE(SUM(amount), 0) FROM biz.refund
            WHERE payment_id = :id AND status = 'success'
        """), {"id": payment_id}).scalar_one()

    @staticmethod
    def create(refund_no, payment_id, order_id, amount, reason, created_by):
        return db.session.execute(text("""
            INSERT INTO biz.refund
                (refund_no, payment_id, order_id, amount, reason, created_by)
            VALUES (:refund_no, :payment_id, :order_id, :amount, :reason, :created_by)
            RETURNING refund_id, refunded_at
        """), locals()).mappings().one()


class DashboardRepository:
    @staticmethod
    def summary():
        return db.session.execute(text("""
            SELECT
              (SELECT COUNT(*) FROM biz.customer WHERE status = 'active') AS customer_count,
              (SELECT COUNT(*) FROM biz.sales_order WHERE ordered_at >= date_trunc('month', now())) AS month_orders,
              (SELECT COALESCE(SUM(net_amount), 0) FROM dwd.consumption_flow
               WHERE occurred_at >= date_trunc('month', now())) AS month_net_amount,
              (SELECT COUNT(*) FROM biz.product WHERE stock_qty < 10 AND status = 'active') AS low_stock_count
        """)).mappings().one()

    @staticmethod
    def trend():
        return db.session.execute(text("""
            SELECT to_char(date_trunc('month', occurred_at), 'YYYY-MM') AS month,
                   SUM(net_amount) AS net_amount
            FROM dwd.consumption_flow
            WHERE occurred_at >= date_trunc('month', now()) - interval '11 months'
            GROUP BY date_trunc('month', occurred_at) ORDER BY date_trunc('month', occurred_at)
        """)).mappings().all()

    @staticmethod
    def recent_orders():
        return OrderRepository.list()[:10]

