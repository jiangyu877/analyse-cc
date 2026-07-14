import argparse
import logging
import os
import signal
import socket
import threading
import time

from app import create_app
from app.extensions import db
from app.services.algorithms import run_churn, run_kmeans, run_rfm
from app.services.analytics import AnalyticsService
from app.services.jobs import JobError, JobService
from app.services.prediction import PredictionService


LOGGER = logging.getLogger("consumer_analysis.worker")

HANDLERS = {
    "analytics_refresh": lambda payload: AnalyticsService.refresh(
        payload["snapshot_date"], payload["operator_id"]
    ),
    "model_rfm": lambda payload: run_rfm(payload["operator_id"]),
    "model_kmeans": lambda payload: run_kmeans(
        payload["operator_id"], payload["clusters"]
    ),
    "model_churn": lambda payload: run_churn(
        payload["operator_id"], payload["observation_days"]
    ),
    "model_customer_amount": lambda payload: PredictionService.run_customer_amount(
        payload["operator_id"], payload["horizon_days"], payload["training_days"]
    ),
    "model_product_sales_forecast": (
        lambda payload: PredictionService.run_product_sales_forecast(
            payload["operator_id"],
            payload["horizon_days"],
            payload["training_days"],
        )
    ),
    "model_product_recommendation": (
        lambda payload: PredictionService.run_product_recommendation(
            payload["operator_id"], payload["top_k"], payload["training_days"]
        )
    ),
}


def run_once(worker_id):
    """Claim and execute at most one job in the active Flask app context."""
    job = None
    started_at = time.monotonic()
    try:
        job = JobService.claim_next(worker_id)
        if job is None:
            return False

        handler = HANDLERS.get(job["job_type"])
        if handler is None:
            raise JobError(f"unknown job type: {job['job_type']}")

        task_id = int(handler(job["payload"]))
        JobService.complete(job["job_id"], worker_id, {"task_id": task_id})
        LOGGER.info(
            "job_succeeded job_id=%s job_type=%s elapsed_seconds=%.3f",
            job["job_id"],
            job["job_type"],
            time.monotonic() - started_at,
        )
        return True
    except Exception as error:
        if job is None:
            raise
        try:
            db.session.rollback()
        except Exception as rollback_error:
            LOGGER.error(
                "job_transaction_rollback_failed job_id=%s job_type=%s error_type=%s",
                job["job_id"],
                job["job_type"],
                type(rollback_error).__name__,
            )
            db.session.remove()
        try:
            JobService.fail(job["job_id"], worker_id, error)
        except Exception as fail_error:
            LOGGER.error(
                "job_failure_persistence_failed job_id=%s job_type=%s error_type=%s",
                job["job_id"],
                job["job_type"],
                type(fail_error).__name__,
            )
            raise
        LOGGER.warning(
            "job_failed job_id=%s job_type=%s error_type=%s elapsed_seconds=%.3f",
            job["job_id"],
            job["job_type"],
            type(error).__name__,
            time.monotonic() - started_at,
        )
        return True
    finally:
        db.session.remove()


def _positive_seconds(value):
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("poll seconds must be a number") from error
    if seconds <= 0:
        raise argparse.ArgumentTypeError("poll seconds must be positive")
    return seconds


def _parser():
    parser = argparse.ArgumentParser(description="Run the background job worker")
    parser.add_argument("--once", action="store_true", help="claim at most one job")
    parser.add_argument("--poll-seconds", type=_positive_seconds, default=2.0)
    parser.add_argument(
        "--worker-id",
        default=f"{socket.gethostname()}-{os.getpid()}",
    )
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    stop_event = threading.Event()

    def request_stop(signum, _frame):
        LOGGER.info("worker_stopping signal=%s worker_id=%s", signum, args.worker_id)
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    app = create_app()
    with app.app_context():
        if args.once:
            run_once(args.worker_id)
            return 0

        while not stop_event.is_set():
            try:
                claimed = run_once(args.worker_id)
            except Exception as error:
                LOGGER.error(
                    "worker_iteration_failed worker_id=%s error_type=%s",
                    args.worker_id,
                    type(error).__name__,
                )
                claimed = False
            if not claimed:
                stop_event.wait(args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
