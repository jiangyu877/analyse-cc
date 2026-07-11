from dataclasses import dataclass
import os
import warnings

import numpy as np
from sklearn.cluster import KMeans
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import silhouette_score

os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(min(os.cpu_count() or 1, 4)))


@dataclass(frozen=True)
class KMeansSelection:
    clusters: int
    score: float
    model: KMeans
    labels: np.ndarray
    distances: np.ndarray
    minimum_cluster_size: int


def select_model_features(frame, candidates, dominance=0.98):
    selected = []
    for feature in candidates:
        values = frame[feature]
        numeric = np.asarray(values, dtype=float)
        if not np.isfinite(numeric).all() or values.nunique(dropna=False) <= 1:
            continue
        if values.value_counts(normalize=True, dropna=False).iloc[0] >= dominance:
            continue
        selected.append(feature)
    return selected


def _stratified_silhouette(features, labels, maximum=1500, random_state=42):
    unique_labels = np.unique(labels)
    if len(unique_labels) < 2:
        return None
    if len(labels) <= maximum:
        return float(silhouette_score(features, labels))

    random = np.random.default_rng(random_state)
    selected = []
    for label in unique_labels:
        indices = np.flatnonzero(labels == label)
        target = max(2, round(maximum * len(indices) / len(labels)))
        selected.extend(random.choice(indices, size=min(target, len(indices)), replace=False))
    selected = np.asarray(selected, dtype=int)
    sampled_labels = labels[selected]
    if len(np.unique(sampled_labels)) < 2:
        return None
    return float(silhouette_score(features[selected], sampled_labels))


def choose_stable_kmeans(
    features,
    maximum_clusters=5,
    minimum_share=0.02,
    random_state=42,
    n_init=20,
):
    matrix = np.asarray(features, dtype=float)
    if matrix.ndim != 2 or len(matrix) < 4 or matrix.shape[1] < 2:
        return None
    if not np.isfinite(matrix).all():
        return None

    minimum_size = max(2, int(np.ceil(len(matrix) * minimum_share)))
    maximum_feasible = min(maximum_clusters, len(matrix) // minimum_size, len(matrix) - 1)
    candidates = []
    for clusters in range(2, maximum_feasible + 1):
        model = KMeans(
            n_clusters=clusters,
            random_state=random_state,
            n_init=n_init,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            labels = model.fit_predict(matrix)
        if len(np.unique(labels)) != clusters:
            continue
        cluster_sizes = np.bincount(labels, minlength=clusters)
        if cluster_sizes.min() < minimum_size:
            continue
        score = _stratified_silhouette(matrix, labels, random_state=random_state)
        if score is None or not np.isfinite(score):
            continue
        distances = np.min(model.transform(matrix), axis=1)
        candidates.append((score, cluster_sizes.min() / len(matrix), -clusters, model, labels, distances))

    if not candidates:
        return None
    score, _, _, model, labels, distances = max(candidates, key=lambda item: item[:3])
    return KMeansSelection(
        clusters=int(model.n_clusters),
        score=float(score),
        model=model,
        labels=labels,
        distances=distances,
        minimum_cluster_size=minimum_size,
    )
