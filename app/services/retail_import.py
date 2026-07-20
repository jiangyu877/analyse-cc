import hashlib
import json
import re
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import StringIO
import csv

from flask import current_app
from sqlalchemy import text

from app.extensions import db
from app.services.tabular import (
    TabularDataError,
    normalize_header,
    read_tabular,
    remap_columns,
)
from app.utils import audit


MONEY = Decimal("0.01")
MAX_AMOUNT = Decimal("999999999999.99")
MAX_QUANTITY = 1_000_000
PAYMENT_METHODS = {
    "wechat": "wechat", "微信": "wechat", "微信支付": "wechat",
    "alipay": "alipay", "支付宝": "alipay",
    "bank_card": "bank_card", "银行卡": "bank_card", "bankcard": "bank_card",
    "cash": "cash", "现金": "cash",
}
REQUIRED_FIELDS = (
    "customer_no", "customer_name", "order_no", "order_time",
    "product_sku", "product_name", "quantity", "unit_price",
)
COLUMN_ALIASES = {
    "customer_no": ("客户编号", "客户号", "用户编号", "customer_id"),
    "customer_name": ("客户姓名", "客户名称", "姓名", "name"),
    "phone": ("手机", "手机号", "电话"),
    "email": ("邮箱", "电子邮箱"),
    "province": ("省份", "省"),
    "city": ("城市", "市"),
    "order_no": ("订单编号", "订单号"),
    "order_time": ("下单时间", "订单时间", "消费时间", "ordered_at"),
    "product_sku": ("商品sku", "sku", "商品编号"),
    "product_name": ("商品名称", "商品"),
    "category": ("商品分类", "分类", "category_name"),
    "quantity": ("数量", "购买数量"),
    "unit_price": ("单价", "商品单价", "price"),
    "payment_method": ("支付方式", "支付渠道", "method"),
}
FIELD_LABELS = {
    "customer_no": "客户编号",
    "customer_name": "客户姓名",
    "order_no": "订单编号",
    "order_time": "下单时间",
    "product_sku": "商品 SKU",
    "product_name": "商品名称",
    "quantity": "数量",
    "unit_price": "单价",
    "payment_method": "支付方式",
}


class RetailImportError(ValueError):
    pass


def _required(value, label, row_number, max_length):
    value = (value or "").strip()
    if not value:
        raise RetailImportError(f"第 {row_number} 行：{label}不能为空")
    if len(value) > max_length:
        raise RetailImportError(f"第 {row_number} 行：{label}不能超过 {max_length} 个字符")
    return value


def _positive_int(value, row_number):
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise RetailImportError(f"第 {row_number} 行：数量格式不正确") from exc
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        raise RetailImportError(f"第 {row_number} 行：数量必须是正整数")
    if number > MAX_QUANTITY:
        raise RetailImportError(f"第 {row_number} 行：数量不能超过 {MAX_QUANTITY:,}")
    return int(number)


def _money(value, row_number):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise InvalidOperation
        amount = amount.quantize(MONEY, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise RetailImportError(f"第 {row_number} 行：单价格式不正确") from exc
    if amount <= 0:
        raise RetailImportError(f"第 {row_number} 行：单价必须大于 0")
    if amount > MAX_AMOUNT:
        raise RetailImportError(f"第 {row_number} 行：单价超出系统金额上限")
    return amount


def _line_amount(unit_price, quantity, row_number):
    amount = (unit_price * quantity).quantize(MONEY)
    if amount > MAX_AMOUNT:
        raise RetailImportError(f"第 {row_number} 行：商品行金额超出系统上限")
    return amount


def _datetime(value, row_number):
    normalized = (value or "").strip().replace("/", "-")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RetailImportError(
            f"第 {row_number} 行：下单时间格式应为 YYYY-MM-DD HH:MM:SS"
        ) from exc


def _payment_no(order_no):
    digest = hashlib.sha256(order_no.encode("utf-8")).hexdigest()[:24].upper()
    return f"UPL-PAY-{digest}"


def _alias_targets(columns):
    targets = {}
    aliases = {}
    for canonical, names in COLUMN_ALIASES.items():
        for name in (canonical, *names):
            aliases[normalize_header(name)] = canonical
    for column in columns:
        canonical = aliases.get(normalize_header(column))
        if canonical:
            targets[column] = canonical
    return targets


def _resolve_mapping(columns, field_mapping):
    available = {normalize_header(column): column for column in columns}
    if field_mapping is None:
        mapping = _alias_targets(columns)
    else:
        if not isinstance(field_mapping, dict):
            raise RetailImportError("字段映射格式不正确")
        mapping = {}
        for source, canonical in field_mapping.items():
            source_key = normalize_header(source)
            source_column = available.get(source_key)
            if source_column is None:
                raise RetailImportError(f"字段映射引用了不存在的列：{source}")
            if canonical not in COLUMN_ALIASES:
                raise RetailImportError(f"字段映射目标不支持：{canonical}")
            mapping[source_column] = canonical

    duplicate_targets = {
        target for target in mapping.values() if list(mapping.values()).count(target) > 1
    }
    if duplicate_targets:
        raise RetailImportError("字段映射存在重复目标：" + "、".join(sorted(duplicate_targets)))
    missing = [field for field in REQUIRED_FIELDS if field not in mapping.values()]
    if missing:
        labels = [FIELD_LABELS[field] for field in missing]
        raise RetailImportError("字段映射缺少必填列：" + "、".join(labels))
    return mapping


def _issue(row_number, field_name, raw_value, issue_code, message):
    return {
        "row_number": row_number,
        "field_name": field_name,
        "raw_value": "" if raw_value is None else str(raw_value),
        "issue_code": issue_code,
        "message": message,
    }


def _validate_row(row, row_number):
    issues = []
    normalized = {}
    required_specs = (
        ("customer_no", 32),
        ("customer_name", 128),
        ("order_no", 40),
        ("product_sku", 64),
        ("product_name", 160),
    )
    for field_name, maximum in required_specs:
        try:
            normalized[field_name] = _required(
                row.get(field_name), FIELD_LABELS[field_name], row_number, maximum
            )
        except RetailImportError as exc:
            issues.append(_issue(
                row_number, field_name, row.get(field_name), "required", str(exc)
            ))

    try:
        normalized["order_time"] = _datetime(row.get("order_time"), row_number)
    except RetailImportError as exc:
        issues.append(_issue(
            row_number, "order_time", row.get("order_time"), "invalid_datetime", str(exc)
        ))

    try:
        normalized["quantity"] = _positive_int(row.get("quantity"), row_number)
    except RetailImportError as exc:
        issues.append(_issue(
            row_number, "quantity", row.get("quantity"), "invalid_quantity", str(exc)
        ))

    try:
        normalized["unit_price"] = _money(row.get("unit_price"), row_number)
    except RetailImportError as exc:
        issues.append(_issue(
            row_number, "unit_price", row.get("unit_price"), "invalid_money", str(exc)
        ))

    method_label = (row.get("payment_method") or "wechat").strip().lower()
    method = PAYMENT_METHODS.get(method_label)
    if not method:
        issues.append(_issue(
            row_number,
            "payment_method",
            row.get("payment_method"),
            "invalid_payment_method",
            f"第 {row_number} 行：支付方式仅支持微信、支付宝、银行卡或现金",
        ))
    else:
        normalized["payment_method"] = method

    for field_name, maximum in (("phone", 32), ("email", 128), ("province", 64), ("city", 64)):
        normalized[field_name] = (row.get(field_name) or "").strip()[:maximum] or None
    normalized["category"] = (row.get("category") or "未分类").strip()[:80] or "未分类"

    if issues:
        return None, issues
    normalized["order_time"] = normalized["order_time"].isoformat(sep=" ", timespec="seconds")
    normalized["quantity"] = str(normalized["quantity"])
    normalized["unit_price"] = str(normalized["unit_price"])
    return normalized, []


def _normalize_rows(rows):
    customers = {}
    products = {}
    orders = {}
    for index, row in enumerate(rows, start=2):
        customer_no = _required(row.get("customer_no"), "客户编号", index, 32)
        customer_name = _required(row.get("customer_name"), "客户姓名", index, 128)
        order_no = _required(row.get("order_no"), "订单编号", index, 40)
        order_time = _datetime(row.get("order_time"), index)
        sku = _required(row.get("product_sku"), "商品 SKU", index, 64)
        product_name = _required(row.get("product_name"), "商品名称", index, 160)
        category = (row.get("category") or "未分类").strip()[:80] or "未分类"
        quantity = _positive_int(row.get("quantity"), index)
        unit_price = _money(row.get("unit_price"), index)
        method_label = (row.get("payment_method") or "wechat").strip().lower()
        method = PAYMENT_METHODS.get(method_label)
        if not method:
            raise RetailImportError(f"第 {index} 行：支付方式仅支持微信、支付宝、银行卡或现金")

        customer = {
            "customer_no": customer_no, "name": customer_name,
            "phone": (row.get("phone") or "").strip()[:32] or None,
            "email": (row.get("email") or "").strip()[:128] or None,
            "province": (row.get("province") or "").strip()[:64] or None,
            "city": (row.get("city") or "").strip()[:64] or None,
        }
        previous_customer = customers.get(customer_no)
        if previous_customer and previous_customer["name"] != customer_name:
            raise RetailImportError(f"第 {index} 行：同一客户编号对应了不同姓名")
        if previous_customer:
            for field in ("phone", "email", "province", "city"):
                customer[field] = customer[field] or previous_customer[field]
        customers[customer_no] = customer

        product = {
            "sku": sku, "product_name": product_name,
            "category_name": category, "unit_price": unit_price,
            "price_at": order_time,
        }
        previous_product = products.get(sku)
        if not previous_product or order_time >= previous_product["price_at"]:
            products[sku] = product

        order = orders.setdefault(order_no, {
            "order_no": order_no, "customer_no": customer_no,
            "ordered_at": order_time, "method": method, "items": {},
        })
        if (order["customer_no"], order["ordered_at"], order["method"]) != (
            customer_no, order_time, method
        ):
            raise RetailImportError(f"第 {index} 行：同一订单的客户、时间或支付方式不一致")
        item = order["items"].get(sku)
        if item:
            if item["unit_price"] != unit_price:
                raise RetailImportError(f"第 {index} 行：同一订单商品出现不同单价")
            combined_quantity = item["quantity"] + quantity
            if combined_quantity > MAX_QUANTITY:
                raise RetailImportError(
                    f"第 {index} 行：同一订单商品累计数量不能超过 {MAX_QUANTITY:,}"
                )
            item["quantity"] = combined_quantity
            item["line_amount"] = _line_amount(item["unit_price"], item["quantity"], index)
        else:
            order["items"][sku] = {
                "sku": sku, "quantity": quantity, "unit_price": unit_price,
                "line_amount": _line_amount(unit_price, quantity, index),
            }
    return customers, products, orders


def _same_datetime(left, right):
    if left.tzinfo and right.tzinfo:
        return left.timestamp() == right.timestamp()
    return left.replace(tzinfo=None) == right.replace(tzinfo=None)


def _load_existing_orders(order_numbers):
    if not order_numbers:
        return {}
    rows = db.session.execute(text("""
        SELECT order_row.order_no, customer.customer_no, order_row.ordered_at,
               order_row.status, order_row.total_amount, payment.method,
               product.sku, item.quantity, item.unit_price
        FROM biz.sales_order order_row
        JOIN biz.customer customer ON customer.customer_id = order_row.customer_id
        LEFT JOIN biz.order_item item ON item.order_id = order_row.order_id
        LEFT JOIN biz.product product ON product.product_id = item.product_id
        LEFT JOIN biz.payment payment
          ON payment.order_id = order_row.order_id AND payment.status = 'success'
        WHERE order_row.order_no = ANY(:numbers)
        ORDER BY order_row.order_no, item.order_item_id
    """), {"numbers": list(order_numbers)}).mappings()
    result = {}
    for row in rows:
        order = result.setdefault(row["order_no"], {
            "customer_no": row["customer_no"], "ordered_at": row["ordered_at"],
            "status": row["status"], "total_amount": Decimal(row["total_amount"]),
            "method": row["method"], "items": {},
        })
        if row["sku"]:
            order["items"][row["sku"]] = {
                "quantity": row["quantity"], "unit_price": Decimal(row["unit_price"]),
            }
    return result


def _order_matches(existing, incoming):
    if existing["customer_no"] != incoming["customer_no"]:
        return False
    if existing["status"] not in {"paid", "partially_refunded", "refunded"}:
        return False
    if existing["method"] != incoming["method"]:
        return False
    if not _same_datetime(existing["ordered_at"], incoming["ordered_at"]):
        return False
    incoming_items = {
        sku: {"quantity": item["quantity"], "unit_price": item["unit_price"]}
        for sku, item in incoming["items"].items()
    }
    return (
        existing["total_amount"] == incoming["total_amount"]
        and existing["items"] == incoming_items
    )


class RetailImportService:
    @staticmethod
    def analyze_dataset(filename, data, field_mapping=None):
        dataset = read_tabular(filename, data, max_rows=10000)
        mapping = _resolve_mapping(dataset.columns, field_mapping)
        issues = []
        stage_rows = []
        gross_amount = Decimal("0.00")
        for row_number, source_row in enumerate(dataset.rows, start=2):
            mapped = {
                target: source_row.get(source_column, "")
                for source_column, target in mapping.items()
            }
            normalized, row_issues = _validate_row(mapped, row_number)
            issues.extend(row_issues)
            stage_rows.append({
                "row_number": row_number,
                "payload": normalized or mapped,
                "is_valid": not row_issues,
            })
            if normalized:
                gross_amount += Decimal(normalized["quantity"]) * Decimal(normalized["unit_price"])
        valid_count = sum(1 for row in stage_rows if row["is_valid"])
        return {
            "mapping": mapping,
            "stage_rows": stage_rows,
            "issues": issues,
            "input_row_count": len(dataset.rows),
            "valid_row_count": valid_count,
            "invalid_row_count": len(dataset.rows) - valid_count,
            "gross_amount": gross_amount.quantize(MONEY),
        }

    @staticmethod
    def preflight_dataset(filename, data, operator_id, field_mapping=None):
        max_bytes = int(current_app.config["MAX_UPLOAD_MB"]) * 1024 * 1024
        if len(data or b"") > max_bytes:
            raise RetailImportError(f"文件不能超过 {current_app.config['MAX_UPLOAD_MB']} MB")

        analysis = RetailImportService.analyze_dataset(filename, data, field_mapping)
        batch_no = f"PREVIEW-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8].upper()}"
        source_name = f"用户上传：{filename}"[:128]
        file_sha256 = hashlib.sha256(data).hexdigest()
        with db.session.begin():
            batch_id = db.session.execute(text("""
                INSERT INTO ods.import_batch
                    (batch_no, source_name, status, file_sha256, file_size,
                     input_row_count, valid_row_count, invalid_row_count,
                     gross_amount, mapping_json, created_by)
                VALUES
                    (:batch_no, :source_name, 'running', :file_sha256, :file_size,
                     :input_row_count, :valid_row_count, :invalid_row_count,
                     :gross_amount, CAST(:mapping_json AS jsonb), :created_by)
                RETURNING batch_id
            """), {
                "batch_no": batch_no,
                "source_name": source_name,
                "file_sha256": file_sha256,
                "file_size": len(data),
                "input_row_count": analysis["input_row_count"],
                "valid_row_count": analysis["valid_row_count"],
                "invalid_row_count": analysis["invalid_row_count"],
                "gross_amount": analysis["gross_amount"],
                "mapping_json": json.dumps(analysis["mapping"], ensure_ascii=False),
                "created_by": operator_id,
            }).scalar_one()
            db.session.execute(text("""
                INSERT INTO ods.import_stage_row
                    (batch_id, row_number, payload, is_valid)
                VALUES
                    (:batch_id, :row_number, CAST(:payload AS jsonb), :is_valid)
            """), [{
                "batch_id": batch_id,
                "row_number": row["row_number"],
                "payload": json.dumps(row["payload"], ensure_ascii=False),
                "is_valid": row["is_valid"],
            } for row in analysis["stage_rows"]])
            if analysis["issues"]:
                db.session.execute(text("""
                    INSERT INTO ods.import_row_issue
                        (batch_id, row_number, field_name, raw_value, issue_code, message)
                    VALUES
                        (:batch_id, :row_number, :field_name, :raw_value, :issue_code, :message)
                """), [{"batch_id": batch_id, **issue} for issue in analysis["issues"]])

        return {
            **analysis,
            "batch_id": batch_id,
            "batch_no": batch_no,
            "file_sha256": file_sha256,
            "preview_rows": analysis["stage_rows"][:20],
        }

    @staticmethod
    def batch_detail(batch_no):
        batch = db.session.execute(text("""
            SELECT batch_id, batch_no, source_name, status, customer_count,
                   transaction_count, started_at, finished_at, error_message,
                   file_sha256, file_size, input_row_count, valid_row_count,
                   invalid_row_count, skipped_order_count, gross_amount,
                   mapping_json, confirmed_at, created_by
            FROM ods.import_batch
            WHERE batch_no = :batch_no
        """), {"batch_no": batch_no}).mappings().one_or_none()
        if not batch:
            return None
        result = dict(batch)
        result["stage_rows"] = [dict(row) for row in db.session.execute(text("""
            SELECT row_number, payload, is_valid
            FROM ods.import_stage_row
            WHERE batch_id = :batch_id
            ORDER BY row_number
            LIMIT 20
        """), {"batch_id": result["batch_id"]}).mappings()]
        result["issues"] = [dict(row) for row in db.session.execute(text("""
            SELECT row_number, field_name, raw_value, issue_code, message
            FROM ods.import_row_issue
            WHERE batch_id = :batch_id
            ORDER BY row_number, issue_id
            LIMIT 200
        """), {"batch_id": result["batch_id"]}).mappings()]
        return result

    @staticmethod
    def error_report(batch_no):
        detail = RetailImportService.batch_detail(batch_no)
        if detail is None:
            raise RetailImportError("导入预检批次不存在")
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(("行号", "字段", "原值", "错误代码", "错误说明"))
        for issue in detail["issues"]:
            writer.writerow((
                issue["row_number"],
                FIELD_LABELS.get(issue["field_name"], issue["field_name"] or ""),
                issue["raw_value"] or "",
                issue["issue_code"],
                issue["message"],
            ))
        return "\ufeff" + output.getvalue()

    @staticmethod
    def import_dataset(filename, data, operator_id):
        max_bytes = int(current_app.config["MAX_UPLOAD_MB"]) * 1024 * 1024
        if len(data or b"") > max_bytes:
            raise RetailImportError(f"文件不能超过 {current_app.config['MAX_UPLOAD_MB']} MB")
        dataset = read_tabular(filename, data, max_rows=10000)
        rows = remap_columns(dataset, COLUMN_ALIASES, REQUIRED_FIELDS)
        customers, products, orders = _normalize_rows(rows)
        batch_no = f"UPLOAD-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8].upper()}"

        with db.session.begin():
            db.session.execute(text("""
                INSERT INTO ods.import_batch (batch_no, source_name, status)
                VALUES (:batch_no, :source_name, 'running')
            """), {"batch_no": batch_no, "source_name": f"用户上传：{filename}"[:128]})
            import_log_id = db.session.execute(text("""
                INSERT INTO audit.import_log (account_id, source_name, status)
                VALUES (:account_id, :source_name, 'running')
                RETURNING import_log_id
            """), {
                "account_id": operator_id, "source_name": f"用户上传：{filename}"[:255],
            }).scalar_one()

        try:
            with db.session.begin():
                for order in orders.values():
                    total = sum(
                        (item["line_amount"] for item in order["items"].values()),
                        Decimal("0.00"),
                    )
                    if total > MAX_AMOUNT:
                        raise RetailImportError(
                            f"订单 {order['order_no']}：总金额超出系统上限"
                        )
                    order["total_amount"] = total

                db.session.execute(text("""
                    WITH ordered_numbers AS MATERIALIZED (
                        SELECT number
                        FROM unnest(CAST(:numbers AS text[])) AS number
                        ORDER BY number
                    )
                    SELECT pg_advisory_xact_lock(
                        hashtextextended('retail-import:' || number, 0)
                    )
                    FROM ordered_numbers
                """), {"numbers": list(orders)}).all()
                existing_orders = _load_existing_orders(orders)
                for order_no, existing in existing_orders.items():
                    if not _order_matches(existing, orders[order_no]):
                        raise RetailImportError(
                            f"订单 {order_no} 已存在，但客户、时间、商品或金额与上传数据不一致"
                        )
                new_orders = [
                    order for number, order in orders.items()
                    if number not in existing_orders
                ]
                needed_customer_nos = {order["customer_no"] for order in new_orders}
                needed_skus = {
                    sku for order in new_orders for sku in order["items"]
                }
                needed_products = [products[sku] for sku in needed_skus]

                categories = sorted({item["category_name"] for item in needed_products})
                if categories:
                    db.session.execute(text("""
                        INSERT INTO biz.product_category (category_name)
                        VALUES (:category_name) ON CONFLICT (category_name) DO NOTHING
                    """), [{"category_name": value} for value in categories])
                    category_ids = dict(db.session.execute(text("""
                        SELECT category_name, category_id FROM biz.product_category
                        WHERE category_name = ANY(:names)
                    """), {"names": categories}).all())
                else:
                    category_ids = {}

                customer_rows = [customers[number] for number in needed_customer_nos]
                if customer_rows:
                    db.session.execute(text("""
                        INSERT INTO biz.customer
                            (customer_no, name, phone, email, province, city)
                        VALUES (:customer_no, :name, :phone, :email, :province, :city)
                        ON CONFLICT (customer_no) DO NOTHING
                    """), customer_rows)

                product_rows = [{
                    "sku": item["sku"], "product_name": item["product_name"],
                    "category_id": category_ids[item["category_name"]],
                    "unit_price": item["unit_price"],
                } for item in needed_products]
                if product_rows:
                    db.session.execute(text("""
                        INSERT INTO biz.product
                            (sku, product_name, category_id, unit_price, stock_qty)
                        VALUES (:sku, :product_name, :category_id, :unit_price, 0)
                        ON CONFLICT (sku) DO NOTHING
                    """), product_rows)

                customer_ids = dict(db.session.execute(text("""
                    SELECT customer_no, customer_id FROM biz.customer
                    WHERE customer_no = ANY(:numbers)
                """), {"numbers": list(needed_customer_nos)}).all()) if needed_customer_nos else {}
                product_ids = dict(db.session.execute(text("""
                    SELECT sku, product_id FROM biz.product WHERE sku = ANY(:skus)
                """), {"skus": list(needed_skus)}).all()) if needed_skus else {}

                order_rows = []
                for order in new_orders:
                    order_rows.append({
                        "order_no": order["order_no"],
                        "customer_id": customer_ids[order["customer_no"]],
                        "total_amount": order["total_amount"], "ordered_at": order["ordered_at"],
                        "operator_id": operator_id,
                    })
                if order_rows:
                    db.session.execute(text("""
                        INSERT INTO biz.sales_order
                            (order_no, customer_id, status, total_amount, paid_amount,
                             ordered_at, paid_at, remark, created_by)
                        VALUES (:order_no, :customer_id, 'paid', :total_amount, :total_amount,
                                :ordered_at, :ordered_at, '用户数据集导入', :operator_id)
                    """), order_rows)
                    order_ids = dict(db.session.execute(text("""
                        SELECT order_no, order_id FROM biz.sales_order
                        WHERE order_no = ANY(:numbers)
                    """), {"numbers": [item["order_no"] for item in new_orders]}).all())

                    item_rows = []
                    payment_rows = []
                    for order in new_orders:
                        order_id = order_ids[order["order_no"]]
                        item_rows.extend({
                            "order_id": order_id, "product_id": product_ids[item["sku"]],
                            "quantity": item["quantity"], "unit_price": item["unit_price"],
                            "line_amount": item["line_amount"],
                        } for item in order["items"].values())
                        payment_rows.append({
                            "payment_no": _payment_no(order["order_no"]), "order_id": order_id,
                            "method": order["method"], "amount": order["total_amount"],
                            "paid_at": order["ordered_at"], "operator_id": operator_id,
                        })
                    db.session.execute(text("""
                        INSERT INTO biz.order_item
                            (order_id, product_id, quantity, unit_price, line_amount)
                        VALUES (:order_id, :product_id, :quantity, :unit_price, :line_amount)
                    """), item_rows)
                    db.session.execute(text("""
                        INSERT INTO biz.payment
                            (payment_no, order_id, method, amount, status, paid_at,
                             transaction_ref, created_by)
                        VALUES (:payment_no, :order_id, :method, :amount, 'success', :paid_at,
                                :payment_no, :operator_id)
                    """), payment_rows)
                    payments = {
                        row.payment_no: row.payment_id
                        for row in db.session.execute(text("""
                            SELECT payment_no, payment_id FROM biz.payment
                            WHERE payment_no = ANY(:numbers)
                        """), {"numbers": [item["payment_no"] for item in payment_rows]})
                    }
                    flow_rows = [{
                        "customer_id": customer_ids[order["customer_no"]],
                        "order_id": order_ids[order["order_no"]],
                        "payment_id": payments[_payment_no(order["order_no"])],
                        "amount": order["total_amount"], "occurred_at": order["ordered_at"],
                    } for order in new_orders]
                    db.session.execute(text("""
                        INSERT INTO dwd.consumption_flow
                            (customer_id, order_id, payment_id, flow_type,
                             gross_amount, net_amount, occurred_at)
                        VALUES (:customer_id, :order_id, :payment_id, 'payment',
                                :amount, :amount, :occurred_at)
                    """), flow_rows)

                db.session.execute(text("""
                    UPDATE ods.import_batch SET status = 'success',
                        customer_count = :customer_count,
                        transaction_count = :transaction_count,
                        finished_at = now(), error_message = NULL
                    WHERE batch_no = :batch_no
                """), {
                    "customer_count": len(customers), "transaction_count": len(new_orders),
                    "batch_no": batch_no,
                })
                db.session.execute(text("""
                    UPDATE audit.import_log SET status = 'success',
                        accepted_rows = :accepted_rows, rejected_rows = 0,
                        finished_at = now(), error_message = NULL
                    WHERE import_log_id = :import_log_id
                """), {"accepted_rows": len(rows), "import_log_id": import_log_id})
                audit("dataset.import", "import_batch", batch_no, json.dumps({
                    "rows": len(rows), "customers": len(customers),
                    "orders": len(new_orders), "skipped_orders": len(existing_orders),
                }, ensure_ascii=False))
        except Exception as exc:
            db.session.rollback()
            if isinstance(exc, RetailImportError):
                public_error = str(exc)[:500]
            else:
                public_error = f"导入处理失败，请联系管理员并提供批次号 {batch_no}"
                current_app.logger.error(
                    "dataset import failed batch=%s error_type=%s",
                    batch_no, type(exc).__name__,
                )
            with db.session.begin():
                db.session.execute(text("""
                    UPDATE ods.import_batch SET status = 'failed', finished_at = now(),
                        error_message = :error_message WHERE batch_no = :batch_no
                """), {"batch_no": batch_no, "error_message": public_error})
                db.session.execute(text("""
                    UPDATE audit.import_log SET status = 'failed', accepted_rows = 0,
                        rejected_rows = :rejected_rows, error_message = :error_message,
                        finished_at = now() WHERE import_log_id = :import_log_id
                """), {
                    "rejected_rows": len(rows), "error_message": public_error,
                    "import_log_id": import_log_id,
                })
            raise

        return {
            "batch_no": batch_no, "row_count": len(rows),
            "customer_count": len(customers), "order_count": len(new_orders),
            "skipped_orders": len(existing_orders),
        }

    @staticmethod
    def confirm_preflight(batch_no, operator_id):
        import_log_id = None
        try:
            with db.session.begin():
                batch = db.session.execute(text("""
                    SELECT batch_id, batch_no, source_name, status, confirmed_at,
                           valid_row_count, invalid_row_count, created_by
                    FROM ods.import_batch
                    WHERE batch_no = :batch_no
                    FOR UPDATE
                """), {"batch_no": batch_no}).mappings().one_or_none()
                if not batch:
                    raise RetailImportError("导入预检批次不存在")
                if batch["status"] != "running" or batch["confirmed_at"] is not None:
                    raise RetailImportError("导入预检批次已确认或不可用")
                if batch["created_by"] != operator_id:
                    raise RetailImportError("只能确认本人创建的导入预检批次")
                if batch["invalid_row_count"]:
                    raise RetailImportError("导入预检存在错误，请修正文件后重新上传")

                rows = [row["payload"] for row in db.session.execute(text("""
                    SELECT payload
                    FROM ods.import_stage_row
                    WHERE batch_id = :batch_id AND is_valid
                    ORDER BY row_number
                """), {"batch_id": batch["batch_id"]}).mappings()]
                if len(rows) != batch["valid_row_count"]:
                    raise RetailImportError("导入预检暂存数据不完整")
                customers, products, orders = _normalize_rows(rows)
                import_log_id = db.session.execute(text("""
                    INSERT INTO audit.import_log (account_id, source_name, status)
                    VALUES (:account_id, :source_name, 'running')
                    RETURNING import_log_id
                """), {
                    "account_id": operator_id,
                    "source_name": batch["source_name"][:255],
                }).scalar_one()

                for order in orders.values():
                    total = sum(
                        (item["line_amount"] for item in order["items"].values()),
                        Decimal("0.00"),
                    )
                    if total > MAX_AMOUNT:
                        raise RetailImportError(
                            f"订单 {order['order_no']}：总金额超出系统上限"
                        )
                    order["total_amount"] = total

                db.session.execute(text("""
                    WITH ordered_numbers AS MATERIALIZED (
                        SELECT number
                        FROM unnest(CAST(:numbers AS text[])) AS number
                        ORDER BY number
                    )
                    SELECT pg_advisory_xact_lock(
                        hashtextextended('retail-import:' || number, 0)
                    )
                    FROM ordered_numbers
                """), {"numbers": list(orders)}).all()
                existing_orders = _load_existing_orders(orders)
                for order_no, existing in existing_orders.items():
                    if not _order_matches(existing, orders[order_no]):
                        raise RetailImportError(
                            f"订单 {order_no} 已存在，但客户、时间、商品或金额与上传数据不一致"
                        )
                new_orders = [
                    order for number, order in orders.items()
                    if number not in existing_orders
                ]
                needed_customer_nos = {order["customer_no"] for order in new_orders}
                needed_skus = {
                    sku for order in new_orders for sku in order["items"]
                }
                needed_products = [products[sku] for sku in needed_skus]

                categories = sorted({item["category_name"] for item in needed_products})
                if categories:
                    db.session.execute(text("""
                        INSERT INTO biz.product_category (category_name)
                        VALUES (:category_name) ON CONFLICT (category_name) DO NOTHING
                    """), [{"category_name": value} for value in categories])
                    category_ids = dict(db.session.execute(text("""
                        SELECT category_name, category_id FROM biz.product_category
                        WHERE category_name = ANY(:names)
                    """), {"names": categories}).all())
                else:
                    category_ids = {}

                customer_rows = [customers[number] for number in needed_customer_nos]
                if customer_rows:
                    db.session.execute(text("""
                        INSERT INTO biz.customer
                            (customer_no, name, phone, email, province, city)
                        VALUES (:customer_no, :name, :phone, :email, :province, :city)
                        ON CONFLICT (customer_no) DO NOTHING
                    """), customer_rows)

                product_rows = [{
                    "sku": item["sku"], "product_name": item["product_name"],
                    "category_id": category_ids[item["category_name"]],
                    "unit_price": item["unit_price"],
                } for item in needed_products]
                if product_rows:
                    db.session.execute(text("""
                        INSERT INTO biz.product
                            (sku, product_name, category_id, unit_price, stock_qty)
                        VALUES (:sku, :product_name, :category_id, :unit_price, 0)
                        ON CONFLICT (sku) DO NOTHING
                    """), product_rows)

                customer_ids = dict(db.session.execute(text("""
                    SELECT customer_no, customer_id FROM biz.customer
                    WHERE customer_no = ANY(:numbers)
                """), {"numbers": list(needed_customer_nos)}).all()) if needed_customer_nos else {}
                product_ids = dict(db.session.execute(text("""
                    SELECT sku, product_id FROM biz.product WHERE sku = ANY(:skus)
                """), {"skus": list(needed_skus)}).all()) if needed_skus else {}

                order_rows = [{
                    "order_no": order["order_no"],
                    "customer_id": customer_ids[order["customer_no"]],
                    "total_amount": order["total_amount"],
                    "ordered_at": order["ordered_at"],
                    "operator_id": operator_id,
                } for order in new_orders]
                if order_rows:
                    db.session.execute(text("""
                        INSERT INTO biz.sales_order
                            (order_no, customer_id, status, total_amount, paid_amount,
                             ordered_at, paid_at, remark, created_by)
                        VALUES (:order_no, :customer_id, 'paid', :total_amount, :total_amount,
                                :ordered_at, :ordered_at, '用户数据集导入', :operator_id)
                    """), order_rows)
                    order_ids = dict(db.session.execute(text("""
                        SELECT order_no, order_id FROM biz.sales_order
                        WHERE order_no = ANY(:numbers)
                    """), {"numbers": [item["order_no"] for item in new_orders]}).all())

                    item_rows = []
                    payment_rows = []
                    for order in new_orders:
                        order_id = order_ids[order["order_no"]]
                        item_rows.extend({
                            "order_id": order_id, "product_id": product_ids[item["sku"]],
                            "quantity": item["quantity"], "unit_price": item["unit_price"],
                            "line_amount": item["line_amount"],
                        } for item in order["items"].values())
                        payment_rows.append({
                            "payment_no": _payment_no(order["order_no"]),
                            "order_id": order_id,
                            "method": order["method"],
                            "amount": order["total_amount"],
                            "paid_at": order["ordered_at"],
                            "operator_id": operator_id,
                        })
                    db.session.execute(text("""
                        INSERT INTO biz.order_item
                            (order_id, product_id, quantity, unit_price, line_amount)
                        VALUES (:order_id, :product_id, :quantity, :unit_price, :line_amount)
                    """), item_rows)
                    db.session.execute(text("""
                        INSERT INTO biz.payment
                            (payment_no, order_id, method, amount, status, paid_at,
                             transaction_ref, created_by)
                        VALUES (:payment_no, :order_id, :method, :amount, 'success', :paid_at,
                                :payment_no, :operator_id)
                    """), payment_rows)
                    payments = {
                        row.payment_no: row.payment_id
                        for row in db.session.execute(text("""
                            SELECT payment_no, payment_id FROM biz.payment
                            WHERE payment_no = ANY(:numbers)
                        """), {"numbers": [item["payment_no"] for item in payment_rows]})
                    }
                    flow_rows = [{
                        "customer_id": customer_ids[order["customer_no"]],
                        "order_id": order_ids[order["order_no"]],
                        "payment_id": payments[_payment_no(order["order_no"])],
                        "amount": order["total_amount"],
                        "occurred_at": order["ordered_at"],
                    } for order in new_orders]
                    db.session.execute(text("""
                        INSERT INTO dwd.consumption_flow
                            (customer_id, order_id, payment_id, flow_type,
                             gross_amount, net_amount, occurred_at)
                        VALUES (:customer_id, :order_id, :payment_id, 'payment',
                                :amount, :amount, :occurred_at)
                    """), flow_rows)

                gross_amount = sum(
                    (order["total_amount"] for order in orders.values()),
                    Decimal("0.00"),
                )
                db.session.execute(text("""
                    UPDATE ods.import_batch SET status = 'success',
                        customer_count = :customer_count,
                        transaction_count = :transaction_count,
                        skipped_order_count = :skipped_order_count,
                        gross_amount = :gross_amount,
                        confirmed_at = now(), finished_at = now(), error_message = NULL
                    WHERE batch_id = :batch_id
                """), {
                    "customer_count": len(customers),
                    "transaction_count": len(new_orders),
                    "skipped_order_count": len(existing_orders),
                    "gross_amount": gross_amount,
                    "batch_id": batch["batch_id"],
                })
                db.session.execute(text("""
                    UPDATE audit.import_log SET status = 'success',
                        accepted_rows = :accepted_rows, rejected_rows = 0,
                        finished_at = now(), error_message = NULL
                    WHERE import_log_id = :import_log_id
                """), {
                    "accepted_rows": len(rows),
                    "import_log_id": import_log_id,
                })
                audit("dataset.import.confirm", "import_batch", batch_no, json.dumps({
                    "rows": len(rows),
                    "customers": len(customers),
                    "orders": len(new_orders),
                    "skipped_orders": len(existing_orders),
                }, ensure_ascii=False))
                return {
                    "batch_no": batch_no,
                    "row_count": len(rows),
                    "customer_count": len(customers),
                    "order_count": len(new_orders),
                    "skipped_orders": len(existing_orders),
                    "gross_amount": gross_amount,
                }
        except Exception as exc:
            db.session.rollback()
            if isinstance(exc, RetailImportError):
                public_error = str(exc)[:500]
            else:
                public_error = f"导入处理失败，请联系管理员并提供批次号 {batch_no}"
                current_app.logger.error(
                    "preflight import confirmation failed batch=%s error_type=%s",
                    batch_no,
                    type(exc).__name__,
                )
            with db.session.begin():
                db.session.execute(text("""
                    UPDATE ods.import_batch SET status = 'failed', finished_at = now(),
                        error_message = :error_message
                    WHERE batch_no = :batch_no AND confirmed_at IS NULL
                """), {"batch_no": batch_no, "error_message": public_error})
            raise RetailImportError(public_error) from exc
