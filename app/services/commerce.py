import json
import secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import text

from app.extensions import db
from app.repositories.retail import (
    InventoryRepository,
    OrderRepository,
    PaymentRepository,
    ProductRepository,
    RefundRepository,
)
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
                    stock = ProductRepository.deduct_stock(product_id, quantity)
                    if stock is None:
                        raise BusinessError(f"商品 {product_id} 库存扣减失败")
                    InventoryRepository.record(
                        product_id=product_id,
                        order_id=order_id,
                        refund_id=None,
                        refund_item_id=None,
                        change_type="sale",
                        quantity_delta=-quantity,
                        before_qty=stock["before_qty"],
                        after_qty=stock["after_qty"],
                        operator_id=operator_id,
                        remark=order_no,
                    )
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
    def request(payment_id, items, reason, requester_id):
        if not reason or not reason.strip():
            raise BusinessError("退款原因不能为空")
        try:
            payment_id = int(payment_id)
        except (TypeError, ValueError):
            raise BusinessError("请选择有效的支付记录")
        normalized = {}
        for item in items:
            try:
                order_item_id = int(item["order_item_id"])
                quantity = int(item["quantity"])
            except (KeyError, TypeError, ValueError):
                raise BusinessError("退款商品明细格式不正确")
            if quantity <= 0:
                raise BusinessError("退款商品数量必须大于 0")
            normalized[order_item_id] = normalized.get(order_item_id, 0) + quantity
        if not normalized:
            raise BusinessError("至少选择一个退款商品")

        try:
            with db.session.begin():
                payment = RefundRepository.lock_payment(payment_id)
                if not payment or payment["status"] != "success":
                    raise BusinessError("成功支付记录不存在")
                if RefundRepository.has_unallocated_active_refund(payment["payment_id"]):
                    raise BusinessError("历史退款缺少商品级明细，需先人工核对")
                order_items = RefundRepository.lock_order_items(
                    payment["order_id"], normalized.keys()
                )
                if len(order_items) != len(normalized):
                    raise BusinessError("退款商品不属于该支付订单")
                reserved = RefundRepository.reserved_quantities(
                    payment["payment_id"], normalized.keys()
                )

                refund_items = []
                amount = Decimal("0.00")
                for row in order_items:
                    quantity = normalized[row["order_item_id"]]
                    available = row["quantity"] - reserved.get(row["order_item_id"], 0)
                    if quantity > available:
                        raise BusinessError(
                            f"{row['product_name']} 可退数量仅剩 {available}"
                        )
                    refund_amount = (Decimal(row["unit_price"]) * quantity).quantize(MONEY)
                    amount += refund_amount
                    refund_items.append({
                        "order_item_id": row["order_item_id"],
                        "quantity": quantity,
                        "refund_amount": refund_amount,
                    })

                successful = Decimal(RefundRepository.successful_total(payment["payment_id"]))
                if successful + amount > Decimal(payment["amount"]):
                    raise BusinessError("退款金额超过可退金额")

                refund_no = _number("REF")
                refund_id = RefundRepository.create_request(
                    refund_no, payment["payment_id"], payment["order_id"],
                    amount, reason.strip(), requester_id,
                )
                RefundRepository.add_items(refund_id, payment["order_id"], refund_items)
                audit(
                    "refund.request",
                    "refund",
                    refund_id,
                    json.dumps({
                        "refund_no": refund_no,
                        "amount": str(amount),
                        "items": refund_items,
                    }, default=str),
                )
            return refund_id
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def approve(refund_id, reviewer_id, review_note=""):
        try:
            with db.session.begin():
                refund = RefundRepository.lock(int(refund_id))
                if not refund:
                    raise BusinessError("退款申请不存在")
                if refund["status"] != "pending":
                    raise BusinessError("仅待审核退款可以通过")
                if refund["payment_status"] != "success":
                    raise BusinessError("原支付记录不是成功状态")

                items = RefundRepository.lock_items(refund["refund_id"])
                if not items:
                    raise BusinessError("退款申请没有商品明细")
                if RefundRepository.has_unallocated_active_refund(
                    refund["payment_id"], refund["refund_id"]
                ):
                    raise BusinessError("历史退款缺少商品级明细，需先人工核对")
                reserved = RefundRepository.reserved_quantities(
                    refund["payment_id"],
                    [item["order_item_id"] for item in items],
                    refund["refund_id"],
                )
                validated_amount = Decimal("0.00")
                for item in items:
                    if item["returned_qty"]:
                        raise BusinessError("退款商品已经回补库存")
                    if (
                        item["refund_item_order_id"] != refund["order_id"]
                        or item["order_id"] != refund["order_id"]
                    ):
                        raise BusinessError("退款商品不属于原订单")
                    available = item["sold_quantity"] - reserved.get(item["order_item_id"], 0)
                    if item["quantity"] > available:
                        raise BusinessError(
                            f"{item['product_name']} 可退数量仅剩 {available}"
                        )
                    expected_amount = (
                        Decimal(item["unit_price"]) * item["quantity"]
                    ).quantize(MONEY)
                    if Decimal(item["refund_amount"]) != expected_amount:
                        raise BusinessError("退款商品金额与原订单不一致")
                    validated_amount += expected_amount
                if validated_amount != Decimal(refund["amount"]):
                    raise BusinessError("退款总金额与商品明细不一致")

                for item in items:
                    stock = ProductRepository.return_stock(item["product_id"], item["quantity"])
                    InventoryRepository.record(
                        product_id=item["product_id"],
                        order_id=refund["order_id"],
                        refund_id=refund["refund_id"],
                        refund_item_id=item["refund_item_id"],
                        change_type="refund_return",
                        quantity_delta=item["quantity"],
                        before_qty=stock["before_qty"],
                        after_qty=stock["after_qty"],
                        operator_id=reviewer_id,
                        remark=review_note.strip() or refund["reason"],
                    )
                    RefundRepository.mark_item_returned(
                        item["refund_item_id"], item["quantity"]
                    )

                refunded_at = RefundRepository.approve(
                    refund["refund_id"], reviewer_id, review_note.strip()
                )
                new_refunded = Decimal(RefundRepository.successful_total(refund["payment_id"]))
                new_status = (
                    "refunded"
                    if new_refunded == Decimal(refund["payment_amount"])
                    else "partially_refunded"
                )
                db.session.execute(text("""
                    UPDATE biz.sales_order SET refunded_amount = :amount, status = :status, updated_at = now()
                    WHERE order_id = :order_id
                """), {
                    "amount": new_refunded,
                    "status": new_status,
                    "order_id": refund["order_id"],
                })
                db.session.execute(text("""
                    INSERT INTO dwd.consumption_flow
                        (customer_id, order_id, payment_id, refund_id, flow_type,
                         gross_amount, net_amount, occurred_at)
                    VALUES (:customer_id, :order_id, :payment_id, :refund_id, 'refund',
                            :amount, -:amount, :occurred_at)
                """), {
                    "customer_id": refund["customer_id"],
                    "order_id": refund["order_id"],
                    "payment_id": refund["payment_id"],
                    "refund_id": refund["refund_id"],
                    "amount": refund["amount"],
                    "occurred_at": refunded_at,
                })
                audit(
                    "refund.approve",
                    "refund",
                    refund["refund_id"],
                    json.dumps({
                        "refund_no": refund["refund_no"],
                        "amount": str(refund["amount"]),
                    }),
                )
            return refund["refund_id"]
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def reject(refund_id, reviewer_id, review_note):
        if not review_note or not review_note.strip():
            raise BusinessError("拒绝退款必须填写审核意见")
        try:
            with db.session.begin():
                refund = RefundRepository.lock(int(refund_id))
                if not refund:
                    raise BusinessError("退款申请不存在")
                if refund["status"] != "pending":
                    raise BusinessError("仅待审核退款可以拒绝")
                RefundRepository.reject(
                    refund["refund_id"], reviewer_id, review_note.strip()
                )
                audit(
                    "refund.reject",
                    "refund",
                    refund["refund_id"],
                    json.dumps({"review_note": review_note.strip()}, ensure_ascii=False),
                )
            return refund["refund_id"]
        except Exception:
            db.session.rollback()
            raise
