from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError

from app.extensions import db
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


@knowledge_bp.post("/documents/<int:document_id>/publish")
@permission_required("knowledge.publish")
def publish_document(document_id):
    try:
        KnowledgeService.publish(document_id, session["user_id"])
        flash("文档已发布，可用于 QA 检索", "success")
    except KnowledgeError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
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

