import json
import secrets
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.repositories.retail import CustomerRepository
from app.security.authorization import permission_required
from app.utils import audit

customers_bp = Blueprint("customers", __name__, url_prefix="/customers")


@customers_bp.get("")
@permission_required("customer.read")
def index():
    search = request.args.get("q", "").strip()[:80]
    return render_template("customers.html", customers=CustomerRepository.list(search), search=search)


@customers_bp.post("")
@permission_required("customer.write")
def create():
    data = {
        "customer_no": request.form.get("customer_no", "").strip() or f"C{datetime.now():%Y%m%d}{secrets.token_hex(2).upper()}",
        "name": request.form.get("name", "").strip(),
        "gender": request.form.get("gender") or None,
        "phone": request.form.get("phone", "").strip() or None,
        "email": request.form.get("email", "").strip() or None,
        "province": request.form.get("province", "").strip() or None,
        "city": request.form.get("city", "").strip() or None,
    }
    if not data["name"]:
        flash("客户姓名不能为空", "danger")
        return redirect(url_for("customers.index"))
    try:
        with db.session.begin():
            customer_id = CustomerRepository.create(data)
            audit("customer.create", "customer", customer_id, json.dumps({"customer_no": data["customer_no"]}))
        flash("客户创建成功", "success")
    except IntegrityError:
        db.session.rollback()
        flash("客户编号已存在", "danger")
    return redirect(url_for("customers.index"))


@customers_bp.get("/<int:customer_id>")
@permission_required("customer.read")
def detail(customer_id):
    customer = CustomerRepository.get(customer_id)
    if not customer:
        return render_template("error.html", code=404, message="客户不存在"), 404
    return render_template("customer_detail.html", customer=customer, orders=CustomerRepository.orders(customer_id))
