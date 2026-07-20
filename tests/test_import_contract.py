from pathlib import Path

from scripts import import_demo_data


ROOT = Path(__file__).resolve().parents[1]


def test_bulk_import_freezes_v1_product_mapping():
    expected_skus = (
        "SKU-F001",
        "SKU-F002",
        "SKU-H001",
        "SKU-D001",
        "SKU-B001",
    )

    assert import_demo_data.V1_PRODUCT_SKUS == expected_skus
    for sql in (import_demo_data.ORDER_SQL, import_demo_data.ITEM_SQL):
        for sku in expected_skus:
            assert f"'{sku}'" in sql
        assert "DEMO2-SKU" not in sql
        assert "status = 'active'" not in sql


def test_bulk_import_requires_at_least_50000_transactions():
    script = (ROOT / "scripts" / "import_demo_data.py").read_text(encoding="utf-8")
    assert 'default=50000' in script
    assert 'transactions < 50000' in script


def test_bulk_import_builds_complete_consumption_chain():
    script = (ROOT / "scripts" / "import_demo_data.py").read_text(encoding="utf-8").lower()
    assert "insert into biz.sales_order" in script
    assert "insert into biz.order_item" in script
    assert "insert into biz.payment" in script
    assert "insert into dwd.consumption_flow" in script


def test_import_preflight_migration_defines_staging_and_issue_storage():
    migration = (
        ROOT / "database" / "migrations" / "010_import_preflight_and_data_maintenance.sql"
    )

    assert migration.exists()

    sql = migration.read_text(encoding="utf-8").lower()
    for fragment in (
        "add column if not exists file_sha256",
        "add column if not exists input_row_count",
        "add column if not exists valid_row_count",
        "add column if not exists invalid_row_count",
        "add column if not exists mapping_json",
        "create table if not exists ods.import_stage_row",
        "create table if not exists ods.import_row_issue",
        "on delete cascade",
    ):
        assert fragment in sql
