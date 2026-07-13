# Commerce Demo Data Design

## Context

The deployed application already contains about 50,000 imported paid orders and payments, but its operational demo surfaces are uneven:

- the catalog has only four categories and five products;
- imported orders are already paid, so the payment form has no `awaiting_payment` orders to operate on;
- the bulk importer creates no refunds, so the refund history is empty;
- the refund form can render every refundable payment, which may produce about 50,000 HTML options;
- the V1 bulk importer assigns products from the complete active product pool, so adding products and rerunning it can remap old order numbers to different products.

## Goals

1. Increase the catalog from five products to about twenty-five products while retaining the existing four categories.
2. Add twenty deterministic pending orders that can be completed from the payment page.
3. Add ten deterministic successful payments and ten linked refunds so both payment and refund histories have representative recent data.
4. Include both partial and full refund states and matching negative consumption flows.
5. Backfill an already-populated Render database on its next deployment without clearing or overwriting user-managed data.
6. Keep repeated deployments idempotent and protect the existing 50,000 imported transactions.

## Non-Goals

- Do not change the product, payment, or refund page layout.
- Do not replace the existing 50,000-transaction history.
- Do not reset prices, inventory, payment status, or refund status after an operator changes data.
- Do not create a generic fixture framework or expose a production data-generation endpoint.

## Chosen Approach

Add a separate transactional SQL seed, `database/demo_commerce_v2.sql`, and execute it from `scripts/init_db.py` after `v2_seed.sql`. The SQL owns a new `DEMO2-*` identifier namespace and inserts only missing rows with stable business keys.

This is preferred over modifying the V1 bulk importer because existing Render databases already skip that importer after 50,000 payments exist. It is also preferred over manual production SQL because the repository remains the repeatable source of truth.

## Demo Dataset

### Products

Add twenty products, bringing the catalog to approximately twenty-five products. Reuse the existing categories:

- food and beverages;
- household goods;
- digital appliances;
- beauty and personal care.

The set includes normal inventory levels plus three products below ten units so the existing low-stock warning is visible. Product identifiers use `DEMO2-SKU-*`. Inserts use `ON CONFLICT (sku) DO NOTHING`; no deployment may update an existing product's price, stock, name, category, or status.

### Pending Orders

Add twenty `awaiting_payment` orders using the five base seed customers and the new products. Identifiers use `DEMO2-PEND-SO-*`. Each order has one deterministic order item and a total equal to `quantity * unit_price`.

The stored inventory values represent inventory after these demo reservations. A later deployment must not recreate a paid pending order or reset it to `awaiting_payment` because the order insert is `ON CONFLICT (order_no) DO NOTHING`.

### Paid and Refunded Orders

Add ten separate paid orders using `DEMO2-PAID-SO-*`, one successful payment per order using `DEMO2-PAY-*`, and one refund per payment using `DEMO2-REF-*`.

- six orders receive deterministic partial refunds;
- four orders receive full refunds;
- payment methods rotate across `wechat`, `alipay`, `bank_card`, and `cash`;
- refund reasons rotate across a short fixed set of realistic after-sales reasons.

The final order state is `partially_refunded` for partial refunds and `refunded` for full refunds.

## Data Integrity Rules

For every seeded paid/refunded order:

- `sales_order.total_amount = order_item.line_amount`;
- `sales_order.paid_amount = payment.amount = sales_order.total_amount`;
- `sales_order.refunded_amount = SUM(successful refund.amount)`;
- cumulative refunds never exceed the successful payment amount;
- `refund.order_id` equals the order attached to `refund.payment_id`;
- each successful payment has one positive `dwd.consumption_flow`;
- each refund has one negative `dwd.consumption_flow` with the same absolute amount;
- timestamps follow `ordered_at <= paid_at <= refunded_at <= now()`.

All inserts run inside one transaction. A failure rolls back the entire demo seed rather than leaving a partial chain.

## Idempotency and Existing Data

Every entity uses a deterministic unique business number and `ON CONFLICT DO NOTHING`. The seed never uses `DO UPDATE` for mutable operational rows.

Running the seed repeatedly must keep these counts stable:

- twenty `DEMO2-SKU-*` products;
- twenty `DEMO2-PEND-SO-*` pending-origin orders;
- ten `DEMO2-PAID-SO-*` paid/refunded orders;
- ten `DEMO2-PAY-*` payments;
- ten `DEMO2-REF-*` refunds;
- ten positive and ten negative demo consumption flows.

If an operator pays one of the pending orders, later deployments preserve its payment and new status. If an operator changes a seeded product's price or stock, later deployments preserve that value.

## V1 Import Protection

Change the V1 `product_pool` queries in `scripts/import_demo_data.py` to select only the original five seed SKUs:

- `SKU-F001`;
- `SKU-F002`;
- `SKU-H001`;
- `SKU-D001`;
- `SKU-B001`.

This keeps the historical order-number-to-product mapping stable after the catalog expands. It also prevents a partial rerun from adding a different product item to an existing V1 order.

## Refund Form Bound

Limit the refundable-payment query to the most recent 200 eligible payments after ordering by `paid_at DESC`. The payment and refund history repositories already use the same 200-row display bound. This avoids rendering tens of thousands of `<option>` elements without changing the workflow.

## Deployment Flow

The startup sequence remains:

1. apply `v2_schema.sql`;
2. apply `v2_seed.sql`;
3. apply `demo_commerce_v2.sql`;
4. check and, when necessary, run the existing 50,000-transaction V1 import;
5. start the web server.

Because the new demo seed is self-contained and uses the five base customers, it does not depend on the V1 import already being present. Existing Render databases receive the new records on the next deployment.

## Error Handling

- Missing base customers, categories, products, or the admin account causes the transaction to fail visibly during bootstrap.
- Foreign-key, check-constraint, or amount-consistency errors abort startup instead of publishing inconsistent demo data.
- No retry loop is added in this feature; existing startup behavior remains responsible for database availability.
- The application services remain authoritative for subsequent manual payment and refund actions.

## Testing

### Contract Tests

Add fast source-level tests that verify:

- `init_db.py` applies the demo seed after the base seed;
- the V1 importer explicitly fixes its product pool to the original five SKUs;
- the refund form query has a 200-row bound;
- the demo SQL contains the expected entity prefixes and positive/negative flow inserts.

### PostgreSQL Integration Test

Run a real PostgreSQL service in GitHub Actions and execute schema, base seed, and demo seed twice against an isolated test database. Verify:

1. entity counts do not change on the second run;
2. twenty products, twenty pending-origin orders, ten payments, and ten refunds exist;
3. six orders are partially refunded and four are fully refunded;
4. payment, refund, order, and flow amounts satisfy the integrity formulas;
5. every refund references its payment's order;
6. a manually changed product stock value survives another seed run;
7. a pending-origin order changed to paid is not reset;
8. the expanded catalog does not change the V1 importer product pool.

Local test runs skip only the PostgreSQL integration case when `TEST_DATABASE_URL` is absent; the complete test runs in CI.

## Acceptance Criteria

- The product page shows approximately twenty-five products, including three low-stock examples.
- The payment form exposes twenty initial pending demo orders.
- The payment history includes ten new deterministic successful payments in addition to the existing history.
- The refund history includes ten refunds with six partial and four full outcomes.
- The refund form loads at most 200 eligible payments.
- Reapplying the seed produces no duplicate rows and preserves operator changes.
- Existing V1 order totals and item mappings remain unchanged.
- All unit, contract, and PostgreSQL integration tests pass.
