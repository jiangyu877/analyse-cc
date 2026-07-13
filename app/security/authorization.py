from functools import wraps

from flask import abort, redirect, request, session, url_for

from app.repositories.auth import AccountRepository


def account_permissions(account_id):
    return AccountRepository.permissions(account_id)


def permission_required(permission_code):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            account_id = session.get("user_id")
            if account_id is None:
                return redirect(url_for("auth.login", next=request.path))
            if permission_code not in account_permissions(account_id):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
