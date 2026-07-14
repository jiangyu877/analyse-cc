from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.extensions import db
from app.repositories.qa import QARepository
from app.security.authorization import permission_required
from app.services.agent import AgentService
from app.services.rag import QAError, RagService


qa_bp = Blueprint("qa", __name__, url_prefix="/qa")


@qa_bp.get("")
@permission_required("qa.read")
def chat():
    sessions = QARepository.list_sessions(session["user_id"])
    selected_id = request.args.get("session_id", type=int)
    selected = next((item for item in sessions if item["session_id"] == selected_id), None)
    if selected is None and sessions:
        selected = sessions[0]
    messages = QARepository.list_messages(selected["session_id"]) if selected else []
    return render_template(
        "qa/chat.html", sessions=sessions, selected_session=selected, messages=messages,
        agent_enabled=AgentService.enabled(),
    )


@qa_bp.post("/ask")
@permission_required("qa.read")
def ask():
    try:
        result = AgentService.ask(
            request.form.get("session_id"),
            request.form.get("question"),
            session["user_id"],
        )
        if result["ticket_id"]:
            flash(f"已创建人工工单 {result['ticket_id']}", "warning")
        return redirect(url_for("qa.chat", session_id=result["session_id"], _anchor="latest"))
    except QAError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("qa.chat"))


@qa_bp.post("/messages/<int:message_id>/feedback")
@permission_required("qa.read")
def feedback(message_id):
    try:
        RagService.feedback(
            message_id,
            session["user_id"],
            request.form.get("is_helpful") == "true",
            request.form.get("comment", ""),
        )
        flash("反馈已记录", "success")
    except QAError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("qa.chat", session_id=request.form.get("session_id")))


@qa_bp.get("/tickets")
@permission_required("qa.handle")
def tickets():
    return render_template("qa/tickets.html", tickets=QARepository.list_tickets())


@qa_bp.post("/tickets/<int:ticket_id>/assign")
@permission_required("qa.handle")
def assign_ticket(ticket_id):
    try:
        RagService.assign_ticket(ticket_id, session["user_id"])
        flash("工单已领取", "success")
    except QAError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("qa.tickets"))


@qa_bp.post("/tickets/<int:ticket_id>/resolve")
@permission_required("qa.handle")
def resolve_ticket(ticket_id):
    try:
        RagService.resolve_ticket(
            ticket_id,
            session["user_id"],
            request.form.get("response_text"),
            request.form.get("resolution_note", ""),
        )
        flash("工单已解决", "success")
    except QAError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("qa.tickets"))
