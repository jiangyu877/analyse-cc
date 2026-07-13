from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.repositories.retail import RefundRepository
from app.security.authorization import permission_required
from app.services.commerce import BusinessError, RefundService


refunds_bp = Blueprint("refunds", __name__, url_prefix="/refunds")


@refunds_bp.get("")
@permission_required("refund.read")
def index():
    return render_template(
        "refunds.html",
        refunds=RefundRepository.list(),
        refundable_items=RefundRepository.refundable_items(),
    )


@refunds_bp.post("")
@permission_required("refund.request")
def create():
    try:
        order_item_ids = request.form.getlist("order_item_id")
        refund_id = RefundService.request(
            request.form.get("payment_id"),
            [
                {
                    "order_item_id": order_item_id,
                    "quantity": request.form.get(
                        f"quantity_{order_item_id}", request.form.get("quantity")
                    ),
                }
                for order_item_id in order_item_ids
            ],
            request.form.get("reason", ""),
            session["user_id"],
        )
        flash(f"退款申请 {refund_id} 已提交，等待财务审核", "success")
    except (BusinessError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("refunds.index"))


@refunds_bp.post("/<int:refund_id>/approve")
@permission_required("refund.approve")
def approve(refund_id):
    try:
        RefundService.approve(
            refund_id, session["user_id"], request.form.get("review_note", "")
        )
        flash("退款审核通过，库存与客户净消费已更新", "success")
    except (BusinessError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("refunds.index"))


@refunds_bp.post("/<int:refund_id>/reject")
@permission_required("refund.approve")
def reject(refund_id):
    try:
        RefundService.reject(
            refund_id, session["user_id"], request.form.get("review_note", "")
        )
        flash("退款申请已拒绝", "success")
    except (BusinessError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("refunds.index"))
