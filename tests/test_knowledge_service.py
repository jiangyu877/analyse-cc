from io import BytesIO
import zipfile

import pytest

from app.services.knowledge import (
    KnowledgeError,
    parse_document,
    search_terms,
    split_text,
)


def test_chinese_search_terms_and_chunks_are_deterministic():
    terms = search_terms("退款申请需要在支付后七天内提交")
    assert {"退款", "退款申", "申请", "申请需"} <= set(terms)

    text = "退款规则。" * 180
    chunks = split_text(text)
    assert len(chunks) >= 2
    assert all(1 <= len(chunk) <= 600 for chunk in chunks)
    assert chunks == split_text(text)


def test_text_parser_rejects_executable_content():
    with pytest.raises(KnowledgeError, match="不匹配"):
        parse_document(".txt", b"MZ" + b"\x00" * 30)


def test_docx_parser_reads_document_xml():
    document_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>退款需要审核</w:t></w:r></w:p></w:body>
    </w:document>""".encode("utf-8")
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    assert parse_document(".docx", payload.getvalue()) == "退款需要审核"


def test_malformed_docx_is_rejected():
    with pytest.raises(KnowledgeError, match="结构无效"):
        parse_document(".docx", b"PK-not-a-zip")
