from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, render_template, session


ROOT = Path(__file__).resolve().parents[1]


def test_chat_hides_unreliable_retrievals_but_keeps_successful_citations():
    app = Flask(__name__, template_folder=str(ROOT / "app" / "templates"))
    app.secret_key = "test-secret"
    app.add_url_rule("/", endpoint="main.dashboard", view_func=lambda: "")
    app.add_url_rule("/logout", endpoint="auth.logout", view_func=lambda: "")
    app.add_url_rule("/qa/ask", endpoint="qa.ask", view_func=lambda: "")
    app.add_url_rule(
        "/qa/messages/<int:message_id>/feedback",
        endpoint="qa.feedback",
        view_func=lambda message_id: str(message_id),
    )

    common = {
        "message_role": "assistant",
        "provider_model": "keyword-search-v1",
        "created_at": datetime(2026, 7, 14, 16, 37),
        "current_feedback": None,
    }
    messages = [
        {
            **common,
            "message_id": 1,
            "message_status": "refusal",
            "content": "现有知识库中没有找到足够可靠的答案，已转交人工客服处理。",
            "citations": [{
                "title": "标准问答",
                "version": 1,
                "excerpt": "不可靠的候选答案不应展示",
            }],
        },
        {
            **common,
            "message_id": 2,
            "message_status": "success",
            "content": "退款申请可以在订单详情中提交。",
            "citations": [{
                "title": "退款规则",
                "version": 2,
                "excerpt": "可靠的引用应正常展示",
            }],
        },
    ]

    with app.test_request_context("/qa"):
        session.update(user_id=7, username="tester", role="qa_operator")
        html = render_template(
            "qa/chat.html",
            can=lambda _permission: False,
            csrf_token=lambda: "csrf-token",
            sessions=[],
            selected_session=SimpleNamespace(session_id=9, title="测试会话"),
            messages=messages,
            agent_enabled=False,
        )

    assert "不可靠的候选答案不应展示" not in html
    assert "可靠的引用应正常展示" in html
