from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.repositories.retail import CustomerRepository, OrderRepository, ProductRepository
from app.services.commerce import BusinessError, OrderService
from app.utils import login_required, role_required

orders_bp = Blueprint("orders", __name__, url_prefix="/orders")


@orders_bp.get("")
@login_required
def index():
    return render_template(
        "orders.html",
        orders=OrderRepository.list(),
        customers=CustomerRepository.list(),
        products=ProductRepository.list(active_only=True),
    )


@orders_bp.post("")
@role_required("admin", "operator")
def create():
    product_ids = request.form.getlist("product_id")
    quantities = request.form.getlist("quantity")
    items = [
        {"product_id": product_id, "quantity": quantity}
        for product_id, quantity in zip(product_ids, quantities)
        if product_id and quantity
    ]
    try:
        order_id = OrderService.create(
            request.form.get("customer_id"), items,
            request.form.get("remark", "").strip(), session["user_id"],
        )
        flash(f"订单 {order_id} 创建成功，库存已扣减", "success")
    except (BusinessError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("orders.index"))

