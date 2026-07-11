BEGIN;

INSERT INTO auth.account (username, password_hash, full_name, role) VALUES
('admin', crypt(encode(gen_random_bytes(32), 'hex'), gen_salt('bf', 12)), '系统管理员', 'admin'),
('operator', crypt(encode(gen_random_bytes(32), 'hex'), gen_salt('bf', 12)), '业务操作员', 'operator'),
('analyst', crypt(encode(gen_random_bytes(32), 'hex'), gen_salt('bf', 12)), '数据分析员', 'analyst')
ON CONFLICT (username) DO NOTHING;

INSERT INTO biz.product_category (category_name) VALUES
('食品饮料'), ('家居日用'), ('数码家电'), ('美妆个护')
ON CONFLICT (category_name) DO NOTHING;

INSERT INTO biz.customer (customer_no, name, gender, phone, email, province, city, registered_at) VALUES
('C20260001', '王晓明', '男', '13800001001', 'xiaoming@example.com', '浙江', '杭州', now() - interval '420 days'),
('C20260002', '李雨桐', '女', '13800001002', 'yutong@example.com', '上海', '上海', now() - interval '360 days'),
('C20260003', '陈思远', '男', '13800001003', 'siyuan@example.com', '四川', '成都', now() - interval '280 days'),
('C20260004', '周可欣', '女', '13800001004', 'kexin@example.com', '广东', '深圳', now() - interval '190 days'),
('C20260005', '赵一诺', '女', '13800001005', 'yinuo@example.com', '北京', '北京', now() - interval '120 days')
ON CONFLICT (customer_no) DO NOTHING;

INSERT INTO biz.product (sku, product_name, category_id, unit_price, stock_qty)
SELECT v.sku, v.product_name, c.category_id, v.unit_price, v.stock_qty
FROM (VALUES
    ('SKU-F001', '精品挂耳咖啡 10包', '食品饮料', 59.90::numeric, 200),
    ('SKU-F002', '每日坚果礼盒', '食品饮料', 89.00::numeric, 120),
    ('SKU-H001', '抑菌洗衣液 2L', '家居日用', 45.80::numeric, 150),
    ('SKU-D001', '便携蓝牙音箱', '数码家电', 199.00::numeric, 80),
    ('SKU-B001', '氨基酸洁面乳', '美妆个护', 79.00::numeric, 100)
) AS v(sku, product_name, category_name, unit_price, stock_qty)
JOIN biz.product_category c ON c.category_name = v.category_name
ON CONFLICT (sku) DO NOTHING;

COMMIT;
