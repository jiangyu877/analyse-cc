from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gradio_app.py").read_text(encoding="utf-8")


def test_gradio_core_algorithms_read_v2_data():
    assert "FROM biz.customer c" in SOURCE
    assert "dwd.consumption_flow" in SOURCE
    assert "ml.model_task" in SOURCE


def test_gradio_has_explainable_core_visuals():
    for label in (
        "客户价值地图", "客户分层结构", "客户群位置与特征热力图",
        "高风险客户 Top20", "风险结构与概率分布", "AUC", "F1",
    ):
        assert label in SOURCE


def test_gradio_is_local_only_and_core_results_are_task_scoped():
    assert "server_name='127.0.0.1'" in SOURCE
    assert "_run_v2_task('rfm')" in SOURCE
    assert "FROM ml.rfm_result r" in SOURCE
    assert "WHERE r.task_id = %s" in SOURCE
    assert "def _save_rfm_task" not in SOURCE
    assert "INSERT INTO ml.cluster_result" in SOURCE
    assert "choose_stable_kmeans(scaled)" in SOURCE
    assert 'DELETE FROM rfm_scores' not in SOURCE
    assert 'DELETE FROM user_segments' not in SOURCE
    assert 'DELETE FROM churn_predictions' not in SOURCE
