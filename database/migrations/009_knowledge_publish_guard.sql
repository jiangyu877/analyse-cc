WITH ranked AS (
    SELECT document_id,
           row_number() OVER (
               PARTITION BY knowledge_base_id, title
               ORDER BY published_at DESC NULLS LAST, document_id DESC
           ) AS position
    FROM kb.document
    WHERE is_published
)
UPDATE kb.document document
SET is_published = FALSE, updated_at = now()
FROM ranked
WHERE ranked.document_id = document.document_id
  AND ranked.position > 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_document_published_title
    ON kb.document(knowledge_base_id, title)
    WHERE is_published;
