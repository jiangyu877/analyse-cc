-- Correct Release A data written by migration 002 without changing an applied file.
LOCK TABLE
    biz.payment,
    biz.sales_order,
    biz.order_item,
    biz.product,
    biz.refund,
    biz.refund_item,
    biz.inventory_log,
    dwd.consumption_flow
IN ACCESS EXCLUSIVE MODE NOWAIT;

DO $$
DECLARE
    correction RECORD;
    current_stock INTEGER;
BEGIN
    IF EXISTS (
        SELECT 1
        FROM biz.refund_item ri
        JOIN audit.schema_migration migration
          ON migration.version = '002_inventory_and_refund_workflow.sql'
         AND ri.created_at = migration.applied_at
        JOIN biz.inventory_log inventory
          ON inventory.refund_item_id = ri.refund_item_id
        WHERE inventory.change_type <> 'refund_return'
           OR inventory.quantity_delta <= 0
    ) THEN
        RAISE EXCEPTION 'unsupported inventory history references a migration-002 refund item';
    END IF;

    FOR correction IN
        SELECT inventory.inventory_log_id, inventory.product_id,
               inventory.order_id, inventory.refund_id,
               inventory.quantity_delta, inventory.operator_id
        FROM biz.refund_item ri
        JOIN audit.schema_migration migration
          ON migration.version = '002_inventory_and_refund_workflow.sql'
         AND ri.created_at = migration.applied_at
        JOIN biz.inventory_log inventory
          ON inventory.refund_item_id = ri.refund_item_id
        WHERE inventory.change_type = 'refund_return'
          AND inventory.quantity_delta > 0
        ORDER BY inventory.inventory_log_id
    LOOP
        SELECT stock_qty INTO current_stock
        FROM biz.product
        WHERE product_id = correction.product_id
        FOR UPDATE;

        IF current_stock < correction.quantity_delta THEN
            RAISE EXCEPTION
                'cannot reconcile fabricated refund inventory log %, product % has stock % but needs %',
                correction.inventory_log_id, correction.product_id,
                current_stock, correction.quantity_delta;
        END IF;

        UPDATE biz.product
        SET stock_qty = current_stock - correction.quantity_delta,
            updated_at = now()
        WHERE product_id = correction.product_id;

        UPDATE biz.inventory_log
        SET refund_item_id = NULL
        WHERE inventory_log_id = correction.inventory_log_id;

        INSERT INTO biz.inventory_log
            (product_id, order_id, refund_id, refund_item_id, change_type,
             quantity_delta, before_qty, after_qty, operator_id, remark)
        VALUES
            (correction.product_id, correction.order_id, correction.refund_id,
             NULL, 'manual_adjustment', -correction.quantity_delta,
             current_stock, current_stock - correction.quantity_delta,
             correction.operator_id,
             'Reversed fabricated migration-002 refund inventory return');
    END LOOP;
END $$;

UPDATE biz.inventory_log inventory
SET refund_item_id = NULL
FROM biz.refund_item ri
JOIN audit.schema_migration migration
  ON migration.version = '002_inventory_and_refund_workflow.sql'
 AND ri.created_at = migration.applied_at
WHERE inventory.refund_item_id = ri.refund_item_id;

DELETE FROM biz.refund_item ri
USING audit.schema_migration migration
WHERE migration.version = '002_inventory_and_refund_workflow.sql'
  AND ri.created_at = migration.applied_at;

ALTER TABLE biz.refund_item
    ADD COLUMN order_id BIGINT;

UPDATE biz.refund_item ri
SET order_id = oi.order_id
FROM biz.order_item oi
WHERE oi.order_item_id = ri.order_item_id;

ALTER TABLE biz.refund_item
    ALTER COLUMN order_id SET NOT NULL;

ALTER TABLE biz.order_item
    ADD CONSTRAINT uq_order_item_id_order UNIQUE (order_item_id, order_id);

ALTER TABLE biz.refund
    ADD CONSTRAINT uq_refund_id_order UNIQUE (refund_id, order_id);

ALTER TABLE biz.refund_item
    ADD CONSTRAINT fk_refund_item_refund_order
        FOREIGN KEY (refund_id, order_id)
        REFERENCES biz.refund(refund_id, order_id) ON DELETE CASCADE,
    ADD CONSTRAINT fk_refund_item_order_item_order
        FOREIGN KEY (order_item_id, order_id)
        REFERENCES biz.order_item(order_item_id, order_id);

WITH role_map(legacy_role, role_code, priority) AS (VALUES
    ('admin', 'system_admin', 1),
    ('operator', 'order_operator', 1),
    ('operator', 'customer_operator', 2),
    ('operator', 'product_operator', 3),
    ('analyst', 'data_analyst', 1),
    ('analyst', 'model_operator', 2)
)
INSERT INTO auth.account_role (account_id, role_id, is_primary)
SELECT account.account_id, role.role_id, FALSE
FROM auth.account account
JOIN role_map mapping ON mapping.legacy_role = account.role
JOIN auth.role role ON role.role_code = mapping.role_code
ON CONFLICT (account_id, role_id) DO NOTHING;

WITH preferred AS (
    SELECT account.account_id, role.role_id
    FROM auth.account account
    JOIN auth.role role ON role.role_code = CASE account.role
        WHEN 'admin' THEN 'system_admin'
        WHEN 'operator' THEN 'order_operator'
        ELSE 'data_analyst'
    END
    WHERE NOT EXISTS (
        SELECT 1
        FROM auth.account_role existing
        WHERE existing.account_id = account.account_id AND existing.is_primary
    )
)
UPDATE auth.account_role assignment
SET is_primary = TRUE
FROM preferred
WHERE assignment.account_id = preferred.account_id
  AND assignment.role_id = preferred.role_id;
