from sqlalchemy import text

from app.extensions import db


class KnowledgeRepository:
    @staticmethod
    def list_bases():
        return db.session.execute(text("""
            SELECT base.knowledge_base_id, base.base_code, base.name,
                   base.description, base.status, base.created_at,
                   COUNT(DISTINCT document.document_id)::int AS document_count,
                   COUNT(DISTINCT document.document_id) FILTER (
                       WHERE document.is_published
                   )::int AS published_count
            FROM kb.knowledge_base base
            LEFT JOIN kb.document document
              ON document.knowledge_base_id = base.knowledge_base_id
            GROUP BY base.knowledge_base_id
            ORDER BY base.created_at, base.knowledge_base_id
        """)).mappings().all()

    @staticmethod
    def list_documents():
        return db.session.execute(text("""
            SELECT document.document_id, document.knowledge_base_id,
                   base.name AS base_name, document.title, document.version,
                   document.original_name, document.extension, document.file_size,
                   document.status, document.is_published, document.chunk_count,
                   document.error_message, document.published_at,
                   document.created_at, document.updated_at
            FROM kb.document document
            JOIN kb.knowledge_base base
              ON base.knowledge_base_id = document.knowledge_base_id
            ORDER BY document.created_at DESC, document.document_id DESC
            LIMIT 200
        """)).mappings().all()

    @staticmethod
    def create_base(base_code, name, description, created_by):
        return db.session.execute(text("""
            INSERT INTO kb.knowledge_base
                (base_code, name, description, created_by)
            VALUES (:base_code, :name, :description, :created_by)
            RETURNING knowledge_base_id
        """), locals()).scalar_one()

    @staticmethod
    def next_version(knowledge_base_id, title):
        return db.session.execute(text("""
            SELECT COALESCE(MAX(version), 0) + 1
            FROM kb.document
            WHERE knowledge_base_id = :knowledge_base_id AND title = :title
        """), {
            "knowledge_base_id": knowledge_base_id,
            "title": title,
        }).scalar_one()

    @staticmethod
    def create_document(values):
        return db.session.execute(text("""
            INSERT INTO kb.document
                (knowledge_base_id, title, version, original_name, storage_name,
                 extension, mime_type, file_size, content_hash, created_by)
            VALUES
                (:knowledge_base_id, :title, :version, :original_name, :storage_name,
                 :extension, :mime_type, :file_size, :content_hash, :created_by)
            RETURNING document_id
        """), values).scalar_one()

    @staticmethod
    def lock_document(document_id):
        return db.session.execute(text("""
            SELECT * FROM kb.document
            WHERE document_id = :document_id
            FOR UPDATE
        """), {"document_id": document_id}).mappings().first()

    @staticmethod
    def get_document(document_id):
        return db.session.execute(text("""
            SELECT * FROM kb.document WHERE document_id = :document_id
        """), {"document_id": document_id}).mappings().first()

    @staticmethod
    def lock_publish_group(knowledge_base_id, title):
        key = f"knowledge-publish:{knowledge_base_id}:{title}"
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        ).scalar_one()

    @staticmethod
    def replace_chunks(document_id, chunks):
        db.session.execute(
            text("DELETE FROM kb.document_chunk WHERE document_id = :document_id"),
            {"document_id": document_id},
        )
        db.session.execute(text("""
            INSERT INTO kb.document_chunk
                (document_id, chunk_no, content, char_count, content_hash, search_terms)
            VALUES
                (:document_id, :chunk_no, :content, :char_count, :content_hash, :search_terms)
        """), [
            {"document_id": document_id, **chunk}
            for chunk in chunks
        ])
        db.session.execute(text("""
            UPDATE kb.document
            SET status = 'ready', chunk_count = :chunk_count,
                error_message = NULL, updated_at = now()
            WHERE document_id = :document_id
        """), {"document_id": document_id, "chunk_count": len(chunks)})

    @staticmethod
    def mark_failed(document_id, error_message):
        db.session.execute(text("""
            UPDATE kb.document
            SET status = 'failed', is_published = FALSE,
                error_message = :error_message, updated_at = now()
            WHERE document_id = :document_id
        """), {
            "document_id": document_id,
            "error_message": error_message[:2000],
        })

    @staticmethod
    def publish(document_id, published_by):
        return db.session.execute(text("""
            UPDATE kb.document
            SET is_published = TRUE, published_by = :published_by,
                published_at = now(), updated_at = now()
            WHERE document_id = :document_id
              AND status = 'ready' AND chunk_count > 0
            RETURNING document_id
        """), {
            "document_id": document_id,
            "published_by": published_by,
        }).scalar_one_or_none()

    @staticmethod
    def unpublish_other_versions(document_id):
        db.session.execute(text("""
            UPDATE kb.document sibling
            SET is_published = FALSE, updated_at = now()
            FROM kb.document current
            WHERE current.document_id = :document_id
              AND sibling.knowledge_base_id = current.knowledge_base_id
              AND sibling.title = current.title
              AND sibling.document_id <> current.document_id
              AND sibling.is_published
        """), {"document_id": document_id})

    @staticmethod
    def disable(document_id):
        return db.session.execute(text("""
            UPDATE kb.document
            SET status = 'disabled', is_published = FALSE, updated_at = now()
            WHERE document_id = :document_id
            RETURNING document_id
        """), {"document_id": document_id}).scalar_one_or_none()

    @staticmethod
    def search(query_terms, limit=5):
        if not query_terms:
            return []
        return db.session.execute(text("""
            WITH candidates AS (
                SELECT chunk.chunk_id, chunk.document_id, chunk.chunk_no,
                       chunk.content, document.title, document.version,
                       document.published_at, base.name AS base_name,
                       (
                           SELECT COUNT(*)::int
                           FROM unnest(chunk.search_terms) AS term
                           WHERE term = ANY(CAST(:query_terms AS text[]))
                       ) AS match_count
                FROM kb.document_chunk chunk
                JOIN kb.document document ON document.document_id = chunk.document_id
                JOIN kb.knowledge_base base
                  ON base.knowledge_base_id = document.knowledge_base_id
                WHERE document.status = 'ready' AND document.is_published
                  AND base.status = 'active'
                  AND chunk.search_terms && CAST(:query_terms AS text[])
            )
            SELECT *, LEAST(1.0, match_count::double precision / :term_count) AS score
            FROM candidates
            WHERE match_count > 0
            ORDER BY match_count DESC, published_at DESC, document_id DESC, chunk_no
            LIMIT :limit
        """), {
            "query_terms": list(query_terms),
            "term_count": max(1, len(query_terms)),
            "limit": min(max(int(limit), 1), 20),
        }).mappings().all()
