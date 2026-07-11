from flask import Blueprint, jsonify
from sqlalchemy import text

from app.extensions import db

system_bp = Blueprint("system", __name__)


@system_bp.get("/healthz")
def health():
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify(status="ok", database="up"), 200
    except Exception:
        db.session.rollback()
        return jsonify(status="degraded", database="down"), 503

