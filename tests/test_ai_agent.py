import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from flask import Flask


class ScriptedClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, tools, tool_choice="auto"):
        self.calls.append({
            "messages": list(messages),
            "tools": tools,
            "tool_choice": tool_choice,
        })
        if not self.responses:
            raise AssertionError("unexpected model request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeSession:
    def __init__(self):
        self.rollback_count = 0

    def rollback(self):
        self.rollback_count += 1

    def begin(self):
        return nullcontext()


def _app():
    app = Flask(__name__)
    app.config.update(
        QA_AGENT_ENABLED=True,
        AI_API_KEY="test-key",
        AI_MODEL="test-model",
        AI_MAX_TOOL_CALLS=2,
        AI_MAX_RESPONSE_CHARS=800,
    )
    return app


def _tool_call(name, arguments, call_id="call-1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
        },
    }


def test_knowledge_tool_uses_server_account_and_returns_citations(monkeypatch):
    from app.services import agent

    server_account_id = 41
    context = {
        "document_id": 7,
        "chunk_id": 13,
        "title": "退款规则",
        "excerpt": "支付成功后七天内可以提交退款申请。",
        "score": 0.88,
    }
    executed = []

    def execute(name, arguments, account_id):
        executed.append((name, arguments, account_id))
        return {
            "found": True,
            "matches": [{"title": context["title"], "answer": context["excerpt"]}],
            "_reliable": True,
            "_contexts": [context],
        }

    monkeypatch.setattr(agent.AgentTools, "execute", staticmethod(execute))
    fake_session = FakeSession()
    monkeypatch.setattr(agent, "db", SimpleNamespace(session=fake_session))

    created_messages = []
    stored_retrievals = []
    monkeypatch.setattr(
        agent.QARepository,
        "create_session",
        staticmethod(lambda account_id, title: 101),
    )

    def create_message(session_id, account_id, role, content, **metadata):
        created_messages.append((session_id, account_id, role, content, metadata))
        return 201 if role == "user" else 202

    monkeypatch.setattr(agent.QARepository, "create_message", staticmethod(create_message))
    monkeypatch.setattr(
        agent.QARepository,
        "add_retrievals",
        staticmethod(lambda message_id, contexts: stored_retrievals.append(
            (message_id, contexts)
        )),
    )
    monkeypatch.setattr(agent.QARepository, "touch_session", staticmethod(lambda _id: None))
    monkeypatch.setattr(agent, "audit", lambda *args, **kwargs: None)

    client = ScriptedClient(
        {
            "content": "",
            "tool_calls": [
                _tool_call(
                    "search_knowledge",
                    {"question": "退款期限是多久"},
                )
            ],
        },
        {"content": "退款申请期限为支付成功后七天内。", "tool_calls": []},
    )

    with _app().app_context():
        result = agent.AgentService.ask(
            None,
            "退款期限是多久",
            server_account_id,
            client=client,
        )

    assert executed == [(
        "search_knowledge",
        {"question": "退款期限是多久"},
        server_account_id,
    )]
    assert all(
        "account_id" not in definition["function"]["parameters"]["properties"]
        for definition in agent.TOOL_DEFINITIONS
    )
    assert result["citations"] == [{
        "document_id": 7,
        "chunk_id": 13,
        "title": "退款规则",
        "rank": 1,
        "excerpt": "支付成功后七天内可以提交退款申请。",
    }]
    assert result["confidence"] == pytest.approx(0.88)
    assert result["ticket_id"] is None
    assert stored_retrievals == [(202, [context])]
    assert created_messages[0][1:3] == (server_account_id, "user")
    assert created_messages[1][1:3] == (None, "assistant")
    outbound_tool_result = json.loads(client.calls[1]["messages"][-1]["content"])
    assert set(outbound_tool_result) == {"found", "matches"}


def test_model_identity_argument_is_rejected_and_server_identity_is_used(monkeypatch):
    from app.services import agent_tools

    checked_accounts = []

    def permissions(account_id):
        checked_accounts.append(account_id)
        return frozenset({"qa.read"})

    monkeypatch.setattr(agent_tools, "account_permissions", permissions)
    with pytest.raises(agent_tools.AgentToolError, match="未授权字段"):
        agent_tools.AgentTools.execute(
            "search_knowledge",
            {"question": "退款期限", "account_id": 999999},
            41,
        )
    assert checked_accounts == []

    result = agent_tools.AgentTools.execute(
        "create_support_ticket", {"reason": "需要核实"}, 41
    )
    assert result["accepted"] is True
    assert checked_accounts == [41]


def test_ai_client_failure_falls_back_exactly_once(monkeypatch):
    from app.services import agent

    fake_session = FakeSession()
    monkeypatch.setattr(agent, "db", SimpleNamespace(session=fake_session))
    fallback_calls = []
    expected = {"answer": "本地知识检索结果", "session_id": 9}

    def fallback(session_id, question, account_id):
        fallback_calls.append((session_id, question, account_id))
        return expected

    monkeypatch.setattr(agent.RagService, "ask", staticmethod(fallback))
    client = ScriptedClient(agent.AIClientError("provider unavailable"))

    with _app().app_context():
        result = agent.AgentService.ask(None, "退款申请如何处理", 51, client=client)

    assert result is expected
    assert fallback_calls == [(None, "退款申请如何处理", 51)]
    assert len(client.calls) == 1


def test_ai_client_timeout_is_one_budget_for_the_whole_request(monkeypatch):
    from app.services import agent

    observed_timeouts = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}

    def post(*args, **kwargs):
        observed_timeouts.append(kwargs["timeout"])
        return Response()

    ticks = iter((100.0, 101.0, 107.5, 108.1))
    monkeypatch.setattr(agent.httpx, "post", post)
    monkeypatch.setattr(agent.time, "monotonic", lambda: next(ticks))
    client = agent.AIClient("https://example.invalid/v1", "key", "model", 8)

    client.complete([], [])
    client.complete([], [])
    with pytest.raises(agent.AIClientError, match="超时"):
        client.complete([], [])

    assert observed_timeouts == pytest.approx([7.0, 0.5])


@pytest.mark.parametrize(
    ("call", "event_name"),
    [
        (_tool_call("search_knowledge", "{not-json"), "search_knowledge"),
        (_tool_call("delete_order", {"order_no": "SO-001"}), "delete_order"),
    ],
)
def test_invalid_tool_or_json_is_rejected_and_handed_off(
    monkeypatch, call, event_name,
):
    from app.services import agent

    monkeypatch.setattr(agent, "db", SimpleNamespace(session=FakeSession()))
    client = ScriptedClient(
        {"content": "", "tool_calls": [call]},
        {"content": "模型自行猜测的回答", "tool_calls": []},
    )

    result = agent.run_agent_loop("查询订单状态", [], 62, client, max_tool_calls=2)

    assert result["evidence"] is False
    assert result["handoff"] is True
    assert result["answer"] != "模型自行猜测的回答"
    assert result["tool_events"] == [{"name": event_name, "status": "rejected"}]


def test_tool_loop_executes_at_most_two_calls_then_hands_off(monkeypatch):
    from app.services import agent

    executed = []

    def execute(name, arguments, account_id):
        executed.append((name, arguments, account_id))
        return {"found": True, "_reliable": True}

    monkeypatch.setattr(agent.AgentTools, "execute", staticmethod(execute))
    monkeypatch.setattr(agent, "db", SimpleNamespace(session=FakeSession()))
    client = ScriptedClient(
        {
            "content": "",
            "tool_calls": [
                _tool_call("search_knowledge", {"question": "问题一"}, "call-1"),
                _tool_call("search_knowledge", {"question": "问题二"}, "call-2"),
                _tool_call("search_knowledge", {"question": "问题三"}, "call-3"),
            ],
        },
        {"content": "没有证据也直接回答", "tool_calls": []},
    )

    result = agent.run_agent_loop("请查询这些问题", [], 73, client, max_tool_calls=2)

    assert [arguments["question"] for _, arguments, _ in executed] == ["问题一", "问题二"]
    assert {account_id for _, _, account_id in executed} == {73}
    assert result["evidence"] is True
    assert result["handoff"] is True
    assert result["answer"] != "没有证据也直接回答"
    assert result["tool_events"] == [
        {"name": "search_knowledge", "status": "success"},
        {"name": "search_knowledge", "status": "success"},
        {"name": "search_knowledge", "status": "limit_reached"},
    ]
    assert len(client.calls) == 1


def test_failed_order_lookup_overrides_successful_knowledge_evidence(monkeypatch):
    from app.services import agent

    def execute(name, arguments, account_id):
        if name == "search_knowledge":
            return {"found": True, "_reliable": True, "_contexts": []}
        return {"found": False, "_reliable": False}

    monkeypatch.setattr(agent.AgentTools, "execute", staticmethod(execute))
    monkeypatch.setattr(agent, "db", SimpleNamespace(session=FakeSession()))
    client = ScriptedClient({
        "content": "",
        "tool_calls": [
            _tool_call("search_knowledge", {"question": "退款规则"}, "call-1"),
            _tool_call(
                "lookup_order_refund",
                {"reference": "DEMO2-PAID-SO-999"},
                "call-2",
            ),
        ],
    })

    result = agent.run_agent_loop(
        "DEMO2-PAID-SO-999 的退款状态", [], 88, client, max_tool_calls=2
    )

    assert result["evidence"] is True
    assert result["handoff"] is True
    assert "人工客服工单" in result["answer"]
    assert len(client.calls) == 1
