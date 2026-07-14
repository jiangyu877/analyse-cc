import re
from decimal import Decimal

from flask import current_app
from sqlalchemy import text

from app.extensions import db
from app.repositories.knowledge import KnowledgeRepository
from app.security.authorization import account_permissions
from app.services.knowledge import search_terms
from app.services.rag import _excerpt, _expanded_question, _faq_match_score, _faq_payload


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "检索已经发布的系统知识、业务规则和标准问答。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "用户的完整问题"},
                },
                "required": ["question"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_order_refund",
            "description": "根据订单号或退款号查询真实订单和退款状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reference": {
                        "type": "string",
                        "description": "订单号或退款号，例如 SO20260001",
                    },
                },
                "required": ["reference"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_support_ticket",
            "description": "用户明确要求人工，或现有数据不足以可靠回答时请求创建人工工单。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "需要人工处理的简短原因"},
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]


class AgentToolError(ValueError):
    pass


def _require_permissions(account_id, *required):
    granted = account_permissions(account_id)
    missing = [permission for permission in required if permission not in granted]
    if missing:
        raise AgentToolError("当前账号无权查询该业务数据")


def _plain(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


class AgentTools:
    @staticmethod
    def execute(name, arguments, account_id):
        handlers = {
            "search_knowledge": AgentTools.search_knowledge,
            "lookup_order_refund": AgentTools.lookup_order_refund,
            "create_support_ticket": AgentTools.create_support_ticket,
        }
        handler = handlers.get(name)
        if handler is None:
            raise AgentToolError("智能体请求了未授权工具")
        if not isinstance(arguments, dict):
            raise AgentToolError("工具参数必须是对象")
        allowed_arguments = {
            "search_knowledge": {"question"},
            "lookup_order_refund": {"reference"},
            "create_support_ticket": {"reason"},
        }[name]
        if set(arguments) - allowed_arguments:
            raise AgentToolError("工具参数包含未授权字段")
        return handler(arguments, account_id)

    @staticmethod
    def search_knowledge(arguments, account_id):
        _require_permissions(account_id, "qa.read")
        question = str(arguments.get("question") or "").strip()
        if len(question) < 2 or len(question) > 1000:
            raise AgentToolError("知识检索问题长度无效")
        terms = search_terms(_expanded_question(question), max_terms=120)
        rows = KnowledgeRepository.search(terms, limit=min(current_app.config["QA_TOP_K"], 5))
        contexts = []
        for row in rows:
            context = dict(row)
            faq = _faq_payload(context["content"])
            context["excerpt"] = faq["answer"] if faq else _excerpt(context["content"], terms)
            context["faq_question"] = faq["question"] if faq else None
            if faq:
                context["score"] = _faq_match_score(question, faq["question"], terms)
            contexts.append(context)
        contexts.sort(key=lambda item: float(item["score"]), reverse=True)
        contexts = [
            context for context in contexts
            if float(context["score"]) >= current_app.config["QA_MIN_MATCH_SCORE"]
        ]
        if contexts and contexts[0].get("faq_question"):
            contexts = contexts[:1]
        else:
            contexts = contexts[:3]
        return {
            "found": bool(contexts),
            "matches": [
                {
                    "title": context["title"],
                    "answer": context["excerpt"],
                    "score": round(float(context["score"]), 4),
                }
                for context in contexts
            ],
            "_reliable": bool(contexts),
            "_contexts": contexts,
        }

    @staticmethod
    def lookup_order_refund(arguments, account_id):
        _require_permissions(account_id, "order.read", "refund.read")
        reference = str(arguments.get("reference") or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{2,63}", reference):
            raise AgentToolError("订单号或退款号格式无效")
        rows = db.session.execute(text("""
            SELECT order_row.order_no, order_row.status AS order_status,
                   order_row.total_amount, order_row.paid_amount,
                   order_row.refunded_amount, order_row.ordered_at,
                   order_row.paid_at,
                   refund.refund_no, refund.status AS refund_status,
                   refund.amount AS refund_amount,
                   refund.created_at AS refund_created_at,
                   refund.reviewed_at, refund.refunded_at
            FROM biz.sales_order order_row
            LEFT JOIN biz.refund refund ON refund.order_id = order_row.order_id
            WHERE upper(order_row.order_no) = :reference
               OR upper(COALESCE(refund.refund_no, '')) = :reference
            ORDER BY refund.created_at DESC NULLS LAST, refund.refund_id DESC
            LIMIT 20
        """), {"reference": reference}).mappings().all()
        if not rows:
            return {
                "found": False,
                "reference": reference,
                "message": "未找到对应的订单或退款记录",
                "_reliable": False,
            }
        first = rows[0]
        refunds = []
        for row in rows:
            if row["refund_no"]:
                refunds.append({
                    "refund_no": row["refund_no"],
                    "status": row["refund_status"],
                    "amount": _plain(row["refund_amount"]),
                    "created_at": _plain(row["refund_created_at"]),
                    "reviewed_at": _plain(row["reviewed_at"]),
                    "refunded_at": _plain(row["refunded_at"]),
                })
        return {
            "found": True,
            "order": {
                "order_no": first["order_no"],
                "status": first["order_status"],
                "total_amount": _plain(first["total_amount"]),
                "paid_amount": _plain(first["paid_amount"]),
                "refunded_amount": _plain(first["refunded_amount"]),
                "ordered_at": _plain(first["ordered_at"]),
                "paid_at": _plain(first["paid_at"]),
            },
            "refunds": refunds,
            "_reliable": True,
        }

    @staticmethod
    def create_support_ticket(arguments, account_id):
        _require_permissions(account_id, "qa.read")
        reason = str(arguments.get("reason") or "需要人工处理").strip()[:200]
        return {
            "accepted": True,
            "message": "将在本轮回答结束时创建人工工单",
            "reason": reason,
            "_handoff": True,
            "_reliable": False,
        }
