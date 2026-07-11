import json
import secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import text

from app.extensions import db
from app.repositories.retail import OrderRepository, PaymentRepository, ProductRepository, RefundRepository
from app.utils import audit

MONEY = Decimal("0.01")
PAYMENT_METHODS = {"wechat", "alipay", "bank_card", "cash"}


class BusinessError(ValueError):
    pass


def money(value):
    try:
        amount = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError):
        raise BusinessError("金额格式不正确")
    if amount <= 0:
        raise BusinessError("金额必须大于 0")
    return amount


def _number(prefix):
    return f"{prefix}{datetime.now():%Y%m%d%H%M%S}{secrets.token_hex(3).upper()}"


class OrderService:
    @staticmethod
    def create(customer_id, items, remark, operator_id):
        if not items:
            raise BusinessError("订单至少需要一个商品明细")
        normalized = {}
        for item in items:
            try:
                product_id = int(item["product_id"])
                quantity = int(item["quantity"])
            except (KeyError, TypeError, ValueError):
                raise BusinessError("商品明细格式不正确")
            if quantity <= 0:
                raise BusinessError("商品数量必须大于 0")
            normalized[product_id] = normalized.get(product_id, 0) + quantity

        try:
            with db.session.begin():
                lines = []
                total = Decimal("0.00")
                for product_id in sorted(normalized):
                    quantity = normalized[product_id]
                    product = ProductRepository.lock(product_id)
                    if not product or product["status"] != "active":
                        raise BusinessError(f"商品 {product_id} 不存在或已停用")
                    if product["stock_qty"] < quantity:
                        raise BusinessError(f"{product['product_name']} 库存不足，当前库存 {product['stock_qty']}")
                    unit_price = Decimal(product["unit_price"]).quantize(MONEY)
                    line_amount = (unit_price * quantity).quantize(MONEY)
                    lines.append((product_id, quantity, unit_price, line_amount))
                    total += line_amount

                order_no = _number("SO")
                order_id = OrderRepository.create(order_no, int(customer_id), total, remark, operator_id)
                for product_id, quantity, unit_price, line_amount in lines:
                    OrderRepository.add_item(order_id, product_id, quantity, unit_price, line_amount)
                    ProductRepository.deduct_stock(product_id, quantity)
                audit("order.create", "sales_order", order_id, json.dumps({"order_no": order_no, "amount": str(total)}))
            return order_id
        except Exception:
            db.session.rollback()
            raise


class PaymentService:
    @staticmethod
    def pay(order_id, method, operator_id, transaction_ref=None):
        if method not in PAYMENT_METHODS:
            raise BusinessError("不支持的支付方式")
        try:
            with db.session.begin():
                order = OrderRepository.lock(int(order_id))
                if not order:
                    raise BusinessError("订单不存在")
                if order["status"] != "awaiting_payment":
                    raise BusinessError("仅待支付订单可支付")
                amount = Decimal(order["total_amount"])
                payment_no = _number("PAY")
                payment = PaymentRepository.create(
                    payment_no, order["order_id"], method, amount, transaction_ref, operator_id
                )
                db.session.execute(text("""
                    UPDATE biz.sales_order
                    SET status = 'paid', paid_amount = total_amount, paid_at = :paid_at, updated_at = now()
                    WHERE order_id = :order_id
                """), {"paid_at": payment["paid_at"], "order_id": order["order_id"]})
                db.session.execute(text("""
                    INSERT INTO dwd.consumption_flow
                        (customer_id, order_id, payment_id, flow_type, gross_amount, net_amount, occurred_at)
                    VALUES (:customer_id, :order_id, :payment_id, 'payment', :amount, :amount, :occurred_at)
                """), {
                    "customer_id": order["customer_id"], "order_id": order["order_id"],
                    "payment_id": payment["payment_id"], "amount": amount,
                    "occurred_at": payment["paid_at"],
                })
                audit("payment.success", "payment", payment["payment_id"], json.dumps({"payment_no": payment_no}))
            return payment["payment_id"]
        except Exception:
            db.session.rollback()
            raise


class RefundService:
    @staticmethod
    def refund(payment_id, amount_value, reason, operator_id):
        amount = money(amount_value)
        if not reason or not reason.strip():
            raise BusinessError("退款原因不能为空")
        try:
            with db.session.begin():
                payment = db.session.execute(text("""
                    SELECT p.*, o.customer_id, o.status AS order_status, o.refunded_amount
                    FROM biz.payment p
                    JOIN biz.sales_order o ON o.order_id = p.order_id
                    WHERE p.payment_id = :id FOR UPDATE OF p, o
                """), {"id": int(payment_id)}).mappings().first()
                if not payment or payment["status"] != "success":
                    raise BusinessError("成功支付记录不存在")
                refunded = Decimal(RefundRepository.successful_total(payment["payment_id"]))
                if refunded + amount > Decimal(payment["amount"]):
                    raise BusinessError("退款金额超过可退金额")

                refund_no = _number("REF")
                refund = RefundRepository.create(
                    refund_no, payment["payment_id"], payment["order_id"], amount, reason.strip(), operator_id
                )
                new_refunded = refunded + amount
                new_status = "refunded" if new_refunded == Decimal(payment["amount"]) else "partially_refunded"
                db.session.execute(text("""
                    UPDATE biz.sales_order SET refunded_amount = :amount, status = :status, updated_at = now()
                    WHERE order_id = :order_id
                """), {"amount": new_refunded, "status": new_status, "order_id": payment["order_id"]})
                db.session.execute(text("""
                    INSERT INTO dwd.consumption_flow
                        (customer_id, order_id, payment_id, refund_id, flow_type,
                         gross_amount, net_amount, occurred_at)
                    VALUES (:customer_id, :order_id, :payment_id, :refund_id, 'refund',
                            :amount, -:amount, :occurred_at)
                """), {
                    "customer_id": payment["customer_id"], "order_id": payment["order_id"],
                    "payment_id": payment["payment_id"], "refund_id": refund["refund_id"],
                    "amount": amount, "occurred_at": refund["refunded_at"],
                })
                audit("refund.success", "refund", refund["refund_id"], json.dumps({"refund_no": refund_no, "amount": str(amount)}))
            return refund["refund_id"]
        except Exception:
            db.session.rollback()
            raise
