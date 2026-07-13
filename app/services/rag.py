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


class RagService:
    @staticmethod
    def ask(session_id, question, account_id):
        started = time.perf_counter()
        question = normalize_text(question or "")
        if len(question) < 2:
            raise QAError("问题至少需要 2 个字符")
        if len(question) > 1000:
            raise QAError("问题不能超过 1000 个字符")
        terms = search_terms(question, max_terms=120)
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
            rows = KnowledgeRepository.search(
                terms, limit=current_app.config["QA_TOP_K"]
            )
            contexts = []
            for row in rows:
                context = dict(row)
                context["excerpt"] = _excerpt(context["content"], terms)
                contexts.append(context)

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
