from flask import session
from sqlalchemy import text


ORDER_NO = "DEMO2-PAID-SO-001"
REFUND_NO = "DEMO2-REF-001"


def _seed_role_account(connection, username, role_code):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO auth.account (username, password_hash, full_name, role)
            VALUES (%s, 'integration-test-only', %s, 'analyst')
            RETURNING account_id
            """,
            (username, username),
        )
        account_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO auth.account_role (account_id, role_id, is_primary)
            SELECT %s, role_id, TRUE
            FROM auth.role
            WHERE role_code = %s
            """,
            (account_id, role_code),
        )
    connection.commit()
    return account_id


def test_order_refund_tool_enforces_rbac_and_projects_only_safe_fields(
    initialized_app, initialized_database
):
    from app.services.agent_tools import AgentToolError, AgentTools

    qa_operator_id = _seed_role_account(
        initialized_database, "agent-qa-operator", "qa_operator"
    )
    knowledge_admin_id = _seed_role_account(
        initialized_database, "agent-knowledge-admin", "knowledge_admin"
    )

    with initialized_app.app_context():
        by_order = AgentTools.lookup_order_refund(
            {"reference": ORDER_NO}, qa_operator_id
        )
        by_refund = AgentTools.lookup_order_refund(
            {"reference": REFUND_NO}, qa_operator_id
        )

        for result in (by_order, by_refund):
            assert set(result) == {"found", "order", "refunds", "_reliable"}
            assert result["found"] is True
            assert result["_reliable"] is True
            assert set(result["order"]) == {
                "order_no",
                "status",
                "total_amount",
                "paid_amount",
                "refunded_amount",
                "ordered_at",
                "paid_at",
            }
            assert len(result["refunds"]) == 1
            assert set(result["refunds"][0]) == {
                "refund_no",
                "status",
                "amount",
                "created_at",
                "reviewed_at",
                "refunded_at",
            }
            assert result["order"]["order_no"] == ORDER_NO
            assert result["refunds"][0]["refund_no"] == REFUND_NO

        for reference in (ORDER_NO, REFUND_NO):
            try:
                AgentTools.lookup_order_refund(
                    {"reference": reference}, knowledge_admin_id
                )
            except AgentToolError as exc:
                assert "无权查询" in str(exc)
            else:
                raise AssertionError("knowledge_admin must not access order/refund data")


class _HandoffClient:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools, tool_choice="auto"):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "handoff-1",
                        "function": {
                            "name": "create_support_ticket",
                            "arguments": {"reason": "退款信息需要人工复核"},
                        },
                    }
                ],
            }
        return {"content": "已提交人工客服复核。", "tool_calls": []}


def test_explicit_agent_handoff_persists_session_messages_and_ticket(
    initialized_app, initialized_database
):
    from app.extensions import db
    from app.services.agent import AgentService

    qa_operator_id = _seed_role_account(
        initialized_database, "agent-handoff-operator", "qa_operator"
    )
    initialized_app.config.update(
        QA_AGENT_ENABLED=True,
        AI_API_KEY="integration-test-key",
        AI_MODEL="integration-test-model",
        AI_MAX_TOOL_CALLS=2,
    )

    question = f"{ORDER_NO} 的退款信息存在冲突，请进一步核实"
    client = _HandoffClient()
    with initialized_app.test_request_context("/qa"):
        session["user_id"] = qa_operator_id
        result = AgentService.ask(None, question, qa_operator_id, client=client)

        ticket = db.session.execute(
            text(
                """
                SELECT ticket.ticket_id, ticket.status, ticket.reason_code,
                       source.content AS question,
                       fallback.message_status, fallback.error_code,
                       fallback.content AS answer
                FROM qa.qa_ticket ticket
                JOIN qa.qa_message source
                  ON source.message_id = ticket.source_message_id
                JOIN qa.qa_message fallback
                  ON fallback.message_id = ticket.fallback_message_id
                WHERE ticket.ticket_id = :ticket_id
                """
            ),
            {"ticket_id": result["ticket_id"]},
        ).mappings().one()

        assert client.calls == 1
        assert result["ticket_id"] is not None
        assert result["citations"] == []
        assert result["confidence"] == 0.0
        assert ticket["status"] == "pending"
        assert ticket["reason_code"] == "agent_handoff"
        assert ticket["question"] == question
        assert ticket["message_status"] == "refusal"
        assert ticket["error_code"] == "AGENT_HANDOFF"
        assert ticket["answer"] == result["answer"]
