from pathlib import Path

from flask import Flask


ROOT = Path(__file__).resolve().parents[1]


def test_permission_decorator_enforces_login_and_permission(monkeypatch):
    path = ROOT / "app" / "security" / "authorization.py"
    assert path.exists(), "authorization module is required"

    from app.security import authorization

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.add_url_rule("/login", endpoint="auth.login", view_func=lambda: "login")

    @app.get("/protected")
    @authorization.permission_required("customer.write")
    def protected():
        return "ok"

    client = app.test_client()
    response = client.get("/protected")
    assert response.status_code == 302

    with client.session_transaction() as flask_session:
        flask_session["user_id"] = 7

    monkeypatch.setattr(authorization, "account_permissions", lambda account_id: frozenset())
    response = client.get("/protected")
    assert response.status_code == 403

    monkeypatch.setattr(
        authorization,
        "account_permissions",
        lambda account_id: frozenset({"customer.write"}),
    )
    response = client.get("/protected")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "ok"


def test_registered_routes_use_resource_permissions():
    route_files = (
        "customers.py", "products.py", "orders.py", "payments.py", "refunds.py",
        "algorithms.py", "custom_query.py", "imports.py", "main.py", "admin.py",
    )
    combined = "\n".join(
        (ROOT / "app" / "routes" / filename).read_text(encoding="utf-8")
        for filename in route_files
    )
    assert "role_required" not in combined
    for permission in (
        "customer.read", "customer.write", "product.read", "product.write",
        "order.read", "order.write", "payment.read", "payment.write",
        "refund.read", "refund.request", "model.read", "model.run",
        "refund.approve", "import.read", "sql.execute", "analysis.read",
        "system.manage", "audit.read",
    ):
        assert f'permission_required("{permission}")' in combined


def test_navigation_uses_permissions_instead_of_legacy_session_role():
    source = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "session.get('role') ==" not in source
    for permission in (
        "analysis.read", "customer.read", "product.read", "order.read",
        "payment.read", "refund.read", "model.read", "import.read",
        "sql.execute", "system.manage", "audit.read",
    ):
        assert f"can('{permission}')" in source
