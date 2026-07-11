from pathlib import Path

from jinja2 import Environment

from app.routes.algorithms import _cluster_profile


ROOT = Path(__file__).resolve().parents[1]


def test_cluster_profiles_translate_metrics_into_business_language():
    baseline = {"recency": 30, "frequency": 4, "monetary": 1000}

    assert _cluster_profile({
        "avg_recency": 12, "avg_frequency": 7, "avg_monetary": 1800,
    }, baseline)[0] == "核心活跃群"
    assert _cluster_profile({
        "avg_recency": 80, "avg_frequency": 1, "avg_monetary": 300,
    }, baseline)[0] == "沉睡客户群"
    assert _cluster_profile({
        "avg_recency": 70, "avg_frequency": 3, "avg_monetary": 1600,
    }, baseline)[0] == "高价值待唤醒群"


def test_algorithm_template_renders_charts_and_readable_tables():
    template = (ROOT / "app" / "templates" / "algorithms.html").read_text(encoding="utf-8")

    Environment().parse(template)
    assert "algorithmResultChart" in template
    assert "客户数 / 占比" in template
    assert "优先回访客户" in template
    assert "查看结果" in template
    assert "<code>{{ t.metrics }}</code>" not in template
    assert "<code>{{ t.parameters }}</code>" not in template


def test_completed_algorithm_redirects_to_its_visual_result():
    source = (ROOT / "app" / "routes" / "algorithms.py").read_text(encoding="utf-8")

    assert '_anchor="task-result"' in source
    assert "task_id=task_id" in source
