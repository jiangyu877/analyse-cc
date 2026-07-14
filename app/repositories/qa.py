from sqlalchemy import text

from app.extensions import db


class QARepository:
    @staticmethod
    def create_session(account_id, title):
        return db.session.execute(text("""
            INSERT INTO qa.qa_session (account_id, title)
            VALUES (:account_id, :title)
            RETURNING session_id
        """), {"account_id": account_id, "title": title[:200]}).scalar_one()

    @staticmethod
    def get_session(session_id, account_id=None):
        return db.session.execute(text("""
            SELECT session_id, account_id, title, status, created_at, updated_at
            FROM qa.qa_session
            WHERE session_id = :session_id
              AND (:account_id IS NULL OR account_id = :account_id)
        """), {
            "session_id": session_id,
            "account_id": account_id,
        }).mappings().first()

    @staticmethod
    def list_sessions(account_id, limit=50):
        return db.session.execute(text("""
            SELECT session.session_id, session.title, session.status,
                   session.created_at, session.updated_at,
                   COUNT(DISTINCT message.message_id)::int AS message_count,
                   COUNT(DISTINCT ticket.ticket_id) FILTER (
                       WHERE ticket.status IN ('pending', 'assigned')
                   )::int AS open_ticket_count
            FROM qa.qa_session session
            LEFT JOIN qa.qa_message message ON message.session_id = session.session_id
            LEFT JOIN qa.qa_ticket ticket ON ticket.session_id = session.session_id
            WHERE session.account_id = :account_id
            GROUP BY session.session_id
            ORDER BY session.updated_at DESC, session.session_id DESC
            LIMIT :limit
        """), {"account_id": account_id, "limit": limit}).mappings().all()

    @staticmethod
    def touch_session(session_id, title=None):
        db.session.execute(text("""
            UPDATE qa.qa_session
            SET updated_at = now(),
                title = CASE WHEN :title IS NULL THEN title ELSE :title END
            WHERE session_id = :session_id
        """), {"session_id": session_id, "title": title[:200] if title else None})

    @staticmethod
    def create_message(
        session_id, account_id, role, content, status="success", confidence=None,
        provider_model=None, prompt_version=None, latency_ms=None,
        prompt_tokens=None, completion_tokens=None, error_code=None,
    ):
        return db.session.execute(text("""
            INSERT INTO qa.qa_message
                (session_id, account_id, message_role, message_status, content,
                 confidence, provider_model, prompt_version, latency_ms,
                 prompt_tokens, completion_tokens, error_code)
            VALUES
                (:session_id, :account_id, :role, :status, :content,
                 :confidence, :provider_model, :prompt_version, :latency_ms,
                 :prompt_tokens, :completion_tokens, :error_code)
            RETURNING message_id
        """), locals()).scalar_one()

    @staticmethod
    def add_retrievals(message_id, contexts):
        if not contexts:
            return
        db.session.execute(text("""
            INSERT INTO qa.qa_retrieval_log
                (message_id, chunk_id, rank, distance, excerpt)
            VALUES (:message_id, :chunk_id, :rank, :distance, :excerpt)
        """), [
            {
                "message_id": message_id,
                "chunk_id": context["chunk_id"],
                "rank": rank,
                "distance": max(0.0, 1.0 - float(context["score"])),
                "excerpt": context["excerpt"][:1000],
            }
            for rank, context in enumerate(contexts, start=1)
        ])

    @staticmethod
    def list_messages(session_id):
        return db.session.execute(text("""
            SELECT message.message_id, message.message_role, message.message_status,
                   message.content, message.confidence, message.error_code,
                   message.provider_model, message.prompt_version,
                   message.created_at,
                   COALESCE(
                       jsonb_agg(
                           jsonb_build_object(
                               'document_id', document.document_id,
                               'chunk_id', chunk.chunk_id,
                               'title', document.title,
                               'version', document.version,
                               'rank', retrieval.rank,
                               'excerpt', retrieval.excerpt
                           ) ORDER BY retrieval.rank
                       ) FILTER (WHERE retrieval.retrieval_log_id IS NOT NULL),
                       '[]'::jsonb
                   ) AS citations,
                   feedback.is_helpful AS current_feedback
            FROM qa.qa_message message
            LEFT JOIN qa.qa_retrieval_log retrieval
              ON retrieval.message_id = message.message_id
            LEFT JOIN kb.document_chunk chunk ON chunk.chunk_id = retrieval.chunk_id
            LEFT JOIN kb.document document ON document.document_id = chunk.document_id
            LEFT JOIN qa.qa_feedback feedback ON feedback.message_id = message.message_id
            WHERE message.session_id = :session_id
            GROUP BY message.message_id, feedback.is_helpful
            ORDER BY message.created_at, message.message_id
        """), {"session_id": session_id}).mappings().all()

    @staticmethod
    def create_ticket(session_id, source_message_id, fallback_message_id, reason_code):
        return db.session.execute(text("""
            INSERT INTO qa.qa_ticket
                (session_id, source_message_id, fallback_message_id, reason_code)
            VALUES (:session_id, :source_message_id, :fallback_message_id, :reason_code)
            ON CONFLICT (source_message_id) DO UPDATE
            SET fallback_message_id = EXCLUDED.fallback_message_id,
                reason_code = EXCLUDED.reason_code,
                updated_at = now()
            RETURNING ticket_id
        """), locals()).scalar_one()

    @staticmethod
    def upsert_feedback(message_id, account_id, is_helpful, comment):
        return db.session.execute(text("""
            INSERT INTO qa.qa_feedback (message_id, account_id, is_helpful, comment)
            SELECT message.message_id, :account_id, :is_helpful, :comment
            FROM qa.qa_message message
            JOIN qa.qa_session session ON session.session_id = message.session_id
            WHERE message.message_id = :message_id
              AND message.message_role = 'assistant'
              AND session.account_id = :account_id
            ON CONFLICT (message_id, account_id) DO UPDATE
            SET is_helpful = EXCLUDED.is_helpful,
                comment = EXCLUDED.comment, updated_at = now()
            RETURNING feedback_id
        """), {
            "message_id": message_id,
            "account_id": account_id,
            "is_helpful": is_helpful,
            "comment": comment[:500],
        }).scalar_one_or_none()

    @staticmethod
    def list_tickets(limit=200):
        return db.session.execute(text("""
            SELECT ticket.ticket_id, ticket.session_id, ticket.status,
                   ticket.priority, ticket.reason_code, ticket.response_text,
                   ticket.resolution_note, ticket.created_at, ticket.assigned_at,
                   ticket.resolved_at, source.content AS question,
                   fallback.content AS fallback_answer,
                   requester.username AS requester_name,
                   assignee.username AS assignee_name
            FROM qa.qa_ticket ticket
            JOIN qa.qa_session session ON session.session_id = ticket.session_id
            JOIN qa.qa_message source ON source.message_id = ticket.source_message_id
            LEFT JOIN qa.qa_message fallback
              ON fallback.message_id = ticket.fallback_message_id
            LEFT JOIN auth.account requester ON requester.account_id = session.account_id
            LEFT JOIN auth.account assignee ON assignee.account_id = ticket.assigned_to
            ORDER BY CASE ticket.status
                WHEN 'pending' THEN 1 WHEN 'assigned' THEN 2 ELSE 3 END,
                ticket.created_at DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()

    @staticmethod
    def assign_ticket(ticket_id, operator_id):
        return db.session.execute(text("""
            UPDATE qa.qa_ticket
            SET status = 'assigned', assigned_to = :operator_id,
                assigned_at = COALESCE(assigned_at, now()), updated_at = now()
            WHERE ticket_id = :ticket_id AND status IN ('pending', 'assigned')
            RETURNING ticket_id
        """), {"ticket_id": ticket_id, "operator_id": operator_id}).scalar_one_or_none()

    @staticmethod
    def resolve_ticket(ticket_id, operator_id, response_text, resolution_note):
        return db.session.execute(text("""
            UPDATE qa.qa_ticket
            SET status = 'resolved', assigned_to = :operator_id,
                assigned_at = COALESCE(assigned_at, now()), resolved_at = now(),
                response_text = :response_text,
                resolution_note = :resolution_note, updated_at = now()
            WHERE ticket_id = :ticket_id AND status IN ('pending', 'assigned')
            RETURNING ticket_id, session_id
        """), {
            "ticket_id": ticket_id,
            "operator_id": operator_id,
            "response_text": response_text,
            "resolution_note": resolution_note[:1000],
        }).mappings().first()
