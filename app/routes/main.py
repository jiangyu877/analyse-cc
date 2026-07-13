from flask import Blueprint, abort, redirect, render_template, session, url_for

from app.repositories.retail import DashboardRepository
from app.security.authorization import account_permissions

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def dashboard():
    account_id = session.get("user_id")
    if account_id is None:
        return redirect(url_for("auth.login", next="/"))

    permissions = account_permissions(account_id)
    if not permissions:
        abort(403)
    return render_template(
        "dashboard.html",
        workspace=DashboardRepository.workspace(permissions),
    )
