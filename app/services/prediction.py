import json
import os
from datetime import date, timedelta

import numpy as np
from sqlalchemy import text

from app.extensions import db


RANDOM_SEED = 42
CODE_VERSION = os.environ.get("MODEL_CODE_VERSION", "release-c-v1")

MODEL_SPECS = {
    "rfm": {
        "algorithm": "NTILE rule segmentation",
        "feature_list": ["recency_days", "frequency", "monetary"],
        "training_window": "all_history",
        "evaluation_metric": "customer_count",
    },
    "kmeans": {
        "algorithm": "scikit-learn KMeans",
        "feature_list": ["recency_days", "frequency", "monetary"],
        "training_window": "source_rfm_snapshot",
        "evaluation_metric": "silhouette",
    },
    "churn": {
        "algorithm": "scikit-learn LogisticRegression",
        "feature_list": ["recency", "frequency", "monetary"],
        "training_window": "observation_window",
        "evaluation_metric": "auc",
    },
    "customer_amount": {
        "algorithm": "scikit-learn Ridge",
        "feature_list": ["recency_days", "frequency", "monetary", "average_order_value"],
        "training_window": "bounded_history",
        "evaluation_metric": "mae",
    },
    "product_sales_forecast": {
        "algorithm": "lagged rolling averages (7/14/28 day)",
        "feature_list": ["lag_7_mean", "lag_14_mean", "lag_28_mean"],
        "training_window": "bounded_daily_history",
        "evaluation_metric": "mae",
    },
    "product_recommendation": {
        "algorithm": "cosine similarity",
        "feature_list": ["customer_product_quantity_matrix"],
        "training_window": "bounded_interaction_history",
        "evaluation_metric": "mean_similarity",
    },
}


class PredictionError(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def _bounded_int(value, *, minimum, maximum, name):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PredictionError(f"{name} must be an integer") from exc
    return max(minimum, min(parsed, maximum))


def ridge_amount_baseline(features, targets, current_features):
    from sklearn.linear_model import Ridge
    from sklearn.metrics import mean_absolute_error, mean_squared_error
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    features = np.asarray(features, dtype=float)
    targets = np.asarray(targets, dtype=float)
    current_features = np.asarray(current_features, dtype=float)
    if (
        features.ndim != 2
        or current_features.ndim != 2
        or len(features) < 4
        or len(targets) != len(features)
        or features.shape[1] != current_features.shape[1]
    ):
        raise PredictionError("insufficient data for customer amount prediction")

    model = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
    model.fit(features, targets)
    fitted = model.predict(features)
    predictions = np.maximum(0.0, model.predict(current_features))
    return predictions, {
        "mae": float(mean_absolute_error(targets, fitted)),
        "rmse": float(mean_squared_error(targets, fitted) ** 0.5),
    }


def _rolling_point(history):
    return float(
        0.5 * np.mean(history[-7:])
        + 0.3 * np.mean(history[-14:])
        + 0.2 * np.mean(history[-28:])
    )


def rolling_sales_baseline(history, horizon_days):
    history = np.asarray(history, dtype=float)
    horizon_days = _bounded_int(
        horizon_days, minimum=1, maximum=90, name="horizon_days"
    )
    if history.ndim != 1 or len(history) < 14:
        raise PredictionError("insufficient data for product sales forecast")

    errors = [
        abs(float(history[index]) - _rolling_point(history[:index]))
        for index in range(14, len(history))
    ]
    forecast = max(0.0, _rolling_point(history))
    return np.full(horizon_days, forecast, dtype=float), {
        "mae": float(np.mean(errors)) if errors else 0.0,
    }


def cosine_recommendation_baseline(interactions, top_k):
    from sklearn.metrics.pairwise import cosine_similarity

    interactions = np.asarray(interactions, dtype=float)
    top_k = _bounded_int(top_k, minimum=1, maximum=20, name="top_k")
    if (
        interactions.ndim != 2
        or interactions.shape[0] < 2
        or interactions.shape[1] < 2
    ):
        raise PredictionError("insufficient data for product recommendations")

    similarity = cosine_similarity(interactions.T)
    np.fill_diagonal(similarity, 0.0)
    recommendations = []
    for customer_index, purchases in enumerate(interactions):
        if purchases.sum() <= 0:
            continue
        scores = np.clip((purchases @ similarity) / purchases.sum(), 0.0, 1.0)
        candidates = [
            product_index
            for product_index in range(interactions.shape[1])
            if purchases[product_index] <= 0 and scores[product_index] > 0
        ]
        candidates.sort(key=lambda product_index: (-scores[product_index], product_index))
        for rank, product_index in enumerate(candidates[:top_k], start=1):
            recommendations.append(
                (customer_index, product_index, rank, float(scores[product_index]))
            )
    if not recommendations:
        raise PredictionError("insufficient data for product recommendations")
    return recommendations


def _model_snapshot():
    row = db.session.execute(text("""
        SELECT COUNT(*)::int AS flow_count,
               COALESCE(MAX(flow_id), 0)::bigint AS max_flow_id,
               MAX(occurred_at) AS max_occurred_at,
               CURRENT_DATE AS snapshot_date,
               (SELECT MAX(task_id) FROM ml.model_task
                WHERE task_type = 'analytics_refresh' AND status = 'success')
                   AS analytics_refresh_task_id
        FROM dwd.consumption_flow
    """)).mappings().one()
    return dict(row)


def _ensure_model(task_type):
    spec = MODEL_SPECS[task_type]
    return db.session.execute(text("""
        INSERT INTO ml.model_registry
            (model_key, model_version, algorithm, feature_list, metadata)
        VALUES
            (:model_key, 'baseline-v1', :algorithm,
             CAST(:feature_list AS jsonb), CAST(:metadata AS jsonb))
        ON CONFLICT (model_key, model_version)
        DO UPDATE SET model_key = EXCLUDED.model_key
        RETURNING model_id
    """), {
        "model_key": task_type,
        "algorithm": spec["algorithm"],
        "feature_list": _json(spec["feature_list"]),
        "metadata": _json({
            "evaluation_metric": spec["evaluation_metric"],
            "random_seed": RANDOM_SEED,
        }),
    }).scalar_one()


def create_model_task(task_type, operator_id, parameters=None):
    if task_type not in MODEL_SPECS:
        raise PredictionError(f"unsupported model task: {task_type}")
    spec = MODEL_SPECS[task_type]
    task_parameters = {
        "feature_list": spec["feature_list"],
        "training_window": spec["training_window"],
        "random_seed": RANDOM_SEED,
        "evaluation_metric": spec["evaluation_metric"],
        **(parameters or {}),
    }
    model_id = _ensure_model(task_type)
    task_id = db.session.execute(text("""
        INSERT INTO ml.model_task
            (task_type, parameters, created_by, model_id,
             dataset_snapshot, code_version)
        VALUES
            (:task_type, CAST(:parameters AS jsonb), :created_by, :model_id,
             CAST(:dataset_snapshot AS jsonb), :code_version)
        RETURNING task_id
    """), {
        "task_type": task_type,
        "parameters": _json(task_parameters),
        "created_by": operator_id,
        "model_id": model_id,
        "dataset_snapshot": _json(_model_snapshot()),
        "code_version": CODE_VERSION,
    }).scalar_one()
    db.session.commit()
    return task_id


def finish_model_task(task_id, status="success", error=None):
    db.session.execute(text("""
        UPDATE ml.model_task
        SET status = :status, finished_at = now(), error_message = :error
        WHERE task_id = :task_id
    """), {
        "task_id": task_id,
        "status": status,
        "error": error,
    })
    db.session.commit()


def record_model_metric(task_id, name, value, dataset="all"):
    db.session.execute(text("""
        INSERT INTO ml.model_metric (task_id, metric_name, metric_value, dataset)
        VALUES (:task_id, :name, :value, :dataset)
        ON CONFLICT (task_id, metric_name, dataset)
        DO UPDATE SET metric_value = EXCLUDED.metric_value
    """), {
        "task_id": task_id,
        "name": name,
        "value": float(value),
        "dataset": dataset,
    })


def _amount_rows(horizon_days, training_days, *, current=False):
    cutoff = date.today() if current else date.today() - timedelta(days=horizon_days)
    target_end = date.today() + timedelta(days=1)
    return db.session.execute(text("""
        SELECT customer.customer_id,
               GREATEST(0, CAST(:cutoff AS date) - COALESCE(
                   MAX(flow.occurred_at::date) FILTER (
                       WHERE flow.flow_type = 'payment'
                         AND flow.occurred_at >= CAST(:cutoff AS date)
                                                - (:training_days || ' days')::interval
                         AND flow.occurred_at < CAST(:cutoff AS date)
                   ), customer.registered_at::date
               ))::float AS recency_days,
               COUNT(DISTINCT flow.order_id) FILTER (
                   WHERE flow.flow_type = 'payment'
                     AND flow.occurred_at >= CAST(:cutoff AS date)
                                            - (:training_days || ' days')::interval
                     AND flow.occurred_at < CAST(:cutoff AS date)
               )::float AS frequency,
               COALESCE(SUM(flow.net_amount) FILTER (
                   WHERE flow.occurred_at >= CAST(:cutoff AS date)
                                            - (:training_days || ' days')::interval
                     AND flow.occurred_at < CAST(:cutoff AS date)
               ), 0)::float AS monetary,
               CASE WHEN COUNT(DISTINCT flow.order_id) FILTER (
                   WHERE flow.flow_type = 'payment'
                     AND flow.occurred_at >= CAST(:cutoff AS date)
                                            - (:training_days || ' days')::interval
                     AND flow.occurred_at < CAST(:cutoff AS date)
               ) > 0 THEN
                   COALESCE(SUM(flow.net_amount) FILTER (
                       WHERE flow.occurred_at >= CAST(:cutoff AS date)
                                                - (:training_days || ' days')::interval
                         AND flow.occurred_at < CAST(:cutoff AS date)
                   ), 0)::float /
                   COUNT(DISTINCT flow.order_id) FILTER (
                       WHERE flow.flow_type = 'payment'
                         AND flow.occurred_at >= CAST(:cutoff AS date)
                                                - (:training_days || ' days')::interval
                         AND flow.occurred_at < CAST(:cutoff AS date)
                   )
               ELSE 0 END AS average_order_value,
               COALESCE(SUM(flow.net_amount) FILTER (
                   WHERE flow.occurred_at >= CAST(:cutoff AS date)
                     AND flow.occurred_at < CAST(:target_end AS date)
               ), 0)::float AS target_amount
        FROM biz.customer customer
        LEFT JOIN dwd.consumption_flow flow
          ON flow.customer_id = customer.customer_id
        WHERE customer.status = 'active'
          AND customer.registered_at < CAST(:cutoff AS date)
        GROUP BY customer.customer_id, customer.registered_at
        ORDER BY customer.customer_id
    """), {
        "cutoff": cutoff,
        "target_end": target_end,
        "training_days": training_days,
    }).mappings().all()


def _feature_matrix(rows):
    return np.asarray([
        [
            row["recency_days"], row["frequency"], row["monetary"],
            row["average_order_value"],
        ]
        for row in rows
    ], dtype=float)


def _product_daily_rows(training_days):
    return db.session.execute(text("""
        SELECT item.product_id, flow.occurred_at::date AS sales_date,
               SUM(item.quantity * flow.net_amount /
                   NULLIF(payment.amount, 0))::float AS quantity
        FROM dwd.consumption_flow flow
        JOIN biz.payment payment ON payment.payment_id = flow.payment_id
        JOIN biz.order_item item ON item.order_id = flow.order_id
        WHERE flow.occurred_at >= CURRENT_DATE - (:training_days || ' days')::interval
          AND flow.occurred_at < CURRENT_DATE + interval '1 day'
        GROUP BY item.product_id, flow.occurred_at::date
        ORDER BY item.product_id, sales_date
    """), {"training_days": training_days}).mappings().all()


def _interaction_rows(training_days):
    return db.session.execute(text("""
        SELECT sales_order.customer_id, item.product_id,
               GREATEST(0, SUM(item.quantity * flow.net_amount /
                   NULLIF(payment.amount, 0)))::float AS quantity
        FROM dwd.consumption_flow flow
        JOIN biz.payment payment ON payment.payment_id = flow.payment_id
        JOIN biz.sales_order sales_order ON sales_order.order_id = flow.order_id
        JOIN biz.order_item item ON item.order_id = flow.order_id
        WHERE flow.occurred_at >= CURRENT_DATE - (:training_days || ' days')::interval
          AND flow.occurred_at < CURRENT_DATE + interval '1 day'
        GROUP BY sales_order.customer_id, item.product_id
        HAVING SUM(item.quantity * flow.net_amount / NULLIF(payment.amount, 0)) > 0
        ORDER BY sales_order.customer_id, item.product_id
    """), {"training_days": training_days}).mappings().all()


class PredictionService:
    @staticmethod
    def run_customer_amount(
        operator_id, horizon_days=30, training_days=180
    ):
        horizon_days = _bounded_int(
            horizon_days, minimum=1, maximum=90, name="horizon_days"
        )
        training_days = _bounded_int(
            training_days, minimum=60, maximum=730, name="training_days"
        )
        task_id = create_model_task("customer_amount", operator_id, {
            "horizon_days": horizon_days,
            "training_window": {"days": training_days, "label_days": horizon_days},
        })
        try:
            training = _amount_rows(horizon_days, training_days)
            current = _amount_rows(0, training_days, current=True)
            predictions, metrics = ridge_amount_baseline(
                _feature_matrix(training),
                np.asarray([row["target_amount"] for row in training], dtype=float),
                _feature_matrix(current),
            )
            db.session.execute(text("""
                INSERT INTO ml.customer_amount_prediction
                    (task_id, customer_id, horizon_days, forecast_start,
                     forecast_end, predicted_amount)
                VALUES
                    (:task_id, :customer_id, :horizon_days, :forecast_start,
                     :forecast_end, :predicted_amount)
            """), [
                {
                    "task_id": task_id,
                    "customer_id": row["customer_id"],
                    "horizon_days": horizon_days,
                    "forecast_start": date.today() + timedelta(days=1),
                    "forecast_end": date.today() + timedelta(days=horizon_days),
                    "predicted_amount": round(float(prediction), 2),
                }
                for row, prediction in zip(current, predictions)
            ])
            record_model_metric(task_id, "mae", metrics["mae"], "training")
            record_model_metric(task_id, "rmse", metrics["rmse"], "training")
            record_model_metric(task_id, "sample_count", len(training), "training")
            record_model_metric(task_id, "prediction_count", len(current))
            db.session.commit()
            finish_model_task(task_id)
            return task_id
        except Exception as exc:
            db.session.rollback()
            finish_model_task(task_id, "failed", str(exc)[:2000])
            raise

    @staticmethod
    def run_product_sales_forecast(
        operator_id, horizon_days=30, training_days=90
    ):
        horizon_days = _bounded_int(
            horizon_days, minimum=1, maximum=90, name="horizon_days"
        )
        training_days = _bounded_int(
            training_days, minimum=28, maximum=730, name="training_days"
        )
        task_id = create_model_task("product_sales_forecast", operator_id, {
            "horizon_days": horizon_days,
            "training_window": {"days": training_days},
        })
        try:
            rows = _product_daily_rows(training_days)
            source_dates = {row["sales_date"] for row in rows}
            if len(source_dates) < 14:
                raise PredictionError(
                    "insufficient data for product sales forecast: at least 14 sales days are required"
                )
            by_product = {}
            for row in rows:
                by_product.setdefault(row["product_id"], {})[row["sales_date"]] = max(
                    0.0, float(row["quantity"])
                )
            product_ids = [row["product_id"] for row in db.session.execute(text(
                "SELECT product_id FROM biz.product WHERE status = 'active' ORDER BY product_id"
            )).mappings().all()]
            start = date.today() - timedelta(days=training_days - 1)
            result_rows = []
            errors = []
            for product_id in product_ids:
                daily = by_product.get(product_id, {})
                history = np.asarray([
                    daily.get(start + timedelta(days=offset), 0.0)
                    for offset in range(training_days)
                ], dtype=float)
                forecast, metrics = rolling_sales_baseline(history, horizon_days)
                errors.append(metrics["mae"])
                result_rows.extend({
                    "task_id": task_id,
                    "product_id": product_id,
                    "forecast_date": date.today() + timedelta(days=offset),
                    "predicted_quantity": round(float(value), 4),
                } for offset, value in enumerate(forecast, start=1))
            if not result_rows:
                raise PredictionError("insufficient data for product sales forecast")
            db.session.execute(text("""
                INSERT INTO ml.product_sales_forecast
                    (task_id, product_id, forecast_date, predicted_quantity)
                VALUES
                    (:task_id, :product_id, :forecast_date, :predicted_quantity)
            """), result_rows)
            record_model_metric(task_id, "mae", float(np.mean(errors)), "backtest")
            record_model_metric(task_id, "product_count", len(product_ids))
            record_model_metric(task_id, "forecast_row_count", len(result_rows))
            db.session.commit()
            finish_model_task(task_id)
            return task_id
        except Exception as exc:
            db.session.rollback()
            finish_model_task(task_id, "failed", str(exc)[:2000])
            raise

    @staticmethod
    def run_product_recommendation(operator_id, top_k=5, training_days=180):
        top_k = _bounded_int(top_k, minimum=1, maximum=20, name="top_k")
        training_days = _bounded_int(
            training_days, minimum=30, maximum=730, name="training_days"
        )
        task_id = create_model_task("product_recommendation", operator_id, {
            "top_k": top_k,
            "training_window": {"days": training_days},
        })
        try:
            rows = _interaction_rows(training_days)
            customer_ids = sorted({row["customer_id"] for row in rows})
            product_ids = sorted({row["product_id"] for row in rows})
            customer_index = {value: index for index, value in enumerate(customer_ids)}
            product_index = {value: index for index, value in enumerate(product_ids)}
            matrix = np.zeros((len(customer_ids), len(product_ids)), dtype=float)
            for row in rows:
                matrix[customer_index[row["customer_id"]], product_index[row["product_id"]]] = row["quantity"]
            recommendations = cosine_recommendation_baseline(matrix, top_k)
            result_rows = [
                {
                    "task_id": task_id,
                    "customer_id": customer_ids[customer_no],
                    "product_id": product_ids[product_no],
                    "rank_no": rank,
                    "score": round(score, 8),
                }
                for customer_no, product_no, rank, score in recommendations
            ]
            db.session.execute(text("""
                INSERT INTO ml.product_recommendation
                    (task_id, customer_id, product_id, rank_no, score)
                VALUES
                    (:task_id, :customer_id, :product_id, :rank_no, :score)
            """), result_rows)
            record_model_metric(task_id, "mean_similarity", np.mean([
                row["score"] for row in result_rows
            ]))
            record_model_metric(task_id, "customer_count", len({
                row["customer_id"] for row in result_rows
            }))
            record_model_metric(task_id, "recommendation_count", len(result_rows))
            db.session.commit()
            finish_model_task(task_id)
            return task_id
        except Exception as exc:
            db.session.rollback()
            finish_model_task(task_id, "failed", str(exc)[:2000])
            raise


def load_customer_amount_predictions(task_id):
    return [dict(row) for row in db.session.execute(text("""
        SELECT prediction.task_id, prediction.customer_id,
               customer.customer_no, customer.name,
               prediction.horizon_days, prediction.forecast_start,
               prediction.forecast_end, prediction.predicted_amount::float AS predicted_amount
        FROM ml.customer_amount_prediction prediction
        JOIN biz.customer customer ON customer.customer_id = prediction.customer_id
        WHERE prediction.task_id = :task_id
        ORDER BY prediction.predicted_amount DESC, prediction.customer_id
    """), {"task_id": task_id}).mappings().all()]


def load_product_sales_forecasts(task_id):
    return [dict(row) for row in db.session.execute(text("""
        SELECT forecast.task_id, forecast.product_id, product.sku,
               product.product_name, forecast.forecast_date,
               forecast.predicted_quantity::float AS predicted_quantity
        FROM ml.product_sales_forecast forecast
        JOIN biz.product product ON product.product_id = forecast.product_id
        WHERE forecast.task_id = :task_id
        ORDER BY forecast.forecast_date, forecast.predicted_quantity DESC,
                 forecast.product_id
    """), {"task_id": task_id}).mappings().all()]


def load_product_recommendations(task_id):
    return [dict(row) for row in db.session.execute(text("""
        SELECT recommendation.task_id, recommendation.customer_id,
               customer.customer_no, customer.name,
               recommendation.product_id, product.sku, product.product_name,
               recommendation.rank_no,
               recommendation.score::float AS score
        FROM ml.product_recommendation recommendation
        JOIN biz.customer customer ON customer.customer_id = recommendation.customer_id
        JOIN biz.product product ON product.product_id = recommendation.product_id
        WHERE recommendation.task_id = :task_id
        ORDER BY recommendation.customer_id, recommendation.rank_no
    """), {"task_id": task_id}).mappings().all()]
