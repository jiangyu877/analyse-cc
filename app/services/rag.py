import json
import re
import time

from flask import current_app

from app.extensions import db
from app.repositories.knowledge import KnowledgeRepository
from app.repositories.qa import QARepository
from app.services.knowledge import normalize_text, search_terms
from app.utils import audit


class QAError(ValueError):
    pass


def _excerpt(content, terms, limit=520):
    sentences = [part.strip() for part in re.split(r"(?<=[。！？.!?])|\n+", content) if part.strip()]
    if not sentences:
        return content[:limit]
    ranked = sorted(
        sentences,
        key=lambda sentence: sum(term in sentence.lower() for term in terms),
        reverse=True,
    )
    selected = ranked[0]
    return selected[:limit]


def _faq_payload(content):
    try:
        payload = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("_type") != "faq-v1":
        return None
    if not isinstance(payload.get("question"), str) or not isinstance(payload.get("answer"), str):
        return None
    return payload


def _basic_answer(question):
    compact = re.sub(r"[\s，。！？,.!?]+", "", question).lower()
    if compact in {"你好", "您好", "嗨", "hello", "hi", "在吗"}:
        return "您好，我是智能客服。您可以询问订单、支付、退款、商品和售后规则。"
    if compact in {"你能做什么", "可以问什么", "怎么使用", "帮助", "使用帮助", "help"}:
        return "我可以根据已发布的知识库回答订单、支付、退款、商品和售后问题；无法可靠回答时会自动转交人工客服。"
    if compact in {"退款", "支付", "订单", "商品", "售后"}:
        return f"请补充您想了解的{compact}问题，例如办理条件、处理时效或当前进度。"
    if compact in {"谢谢", "感谢", "多谢", "thanks", "thankyou"}:
        return "不客气。您还可以继续询问订单、支付、退款或售后问题。"
    if compact in {"再见", "拜拜", "bye", "goodbye"}:
        return "再见，感谢您的咨询。"
    return None


def _wants_human(question):
    compact = re.sub(r"[\s，。！？,.!?]+", "", question).lower()
    negative_pattern = re.compile(
        r"(?:不需要|不要|无需|不用|不想|别)(?:转|联系|找|接入)?人工(?:客服)?"
    )
    negative_spans = [match.span() for match in negative_pattern.finditer(compact)]
    positive_patterns = (
        re.compile(r"(?:转|联系|找|接入)(?:一下)?人工(?:客服)?"),
        re.compile(
            r"(?:请|帮我|麻烦|能否|可以|我要|我想|我需要)"
            r"[^，。！？,.!?]{0,12}(?:人工客服|转人工|联系人工|找人工)"
        ),
    )
    positive_positions = []
    for pattern in positive_patterns:
        for match in pattern.finditer(compact):
            if any(match.start() < end and match.end() > start for start, end in negative_spans):
                continue
            suffix = compact[match.end():]
            if suffix.startswith(("后", "之后", "以后")):
                continue
            positive_positions.append(match.start())
    if compact in {"人工客服", "转人工", "转人工客服", "联系人工", "找人工客服"}:
        positive_positions.append(0)
    last_positive = max(positive_positions, default=-1)
    last_negative = max((start for start, _ in negative_spans), default=-1)
    return last_positive > last_negative


def _expanded_question(question):
    replacements = {
        "退费": "退款", "款项退回": "退款", "客服人员": "人工客服",
        "购买记录": "订单", "付款": "支付",
    }
    expanded = question
    for source, target in replacements.items():
        expanded = expanded.replace(source, target)
    return expanded


def _faq_match_score(question, faq_question, query_terms):
    compact_query = re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", _expanded_question(question).lower())
    compact_faq = re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", faq_question.lower())
    if compact_query == compact_faq:
        return 1.0
    if len(compact_query) < 4:
        return 0.0
    if compact_query in compact_faq or compact_faq in compact_query:
        return 0.9
    faq_terms = set(search_terms(faq_question, max_terms=120))
    query_term_set = set(query_terms)
    common = len(query_term_set & faq_terms)
    topic_markers = {
        "refund": ("退款", "退费", "退货"),
        "payment": ("支付", "付款", "扣款"),
        "order": ("订单", "购买记录"),
        "product": ("商品", "产品"),
    }
    intent_markers = {
        "timing": ("多久", "时效", "期限", "几天", "何时", "什么时候"),
        "failure": ("失败", "不成功", "报错", "异常", "不了", "不能", "无法"),
        "status": ("状态", "进度", "查询", "怎么查"),
        "apply": ("申请", "办理"),
        "eligibility": ("可以", "能否", "条件", "要求"),
        "cancel": ("取消", "撤销"),
        "arrival": ("到账", "退回", "原路返回"),
        "shipping": ("发货", "物流", "配送", "收货"),
        "complaint": ("投诉", "举报"),
    }

    def markers(value, groups):
        return {name for name, words in groups.items() if any(word in value for word in words)}

    query_topics = markers(compact_query, topic_markers)
    faq_topics = markers(compact_faq, topic_markers)
    if query_topics and not (query_topics & faq_topics):
        return 0.0
    query_intents = markers(compact_query, intent_markers)
    faq_intents = markers(compact_faq, intent_markers)
    for strict_intent in (
        "failure", "status", "timing", "cancel", "arrival", "shipping", "complaint",
    ):
        if strict_intent in query_intents and strict_intent not in faq_intents:
            return 0.0
    if query_intents and not (query_intents & faq_intents):
        return 0.0
    if not query_intents and len(compact_query) < 6:
        return 0.0
    lexical_score = common / max(1, len(query_term_set))
    intent_score = (
        len(query_intents & faq_intents) / len(query_intents)
        if query_intents else 0.0
    )
    return min(0.85, lexical_score * 0.4 + intent_score * 0.6)


class RagService:
    @staticmethod
    def ask(session_id, question, account_id):
        started = time.perf_counter()
        question = normalize_text(question or "")
        if len(question) < 2:
            raise QAError("问题至少需要 2 个字符")
        if len(question) > 1000:
            raise QAError("问题不能超过 1000 个字符")
        terms = search_terms(_expanded_question(question), max_terms=120)
        if not terms:
            raise QAError("问题缺少可检索关键词")

        with db.session.begin():
            if session_id:
                qa_session = QARepository.get_session(int(session_id), account_id)
                if not qa_session or qa_session["status"] != "active":
                    raise QAError("会话不存在或已关闭")
                session_id = qa_session["session_id"]
            else:
                session_id = QARepository.create_session(account_id, question[:40])

            user_message_id = QARepository.create_message(
                session_id, account_id, "user", question,
                prompt_tokens=len(question),
            )
            if _wants_human(question):
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                answer = "已为您创建人工客服工单，客服人员可以在工单列表中查看并处理。"
                assistant_id = QARepository.create_message(
                    session_id, None, "assistant", answer,
                    status="refusal", confidence=1.0, provider_model="rule-based-v1",
                    prompt_version="handoff-v1", latency_ms=elapsed_ms,
                    prompt_tokens=len(question), completion_tokens=len(answer),
                    error_code="USER_HANDOFF",
                )
                ticket_id = QARepository.create_ticket(
                    session_id, user_message_id, assistant_id, "user_requested"
                )
                QARepository.touch_session(session_id)
                audit(
                    "qa.user_handoff", "qa_session", session_id,
                    f'{{"ticket_id": {ticket_id}}}',
                )
                return {
                    "session_id": session_id, "message_id": assistant_id,
                    "answer": answer, "citations": [], "confidence": 1.0,
                    "ticket_id": ticket_id,
                }
            basic_answer = _basic_answer(question)
            if basic_answer:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                assistant_id = QARepository.create_message(
                    session_id, None, "assistant", basic_answer,
                    confidence=1.0, provider_model="rule-based-v1",
                    prompt_version="basic-qa-v1", latency_ms=elapsed_ms,
                    prompt_tokens=len(question), completion_tokens=len(basic_answer),
                )
                QARepository.touch_session(session_id)
                audit("qa.basic_answer", "qa_session", session_id)
                return {
                    "session_id": session_id, "message_id": assistant_id,
                    "answer": basic_answer, "citations": [],
                    "confidence": 1.0, "ticket_id": None,
                }
            rows = KnowledgeRepository.search(
                terms, limit=current_app.config["QA_TOP_K"]
            )
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

            top_score = float(contexts[0]["score"]) if contexts else 0.0
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if not contexts or top_score < current_app.config["QA_MIN_MATCH_SCORE"]:
                answer = "现有知识库中没有找到足够可靠的答案，已转交人工客服处理。"
                assistant_id = QARepository.create_message(
                    session_id, None, "assistant", answer,
                    status="refusal", confidence=top_score,
                    provider_model="keyword-search-v1", prompt_version="simple-qa-v1",
                    latency_ms=elapsed_ms, prompt_tokens=len(question),
                    completion_tokens=len(answer), error_code="LOW_MATCH",
                )
                QARepository.add_retrievals(assistant_id, contexts)
                ticket_id = QARepository.create_ticket(
                    session_id, user_message_id, assistant_id, "low_match"
                )
                QARepository.touch_session(session_id)
                audit(
                    "qa.fallback", "qa_session", session_id,
                    f'{{"ticket_id": {ticket_id}}}',
                )
                return {
                    "session_id": session_id,
                    "message_id": assistant_id,
                    "answer": answer,
                    "citations": [],
                    "confidence": top_score,
                    "ticket_id": ticket_id,
                }

            supporting_contexts = [
                context for context in contexts
                if float(context["score"]) >= current_app.config["QA_MIN_MATCH_SCORE"]
            ]
            if contexts[0].get("faq_question"):
                supporting_contexts = supporting_contexts[:1]
            contexts = supporting_contexts
            answer = contexts[0]["excerpt"]
            assistant_id = QARepository.create_message(
                session_id, None, "assistant", answer,
                confidence=top_score, provider_model="keyword-search-v1",
                prompt_version="simple-qa-v1", latency_ms=elapsed_ms,
                prompt_tokens=len(question), completion_tokens=len(answer),
            )
            QARepository.add_retrievals(assistant_id, contexts)
            QARepository.touch_session(session_id)
            audit(
                "qa.answer", "qa_session", session_id,
                f'{{"message_id": {assistant_id}, "source_count": {len(contexts)}}}',
            )
            return {
                "session_id": session_id,
                "message_id": assistant_id,
                "answer": answer,
                "citations": [
                    {
                        "document_id": context["document_id"],
                        "chunk_id": context["chunk_id"],
                        "title": context["title"],
                        "rank": rank,
                        "excerpt": context["excerpt"],
                    }
                    for rank, context in enumerate(contexts, start=1)
                ],
                "confidence": top_score,
                "ticket_id": None,
            }

    @staticmethod
    def feedback(message_id, account_id, is_helpful, comment=""):
        with db.session.begin():
            feedback_id = QARepository.upsert_feedback(
                int(message_id), account_id, bool(is_helpful), (comment or "").strip()
            )
            if feedback_id is None:
                raise QAError("消息不存在或不属于当前账号")
            audit("qa.feedback", "qa_message", message_id)
        return feedback_id

    @staticmethod
    def assign_ticket(ticket_id, operator_id):
        with db.session.begin():
            if QARepository.assign_ticket(int(ticket_id), operator_id) is None:
                raise QAError("工单不存在或已结束")
            audit("qa_ticket.assign", "qa_ticket", ticket_id)
        return int(ticket_id)

    @staticmethod
    def resolve_ticket(ticket_id, operator_id, response_text, resolution_note=""):
        response_text = normalize_text(response_text or "")
        if not response_text:
            raise QAError("人工回复不能为空")
        with db.session.begin():
            resolved = QARepository.resolve_ticket(
                int(ticket_id), operator_id, response_text, (resolution_note or "").strip()
            )
            if resolved is None:
                raise QAError("工单不存在或已结束")
            QARepository.create_message(
                resolved["session_id"], operator_id, "assistant", response_text,
                provider_model="human-support", prompt_version="manual-v1",
                completion_tokens=len(response_text),
            )
            QARepository.touch_session(resolved["session_id"])
            audit("qa_ticket.resolve", "qa_ticket", ticket_id)
        return int(ticket_id)
