import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv


load_dotenv()
ROOT = Path(__file__).resolve().parents[1]


def _database_url():
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit
    user = quote_plus(os.environ.get("DB_USER", "postgres"))
    password = quote_plus(os.environ.get("DB_PASSWORD", ""))
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ.get("DB_NAME", "consumer_analysis")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-only-change-me"
    DEBUG = os.environ.get("FLASK_DEBUG", os.environ.get("DEBUG", "false")).lower() == "true"
    TESTING = False
    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = int(os.environ.get("PORT", "5000"))
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
    WTF_CSRF_TIME_LIMIT = 3600
    SQL_QUERY_TIMEOUT_MS = int(os.environ.get("SQL_QUERY_TIMEOUT_MS", "3000"))
    SQL_QUERY_MAX_ROWS = min(int(os.environ.get("SQL_QUERY_MAX_ROWS", "200")), 1000)
    TRUST_PROXY = os.environ.get("TRUST_PROXY", "false").lower() == "true"
    GRADIO_PUBLIC_URL = os.environ.get("GRADIO_PUBLIC_URL", "").strip().rstrip("/")
    MAX_UPLOAD_MB = min(max(int(os.environ.get("MAX_UPLOAD_MB", "8")), 1), 20)
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024
    KNOWLEDGE_UPLOAD_DIR = os.environ.get(
        "KNOWLEDGE_UPLOAD_DIR", str(ROOT / "instance" / "knowledge_uploads")
    )
    QA_TOP_K = min(max(int(os.environ.get("QA_TOP_K", "5")), 1), 10)
    QA_MIN_MATCH_SCORE = min(max(float(os.environ.get("QA_MIN_MATCH_SCORE", "0.2")), 0), 1)

    @classmethod
    def validate(cls):
        environment = os.environ.get("FLASK_ENV", "development").lower()
        if environment == "production" and len(cls.SECRET_KEY) < 32:
            raise RuntimeError("生产环境必须通过 SECRET_KEY 配置至少 32 位随机密钥")
        if environment == "production" and not os.environ.get("DB_PASSWORD") and not os.environ.get("DATABASE_URL"):
            raise RuntimeError("生产环境必须配置 DATABASE_URL 或 DB_PASSWORD")


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL", Config.SQLALCHEMY_DATABASE_URI)
