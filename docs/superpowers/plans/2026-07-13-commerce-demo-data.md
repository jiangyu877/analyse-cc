# Commerce Demo Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an idempotent operational demo dataset with about twenty-five products, twenty payable orders, ten successful payments, and ten linked refunds without changing or duplicating the existing 50,000-transaction history.

**Architecture:** Add a self-contained PostgreSQL seed that owns a `DEMO2-*` identifier namespace and is applied after the base seed on every deployment. Protect the V1 importer with an immutable five-SKU product pool that retains original products even when their mutable status is inactive, bound the refund selector to 200 rows, and verify repeatability and cross-table amounts with a real PostgreSQL integration test in CI.

**Tech Stack:** Python 3.12, PostgreSQL, psycopg2, Flask, pytest, GitHub Actions, Render Blueprint.

---

### Task 0: Commit the approved design artifacts

**Files:**
- Add: `docs/superpowers/specs/2026-07-13-commerce-demo-data-design.md`
- Add: `docs/superpowers/plans/2026-07-13-commerce-demo-data.md`

- [ ] **Step 1: Verify only the approved design artifacts are untracked**

Run:

```powershell
$Git = 'C:\Users\jiang\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe'
& $Git status --short
```

Expected: the design and plan files are the only untracked files.

- [ ] **Step 2: Commit the design artifacts**

```powershell
& $Git add -- `
  docs/superpowers/specs/2026-07-13-commerce-demo-data-design.md `
  docs/superpowers/plans/2026-07-13-commerce-demo-data.md
& $Git commit -m 'Document commerce demo data design'
```

Expected: one documentation commit containing exactly two files.

### Task 1: Freeze the V1 importer product pool

**Files:**
- Modify: `tests/test_import_contract.py`
- Modify: `scripts/import_demo_data.py`

- [ ] **Step 1: Write the failing V1 product-pool test**

Append to `tests/test_import_contract.py`:

```python
from scripts import import_demo_data


def test_v1_import_uses_only_the_original_product_pool():
    expected = (
        "SKU-F001",
        "SKU-F002",
        "SKU-H001",
        "SKU-D001",
        "SKU-B001",
    )

    assert import_demo_data.V1_PRODUCT_SKUS == expected
    for statement in (import_demo_data.ORDER_SQL, import_demo_data.ITEM_SQL):
        for sku in expected:
            assert f"'{sku}'" in statement
        assert "DEMO2-SKU" not in statement
        assert "status = 'active'" not in statement
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_import_contract.py::test_v1_import_uses_only_the_original_product_pool -q
```

Expected: FAIL until the V1 pool is fixed to the five immutable SKUs without filtering on mutable product status.

- [ ] **Step 3: Add the stable V1 SKU list**

Add immediately before `CUSTOMER_SQL` in `scripts/import_demo_data.py`:

```python
V1_PRODUCT_SKUS = (
    "SKU-F001",
    "SKU-F002",
    "SKU-H001",
    "SKU-D001",
    "SKU-B001",
)
V1_PRODUCT_LIST_SQL = ", ".join(f"'{sku}'" for sku in V1_PRODUCT_SKUS)
```

Replace `ORDER_SQL` and `ITEM_SQL` with these complete f-strings. The original five products remain in the immutable V1 mapping even when an operator changes one of them to inactive:

```python
ORDER_SQL = f"""
WITH customer_pool AS (
    SELECT customer_id, row_number() OVER (ORDER BY customer_no) AS rn
    FROM biz.customer WHERE customer_no LIKE 'IMP-C-%%'
), product_pool AS (
    SELECT product_id, unit_price, row_number() OVER (ORDER BY product_id) AS rn,
           count(*) OVER () AS total_products
    FROM biz.product
    WHERE sku IN ({V1_PRODUCT_LIST_SQL})
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

ITEM_SQL = f"""
WITH product_pool AS (
    SELECT product_id, unit_price, row_number() OVER (ORDER BY product_id) AS rn,
           count(*) OVER () AS total_products
    FROM biz.product
    WHERE sku IN ({V1_PRODUCT_LIST_SQL})
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
```

Do not alter the order, item, payment, or flow identifier formats.

- [ ] **Step 4: Run the import contract module and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_import_contract.py -q
```

Expected: all import contract tests pass.

- [ ] **Step 5: Commit the V1 protection**

```powershell
& $Git add -- tests/test_import_contract.py scripts/import_demo_data.py
& $Git commit -m 'Keep demo import product mapping stable'
```

### Task 2: Add and wire the idempotent commerce demo seed

**Files:**
- Create: `database/demo_commerce_v2.sql`
- Modify: `scripts/init_db.py`
- Create: `tests/test_demo_commerce_seed.py`

- [ ] **Step 1: Write the failing seed contract tests**

Create `tests/test_demo_commerce_seed.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "database" / "demo_commerce_v2.sql"


def test_demo_commerce_seed_exists_and_is_wired_after_base_seed():
    assert SEED.exists()
    initializer = (ROOT / "scripts" / "init_db.py").read_text(encoding="utf-8")
    base_call = 'run_script(cursor, "v2_seed.sql")'
    demo_call = 'run_script(cursor, "demo_commerce_v2.sql")'

    assert base_call in initializer
    assert demo_call in initializer
    assert initializer.index(base_call) < initializer.index(demo_call)


def test_demo_commerce_seed_declares_the_approved_dataset():
    sql = SEED.read_text(encoding="utf-8")
    lowered = sql.lower()

    assert "generate_series(1, 20)" in lowered
    assert "generate_series(1, 10)" in lowered
    for prefix in (
        "DEMO2-SKU-",
        "DEMO2-PEND-SO-",
        "DEMO2-PAID-SO-",
        "DEMO2-PAY-",
        "DEMO2-REF-",
    ):
        assert prefix in sql
    assert "insert into biz.payment" in lowered
    assert "insert into biz.refund" in lowered
    assert "'payment'" in lowered
    assert "'refund'" in lowered
    assert "do update" not in lowered
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_demo_commerce_seed.py -q
```

Expected: FAIL at `assert SEED.exists()`.

- [ ] **Step 3: Create the complete transactional demo seed**

Create `database/demo_commerce_v2.sql` with the following content:

```sql
BEGIN;

INSERT INTO biz.product (sku, product_name, category_id, unit_price, stock_qty)
SELECT v.sku, v.product_name, c.category_id, v.unit_price, v.stock_qty
FROM (VALUES
    ('DEMO2-SKU-F003', '云南小粒咖啡豆 500g', '食品饮料', 128.00::numeric, 85),
    ('DEMO2-SKU-F004', '低糖燕麦饼干 600g', '食品饮料', 39.90::numeric, 160),
    ('DEMO2-SKU-F005', '原味酸奶 12杯', '食品饮料', 72.00::numeric, 96),
    ('DEMO2-SKU-F006', '冷泡乌龙茶 15袋', '食品饮料', 45.00::numeric, 7),
    ('DEMO2-SKU-F007', '有机五常大米 5kg', '食品饮料', 119.00::numeric, 68),
    ('DEMO2-SKU-H002', '天然洗衣凝珠 40颗', '家居日用', 69.90::numeric, 110),
    ('DEMO2-SKU-H003', '抗菌厨房纸 12卷', '家居日用', 42.90::numeric, 140),
    ('DEMO2-SKU-H004', '可降解垃圾袋 5卷', '家居日用', 25.90::numeric, 180),
    ('DEMO2-SKU-H005', '无香抽纸 24包', '家居日用', 79.90::numeric, 8),
    ('DEMO2-SKU-H006', '便携保温杯 500ml', '家居日用', 89.00::numeric, 74),
    ('DEMO2-SKU-D002', '降噪蓝牙耳机', '数码家电', 299.00::numeric, 55),
    ('DEMO2-SKU-D003', '智能手环', '数码家电', 229.00::numeric, 63),
    ('DEMO2-SKU-D004', '65W氮化镓充电器', '数码家电', 159.00::numeric, 92),
    ('DEMO2-SKU-D005', '机械键盘 87键', '数码家电', 399.00::numeric, 35),
    ('DEMO2-SKU-D006', '2TB移动固态硬盘', '数码家电', 699.00::numeric, 28),
    ('DEMO2-SKU-B002', '舒缓修护面霜', '美妆个护', 129.00::numeric, 88),
    ('DEMO2-SKU-B003', '清透防晒乳 SPF50', '美妆个护', 109.00::numeric, 120),
    ('DEMO2-SKU-B004', '柔润护手霜 3支', '美妆个护', 49.90::numeric, 150),
    ('DEMO2-SKU-B005', '温和卸妆油 150ml', '美妆个护', 89.00::numeric, 64),
    ('DEMO2-SKU-B006', '玻尿酸面膜 10片', '美妆个护', 99.00::numeric, 5)
) AS v(sku, product_name, category_name, unit_price, stock_qty)
JOIN biz.product_category c ON c.category_name = v.category_name
ON CONFLICT (sku) DO NOTHING;

WITH product_pool AS (
    SELECT product_id, unit_price,
           row_number() OVER (ORDER BY sku) AS rn
    FROM biz.product
    WHERE sku LIKE 'DEMO2-SKU-%'
), pending_spec AS (
    SELECT n,
           'DEMO2-PEND-SO-' || lpad(n::text, 3, '0') AS order_no,
           'C2026000' || (((n - 1) % 5) + 1)::text AS customer_no,
           1 + (n % 2) AS quantity,
           now() - n * interval '1 minute' AS ordered_at
    FROM generate_series(1, 20) AS n
), pending_rows AS (
    SELECT s.*, c.customer_id, p.product_id, p.unit_price,
           round(p.unit_price * s.quantity, 2) AS total_amount
    FROM pending_spec s
    JOIN biz.customer c ON c.customer_no = s.customer_no
    JOIN product_pool p ON p.rn = s.n
)
INSERT INTO biz.sales_order
    (order_no, customer_id, status, total_amount, paid_amount,
     refunded_amount, ordered_at, remark, created_by)
SELECT order_no, customer_id, 'awaiting_payment', total_amount, 0, 0,
       ordered_at, '可操作演示待支付订单',
       (SELECT account_id FROM auth.account WHERE username = 'admin')
FROM pending_rows
ON CONFLICT (order_no) DO NOTHING;

WITH product_pool AS (
    SELECT product_id, row_number() OVER (ORDER BY sku) AS rn
    FROM biz.product WHERE sku LIKE 'DEMO2-SKU-%'
), pending_spec AS (
    SELECT n, 'DEMO2-PEND-SO-' || lpad(n::text, 3, '0') AS order_no,
           1 + (n % 2) AS quantity
    FROM generate_series(1, 20) AS n
)
INSERT INTO biz.order_item (order_id, product_id, quantity, unit_price, line_amount)
SELECT o.order_id, p.product_id, s.quantity, o.total_amount / s.quantity,
       o.total_amount
FROM pending_spec s
JOIN biz.sales_order o ON o.order_no = s.order_no
JOIN product_pool p ON p.rn = s.n
ON CONFLICT (order_id, product_id) DO NOTHING;

WITH product_pool AS (
    SELECT product_id, unit_price,
           row_number() OVER (ORDER BY sku) AS rn
    FROM biz.product WHERE sku LIKE 'DEMO2-SKU-%'
), paid_spec AS (
    SELECT n,
           'DEMO2-PAID-SO-' || lpad(n::text, 3, '0') AS order_no,
           'C2026000' || (((n - 1) % 5) + 1)::text AS customer_no,
           1 + (n % 3) AS quantity,
           now() - (n + 20) * interval '1 minute' AS ordered_at,
           now() - (n + 10) * interval '1 minute' AS paid_at
    FROM generate_series(1, 10) AS n
), paid_amounts AS (
    SELECT s.*, c.customer_id, p.product_id, p.unit_price,
           round(p.unit_price * s.quantity, 2) AS total_amount
    FROM paid_spec s
    JOIN biz.customer c ON c.customer_no = s.customer_no
    JOIN product_pool p ON p.rn = s.n
), paid_rows AS (
    SELECT *, CASE WHEN n <= 6 THEN round(total_amount * 0.25, 2)
                   ELSE total_amount END AS refund_amount
    FROM paid_amounts
)
INSERT INTO biz.sales_order
    (order_no, customer_id, status, total_amount, paid_amount,
     refunded_amount, ordered_at, paid_at, remark, created_by)
SELECT order_no, customer_id,
       CASE WHEN n <= 6 THEN 'partially_refunded' ELSE 'refunded' END,
       total_amount, total_amount, refund_amount, ordered_at, paid_at,
       '演示支付退款订单',
       (SELECT account_id FROM auth.account WHERE username = 'admin')
FROM paid_rows
ON CONFLICT (order_no) DO NOTHING;

WITH product_pool AS (
    SELECT product_id, row_number() OVER (ORDER BY sku) AS rn
    FROM biz.product WHERE sku LIKE 'DEMO2-SKU-%'
), paid_spec AS (
    SELECT n, 'DEMO2-PAID-SO-' || lpad(n::text, 3, '0') AS order_no,
           1 + (n % 3) AS quantity
    FROM generate_series(1, 10) AS n
)
INSERT INTO biz.order_item (order_id, product_id, quantity, unit_price, line_amount)
SELECT o.order_id, p.product_id, s.quantity, o.total_amount / s.quantity,
       o.total_amount
FROM paid_spec s
JOIN biz.sales_order o ON o.order_no = s.order_no
JOIN product_pool p ON p.rn = s.n
ON CONFLICT (order_id, product_id) DO NOTHING;

WITH payment_spec AS (
    SELECT n,
           'DEMO2-PAID-SO-' || lpad(n::text, 3, '0') AS order_no,
           'DEMO2-PAY-' || lpad(n::text, 3, '0') AS payment_no,
           (ARRAY['wechat', 'alipay', 'bank_card', 'cash'])[((n - 1) % 4) + 1] AS method
    FROM generate_series(1, 10) AS n
)
INSERT INTO biz.payment
    (payment_no, order_id, method, amount, status, paid_at,
     transaction_ref, created_by)
SELECT s.payment_no, o.order_id, s.method, o.total_amount, 'success', o.paid_at,
       'DEMO2-TXN-' || lpad(s.n::text, 3, '0'),
       (SELECT account_id FROM auth.account WHERE username = 'admin')
FROM payment_spec s
JOIN biz.sales_order o ON o.order_no = s.order_no
ON CONFLICT (payment_no) DO NOTHING;

WITH refund_spec AS (
    SELECT n,
           'DEMO2-PAY-' || lpad(n::text, 3, '0') AS payment_no,
           'DEMO2-REF-' || lpad(n::text, 3, '0') AS refund_no,
           (ARRAY['商品体验未达预期', '包装破损', '规格不合适', '重复购买'])[((n - 1) % 4) + 1] AS reason
    FROM generate_series(1, 10) AS n
)
INSERT INTO biz.refund
    (refund_no, payment_id, order_id, amount, reason, status,
     refunded_at, created_by)
SELECT s.refund_no, p.payment_id, p.order_id, o.refunded_amount, s.reason,
       'success', p.paid_at + interval '5 minutes',
       (SELECT account_id FROM auth.account WHERE username = 'admin')
FROM refund_spec s
JOIN biz.payment p ON p.payment_no = s.payment_no
JOIN biz.sales_order o ON o.order_id = p.order_id
ON CONFLICT (refund_no) DO NOTHING;

INSERT INTO dwd.consumption_flow
    (customer_id, order_id, payment_id, flow_type,
     gross_amount, net_amount, occurred_at)
SELECT o.customer_id, o.order_id, p.payment_id, 'payment',
       p.amount, p.amount, p.paid_at
FROM biz.payment p
JOIN biz.sales_order o ON o.order_id = p.order_id
WHERE p.payment_no LIKE 'DEMO2-PAY-%'
ON CONFLICT (payment_id) WHERE flow_type = 'payment' DO NOTHING;

INSERT INTO dwd.consumption_flow
    (customer_id, order_id, payment_id, refund_id, flow_type,
     gross_amount, net_amount, occurred_at)
SELECT o.customer_id, o.order_id, p.payment_id, r.refund_id, 'refund',
       r.amount, -r.amount, r.refunded_at
FROM biz.refund r
JOIN biz.payment p ON p.payment_id = r.payment_id
JOIN biz.sales_order o ON o.order_id = r.order_id
WHERE r.refund_no LIKE 'DEMO2-REF-%'
ON CONFLICT (refund_id) WHERE flow_type = 'refund' DO NOTHING;

COMMIT;
```

- [ ] **Step 4: Wire the seed after the base seed**

In `scripts/init_db.py`, change the initializer sequence to:

```python
with connection.cursor() as cursor:
    run_script(cursor, "v2_schema.sql")
    run_script(cursor, "v2_seed.sql")
    run_script(cursor, "demo_commerce_v2.sql")
    configure_account_passwords(
        cursor,
        required=os.environ.get("FLASK_ENV", "development").lower() == "production",
    )
```

- [ ] **Step 5: Run the seed contract tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_demo_commerce_seed.py tests\test_deploy_contract.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the demo seed**

```powershell
& $Git add -- database/demo_commerce_v2.sql scripts/init_db.py tests/test_demo_commerce_seed.py
& $Git commit -m 'Add commerce demo seed data'
```

### Task 3: Bound the refundable-payment selector

**Files:**
- Modify: `tests/test_demo_commerce_seed.py`
- Modify: `app/routes/refunds.py`

- [ ] **Step 1: Write the failing query-bound test**

Append to `tests/test_demo_commerce_seed.py`:

```python
def test_refund_form_limits_refundable_payments():
    source = (ROOT / "app" / "routes" / "refunds.py").read_text(encoding="utf-8")

    assert "ORDER BY p.paid_at DESC\n        LIMIT 200" in source
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_demo_commerce_seed.py::test_refund_form_limits_refundable_payments -q
```

Expected: FAIL because the query has no limit.

- [ ] **Step 3: Add the 200-row bound**

In the refundable query in `app/routes/refunds.py`, change the tail to:

```sql
HAVING p.amount - COALESCE(SUM(r.amount) FILTER (WHERE r.status = 'success'), 0) > 0
ORDER BY p.paid_at DESC
LIMIT 200
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_demo_commerce_seed.py -q
```

Expected: all demo seed contract tests pass.

- [ ] **Step 5: Commit the bounded query**

```powershell
& $Git add -- app/routes/refunds.py tests/test_demo_commerce_seed.py
& $Git commit -m 'Limit refundable payment choices'
```

### Task 4: Verify idempotency with a real PostgreSQL database

**Files:**
- Create: `tests/test_demo_commerce_integration.py`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the PostgreSQL integration test**

Create `tests/test_demo_commerce_integration.py`:

```python
import os
import uuid
from pathlib import Path

import psycopg2
import pytest
from psycopg2 import sql
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]


def _connect(url, database=None):
    return psycopg2.connect(
        host=url.host,
        port=url.port or 5432,
        dbname=database or url.database,
        user=url.username,
        password=url.password,
    )


@pytest.fixture
def isolated_database():
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")

    url = make_url(raw_url)
    database = f"consumer_analysis_test_{uuid.uuid4().hex[:12]}"
    admin = _connect(url)
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

    connection = _connect(url, database=database)
    connection.autocommit = True
    try:
        yield connection
    finally:
        connection.close()
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database,),
            )
            cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
        admin.close()


def _apply(connection, filename):
    source = (ROOT / "database" / filename).read_text(encoding="utf-8")
    with connection.cursor() as cursor:
        cursor.execute(source)


def _counts(connection):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
              (SELECT count(*) FROM biz.product WHERE sku LIKE 'DEMO2-SKU-%'),
              (SELECT count(*) FROM biz.sales_order WHERE order_no LIKE 'DEMO2-PEND-SO-%'),
              (SELECT count(*) FROM biz.sales_order WHERE order_no LIKE 'DEMO2-PAID-SO-%'),
              (SELECT count(*) FROM biz.payment WHERE payment_no LIKE 'DEMO2-PAY-%'),
              (SELECT count(*) FROM biz.refund WHERE refund_no LIKE 'DEMO2-REF-%'),
              (SELECT count(*) FROM dwd.consumption_flow f
               JOIN biz.payment p ON p.payment_id = f.payment_id
               WHERE p.payment_no LIKE 'DEMO2-PAY-%' AND f.flow_type = 'payment'),
              (SELECT count(*) FROM dwd.consumption_flow f
               JOIN biz.refund r ON r.refund_id = f.refund_id
               WHERE r.refund_no LIKE 'DEMO2-REF-%' AND f.flow_type = 'refund')
        """)
        return cursor.fetchone()


def _assert_pending_order_state(connection, paid_order_numbers=frozenset()):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT o.order_no, o.status, o.total_amount,
                   count(oi.order_item_id), min(oi.line_amount)
            FROM biz.sales_order o
            LEFT JOIN biz.order_item oi ON oi.order_id = o.order_id
            WHERE o.order_no LIKE 'DEMO2-PEND-SO-%'
            GROUP BY o.order_id, o.order_no, o.status, o.total_amount
            ORDER BY o.order_no
        """)
        rows = cursor.fetchall()

    expected_order_numbers = [
        f"DEMO2-PEND-SO-{number:03d}" for number in range(1, 21)
    ]
    assert [row[0] for row in rows] == expected_order_numbers
    for order_no, status, total_amount, item_count, line_amount in rows:
        expected_status = (
            "paid" if order_no in paid_order_numbers else "awaiting_payment"
        )
        assert status == expected_status
        assert item_count == 1
        assert line_amount == total_amount


def test_demo_seed_is_idempotent_and_keeps_commerce_amounts_consistent(isolated_database):
    connection = isolated_database
    for filename in ("v2_schema.sql", "v2_seed.sql", "demo_commerce_v2.sql"):
        _apply(connection, filename)

    first_counts = _counts(connection)
    assert first_counts == (20, 20, 10, 10, 10, 10, 10)
    _assert_pending_order_state(connection)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT count(*) FROM biz.product
            WHERE sku LIKE 'DEMO2-SKU-%' AND stock_qty < 10
        """)
        assert cursor.fetchone()[0] == 3

        cursor.execute("""
            SELECT status, count(*)
            FROM biz.sales_order
            WHERE order_no LIKE 'DEMO2-PAID-SO-%'
            GROUP BY status
        """)
        assert dict(cursor.fetchall()) == {"partially_refunded": 6, "refunded": 4}

        cursor.execute("""
            SELECT count(*)
            FROM biz.sales_order o
            JOIN biz.order_item i ON i.order_id = o.order_id
            JOIN biz.payment p ON p.order_id = o.order_id AND p.status = 'success'
            JOIN biz.refund r ON r.payment_id = p.payment_id AND r.status = 'success'
            JOIN dwd.consumption_flow pf
              ON pf.payment_id = p.payment_id AND pf.flow_type = 'payment'
            JOIN dwd.consumption_flow rf
              ON rf.refund_id = r.refund_id AND rf.flow_type = 'refund'
            WHERE o.order_no LIKE 'DEMO2-PAID-SO-%'
              AND (
                i.line_amount <> o.total_amount OR
                p.amount <> o.total_amount OR
                o.paid_amount <> p.amount OR
                o.refunded_amount <> r.amount OR
                r.amount > p.amount OR
                r.order_id <> p.order_id OR
                pf.net_amount <> p.amount OR
                rf.net_amount <> -r.amount OR
                NOT (o.ordered_at <= p.paid_at AND p.paid_at <= r.refunded_at) OR
                r.refunded_at > now()
              )
        """)
        assert cursor.fetchone()[0] == 0

        cursor.execute("""
            UPDATE biz.product SET stock_qty = 321
            WHERE sku = 'DEMO2-SKU-F003'
        """)
        cursor.execute("""
            UPDATE biz.sales_order
            SET status = 'paid', paid_amount = total_amount, paid_at = now()
            WHERE order_no = 'DEMO2-PEND-SO-001'
        """)

    for filename in ("v2_schema.sql", "v2_seed.sql", "demo_commerce_v2.sql"):
        _apply(connection, filename)

    assert _counts(connection) == first_counts
    _assert_pending_order_state(connection, {"DEMO2-PEND-SO-001"})
    with connection.cursor() as cursor:
        cursor.execute("SELECT stock_qty FROM biz.product WHERE sku = 'DEMO2-SKU-F003'")
        assert cursor.fetchone()[0] == 321
        cursor.execute("SELECT status FROM biz.sales_order WHERE order_no = 'DEMO2-PEND-SO-001'")
        assert cursor.fetchone()[0] == "paid"
```

- [ ] **Step 2: Run the integration test without a database and verify the local skip**

Run:

```powershell
Remove-Item Env:TEST_DATABASE_URL -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest tests\test_demo_commerce_integration.py -q
```

Expected: one skipped test with the documented reason.

- [ ] **Step 3: Add a PostgreSQL CI service**

Update `.github/workflows/ci.yml` so the test job contains:

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_DB: postgres
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    env:
      TEST_DATABASE_URL: postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/postgres
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt
      - run: pytest -q
```

- [ ] **Step 4: Run the complete local suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all existing and new non-integration tests pass; the PostgreSQL integration test is skipped locally when `TEST_DATABASE_URL` is absent.

- [ ] **Step 5: Commit the integration coverage**

```powershell
& $Git add -- tests/test_demo_commerce_integration.py .github/workflows/ci.yml
& $Git commit -m 'Test commerce demo seed in PostgreSQL'
```

### Task 5: Final verification and deployment handoff

**Files:**
- Verify: `database/demo_commerce_v2.sql`
- Verify: `scripts/init_db.py`
- Verify: `scripts/import_demo_data.py`
- Verify: `app/routes/refunds.py`
- Verify: `.github/workflows/ci.yml`
- Verify: `tests/test_import_contract.py`
- Verify: `tests/test_demo_commerce_seed.py`
- Verify: `tests/test_demo_commerce_integration.py`

- [ ] **Step 1: Run all automated checks**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
& $Git diff --check
```

Expected: pytest exits zero, only the documented integration test may be skipped locally, and `git diff --check` exits zero.

- [ ] **Step 2: Inspect the final change set**

```powershell
& $Git status --short
& $Git --no-pager diff --stat HEAD
& $Git --no-pager diff HEAD -- `
  database/demo_commerce_v2.sql `
  scripts/init_db.py `
  scripts/import_demo_data.py `
  app/routes/refunds.py `
  .github/workflows/ci.yml `
  tests/test_import_contract.py `
  tests/test_demo_commerce_seed.py `
  tests/test_demo_commerce_integration.py
```

Expected: no unrelated files, secrets, screenshots, caches, or generated database files.

- [ ] **Step 3: Push the completed commits after approval**

```powershell
& $Git pull --rebase origin main
& $Git push origin main
& $Git status --short --branch
& $Git --no-pager log -5 --oneline --decorate
```

Expected: `main` matches `origin/main`, the worktree is clean, and Render auto-deploy starts.

- [ ] **Step 4: Verify the deployed data after Render reports Live**

Check the authenticated pages:

```text
/products  -> approximately 25 products and 3 DEMO2 low-stock products
/payments  -> 20 initial DEMO2 pending orders and 10 DEMO2 successful payments
/refunds   -> 10 DEMO2 refunds; refundable selector has at most 200 options
```

Also verify `/healthz` returns HTTP 200 with `{"status":"ok","database":"up"}`.
