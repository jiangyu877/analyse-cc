from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "gradio_app.py").read_text(encoding="utf-8")


def test_gradio_core_algorithms_read_v2_data():
    assert "from app.services.algorithms import run_churn, run_kmeans, run_rfm" in SOURCE
    assert "from app.services.workbench import" in SOURCE
    assert "load_rfm_result" in SOURCE
    assert "load_cluster_result" in SOURCE
    assert "load_churn_result" in SOURCE
    assert "spending_record" not in SOURCE.lower()
    assert "from users" not in SOURCE.lower()
    assert "join users" not in SOURCE.lower()


def test_gradio_has_explainable_core_visuals():
    for label in (
        "客户价值地图", "客户分层结构", "客户群位置与特征热力图",
        "高风险客户 Top20", "风险结构与概率分布", "AUC", "F1",
    ):
        assert label in SOURCE


def test_gradio_is_local_only_and_core_results_are_task_scoped():
    assert "server_name='127.0.0.1'" in SOURCE
    assert "task_id = run_rfm(None)" in SOURCE
    assert "task_id = run_kmeans(None" in SOURCE
    assert "task_id = run_churn(None" in SOURCE
    assert "db.session.execute" not in SOURCE
    assert "psycopg2" not in SOURCE
    assert "def rfm_save" not in SOURCE
    assert "def user_segmentation_save" not in SOURCE
    assert "def churn_prediction_save" not in SOURCE


def test_gradio_uses_a_writable_matplotlib_cache_without_deprecated_theme_argument():
    assert 'os.environ.setdefault("MPLCONFIGDIR"' in SOURCE
    assert "gr.Blocks(theme=" not in SOURCE
