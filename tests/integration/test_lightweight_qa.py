from pathlib import Path

from flask import session
from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]


def _initialize(connection):
    from scripts.init_db import apply_migrations

    with connection.cursor() as cursor:
        cursor.execute((ROOT / "database" / "v2_schema.sql").read_text(encoding="utf-8"))
        cursor.execute((ROOT / "database" / "v2_seed.sql").read_text(encoding="utf-8"))
    connection.commit()
    apply_migrations(connection)


def test_published_document_answers_with_citation_and_low_match_creates_ticket(
    isolated_database, isolated_app, tmp_path
):
    from app.extensions import db
    from app.repositories.qa import QARepository
    from app.services.knowledge import KnowledgeService
    from app.services.rag import RagService

    _initialize(isolated_database)
    isolated_app.config["KNOWLEDGE_UPLOAD_DIR"] = str(tmp_path / "knowledge")
    isolated_app.config["QA_MIN_MATCH_SCORE"] = 0.2

    with isolated_app.test_request_context("/qa"):
        account_id = db.session.execute(text(
            "SELECT account_id FROM auth.account WHERE username = 'admin'"
        )).scalar_one()
        db.session.commit()
        session["user_id"] = account_id

        base_id = KnowledgeService.create_base(
            "refund_rules", "退款规则", "", account_id
        )
        document_id = KnowledgeService.ingest(
            base_id,
            "退款处理规范",
            "refund-rules.txt",
            "退款申请需要在支付后七天内提交。财务审核通过后，系统回补库存并记录退款流水。".encode(),
            account_id,
        )
        KnowledgeService.publish(document_id, account_id)

        answer = RagService.ask(None, "退款申请需要在什么时候提交", account_id)
        assert answer["ticket_id"] is None
        assert answer["citations"]
        assert answer["citations"][0]["document_id"] == document_id
        assert "七天" in answer["answer"]

        retrieval_count = db.session.execute(text("""
            SELECT COUNT(*) FROM qa.qa_retrieval_log WHERE message_id = :message_id
        """), {"message_id": answer["message_id"]}).scalar_one()
        db.session.commit()
        assert retrieval_count >= 1

        fallback = RagService.ask(
            answer["session_id"], "宇宙飞船发动机燃料如何配置", account_id
        )
        assert fallback["ticket_id"] is not None
        assert fallback["citations"] == []

        feedback_id = RagService.feedback(
            answer["message_id"], account_id, True, "答案清楚"
        )
        assert feedback_id
        RagService.assign_ticket(fallback["ticket_id"], account_id)
        RagService.resolve_ticket(
            fallback["ticket_id"], account_id, "该问题不属于当前知识库", "已人工回复"
        )

        ticket = next(
            item for item in QARepository.list_tickets()
            if item["ticket_id"] == fallback["ticket_id"]
        )
        assert ticket["status"] == "resolved"
        assert ticket["response_text"] == "该问题不属于当前知识库"


def test_release_b_routes_are_registered_and_permission_protected():
    from app import create_app

    app = create_app()
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    assert {
        "knowledge.index", "knowledge.create_base", "knowledge.upload_document",
        "knowledge.publish_document", "qa.chat", "qa.ask", "qa.feedback",
        "qa.tickets", "qa.assign_ticket", "qa.resolve_ticket",
    } <= endpoints

    client = app.test_client()
    assert client.get("/knowledge").status_code == 302
    assert client.get("/qa").status_code == 302
    assert client.get("/qa/tickets").status_code == 302

