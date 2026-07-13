import json
import time
from datetime import date, datetime

from sqlalchemy import text

from app.extensions import db


ANALYTICS_LOCK_KEY = 704509013006


class AnalyticsRefreshError(RuntimeError):
    pass


def _snapshot_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("snapshot_date must be an ISO date") from exc
    raise TypeError("snapshot_date must be a date or ISO date string")


def _create_task(snapshot_date, operator_id):
    task_id = db.session.execute(text("""
        INSERT INTO ml.model_task (task_type, parameters, created_by)
        VALUES ('analytics_refresh', CAST(:parameters AS jsonb), :operator_id)
        RETURNING task_id
    """), {
        "parameters": json.dumps({"snapshot_date": snapshot_date.isoformat()}),
        "operator_id": operator_id,
    }).scalar_one()
    db.session.commit()
    return task_id


def _source_flow_count(snapshot_date):
    row = db.session.execute(text("""
        SELECT
            COUNT(*)::int AS flow_count,
            COUNT(*) FILTER (
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM biz.order_item item
                    WHERE item.order_id = flow.order_id
                )
            )::int AS flow_without_items
        FROM dwd.consumption_flow flow
        WHERE flow.occurred_at < CAST(:snapshot_date AS date) + INTERVAL '1 day'
    """), {"snapshot_date": snapshot_date}).mappings().one()
    if row["flow_without_items"]:
        raise AnalyticsRefreshError(
            f"cannot allocate {row['flow_without_items']} consumption flows without order items"
        )
    return row["flow_count"]


def _replace_daily_sales(snapshot_date, task_id):
    db.session.execute(text("""
        DELETE FROM ads.daily_sales
        WHERE snapshot_date = :snapshot_date
    """), {"snapshot_date": snapshot_date})
    return db.session.execute(text("""
        WITH eligible_flow AS (
            SELECT flow.*
            FROM dwd.consumption_flow flow
            WHERE flow.occurred_at < CAST(:snapshot_date AS date) + INTERVAL '1 day'
        ), order_basis AS (
            SELECT item.order_id,
                   SUM(item.quantity)::numeric AS total_quantity,
                   SUM(item.line_amount)::numeric AS total_amount
            FROM biz.order_item item
            GROUP BY item.order_id
        ), daily AS (
            SELECT flow.occurred_at::date AS sales_date,
                   COUNT(DISTINCT flow.order_id) FILTER (
                       WHERE flow.flow_type = 'payment'
                   )::int AS order_count,
                   ROUND(SUM(
                       CASE
                           WHEN basis.total_amount > 0
                               THEN basis.total_quantity * flow.net_amount / basis.total_amount
                           ELSE SIGN(flow.net_amount) * basis.total_quantity
                       END
                   ), 0)::int AS item_quantity,
                   COALESCE(SUM(flow.gross_amount), 0)::numeric(18,2) AS gross_amount,
                   COALESCE(SUM(flow.net_amount), 0)::numeric(18,2) AS net_amount
            FROM eligible_flow flow
            JOIN order_basis basis ON basis.order_id = flow.order_id
            GROUP BY flow.occurred_at::date
        ), bounds AS (
            SELECT COALESCE(MIN(sales_date), CAST(:snapshot_date AS date)) AS first_date
            FROM daily
        ), calendar AS (
            SELECT generated.day::date AS sales_date
            FROM bounds
            CROSS JOIN LATERAL generate_series(
                bounds.first_date,
                CAST(:snapshot_date AS date),
                INTERVAL '1 day'
            ) AS generated(day)
        )
        INSERT INTO ads.daily_sales
            (snapshot_date, sales_date, order_count, item_quantity,
             gross_amount, net_amount, refresh_task_id)
        SELECT :snapshot_date, calendar.sales_date,
               COALESCE(daily.order_count, 0),
               COALESCE(daily.item_quantity, 0),
               COALESCE(daily.gross_amount, 0)::numeric(18,2),
               COALESCE(daily.net_amount, 0)::numeric(18,2),
               :task_id
        FROM calendar
        LEFT JOIN daily USING (sales_date)
        ORDER BY calendar.sales_date
    """), {"snapshot_date": snapshot_date, "task_id": task_id}).rowcount


def _replace_product_sales(snapshot_date, task_id):
    db.session.execute(text("""
        DELETE FROM ads.product_sales
        WHERE snapshot_date = :snapshot_date
    """), {"snapshot_date": snapshot_date})
    return db.session.execute(text("""
        WITH eligible_flow AS (
            SELECT flow.*
            FROM dwd.consumption_flow flow
            WHERE flow.occurred_at < CAST(:snapshot_date AS date) + INTERVAL '1 day'
        ), item_basis AS (
            SELECT flow.flow_id, flow.flow_type, flow.gross_amount, flow.net_amount,
                   item.order_item_id, item.product_id, item.quantity, item.line_amount,
                   product.category_id,
                   SUM(item.line_amount) OVER (
                       PARTITION BY flow.flow_id
                   )::numeric AS order_amount,
                   SUM(item.quantity) OVER (
                       PARTITION BY flow.flow_id
                   )::numeric AS order_quantity,
                   ROW_NUMBER() OVER (
                       PARTITION BY flow.flow_id
                       ORDER BY item.line_amount DESC, item.order_item_id
                   ) AS residual_rank
            FROM eligible_flow flow
            JOIN biz.order_item item ON item.order_id = flow.order_id
            JOIN biz.product product ON product.product_id = item.product_id
        ), weighted AS (
            SELECT basis.*,
                   CASE
                       WHEN basis.order_amount > 0
                           THEN basis.line_amount / basis.order_amount
                       ELSE basis.quantity / basis.order_quantity
                   END AS allocation_weight,
                   basis.gross_amount::numeric AS sales_gross_amount,
                   CASE
                       WHEN basis.order_amount > 0
                           THEN basis.quantity * basis.net_amount / basis.order_amount
                       ELSE SIGN(basis.net_amount) * basis.quantity
                   END AS allocated_quantity
            FROM item_basis basis
        ), rounded AS (
            SELECT weighted.*,
                   ROUND(sales_gross_amount * allocation_weight, 2) AS base_gross,
                   ROUND(net_amount * allocation_weight, 2) AS base_net
            FROM weighted
        ), allocated AS (
            SELECT rounded.*,
                   base_gross + CASE WHEN residual_rank = 1 THEN
                       sales_gross_amount
                       - SUM(base_gross) OVER (PARTITION BY flow_id)
                   ELSE 0 END AS allocated_gross,
                   base_net + CASE WHEN residual_rank = 1 THEN
                       net_amount - SUM(base_net) OVER (PARTITION BY flow_id)
                   ELSE 0 END AS allocated_net
            FROM rounded
        )
        INSERT INTO ads.product_sales
            (snapshot_date, product_id, category_id, quantity,
             gross_amount, net_amount, refresh_task_id)
        SELECT :snapshot_date, product_id, category_id,
               ROUND(SUM(allocated_quantity), 0)::int,
               SUM(allocated_gross)::numeric(18,2),
               SUM(allocated_net)::numeric(18,2),
               :task_id
        FROM allocated
        GROUP BY product_id, category_id
        ORDER BY product_id
    """), {"snapshot_date": snapshot_date, "task_id": task_id}).rowcount


def _replace_category_sales(snapshot_date, task_id):
    db.session.execute(text("""
        DELETE FROM ads.category_sales
        WHERE snapshot_date = :snapshot_date
    """), {"snapshot_date": snapshot_date})
    return db.session.execute(text("""
        INSERT INTO ads.category_sales
            (snapshot_date, category_id, quantity, gross_amount,
             net_amount, refresh_task_id)
        SELECT :snapshot_date, category_id,
               SUM(quantity)::int,
               SUM(gross_amount)::numeric(18,2),
               SUM(net_amount)::numeric(18,2),
               :task_id
        FROM ads.product_sales
        WHERE snapshot_date = :snapshot_date
          AND refresh_task_id = :task_id
        GROUP BY category_id
        ORDER BY category_id NULLS LAST
    """), {"snapshot_date": snapshot_date, "task_id": task_id}).rowcount


def _replace_customer_profile(snapshot_date, task_id):
    db.session.execute(text("""
        DELETE FROM ads.customer_profile
        WHERE snapshot_date = :snapshot_date
    """), {"snapshot_date": snapshot_date})
    return db.session.execute(text("""
        WITH eligible_flow AS (
            SELECT flow.*
            FROM dwd.consumption_flow flow
            WHERE flow.occurred_at < CAST(:snapshot_date AS date) + INTERVAL '1 day'
        )
        INSERT INTO ads.customer_profile
            (snapshot_date, customer_id, frequency, monetary,
             recency_days, refresh_task_id)
        SELECT :snapshot_date, customer.customer_id,
               COUNT(DISTINCT flow.order_id) FILTER (
                   WHERE flow.flow_type = 'payment'
               )::int AS frequency,
               COALESCE(SUM(flow.net_amount), 0)::numeric(18,2) AS monetary,
               GREATEST(
                   0,
                   CAST(:snapshot_date AS date) - COALESCE(
                       MAX(flow.occurred_at::date) FILTER (
                           WHERE flow.flow_type = 'payment'
                       ),
                       customer.registered_at::date
                   )
               )::int AS recency_days,
               :task_id
        FROM biz.customer customer
        LEFT JOIN eligible_flow flow ON flow.customer_id = customer.customer_id
        WHERE customer.registered_at
              < CAST(:snapshot_date AS date) + INTERVAL '1 day'
        GROUP BY customer.customer_id, customer.registered_at
        ORDER BY customer.customer_id
    """), {"snapshot_date": snapshot_date, "task_id": task_id}).rowcount


def _replace_customer_rfm(snapshot_date, task_id):
    db.session.execute(text("DELETE FROM ads.customer_rfm"))
    return db.session.execute(text("""
        WITH scored AS (
            SELECT profile.*,
                   (6 - NTILE(5) OVER (
                       ORDER BY recency_days ASC, customer_id
                   ))::smallint AS r_score,
                   NTILE(5) OVER (
                       ORDER BY frequency ASC, customer_id
                   )::smallint AS f_score,
                   NTILE(5) OVER (
                       ORDER BY monetary ASC, customer_id
                   )::smallint AS m_score
            FROM ads.customer_profile profile
            WHERE profile.snapshot_date = :snapshot_date
              AND profile.refresh_task_id = :task_id
        ), segmented AS (
            SELECT scored.*,
                   CASE
                       WHEN r_score >= 4 AND f_score >= 4 THEN 'high_value'
                       WHEN r_score >= 4 THEN 'recent'
                       WHEN f_score >= 4 AND m_score >= 4 THEN 'loyal'
                       WHEN r_score <= 2 THEN 'at_risk'
                       ELSE 'standard'
                   END AS segment
            FROM scored
        )
        INSERT INTO ads.customer_rfm
            (customer_id, recency_days, frequency, monetary,
             r_score, f_score, m_score, segment, task_id, calculated_at,
             snapshot_date, refresh_task_id)
        SELECT customer_id, recency_days, frequency, monetary,
               r_score, f_score, m_score, segment, NULL, now(),
               :snapshot_date, :task_id
        FROM segmented
        ORDER BY customer_id
    """), {"snapshot_date": snapshot_date, "task_id": task_id}).rowcount


def _record_success(task_id, snapshot_date, row_counts, elapsed_seconds):
    metric_rows = [
        {
            "task_id": task_id,
            "metric_name": "row_count",
            "metric_value": count,
            "dataset": dataset,
        }
        for dataset, count in row_counts.items()
    ]
    metric_rows.append({
        "task_id": task_id,
        "metric_name": "elapsed_seconds",
        "metric_value": elapsed_seconds,
        "dataset": "all",
    })
    db.session.execute(text("""
        INSERT INTO ml.model_metric
            (task_id, metric_name, metric_value, dataset)
        VALUES (:task_id, :metric_name, :metric_value, :dataset)
        ON CONFLICT (task_id, metric_name, dataset)
        DO UPDATE SET metric_value = EXCLUDED.metric_value
    """), metric_rows)
    parameters = {
        "snapshot_date": snapshot_date.isoformat(),
        "row_counts": row_counts,
        "elapsed_seconds": round(elapsed_seconds, 6),
    }
    db.session.execute(text("""
        UPDATE ml.model_task
        SET status = 'success', finished_at = now(), error_message = NULL,
            parameters = parameters || CAST(:parameters AS jsonb)
        WHERE task_id = :task_id
    """), {"task_id": task_id, "parameters": json.dumps(parameters)})


def _record_failure(task_id, snapshot_date, elapsed_seconds, error):
    parameters = {
        "snapshot_date": snapshot_date.isoformat(),
        "elapsed_seconds": round(elapsed_seconds, 6),
    }
    db.session.execute(text("""
        INSERT INTO ml.model_metric
            (task_id, metric_name, metric_value, dataset)
        VALUES (:task_id, 'elapsed_seconds', :elapsed_seconds, 'all')
        ON CONFLICT (task_id, metric_name, dataset)
        DO UPDATE SET metric_value = EXCLUDED.metric_value
    """), {"task_id": task_id, "elapsed_seconds": elapsed_seconds})
    db.session.execute(text("""
        UPDATE ml.model_task
        SET status = 'failed', finished_at = now(), error_message = :error,
            parameters = parameters || CAST(:parameters AS jsonb)
        WHERE task_id = :task_id
    """), {
        "task_id": task_id,
        "error": f"{type(error).__name__}: {error}"[:2000],
        "parameters": json.dumps(parameters),
    })
    db.session.commit()


class AnalyticsService:
    @staticmethod
    def refresh(snapshot_date, operator_id):
        snapshot_date = _snapshot_date(snapshot_date)
        started_at = time.perf_counter()
        task_id = _create_task(snapshot_date, operator_id)

        try:
            with db.session.begin():
                db.session.execute(text(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                ))
                db.session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": ANALYTICS_LOCK_KEY},
                )

                row_counts = {
                    "source_flow": _source_flow_count(snapshot_date),
                    "daily_sales": _replace_daily_sales(snapshot_date, task_id),
                    "product_sales": _replace_product_sales(snapshot_date, task_id),
                    "category_sales": _replace_category_sales(snapshot_date, task_id),
                    "customer_profile": _replace_customer_profile(snapshot_date, task_id),
                    "customer_rfm": _replace_customer_rfm(snapshot_date, task_id),
                }
                elapsed_seconds = time.perf_counter() - started_at
                _record_success(
                    task_id,
                    snapshot_date,
                    row_counts,
                    elapsed_seconds,
                )
            return task_id
        except Exception as error:
            db.session.rollback()
            elapsed_seconds = time.perf_counter() - started_at
            try:
                _record_failure(task_id, snapshot_date, elapsed_seconds, error)
            except Exception:
                db.session.rollback()
                raise
            raise
