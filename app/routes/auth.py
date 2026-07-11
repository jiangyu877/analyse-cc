import time
from collections import defaultdict, deque

from flask import Blueprint, redirect, render_template, request, session, url_for
from sqlalchemy import text

from app.extensions import db
from app.utils import check_password

auth_bp = Blueprint("auth", __name__)
_attempts = defaultdict(deque)


def _rate_limited(key, limit=5, window=300):
    now = time.monotonic()
    attempts = _attempts[key]
    while attempts and now - attempts[0] > window:
        attempts.popleft()
    return len(attempts) >= limit


def _write_login_log(user_id, username, status, reason=None):
    db.session.execute(text("""
        INSERT INTO audit.login_log
            (account_id, username, status, fail_reason, ip_address, user_agent)
        VALUES (:account_id, :username, :status, :reason, :ip, :agent)
    """), {
        "account_id": user_id,
        "username": username,
        "status": status,
        "reason": reason,
        "ip": request.remote_addr,
        "agent": request.user_agent.string[:255],
    })
    db.session.commit()


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    key = f"{request.remote_addr}:{username.lower()}"
    if _rate_limited(key):
        return render_template("login.html", error="登录失败次数过多，请 5 分钟后重试"), 429

    user = db.session.execute(text("""
        SELECT account_id, username, password_hash, full_name, role, is_active
        FROM auth.account WHERE username = :username
    """), {"username": username}).mappings().first()

    if not user or not check_password(password, user["password_hash"]):
        _attempts[key].append(time.monotonic())
        _write_login_log(user["account_id"] if user else None, username, "failed", "invalid_credentials")
        return render_template("login.html", error="用户名或密码错误"), 401
    if not user["is_active"]:
        _write_login_log(user["account_id"], username, "failed", "disabled")
        return render_template("login.html", error="账号已停用"), 403

    _attempts.pop(key, None)
    session.clear()
    session.update(
        user_id=user["account_id"], username=user["username"],
        full_name=user["full_name"], role=user["role"],
    )
    db.session.execute(text("UPDATE auth.account SET last_login_at = now() WHERE account_id = :id"), {"id": user["account_id"]})
    db.session.commit()
    _write_login_log(user["account_id"], username, "success")
    next_url = request.args.get("next", "")
    return redirect(next_url if next_url.startswith("/") and not next_url.startswith("//") else url_for("main.dashboard"))


@auth_bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
