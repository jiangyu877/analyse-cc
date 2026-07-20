import csv
from io import StringIO

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.security.authorization import permission_required
from app.services.retail_import import RetailImportError, RetailImportService
from app.services.tabular import TabularDataError

imports_bp = Blueprint("imports", __name__, url_prefix="/imports")


@imports_bp.get("")
@permission_required("import.read")
def index():
    summary = db.session.execute(text("""
        SELECT
          (SELECT COUNT(*) FROM biz.customer) AS customer_count,
          (SELECT COUNT(*) FROM biz.sales_order) AS order_count,
          (SELECT COUNT(*) FROM dwd.consumption_flow WHERE flow_type = 'payment') AS consumption_count,
          (SELECT COALESCE(SUM(net_amount), 0) FROM dwd.consumption_flow) AS net_amount
    """)).mappings().one()
    batches = db.session.execute(text("""
        SELECT batch_no, source_name, status, customer_count, transaction_count,
               started_at, finished_at, error_message
        FROM ods.import_batch ORDER BY started_at DESC LIMIT 50
    """)).mappings().all()
    return render_template("imports.html", summary=summary, batches=batches)


@imports_bp.get("/template")
@permission_required("import.read")
def download_template():
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "客户编号", "客户姓名", "手机号", "邮箱", "省份", "城市",
        "订单编号", "下单时间", "商品SKU", "商品名称", "商品分类",
        "数量", "单价", "支付方式",
    ])
    writer.writerow([
        "C10001", "张三", "13800000000", "zhangsan@example.com", "浙江", "杭州",
        "SO20260001", "2026-07-14 10:30:00", "SKU-001", "经典咖啡", "饮品",
        "2", "29.90", "微信",
    ])
    return Response(
        "\ufeff" + output.getvalue(),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=retail-import-template.csv"},
    )


@imports_bp.post("/upload")
@permission_required("import.run")
def upload_dataset():
    upload = request.files.get("dataset")
    if not upload or not upload.filename:
        flash("请选择 CSV 或 XLSX 数据集", "danger")
        return redirect(url_for("imports.index"))
    data = upload.read()
    try:
        result = RetailImportService.import_dataset(
            upload.filename, data, session["user_id"]
        )
        message = (
            f"批次 {result['batch_no']} 导入完成：{result['customer_count']} 个客户、"
            f"{result['order_count']} 笔新订单"
        )
        if result["skipped_orders"]:
            message += f"，跳过 {result['skipped_orders']} 笔已有订单"
        flash(message, "success")
    except (RetailImportError, TabularDataError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("retail dataset import failed")
        flash("数据导入失败，请检查数据是否与现有业务记录冲突", "danger")
    return redirect(url_for("imports.index"))


@imports_bp.post("/preview")
@permission_required("import.run")
def preview_dataset():
    upload = request.files.get("dataset")
    if not upload or not upload.filename:
        flash("请选择 CSV 或 XLSX 数据集", "danger")
        return redirect(url_for("imports.index"))
    try:
        result = RetailImportService.preflight_dataset(
            upload.filename, upload.read(), session["user_id"]
        )
        return redirect(url_for("imports.batch_detail", batch_no=result["batch_no"]))
    except (RetailImportError, TabularDataError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("retail import preflight failed")
        flash("数据预检失败，请检查文件后重试", "danger")
    return redirect(url_for("imports.index"))


@imports_bp.post("/<batch_no>/confirm")
@permission_required("import.run")
def confirm_dataset(batch_no):
    try:
        RetailImportService.confirm_preflight(batch_no, session["user_id"])
        flash(f"批次 {batch_no} 导入完成", "success")
    except RetailImportError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("imports.batch_detail", batch_no=batch_no))


@imports_bp.get("/<batch_no>/errors.csv")
@permission_required("import.read")
def download_error_report(batch_no):
    try:
        report = RetailImportService.error_report(batch_no)
    except RetailImportError:
        return render_template("error.html", code=404, message="导入预检批次不存在"), 404
    return Response(
        report,
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={batch_no}-errors.csv"},
    )


@imports_bp.get("/<batch_no>")
@permission_required("import.read")
def batch_detail(batch_no):
    batch = RetailImportService.batch_detail(batch_no)
    if batch is None:
        return render_template("error.html", code=404, message="导入预检批次不存在"), 404
    return render_template("import_batch_detail.html", batch=batch)
