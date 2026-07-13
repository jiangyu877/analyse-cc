from functools import wraps

import bcrypt
from flask import redirect, request, session, url_for
from sqlalchemy import text

from app.extensions import db


def hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password, password_hash):
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (TypeError, ValueError):
        return False


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def audit(action, entity_type=None, entity_id=None, details=None):
    db.session.execute(text("""
        INSERT INTO audit.operation_log
            (operator_id, action, entity_type, entity_id, details, ip_address)
        VALUES (:operator_id, :action, :entity_type, :entity_id, CAST(:details AS jsonb), :ip)
    """), {
        "operator_id": session.get("user_id"),
        "action": action,
        "entity_type": entity_type,
        "entity_id": str(entity_id) if entity_id is not None else None,
        "details": details or "{}",
        "ip": request.remote_addr,
    })


def validate_password(password):
    if len(password) < 10:
        return "密码至少需要 10 位"
    if not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        return "密码必须同时包含字母和数字"
    return None
