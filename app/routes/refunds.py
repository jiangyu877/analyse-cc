from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import text

from app.extensions import db
from app.repositories.retail import RefundRepository
from app.services.commerce import BusinessError, RefundService
from app.utils import login_required, role_required

refunds_bp = Blueprint("refunds", __name__, url_prefix="/refunds")


@refunds_bp.get("")
@login_required
def index():
    refundable = db.session.execute(text("""
        SELECT p.payment_id, p.payment_no, o.order_no, c.name AS customer_name,
               p.amount - COALESCE(SUM(r.amount) FILTER (WHERE r.status = 'success'), 0) AS remaining
        FROM biz.payment p
        JOIN biz.sales_order o ON o.order_id = p.order_id
        JOIN biz.customer c ON c.customer_id = o.customer_id
        LEFT JOIN biz.refund r ON r.payment_id = p.payment_id
        WHERE p.status = 'success'
        GROUP BY p.payment_id, p.payment_no, o.order_no, c.name, p.amount
        HAVING p.amount - COALESCE(SUM(r.amount) FILTER (WHERE r.status = 'success'), 0) > 0
        ORDER BY p.paid_at DESC
        LIMIT 200
    """)).mappings().all()
    return render_template("refunds.html", refunds=RefundRepository.list(), refundable=refundable)


@refunds_bp.post("")
@role_required("admin", "operator")
def create():
    try:
        refund_id = RefundService.refund(
            request.form.get("payment_id"), request.form.get("amount"),
            request.form.get("reason", ""), session["user_id"],
        )
        flash(f"退款 {refund_id} 成功，客户净消费已更新", "success")
    except (BusinessError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("refunds.index"))

