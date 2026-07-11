from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
