from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_workbench_service_reads_only_task_scoped_v2_results():
    path = ROOT / "app" / "services" / "workbench.py"
    assert path.exists()
    source = path.read_text(encoding="utf-8").lower()
    assert "from ml.rfm_result" in source
    assert "from ml.cluster_result" in source
    assert "from ml.churn_prediction" in source
    assert "where r.task_id = :task_id" in source
    assert "where cr.task_id = :task_id" in source
    assert "where p.task_id = :task_id" in source
    assert "ads.customer_rfm" not in source
    assert "rfm_task_id" in source
    assert "spending_record" not in source
    assert "from users" not in source
