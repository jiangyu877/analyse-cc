import json

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.repositories.auth import AccountRepository
from app.security.authorization import permission_required
from app.utils import audit, hash_password, validate_password


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.get("/accounts")
@permission_required("system.manage")
def accounts():
    actor_is_super_admin = AccountRepository.is_super_admin(session["user_id"])
    return render_template(
        "admin/users.html",
        accounts=AccountRepository.list_accounts(),
        roles=AccountRepository.list_roles(session["user_id"]),
        actor_is_super_admin=actor_is_super_admin,
    )


@admin_bp.post("/accounts")
@permission_required("system.manage")
def create_account():
    username = request.form.get("username", "").strip().lower()
    full_name = request.form.get("full_name", "").strip()
    password = request.form.get("password", "")
    role_codes = request.form.getlist("role_codes")
    password_error = validate_password(password)
    if not username or not role_codes or password_error:
        flash(password_error or "用户名和角色不能为空", "danger")
        return redirect(url_for("admin.accounts"))
    try:
        with db.session.begin():
            account_id = AccountRepository.create(username, hash_password(password), full_name)
            AccountRepository.set_roles(account_id, role_codes, session["user_id"])
            audit(
                "account.create",
                "account",
                account_id,
                json.dumps({"username": username, "roles": role_codes}, ensure_ascii=False),
            )
        flash("账号创建成功", "success")
    except IntegrityError:
        db.session.rollback()
        flash("账号创建失败：用户名已存在或账号数据冲突", "danger")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("admin.accounts"))


@admin_bp.post("/accounts/<int:account_id>/toggle")
@permission_required("system.manage")
def toggle_account(account_id):
    if account_id == session["user_id"]:
        flash("不能停用当前登录账号", "danger")
        return redirect(url_for("admin.accounts"))
    with db.session.begin():
        is_active = AccountRepository.toggle(account_id, session["user_id"])
        if is_active is None:
            flash("无权管理该账号，或账号不存在", "danger")
            return redirect(url_for("admin.accounts"))
        audit("account.toggle", "account", account_id, json.dumps({"is_active": is_active}))
    flash("账号状态已更新", "success")
    return redirect(url_for("admin.accounts"))


@admin_bp.post("/accounts/<int:account_id>/password")
@permission_required("system.manage")
def reset_password(account_id):
    password = request.form.get("password", "")
    password_error = validate_password(password)
    if password_error:
        flash(password_error, "danger")
        return redirect(url_for("admin.accounts"))
    with db.session.begin():
        if AccountRepository.update_password(
            account_id, hash_password(password), session["user_id"]
        ) != 1:
            flash("无权管理该账号，或账号不存在", "danger")
            return redirect(url_for("admin.accounts"))
        audit("account.password_reset", "account", account_id)
    flash("密码已重置", "success")
    return redirect(url_for("admin.accounts"))


@admin_bp.get("/logs")
@permission_required("audit.read")
def logs():
    return render_template(
        "admin/logs.html",
        login_logs=AccountRepository.login_logs(),
        operation_logs=AccountRepository.operation_logs(),
    )
