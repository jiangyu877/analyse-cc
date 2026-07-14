import os
from uuid import uuid4

import psycopg2
import pytest
from psycopg2 import sql
from sqlalchemy.engine import make_url


BLOCKED_TARGET_QUERY_KEYS = frozenset(
    {"dbname", "database", "host", "hostaddr", "port", "user", "password", "service"}
)


def _test_database_url():
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    if os.environ.get("ALLOW_LOCAL_DB_TESTS", "").lower() == "true":
        from app.config import Config

        return Config.SQLALCHEMY_DATABASE_URI
    return None


def _connection_kwargs(url, database_name=None):
    blocked = BLOCKED_TARGET_QUERY_KEYS.intersection(url.query)
    if blocked:
        raise ValueError("database target cannot be overridden in query parameters")
    kwargs = {
        key: value[-1] if isinstance(value, tuple) else value
        for key, value in url.query.items()
        if key not in BLOCKED_TARGET_QUERY_KEYS
    }
    kwargs.update({
        "host": url.host,
        "port": url.port or 5432,
        "user": url.username,
        "password": url.password,
        "dbname": database_name or url.database or "postgres",
    })
    return {key: value for key, value in kwargs.items() if value is not None}


@pytest.fixture
def isolated_database():
    raw_url = _test_database_url()
    if not raw_url:
        pytest.skip("set TEST_DATABASE_URL or ALLOW_LOCAL_DB_TESTS=true")

    url = make_url(raw_url)
    database_name = f"consumer_release_d_{uuid4().hex}"
    admin = psycopg2.connect(**_connection_kwargs(url))
    admin.autocommit = True
    database = None
    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        database = psycopg2.connect(**_connection_kwargs(url, database_name))
        yield database
    finally:
        if database is not None:
            database.close()
        try:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (database_name,),
                )
                cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))
        finally:
            admin.close()


@pytest.fixture
def isolated_app(isolated_database):
    from app import create_app
    from app.config import TestConfig

    raw_url = _test_database_url()
    url = make_url(raw_url)
    with isolated_database.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        database_name = cursor.fetchone()[0]

    class IntegrationConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = url.set(database=database_name).render_as_string(
            hide_password=False
        )
        SECRET_KEY = "integration-test-secret"

    app = create_app(IntegrationConfig)
    yield app

    with app.app_context():
        from app.extensions import db

        db.session.remove()


@pytest.fixture
def initialized_database(isolated_database):
    from scripts.init_db import ROOT as project_root, apply_migrations

    with isolated_database.cursor() as cursor:
        for filename in ("v2_schema.sql", "v2_seed.sql", "demo_commerce_v2.sql"):
            cursor.execute((project_root / "database" / filename).read_text(encoding="utf-8"))
    isolated_database.commit()
    apply_migrations(isolated_database)
    return isolated_database


@pytest.fixture
def initialized_app(initialized_database):
    from app import create_app
    from app.config import TestConfig

    initialized_database.rollback()
    with initialized_database.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        database_name = cursor.fetchone()[0]
    url = make_url(_test_database_url()).set(database=database_name)

    class IntegrationConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = url.render_as_string(hide_password=False)
        SECRET_KEY = "integration-test-secret"

    app = create_app(IntegrationConfig)
    yield app

    with app.app_context():
        from app.extensions import db

        db.session.remove()
