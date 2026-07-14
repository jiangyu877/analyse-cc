import numpy as np
import pytest


def test_ridge_amount_baseline_is_deterministic_and_non_negative():
    from app.services.prediction import ridge_amount_baseline

    features = np.array([
        [7, 3, 120, 40],
        [14, 2, 70, 35],
        [30, 1, 20, 20],
        [3, 5, 260, 52],
        [45, 1, 10, 10],
        [9, 4, 180, 45],
    ], dtype=float)
    targets = np.array([90, 55, 0, 180, 0, 130], dtype=float)
    current = np.array([[5, 4, 200, 50], [60, 0, 0, 0]], dtype=float)

    first, metrics = ridge_amount_baseline(features, targets, current)
    second, _ = ridge_amount_baseline(features, targets, current)

    assert np.allclose(first, second)
    assert np.all(first >= 0)
    assert set(metrics) == {"mae", "rmse"}


def test_rolling_sales_baseline_uses_only_lagged_history():
    from app.services.prediction import rolling_sales_baseline

    history = np.arange(1, 29, dtype=float)
    forecast, metrics = rolling_sales_baseline(history, horizon_days=5)

    expected = (
        0.5 * history[-7:].mean()
        + 0.3 * history[-14:].mean()
        + 0.2 * history[-28:].mean()
    )
    assert np.allclose(forecast, [expected] * 5)
    assert metrics["mae"] >= 0


def test_cosine_recommendations_are_ranked_and_exclude_purchases():
    from app.services.prediction import cosine_recommendation_baseline

    interactions = np.array([
        [3, 1, 0, 0],
        [2, 1, 1, 0],
        [0, 2, 3, 1],
        [1, 0, 2, 2],
    ], dtype=float)
    recommendations = cosine_recommendation_baseline(interactions, top_k=2)

    assert recommendations
    for customer_index, product_index, rank, score in recommendations:
        assert interactions[customer_index, product_index] == 0
        assert rank in (1, 2)
        assert 0 <= score <= 1
    assert recommendations == cosine_recommendation_baseline(interactions, top_k=2)


def test_cosine_recommendations_fall_back_to_popularity_for_sparse_interactions():
    from app.services.prediction import cosine_recommendation_baseline

    interactions = np.array([
        [3, 0, 0],
        [0, 2, 0],
        [0, 0, 1],
    ], dtype=float)

    recommendations = cosine_recommendation_baseline(interactions, top_k=2)

    assert recommendations
    assert recommendations == cosine_recommendation_baseline(interactions, top_k=2)
    for customer_index, product_index, rank, score in recommendations:
        assert interactions[customer_index, product_index] == 0
        assert rank in (1, 2)
        assert 0 < score <= 1
    customer_zero = [row for row in recommendations if row[0] == 0]
    assert customer_zero[0][1] == 1


@pytest.mark.parametrize(
    ("function_name", "args"),
    [
        ("ridge_amount_baseline", (np.ones((2, 4)), np.ones(2), np.ones((1, 4)))),
        ("rolling_sales_baseline", (np.ones(13), 3)),
        ("cosine_recommendation_baseline", (np.ones((1, 3)), 2)),
    ],
)
def test_baselines_report_clear_insufficient_data_errors(function_name, args):
    from app.services import prediction

    with pytest.raises(prediction.PredictionError, match="insufficient data"):
        getattr(prediction, function_name)(*args)


def test_flask_and_gradio_expose_all_six_task_families_without_raw_sql():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    route_source = (root / "app" / "routes" / "algorithms.py").read_text(encoding="utf-8")
    gradio_source = (root / "gradio_app.py").read_text(encoding="utf-8")
    for task_type in (
        "rfm",
        "kmeans",
        "churn",
        "customer_amount",
        "product_sales_forecast",
        "product_recommendation",
    ):
        assert task_type in route_source
        assert task_type in gradio_source
    assert "db.session.execute" not in gradio_source
    assert "psycopg2" not in gradio_source
