import hashlib
import mimetypes
import re
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from flask import current_app

from app.extensions import db
from app.repositories.knowledge import KnowledgeRepository
from app.utils import audit


ALLOWED_EXTENSIONS = {".txt", ".md", ".docx"}
WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class KnowledgeError(ValueError):
    pass


def normalize_text(value):
    value = value.replace("\u3000", " ").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def search_terms(value, max_terms=500):
    normalized = normalize_text(value).lower()
    terms = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,63}", normalized))
    chinese_runs = re.findall(r"[\u3400-\u9fff]+", normalized)
    for run in chinese_runs:
        if len(run) == 1:
            terms.add(run)
        for size in (2, 3):
            terms.update(run[index:index + size] for index in range(len(run) - size + 1))
    return sorted(terms, key=lambda item: (len(item), item))[:max_terms]


def split_text(value, target_size=600, overlap=80):
    value = normalize_text(value)
    if not value:
        return []
    chunks = []
    start = 0
    while start < len(value):
        end = min(start + target_size, len(value))
        if end < len(value):
            boundary = max(
                value.rfind(marker, start + 400, end)
                for marker in ("。", "！", "？", "\n", ".", "!", "?")
            )
            if boundary > start:
                end = boundary + 1
        chunk = value[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(value):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _parse_docx(data):
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            document_xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise KnowledgeError("DOCX 文件结构无效") from exc
    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        raise KnowledgeError("DOCX 文档内容损坏") from exc
    paragraphs = []
    for paragraph in root.iter(f"{WORD_NAMESPACE}p"):
        text_parts = [node.text or "" for node in paragraph.iter(f"{WORD_NAMESPACE}t")]
        if text_parts:
            paragraphs.append("".join(text_parts))
    return "\n".join(paragraphs)


def parse_document(extension, data):
    if extension == ".docx":
        return normalize_text(_parse_docx(data))
    if b"\x00" in data or data.startswith((b"MZ", b"\x7fELF")):
        raise KnowledgeError("文件内容与文本扩展名不匹配")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return normalize_text(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    raise KnowledgeError("文本文件必须使用 UTF-8 或 GB18030 编码")


class KnowledgeService:
    @staticmethod
    def create_base(base_code, name, description, operator_id):
        base_code = (base_code or "").strip().lower()
        name = (name or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", base_code):
            raise KnowledgeError("知识库编码需为 2-64 位小写字母、数字、下划线或连字符")
        if not name:
            raise KnowledgeError("知识库名称不能为空")
        with db.session.begin():
            knowledge_base_id = KnowledgeRepository.create_base(
                base_code, name[:160], (description or "").strip()[:500], operator_id
            )
            audit("knowledge_base.create", "knowledge_base", knowledge_base_id)
        return knowledge_base_id

    @staticmethod
    def ingest(knowledge_base_id, title, filename, data, operator_id):
        title = (title or "").strip()[:200]
        filename = (filename or "").strip()
        if not title:
            raise KnowledgeError("文档标题不能为空")
        if not filename or "/" in filename or "\\" in filename or Path(filename).name != filename:
            raise KnowledgeError("文件名不安全")
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise KnowledgeError("仅支持 TXT、Markdown 和 DOCX 文件")
        max_bytes = int(current_app.config["MAX_UPLOAD_MB"]) * 1024 * 1024
        if not data:
            raise KnowledgeError("不能上传空文件")
        if len(data) > max_bytes:
            raise KnowledgeError(f"文件不能超过 {current_app.config['MAX_UPLOAD_MB']} MB")
        if extension == ".docx" and not data.startswith(b"PK"):
            raise KnowledgeError("文件内容不是有效 DOCX")

        content = parse_document(extension, data)
        if not content:
            raise KnowledgeError("文档没有可检索文本")
        chunk_values = split_text(content)
        if not chunk_values:
            raise KnowledgeError("文档无法生成有效分块")
        chunks = []
        for index, chunk in enumerate(chunk_values, start=1):
            terms = search_terms(chunk)
            if not terms:
                continue
            chunks.append({
                "chunk_no": index,
                "content": chunk,
                "char_count": len(chunk),
                "content_hash": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                "search_terms": terms,
            })
        if not chunks:
            raise KnowledgeError("文档没有可建立索引的关键词")

        storage_dir = Path(current_app.config["KNOWLEDGE_UPLOAD_DIR"])
        storage_dir.mkdir(parents=True, exist_ok=True)
        storage_name = f"{uuid.uuid4().hex}{extension}"
        storage_path = storage_dir / storage_name
        storage_path.write_bytes(data)
        try:
            with db.session.begin():
                version = KnowledgeRepository.next_version(int(knowledge_base_id), title)
                document_id = KnowledgeRepository.create_document({
                    "knowledge_base_id": int(knowledge_base_id),
                    "title": title,
                    "version": version,
                    "original_name": filename[:255],
                    "storage_name": storage_name,
                    "extension": extension,
                    "mime_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
                    "file_size": len(data),
                    "content_hash": hashlib.sha256(data).hexdigest(),
                    "created_by": operator_id,
                })
                KnowledgeRepository.replace_chunks(document_id, chunks)
                audit(
                    "document.ingest", "document", document_id,
                    f'{{"chunk_count": {len(chunks)}}}',
                )
            return document_id
        except Exception:
            storage_path.unlink(missing_ok=True)
            db.session.rollback()
            raise

    @staticmethod
    def publish(document_id, operator_id):
        with db.session.begin():
            document = KnowledgeRepository.lock_document(int(document_id))
            if not document:
                raise KnowledgeError("文档不存在")
            if document["status"] != "ready" or document["chunk_count"] <= 0:
                raise KnowledgeError("文档尚未完成解析")
            if KnowledgeRepository.publish(document["document_id"], operator_id) is None:
                raise KnowledgeError("文档无法发布")
            audit("document.publish", "document", document["document_id"])
        return document["document_id"]

    @staticmethod
    def disable(document_id, operator_id):
        with db.session.begin():
            if KnowledgeRepository.disable(int(document_id)) is None:
                raise KnowledgeError("文档不存在")
            audit("document.disable", "document", document_id)
        return int(document_id)
