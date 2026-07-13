from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy import text

from app.extensions import db
from app.repositories.retail import PaymentRepository
from app.security.authorization import permission_required
from app.services.commerce import BusinessError, PaymentService

payments_bp = Blueprint("payments", __name__, url_prefix="/payments")


@payments_bp.get("")
@permission_required("payment.read")
def index():
    pending_orders = db.session.execute(text("""
        SELECT o.order_id, o.order_no, o.total_amount, c.name AS customer_name
        FROM biz.sales_order o JOIN biz.customer c ON c.customer_id = o.customer_id
        WHERE o.status = 'awaiting_payment' ORDER BY o.ordered_at DESC
    """)).mappings().all()
    return render_template("payments.html", payments=PaymentRepository.list(), pending_orders=pending_orders)


@payments_bp.post("")
@permission_required("payment.write")
def create():
    try:
        payment_id = PaymentService.pay(
            request.form.get("order_id"), request.form.get("method"), session["user_id"],
            request.form.get("transaction_ref", "").strip() or None,
        )
        flash(f"支付 {payment_id} 成功，消费流水已生成", "success")
    except (BusinessError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("payments.index"))
