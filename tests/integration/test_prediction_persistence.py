from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]


def _initialize(connection):
    from scripts.init_db import apply_migrations

    with connection.cursor() as cursor:
        cursor.execute((ROOT / "database" / "v2_schema.sql").read_text(encoding="utf-8"))
        cursor.execute((ROOT / "database" / "v2_seed.sql").read_text(encoding="utf-8"))
    connection.commit()
    apply_migrations(connection)


def _seed_prediction_history(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT account_id FROM auth.account WHERE username = 'admin'")
        account_id = cursor.fetchone()[0]
        for index in range(8):
            cursor.execute(
                """
                INSERT INTO biz.customer
                    (customer_no, name, registered_at)
                VALUES (%s, %s, %s)
                RETURNING customer_id
                """,
                (
                    f"PRED-C{index:03d}",
                    f"Prediction Customer {index}",
                    date.today() - timedelta(days=300 + index),
                ),
            )

        cursor.execute("SELECT customer_id FROM biz.customer ORDER BY customer_id")
        customer_ids = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            "SELECT product_id, unit_price FROM biz.product ORDER BY product_id"
        )
        products = cursor.fetchall()

        def add_payment(sequence, customer_id, product, occurred_at, quantity):
            product_id, unit_price = product
            amount = unit_price * quantity
            cursor.execute(
                """
                INSERT INTO biz.sales_order
                    (order_no, customer_id, status, total_amount, paid_amount,
                     ordered_at, paid_at, created_by)
                VALUES (%s, %s, 'paid', %s, %s, %s, %s, %s)
                RETURNING order_id
                """,
                (
                    f"PRED-O{sequence:04d}", customer_id, amount, amount,
                    occurred_at, occurred_at, account_id,
                ),
            )
            order_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO biz.order_item
                    (order_id, product_id, quantity, unit_price, line_amount)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (order_id, product_id, quantity, unit_price, amount),
            )
            cursor.execute(
                """
                INSERT INTO biz.payment
                    (payment_no, order_id, method, amount, status, paid_at, created_by)
                VALUES (%s, %s, 'wechat', %s, 'success', %s, %s)
                RETURNING payment_id
                """,
                (f"PRED-P{sequence:04d}", order_id, amount, occurred_at, account_id),
            )
            payment_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO dwd.consumption_flow
                    (customer_id, order_id, payment_id, flow_type,
                     gross_amount, net_amount, occurred_at)
                VALUES (%s, %s, %s, 'payment', %s, %s, %s)
                """,
                (customer_id, order_id, payment_id, amount, amount, occurred_at),
            )

        sequence = 1
        for index, customer_id in enumerate(customer_ids):
            add_payment(
                sequence,
                customer_id,
                products[index % len(products)],
                date.today() - timedelta(days=90 - index),
                1 + index % 2,
            )
            sequence += 1
        recent_customers = customer_ids[::2]
        for days_ago in range(1, 29):
            customer_id = recent_customers[days_ago % len(recent_customers)]
            add_payment(
                sequence,
                customer_id,
                products[(days_ago + 1) % len(products)],
                date.today() - timedelta(days=days_ago),
                1 + days_ago % 3,
            )
            sequence += 1
    connection.commit()
    return account_id


def test_all_model_runs_are_versioned_task_scoped_and_preserve_history(
    isolated_database, isolated_app
):
    from app.extensions import db
    from app.services.algorithms import run_churn, run_kmeans, run_rfm
    from app.services.prediction import PredictionService

    _initialize(isolated_database)
    operator_id = _seed_prediction_history(isolated_database)

    with isolated_app.app_context():
        task_ids = {
            "rfm": run_rfm(operator_id),
        }
        task_ids["kmeans"] = run_kmeans(operator_id, 3)
        task_ids["churn"] = run_churn(operator_id, 30)
        task_ids["customer_amount"] = PredictionService.run_customer_amount(
            operator_id, horizon_days=30, training_days=120
        )
        task_ids["product_sales_forecast"] = PredictionService.run_product_sales_forecast(
            operator_id, horizon_days=30, training_days=120
        )
        task_ids["product_recommendation"] = PredictionService.run_product_recommendation(
            operator_id, top_k=3, training_days=120
        )

        lifecycle = db.session.execute(text("""
            SELECT task.task_id, task.task_type, task.status, task.parameters,
                   task.dataset_snapshot, task.code_version,
                   registry.model_key, registry.model_version,
                   registry.feature_list,
                   COUNT(metric.metric_id)::int AS metric_count
            FROM ml.model_task task
            JOIN ml.model_registry registry ON registry.model_id = task.model_id
            LEFT JOIN ml.model_metric metric ON metric.task_id = task.task_id
            WHERE task.task_id = ANY(:task_ids)
            GROUP BY task.task_id, registry.model_id
            ORDER BY task.task_id
        """), {"task_ids": list(task_ids.values())}).mappings().all()

        assert len(lifecycle) == 6
        assert {row["task_type"] for row in lifecycle} == set(task_ids)
        for row in lifecycle:
            assert row["status"] == "success"
            assert row["model_key"] == row["task_type"]
            assert row["model_version"]
            assert row["feature_list"]
            assert row["dataset_snapshot"]["max_flow_id"] > 0
            assert row["code_version"]
            assert row["metric_count"] > 0
            for key in (
                "feature_list", "training_window", "random_seed", "evaluation_metric"
            ):
                assert key in row["parameters"]

        result_tables = {
            "rfm": "ml.rfm_result",
            "kmeans": "ml.cluster_result",
            "churn": "ml.churn_prediction",
            "customer_amount": "ml.customer_amount_prediction",
            "product_sales_forecast": "ml.product_sales_forecast",
            "product_recommendation": "ml.product_recommendation",
        }
        first_counts = {}
        for task_type, table in result_tables.items():
            first_counts[task_type] = db.session.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE task_id = :task_id"),
                {"task_id": task_ids[task_type]},
            ).scalar_one()
            assert first_counts[task_type] > 0

        previous_amount_task = task_ids["customer_amount"]
        next_amount_task = PredictionService.run_customer_amount(
            operator_id, horizon_days=30, training_days=120
        )
        assert next_amount_task != previous_amount_task
        assert db.session.execute(text("""
            SELECT COUNT(*) FROM ml.customer_amount_prediction
            WHERE task_id = :task_id
        """), {"task_id": previous_amount_task}).scalar_one() == first_counts["customer_amount"]


def test_prediction_loaders_cannot_mix_task_results(isolated_database, isolated_app):
    from app.extensions import db
    from app.services.prediction import (
        load_customer_amount_predictions,
        load_product_recommendations,
        load_product_sales_forecasts,
    )

    _initialize(isolated_database)
    with isolated_app.app_context():
        for loader in (
            load_customer_amount_predictions,
            load_product_sales_forecasts,
            load_product_recommendations,
        ):
            assert loader(-1) == []
        assert db.session.execute(text(
            "SELECT COUNT(*) FROM ml.model_registry"
        )).scalar_one() >= 3
