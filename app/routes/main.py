from flask import Blueprint, render_template

from app.repositories.retail import DashboardRepository
from app.utils import login_required

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
@login_required
def dashboard():
    summary = DashboardRepository.summary()
    trend = DashboardRepository.trend()
    return render_template(
        "dashboard.html",
        summary=summary,
        trend_labels=[row["month"] for row in trend],
        trend_values=[float(row["net_amount"]) for row in trend],
        recent_orders=DashboardRepository.recent_orders(),
    )

