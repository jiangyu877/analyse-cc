import json

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.repositories.retail import ProductRepository
from app.services.commerce import BusinessError, money
from app.utils import audit, login_required, role_required

products_bp = Blueprint("products", __name__, url_prefix="/products")


@products_bp.get("")
@login_required
def index():
    return render_template("products.html", products=ProductRepository.list(), categories=ProductRepository.categories())


@products_bp.post("")
@role_required("admin", "operator")
def create():
    try:
        data = {
            "sku": request.form.get("sku", "").strip(),
            "product_name": request.form.get("product_name", "").strip(),
            "category_id": int(request.form["category_id"]),
            "unit_price": money(request.form.get("unit_price")),
            "stock_qty": int(request.form.get("stock_qty", 0)),
        }
        if not data["sku"] or not data["product_name"] or data["stock_qty"] < 0:
            raise BusinessError("商品名称、SKU 或库存不正确")
        with db.session.begin():
            product_id = ProductRepository.create(data)
            audit("product.create", "product", product_id, json.dumps({"sku": data["sku"]}))
        flash("商品创建成功", "success")
    except (BusinessError, ValueError, KeyError, IntegrityError) as exc:
        db.session.rollback()
        flash(f"创建失败：{exc}", "danger")
    return redirect(url_for("products.index"))

