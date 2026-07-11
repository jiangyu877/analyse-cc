"""Compatibility helpers backed by the application's SQLAlchemy session.

New V2 code uses repositories directly. These functions remain only for legacy
modules and intentionally share the configured server-side database session.
"""

from sqlalchemy import text

from app.extensions import db


def query(sql, params=None):
    result = db.session.execute(text(sql), params or {})
    return result.mappings().all()


def query_one(sql, params=None):
    result = db.session.execute(text(sql), params or {})
    return result.mappings().first()


def execute(sql, params=None):
    try:
        result = db.session.execute(text(sql), params or {})
        db.session.commit()
        return result.rowcount
    except Exception:
        db.session.rollback()
        raise


def init_app(app):
    """Kept as a no-op for older imports; db.init_app is called by create_app."""

