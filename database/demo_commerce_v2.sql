BEGIN;

INSERT INTO biz.product (sku, product_name, category_id, unit_price, stock_qty)
SELECT v.sku, v.product_name, c.category_id, v.unit_price, v.stock_qty
FROM (VALUES
    ('DEMO2-SKU-F003', '精品咖啡豆礼盒', '食品饮料', 128.00::numeric, 85),
    ('DEMO2-SKU-F004', '燕麦脆饼组合装', '食品饮料', 39.90::numeric, 160),
    ('DEMO2-SKU-F005', '常温酸奶整箱', '食品饮料', 72.00::numeric, 96),
    ('DEMO2-SKU-F006', '冷萃茶饮组合', '食品饮料', 45.00::numeric, 7),
    ('DEMO2-SKU-F007', '东北五常大米', '食品饮料', 119.00::numeric, 68),
    ('DEMO2-SKU-H002', '浓缩洗衣凝珠', '家居日用', 69.90::numeric, 110),
    ('DEMO2-SKU-H003', '原生木浆厨房纸', '家居日用', 42.90::numeric, 140),
    ('DEMO2-SKU-H004', '加厚抽绳垃圾袋', '家居日用', 25.90::numeric, 180),
    ('DEMO2-SKU-H005', '柔韧保湿纸巾', '家居日用', 79.90::numeric, 8),
    ('DEMO2-SKU-H006', '不锈钢保温杯', '家居日用', 89.00::numeric, 74),
    ('DEMO2-SKU-D002', '主动降噪蓝牙耳机', '数码家电', 299.00::numeric, 55),
    ('DEMO2-SKU-D003', '多功能智能手环', '数码家电', 229.00::numeric, 63),
    ('DEMO2-SKU-D004', '氮化镓快速充电器', '数码家电', 159.00::numeric, 92),
    ('DEMO2-SKU-D005', '无线机械键盘', '数码家电', 399.00::numeric, 35),
    ('DEMO2-SKU-D006', '高速移动固态硬盘', '数码家电', 699.00::numeric, 28),
    ('DEMO2-SKU-B002', '修护保湿面霜', '美妆个护', 129.00::numeric, 88),
    ('DEMO2-SKU-B003', '清透防晒乳', '美妆个护', 109.00::numeric, 120),
    ('DEMO2-SKU-B004', '滋润护手霜套装', '美妆个护', 49.90::numeric, 150),
    ('DEMO2-SKU-B005', '温和卸妆油', '美妆个护', 89.00::numeric, 64),
    ('DEMO2-SKU-B006', '补水面膜礼盒', '美妆个护', 99.00::numeric, 5)
) AS v(sku, product_name, category_name, unit_price, stock_qty)
JOIN biz.product_category c ON c.category_name = v.category_name
ON CONFLICT (sku) DO NOTHING;

WITH pending_seed AS (
    SELECT
        n,
        format('DEMO2-PEND-SO-%s', lpad(n::text, 3, '0')) AS order_no,
        (ARRAY[
            'DEMO2-SKU-F003', 'DEMO2-SKU-F004', 'DEMO2-SKU-F005', 'DEMO2-SKU-F006', 'DEMO2-SKU-F007',
            'DEMO2-SKU-H002', 'DEMO2-SKU-H003', 'DEMO2-SKU-H004', 'DEMO2-SKU-H005', 'DEMO2-SKU-H006',
            'DEMO2-SKU-D002', 'DEMO2-SKU-D003', 'DEMO2-SKU-D004', 'DEMO2-SKU-D005', 'DEMO2-SKU-D006',
            'DEMO2-SKU-B002', 'DEMO2-SKU-B003', 'DEMO2-SKU-B004', 'DEMO2-SKU-B005', 'DEMO2-SKU-B006'
        ]::text[])[n] AS sku,
        format('C2026000%s', ((n - 1) % 5) + 1) AS customer_no,
        CASE WHEN n % 2 = 0 THEN 2 ELSE 1 END AS quantity,
        now() - make_interval(days => 41 - n) AS ordered_at
    FROM generate_series(1, 20) AS series(n)
)
INSERT INTO biz.sales_order
    (order_no, customer_id, status, total_amount, paid_amount, refunded_amount,
     ordered_at, remark, created_by)
SELECT
    s.order_no,
    c.customer_id,
    'awaiting_payment',
    round(p.unit_price * s.quantity, 2),
    0,
    0,
    s.ordered_at,
    'V2 演示待支付订单',
    a.account_id
FROM pending_seed s
JOIN biz.product p ON p.sku = s.sku
JOIN biz.customer c ON c.customer_no = s.customer_no
JOIN auth.account a ON a.username = 'admin'
ON CONFLICT (order_no) DO NOTHING;

WITH pending_seed AS (
    SELECT
        n,
        format('DEMO2-PEND-SO-%s', lpad(n::text, 3, '0')) AS order_no,
        (ARRAY[
            'DEMO2-SKU-F003', 'DEMO2-SKU-F004', 'DEMO2-SKU-F005', 'DEMO2-SKU-F006', 'DEMO2-SKU-F007',
            'DEMO2-SKU-H002', 'DEMO2-SKU-H003', 'DEMO2-SKU-H004', 'DEMO2-SKU-H005', 'DEMO2-SKU-H006',
            'DEMO2-SKU-D002', 'DEMO2-SKU-D003', 'DEMO2-SKU-D004', 'DEMO2-SKU-D005', 'DEMO2-SKU-D006',
            'DEMO2-SKU-B002', 'DEMO2-SKU-B003', 'DEMO2-SKU-B004', 'DEMO2-SKU-B005', 'DEMO2-SKU-B006'
        ]::text[])[n] AS sku,
        CASE WHEN n % 2 = 0 THEN 2 ELSE 1 END AS quantity
    FROM generate_series(1, 20) AS series(n)
)
INSERT INTO biz.order_item (order_id, product_id, quantity, unit_price, line_amount)
SELECT
    o.order_id,
    p.product_id,
    s.quantity,
    p.unit_price,
    round(p.unit_price * s.quantity, 2)
FROM pending_seed s
JOIN biz.sales_order o ON o.order_no = s.order_no
JOIN biz.product p ON p.sku = s.sku
ON CONFLICT (order_id, product_id) DO NOTHING;

WITH paid_seed AS (
    SELECT
        n,
        format('DEMO2-PAID-SO-%s', lpad(n::text, 3, '0')) AS order_no,
        (ARRAY[
            'DEMO2-SKU-F003', 'DEMO2-SKU-F004', 'DEMO2-SKU-F005', 'DEMO2-SKU-F006', 'DEMO2-SKU-F007',
            'DEMO2-SKU-H002', 'DEMO2-SKU-H003', 'DEMO2-SKU-H004', 'DEMO2-SKU-H005', 'DEMO2-SKU-H006'
        ]::text[])[n] AS sku,
        format('C2026000%s', ((n - 1) % 5) + 1) AS customer_no,
        ((n - 1) % 3) + 1 AS quantity,
        now() - make_interval(days => 11 - n) - interval '4 hours' AS ordered_at,
        now() - make_interval(days => 11 - n) - interval '2 hours' AS paid_at
    FROM generate_series(1, 10) AS series(n)
), priced_paid_seed AS (
    SELECT s.*, round(p.unit_price * s.quantity, 2) AS total_amount
    FROM paid_seed s
    JOIN biz.product p ON p.sku = s.sku
)
INSERT INTO biz.sales_order
    (order_no, customer_id, status, total_amount, paid_amount, refunded_amount,
     ordered_at, paid_at, remark, created_by)
SELECT
    s.order_no,
    c.customer_id,
    CASE WHEN s.n <= 6 THEN 'partially_refunded' ELSE 'refunded' END,
    s.total_amount,
    s.total_amount,
    CASE WHEN s.n <= 6 THEN round(s.total_amount * 0.25, 2) ELSE s.total_amount END,
    s.ordered_at,
    s.paid_at,
    'V2 演示已支付退款订单',
    a.account_id
FROM priced_paid_seed s
JOIN biz.customer c ON c.customer_no = s.customer_no
JOIN auth.account a ON a.username = 'admin'
ON CONFLICT (order_no) DO NOTHING;

WITH paid_seed AS (
    SELECT
        n,
        format('DEMO2-PAID-SO-%s', lpad(n::text, 3, '0')) AS order_no,
        (ARRAY[
            'DEMO2-SKU-F003', 'DEMO2-SKU-F004', 'DEMO2-SKU-F005', 'DEMO2-SKU-F006', 'DEMO2-SKU-F007',
            'DEMO2-SKU-H002', 'DEMO2-SKU-H003', 'DEMO2-SKU-H004', 'DEMO2-SKU-H005', 'DEMO2-SKU-H006'
        ]::text[])[n] AS sku,
        ((n - 1) % 3) + 1 AS quantity
    FROM generate_series(1, 10) AS series(n)
)
INSERT INTO biz.order_item (order_id, product_id, quantity, unit_price, line_amount)
SELECT
    o.order_id,
    p.product_id,
    s.quantity,
    p.unit_price,
    round(p.unit_price * s.quantity, 2)
FROM paid_seed s
JOIN biz.sales_order o ON o.order_no = s.order_no
JOIN biz.product p ON p.sku = s.sku
ON CONFLICT (order_id, product_id) DO NOTHING;

WITH payment_seed AS (
    SELECT
        n,
        format('DEMO2-PAID-SO-%s', lpad(n::text, 3, '0')) AS order_no,
        format('DEMO2-PAY-%s', lpad(n::text, 3, '0')) AS payment_no,
        CASE (n - 1) % 4
            WHEN 0 THEN 'wechat'
            WHEN 1 THEN 'alipay'
            WHEN 2 THEN 'bank_card'
            ELSE 'cash'
        END AS method
    FROM generate_series(1, 10) AS series(n)
)
INSERT INTO biz.payment
    (payment_no, order_id, method, amount, status, paid_at, transaction_ref, created_by)
SELECT
    s.payment_no,
    o.order_id,
    s.method,
    o.paid_amount,
    'success',
    o.paid_at,
    format('DEMO2-TXN-%s', lpad(s.n::text, 3, '0')),
    a.account_id
FROM payment_seed s
JOIN biz.sales_order o ON o.order_no = s.order_no
JOIN auth.account a ON a.username = 'admin'
ON CONFLICT DO NOTHING;

WITH refund_seed AS (
    SELECT
        n,
        format('DEMO2-PAY-%s', lpad(n::text, 3, '0')) AS payment_no,
        format('DEMO2-REF-%s', lpad(n::text, 3, '0')) AS refund_no,
        (ARRAY[
            '商品包装轻微破损', '到货时间晚于预期', '商品颜色与页面存在差异', '重复下单', '配件缺失',
            '尺寸不合适', '商品质量问题', '发错商品', '运输途中严重破损', '客户取消整单'
        ]::text[])[n] AS reason
    FROM generate_series(1, 10) AS series(n)
)
INSERT INTO biz.refund
    (refund_no, payment_id, order_id, amount, reason, status, refunded_at, created_by)
SELECT
    s.refund_no,
    p.payment_id,
    o.order_id,
    o.refunded_amount,
    s.reason,
    'success',
    p.paid_at + make_interval(hours => 4 + s.n),
    a.account_id
FROM refund_seed s
JOIN biz.payment p ON p.payment_no = s.payment_no
JOIN biz.sales_order o ON o.order_id = p.order_id
JOIN auth.account a ON a.username = 'admin'
ON CONFLICT DO NOTHING;

INSERT INTO dwd.consumption_flow
    (customer_id, order_id, payment_id, refund_id, flow_type,
     gross_amount, net_amount, occurred_at)
SELECT
    o.customer_id,
    o.order_id,
    p.payment_id,
    NULL,
    'payment',
    p.amount,
    p.amount,
    p.paid_at
FROM biz.payment p
JOIN biz.sales_order o ON o.order_id = p.order_id
WHERE p.payment_no LIKE 'DEMO2-PAY-%'
ON CONFLICT (payment_id) WHERE flow_type = 'payment' DO NOTHING;

INSERT INTO dwd.consumption_flow
    (customer_id, order_id, payment_id, refund_id, flow_type,
     gross_amount, net_amount, occurred_at)
SELECT
    o.customer_id,
    o.order_id,
    r.payment_id,
    r.refund_id,
    'refund',
    r.amount,
    -r.amount,
    r.refunded_at
FROM biz.refund r
JOIN biz.sales_order o ON o.order_id = r.order_id
WHERE r.refund_no LIKE 'DEMO2-REF-%'
ON CONFLICT (refund_id) WHERE flow_type = 'refund' DO NOTHING;

COMMIT;
