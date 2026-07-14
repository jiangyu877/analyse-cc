import csv
from io import StringIO

from flask import Blueprint, Response, flash, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.security.authorization import account_permissions
from app.repositories.knowledge import KnowledgeRepository
from app.security.authorization import permission_required
from app.services.knowledge import KnowledgeError, KnowledgeService


knowledge_bp = Blueprint("knowledge", __name__, url_prefix="/knowledge")


@knowledge_bp.get("")
@permission_required("knowledge.read")
def index():
    return render_template(
        "knowledge/index.html",
        knowledge_bases=KnowledgeRepository.list_bases(),
        documents=KnowledgeRepository.list_documents(),
    )


@knowledge_bp.post("/bases")
@permission_required("knowledge.write")
def create_base():
    try:
        KnowledgeService.create_base(
            request.form.get("base_code"),
            request.form.get("name"),
            request.form.get("description"),
            session["user_id"],
        )
        flash("知识库已创建", "success")
    except IntegrityError:
        db.session.rollback()
        flash("知识库编码已存在", "danger")
    except (KnowledgeError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("knowledge.index"))


@knowledge_bp.post("/documents")
@permission_required("knowledge.write")
def upload_document():
    upload = request.files.get("document")
    if not upload:
        flash("请选择文档", "danger")
        return redirect(url_for("knowledge.index"))
    try:
        document_id = KnowledgeService.ingest(
            request.form.get("knowledge_base_id"),
            request.form.get("title"),
            upload.filename,
            upload.read(),
            session["user_id"],
        )
        flash(f"文档 {document_id} 已解析并建立关键词索引", "success")
    except (KnowledgeError, ValueError, IntegrityError) as exc:
        db.session.rollback()
        flash(str(exc) if not isinstance(exc, IntegrityError) else "文档版本冲突", "danger")
    return redirect(url_for("knowledge.index"))


@knowledge_bp.get("/datasets/template")
@permission_required("knowledge.read")
def download_dataset_template():
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["问题", "答案", "分类", "关键词"])
    writer.writerow(["订单支付后多久可以申请退款？", "支付后 7 天内可以提交退款申请。", "退款", "退款时效 售后"])
    return Response(
        "\ufeff" + output.getvalue(),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=faq-dataset-template.csv"},
    )


@knowledge_bp.post("/datasets")
@permission_required("knowledge.write")
def upload_dataset():
    upload = request.files.get("dataset")
    if not upload or not upload.filename:
        flash("请选择 CSV 或 XLSX 问答数据集", "danger")
        return redirect(url_for("knowledge.index", _anchor="dataset-import"))
    try:
        document_id, row_count = KnowledgeService.ingest_faq_dataset(
            request.form.get("knowledge_base_id"), request.form.get("title"),
            upload.filename, upload.read(), session["user_id"],
        )
        publish_requested = request.form.get("publish_now") == "true"
        can_publish = "knowledge.publish" in account_permissions(session["user_id"])
        if publish_requested and can_publish:
            KnowledgeService.publish(document_id, session["user_id"])
            flash(f"已导入并发布 {row_count} 条问答，QA 客服现在可以使用", "success")
        else:
            flash(f"已导入 {row_count} 条问答，请审核后发布", "success")
    except (KnowledgeError, ValueError, IntegrityError) as exc:
        db.session.rollback()
        flash(str(exc) if not isinstance(exc, IntegrityError) else "数据集版本冲突", "danger")
    return redirect(url_for("knowledge.index", _anchor="dataset-import"))


@knowledge_bp.post("/documents/<int:document_id>/publish")
@permission_required("knowledge.publish")
def publish_document(document_id):
    try:
        KnowledgeService.publish(document_id, session["user_id"])
        flash("文档已发布，可用于 QA 检索", "success")
    except (KnowledgeError, SQLAlchemyError) as exc:
        db.session.rollback()
        flash(str(exc) if isinstance(exc, KnowledgeError) else "发布冲突，请刷新后重试", "danger")
    return redirect(url_for("knowledge.index"))


@knowledge_bp.post("/documents/<int:document_id>/disable")
@permission_required("knowledge.publish")
def disable_document(document_id):
    try:
        KnowledgeService.disable(document_id, session["user_id"])
        flash("文档已停用", "success")
    except KnowledgeError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("knowledge.index"))
