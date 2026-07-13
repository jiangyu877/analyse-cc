from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "database" / "demo_commerce_v2.sql"
INIT_PATH = ROOT / "scripts" / "init_db.py"
REFUNDS_PATH = ROOT / "app" / "routes" / "refunds.py"
RETAIL_REPOSITORY_PATH = ROOT / "app" / "repositories" / "retail.py"


def test_demo_commerce_seed_exists_and_runs_after_base_seed():
    assert SEED_PATH.exists()

    init_source = INIT_PATH.read_text(encoding="utf-8")
    base_seed_call = 'run_script(cursor, "v2_seed.sql")'
    demo_seed_call = 'run_script(cursor, "demo_commerce_v2.sql")'
    password_config_call = "configure_account_passwords("

    assert demo_seed_call in init_source
    assert init_source.index(demo_seed_call) > init_source.index(base_seed_call)
    assert init_source.index(demo_seed_call) < init_source.rindex(password_config_call)


def test_demo_commerce_seed_contains_required_dataset_and_is_append_only():
    assert SEED_PATH.exists()
    seed = SEED_PATH.read_text(encoding="utf-8").lower()

    for marker in (
        "generate_series(1, 20)",
        "generate_series(1, 10)",
        "demo2-sku-",
        "demo2-pend-so-",
        "demo2-paid-so-",
        "demo2-pay-",
        "demo2-ref-",
        "insert into biz.product",
        "insert into biz.sales_order",
        "insert into biz.order_item",
        "insert into biz.payment",
        "insert into biz.refund",
        "insert into dwd.consumption_flow",
        "'payment'",
        "'refund'",
    ):
        assert marker in seed

    assert "do update" not in seed


def test_refund_form_limits_refundable_payments():
    refunds_source = REFUNDS_PATH.read_text(encoding="utf-8")
    repository_source = RETAIL_REPOSITORY_PATH.read_text(encoding="utf-8")

    assert "refundable_items=RefundRepository.refundable_items()" in refunds_source
    refundable_query = repository_source.split("def refundable_items", 1)[1].split(
        "def successful_total", 1
    )[0]
    assert "ORDER BY p.paid_at DESC, oi.order_item_id" in refundable_query
    assert "LIMIT 200" in refundable_query
