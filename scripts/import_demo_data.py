import argparse
import sys
from pathlib import Path

import psycopg2
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Config


CUSTOMER_SQL = """
INSERT INTO biz.customer
    (customer_no, name, gender, phone, email, province, city, registered_at)
SELECT
    'IMP-C-' || lpad(n::text, 6, '0'),
    (ARRAY['王','李','张','刘','陈','杨','赵','黄','周','吴'])[(n - 1) %% 10 + 1]
        || (ARRAY['子涵','雨桐','思远','可欣','一诺','嘉怡','浩然','梓萱','宇轩','若曦'])[((n * 7) - 1) %% 10 + 1],
    CASE WHEN n %% 2 = 0 THEN '女' ELSE '男' END,
    '13' || lpad((800000000 + n)::text, 9, '0'),
    'customer' || n || '@example.com',
    (ARRAY['浙江','上海','四川','广东','北京','江苏','湖北','福建'])[(n - 1) %% 8 + 1],
    (ARRAY['杭州','上海','成都','深圳','北京','南京','武汉','厦门'])[(n - 1) %% 8 + 1],
    now() - ((n * 17) %% 900) * interval '1 day'
FROM generate_series(1, %s) AS n
ON CONFLICT (customer_no) DO NOTHING
"""

ORDER_SQL = """
WITH customer_pool AS (
    SELECT customer_id, row_number() OVER (ORDER BY customer_no) AS rn
    FROM biz.customer WHERE customer_no LIKE 'IMP-C-%%'
), product_pool AS (
    SELECT product_id, unit_price, row_number() OVER (ORDER BY product_id) AS rn,
           count(*) OVER () AS total_products
    FROM biz.product WHERE status = 'active'
), generated AS (
    SELECT n, c.customer_id, p.product_id, p.unit_price,
           1 + (n %% 3) AS quantity,
           now() - ((n * 37) %% 730) * interval '1 day'
               - ((n * 13) %% 86400) * interval '1 second' AS occurred_at
    FROM generate_series(1, %s) AS n
    JOIN customer_pool c ON c.rn = ((n - 1) %% %s) + 1
    JOIN product_pool p ON p.rn = ((n - 1) %% p.total_products) + 1
)
INSERT INTO biz.sales_order
    (order_no, customer_id, status, total_amount, paid_amount, ordered_at,
     paid_at, remark, created_by)
SELECT 'IMP-SO-' || lpad(n::text, 8, '0'), customer_id, 'paid',
       round(unit_price * quantity, 2), round(unit_price * quantity, 2),
       occurred_at, occurred_at + interval '5 minutes', '批量导入历史消费',
       (SELECT account_id FROM auth.account WHERE username = 'admin')
FROM generated
ON CONFLICT (order_no) DO NOTHING
"""

ITEM_SQL = """
WITH product_pool AS (
    SELECT product_id, unit_price, row_number() OVER (ORDER BY product_id) AS rn,
           count(*) OVER () AS total_products
    FROM biz.product WHERE status = 'active'
), generated AS (
    SELECT n, p.product_id, p.unit_price, 1 + (n %% 3) AS quantity
    FROM generate_series(1, %s) AS n
    JOIN product_pool p ON p.rn = ((n - 1) %% p.total_products) + 1
)
INSERT INTO biz.order_item (order_id, product_id, quantity, unit_price, line_amount)
SELECT o.order_id, g.product_id, g.quantity, g.unit_price,
       round(g.unit_price * g.quantity, 2)
FROM generated g
JOIN biz.sales_order o ON o.order_no = 'IMP-SO-' || lpad(g.n::text, 8, '0')
ON CONFLICT (order_id, product_id) DO NOTHING
"""

PAYMENT_SQL = """
INSERT INTO biz.payment
    (payment_no, order_id, method, amount, status, paid_at, transaction_ref, created_by)
SELECT 'IMP-PAY-' || substring(o.order_no FROM 8), o.order_id,
       (ARRAY['wechat','alipay','bank_card','cash'])[((o.order_id - 1) % 4) + 1],
       o.total_amount, 'success', o.paid_at,
       'HIST-' || substring(o.order_no FROM 8),
       (SELECT account_id FROM auth.account WHERE username = 'admin')
FROM biz.sales_order o
WHERE o.order_no LIKE 'IMP-SO-%'
ON CONFLICT (payment_no) DO NOTHING
"""

FLOW_SQL = """
INSERT INTO dwd.consumption_flow
    (customer_id, order_id, payment_id, flow_type, gross_amount, net_amount, occurred_at)
SELECT o.customer_id, o.order_id, p.payment_id, 'payment', p.amount, p.amount, p.paid_at
FROM biz.payment p
JOIN biz.sales_order o ON o.order_id = p.order_id
WHERE p.payment_no LIKE 'IMP-PAY-%'
  AND NOT EXISTS (
      SELECT 1 FROM dwd.consumption_flow f
      WHERE f.payment_id = p.payment_id AND f.flow_type = 'payment'
  )
"""


def connect():
    url = make_url(Config.SQLALCHEMY_DATABASE_URI)
    return psycopg2.connect(
        host=url.host, port=url.port or 5432, dbname=url.database,
        user=url.username, password=url.password,
    )


def main():
    parser = argparse.ArgumentParser(description="Import deterministic retail demo data")
    parser.add_argument("--customers", type=int, default=5000)
    parser.add_argument("--transactions", type=int, default=50000)
    args = parser.parse_args()
    if args.customers < 1 or args.transactions < 50000:
        parser.error("customers must be positive and transactions must be at least 50000")

    batch_no = f"DEMO-{args.customers}C-{args.transactions}T-V1"
    connection = connect()
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO ods.import_batch (batch_no, source_name, status, started_at)
                    VALUES (%s, '系统生成零售历史数据', 'running', now())
                    ON CONFLICT (batch_no) DO UPDATE SET
                        status = 'running', started_at = now(), finished_at = NULL,
                        error_message = NULL
                """, (batch_no,))
                cursor.execute(CUSTOMER_SQL, (args.customers,))
                cursor.execute(ORDER_SQL, (args.transactions, args.customers))
                cursor.execute(ITEM_SQL, (args.transactions,))
                cursor.execute(PAYMENT_SQL)
                cursor.execute(FLOW_SQL)
                cursor.execute("""
                    UPDATE ods.import_batch SET status = 'success',
                        customer_count = (SELECT count(*) FROM biz.customer WHERE customer_no LIKE 'IMP-C-%%'),
                        transaction_count = (
                            SELECT count(*) FROM dwd.consumption_flow f
                            JOIN biz.payment p ON p.payment_id = f.payment_id
                            WHERE f.flow_type = 'payment' AND p.payment_no LIKE 'IMP-PAY-%%'
                        ),
                        finished_at = now()
                    WHERE batch_no = %s
                    RETURNING customer_count, transaction_count
                """, (batch_no,))
                customers, transactions = cursor.fetchone()
        print(f"batch={batch_no} customers={customers} transactions={transactions}")
    except Exception as exc:
        connection.rollback()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute("""
                        UPDATE ods.import_batch SET status = 'failed', finished_at = now(),
                            error_message = %s WHERE batch_no = %s
                    """, (str(exc)[:2000], batch_no))
        finally:
            connection.close()
        raise
    else:
        connection.close()


if __name__ == "__main__":
    main()
