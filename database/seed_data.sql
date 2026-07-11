-- ============================================================
-- 消费记录模拟数据生成脚本
-- 生成约2年的真实感消费数据
-- ============================================================

DO 
DECLARE
    v_user_id INT;
    v_start_date DATE := '2024-07-01';
    v_end_date DATE := '2026-07-09';
    v_cur_date DATE;
    v_category_id INT;
    v_merchant_id INT;
    v_cu_id INT;
    v_amount NUMERIC(12,2);
    v_records_per_day INT;
    v_i INT;
    v_payment TEXT;
    v_payments TEXT[] := ARRAY['微信支付', '支付宝', '银行卡', '现金'];
    v_remarks TEXT[] := ARRAY['日常消费', '周末购物', '生活用品', '', '', ''];
BEGIN
    -- 获取 demo 用户 ID
    SELECT id INTO v_user_id FROM users WHERE username = 'demo';
    IF v_user_id IS NULL THEN
        RAISE NOTICE 'demo user not found, skipping seed data';
        RETURN;
    END IF;

    v_cur_date := v_start_date;
    WHILE v_cur_date <= v_end_date LOOP
        -- 周末多消费一些
        IF EXTRACT(DOW FROM v_cur_date) IN (0, 6) THEN
            v_records_per_day := 2 + floor(random() * 4)::INT;
        ELSE
            v_records_per_day := 1 + floor(random() * 3)::INT;
        END IF;

        FOR v_i IN 1..v_records_per_day LOOP
            -- 随机选择分类
            SELECT category_id INTO v_category_id
            FROM spending_category ORDER BY random() LIMIT 1;

            -- 根据分类设置金额范围
            CASE (SELECT parent_category FROM spending_category WHERE category_id = v_category_id)
                WHEN '食品烟酒' THEN v_amount := round((random() * 80 + 10)::numeric, 2);
                WHEN '交通出行' THEN v_amount := round((random() * 50 + 3)::numeric, 2);
                WHEN '居住' THEN v_amount := round((random() * 200 + 50)::numeric, 2);
                WHEN '购物' THEN v_amount := round((random() * 300 + 20)::numeric, 2);
                WHEN '娱乐' THEN v_amount := round((random() * 100 + 15)::numeric, 2);
                WHEN '教育' THEN v_amount := round((random() * 150 + 20)::numeric, 2);
                WHEN '医疗' THEN v_amount := round((random() * 200 + 10)::numeric, 2);
                ELSE v_amount := round((random() * 100 + 10)::numeric, 2);
            END CASE;

            -- 随机选择商户
            SELECT merchant_id INTO v_merchant_id
            FROM merchant ORDER BY random() LIMIT 1;

            -- 随机选择地域
            SELECT cu_id INTO v_cu_id
            FROM consumer_unit ORDER BY random() LIMIT 1;

            -- 随机支付方式
            v_payment := v_payments[1 + floor(random() * array_length(v_payments, 1))::INT];

            INSERT INTO spending_record (user_id, spend_date, amount, payment_method, merchant_id, category_id, cu_id, remarks)
            VALUES (v_user_id, v_cur_date, v_amount, v_payment, v_merchant_id, v_category_id, v_cu_id,
                    v_remarks[1 + floor(random() * array_length(v_remarks, 1))::INT]);
        END LOOP;

        v_cur_date := v_cur_date + 1;
    END LOOP;

    RAISE NOTICE 'Seed data generated successfully!';
END ;

-- 刷新物化视图
SELECT refresh_materialized_views();