from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v2_has_required_schemas_and_tables():
    schema = (ROOT / "database" / "v2_schema.sql").read_text(encoding="utf-8").lower()
    for name in ("auth", "biz", "ods", "dwd", "ads", "ml", "audit"):
        assert f"create schema if not exists {name}" in schema
    for table in ("biz.customer", "biz.product", "biz.sales_order", "biz.payment",
                  "biz.refund", "dwd.consumption_flow", "ml.model_task", "ml.model_metric",
                  "ods.import_batch"):
        assert f"create table if not exists {table}" in schema


def test_v2_initializer_does_not_drop_legacy_tables():
    schema = (ROOT / "database" / "v2_schema.sql").read_text(encoding="utf-8").lower()
    assert "drop table" not in schema


def test_order_service_uses_row_lock_and_algorithm_keeps_history():
    retail = (ROOT / "app" / "repositories" / "retail.py").read_text(encoding="utf-8").lower()
    algorithms = (ROOT / "app" / "services" / "algorithms.py").read_text(encoding="utf-8").lower()
    assert "for update" in retail
    assert "delete from ml." not in algorithms
