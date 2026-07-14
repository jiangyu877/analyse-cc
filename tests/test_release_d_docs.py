from pathlib import Path

ROOT = Path(__file__).parents[1]

def test_release_d_documents_exist_and_targets_present():
    text = "\n".join((ROOT / "docs" / n).read_text(encoding="utf-8") for n in ("deployment.md", "user-guide.md", "data-retention.md", "acceptance-checklist.md"))
    for term in ("RPO 24 hours", "RTO 4 hours", "99.5%", "30 days", "180 days", "quarterly", "worker.py", "/healthz", "/readyz", "migration 008", "backup", "restore", "rollback", "PG_BIN_DIR"):
        assert term in text

def test_user_guide_covers_business_surfaces():
    text = (ROOT / "docs/user-guide.md").read_text(encoding="utf-8")
    for term in ("roles", "orders", "payments", "refund", "publish", "citations", "tickets", "reports", "RFM", "KMeans", "churn", "amount", "sales", "recommendations"):
        assert term.lower() in text.lower()
