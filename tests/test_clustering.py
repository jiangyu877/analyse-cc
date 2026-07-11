import numpy as np
import pandas as pd

from app.services.clustering import choose_stable_kmeans, select_model_features


def test_small_balanced_dataset_is_not_rejected_by_fixed_minimum():
    left = np.column_stack((np.linspace(-1.2, -0.8, 5), np.linspace(-1.1, -0.9, 5)))
    right = np.column_stack((np.linspace(0.8, 1.2, 5), np.linspace(0.9, 1.1, 5)))

    result = choose_stable_kmeans(np.vstack((left, right)))

    assert result is not None
    assert result.clusters == 2
    assert result.minimum_cluster_size == 2
    assert np.bincount(result.labels).min() >= 2
    assert result.score > 0


def test_single_outlier_cannot_be_saved_as_a_customer_group():
    matrix = np.vstack((np.zeros((99, 2)), np.array([[20.0, 20.0]])))

    assert choose_stable_kmeans(matrix) is None


def test_feature_selection_rejects_constant_and_dominant_columns():
    frame = pd.DataFrame({
        'constant': [1.0] * 100,
        'dominant': [0.0] * 98 + [1.0, 2.0],
        'useful_a': np.arange(100, dtype=float),
        'useful_b': np.tile([0.0, 1.0], 50),
    })

    selected = select_model_features(frame, list(frame.columns))

    assert selected == ['useful_a', 'useful_b']


def test_selection_is_deterministic_for_the_same_data():
    random = np.random.default_rng(7)
    matrix = np.vstack((
        random.normal(-2, 0.15, size=(60, 2)),
        random.normal(2, 0.15, size=(60, 2)),
    ))

    first = choose_stable_kmeans(matrix)
    second = choose_stable_kmeans(matrix)

    assert first is not None and second is not None
    assert first.clusters == second.clusters
    assert first.score == second.score
    assert np.array_equal(first.labels, second.labels)
