import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from jinja2 import Environment, FileSystemLoader, select_autoescape


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = ROOT / "app" / "templates"

NAV_PERMISSIONS = {
    "customers": "customer.read",
    "products": "product.read",
    "orders": "order.read",
    "payments": "payment.read",
    "refunds": "refund.read",
    "reports": "analysis.read",
    "imports": "import.read",
    "models": "model.read",
    "sql": "sql.execute",
    "knowledge": "knowledge.read",
    "qa-chat": "qa.read",
    "qa-tickets": "qa.handle",
    "accounts": "system.manage",
    "audit": "audit.read",
}


def _workspace():
    return {
        "snapshot": {"snapshot_date": "2026-07-13"},
        "summary": {
            "customer_count": 124,
            "month_orders": 86,
            "month_net_amount": 32145.67,
            "low_stock_count": 2,
        },
        "trend_labels": ["2026-06", "2026-07"],
        "trend_values": [25000.0, 32145.67],
        "recent_orders": [],
        "alerts": {
            "low_stock": {
                "count": 2,
                "items": [{"sku": "SKU-LOW", "product_name": "低库存商品", "stock_qty": 3}],
            },
            "refunds": {
                "count": 3,
                "items": [{"refund_no": "RF-1001", "amount": 88.0, "created_at": "2026-07-13"}],
            },
            "models": {
                "count": 1,
                "items": [{"task_id": 17, "task_type": "churn", "status": "failed"}],
            },
            "knowledge": {
                "count": 4,
                "items": [{"document_id": 9, "title": "退款规则", "status": "ready"}],
            },
            "tickets": {
                "count": 5,
                "items": [{"ticket_id": 31, "question": "如何申请退款", "status": "pending"}],
            },
        },
    }


def _render_dashboard(permissions):
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_ROOT),
        autoescape=select_autoescape(("html",)),
    )
    environment.globals.update(
        can=lambda permission: permission in permissions,
        csrf_token=lambda: "csrf-token",
        get_flashed_messages=lambda **_kwargs: [],
        request=SimpleNamespace(endpoint="main.dashboard"),
        session={
            "user_id": 7,
            "username": "operator",
            "full_name": "测试用户",
            "role": "legacy-display-only",
        },
        url_for=lambda endpoint, **_kwargs: f"/{endpoint}",
    )
    return environment.get_template("dashboard.html").render(workspace=_workspace())


@pytest.mark.parametrize(
    ("perspective", "permissions", "primary_action", "pending_kind", "alert_kind"),
    (
        (
            "finance",
            {"analysis.read", "analysis.export", "payment.read", "refund.read", "refund.approve", "audit.read"},
            "refund-review",
            "refunds",
            "refunds",
        ),
        (
            "knowledge",
            {"knowledge.read", "knowledge.write", "knowledge.publish", "qa.read"},
            "knowledge-manage",
            "knowledge",
            "knowledge",
        ),
        (
            "qa",
            {"knowledge.read", "qa.read", "qa.handle", "customer.read", "order.read", "payment.read", "refund.read"},
            "ticket-handle",
            "tickets",
            "tickets",
        ),
        (
            "model",
            {"analysis.read", "analysis.run", "analysis.export", "model.read", "model.run", "customer.read", "product.read"},
            "model-run",
            "models",
            "models",
        ),
        (
            "administrator",
            set(NAV_PERMISSIONS.values()) | {"refund.approve", "knowledge.write", "knowledge.publish", "model.run"},
            "account-manage",
            "all",
            "tickets",
        ),
    ),
)
def test_dashboard_renders_authorized_role_workspace(
    perspective, permissions, primary_action, pending_kind, alert_kind
):
    rendered = _render_dashboard(permissions)

    visible_modules = set(re.findall(r'data-module="([^"]+)"', rendered))
    expected_modules = {"dashboard"} | {
        module for module, permission in NAV_PERMISSIONS.items() if permission in permissions
    }
    assert visible_modules == expected_modules, perspective
    assert f'data-primary-action="{primary_action}"' in rendered
    assert f'data-pending-kind="{pending_kind}"' in rendered
    assert f'data-alert="{alert_kind}"' in rendered

    for module, permission in NAV_PERMISSIONS.items():
        if permission not in permissions:
            assert f'data-module="{module}"' not in rendered


def test_dashboard_does_not_render_unauthorized_operational_data():
    rendered = _render_dashboard({"knowledge.read", "knowledge.write"})

    assert 'data-alert="knowledge"' in rendered
    assert "退款规则" in rendered
    assert "RF-1001" not in rendered
    assert "SKU-LOW" not in rendered
    assert "如何申请退款" not in rendered
    assert 'data-alert="refunds"' not in rendered
    assert 'data-alert="low-stock"' not in rendered
    assert 'data-alert="tickets"' not in rendered


def test_dashboard_uses_compact_mobile_rows_and_normal_anchors():
    source = (TEMPLATE_ROOT / "dashboard.html").read_text(encoding="utf-8")

    Environment().parse(source)
    assert "data-workspace-modules" in source
    assert "@media (max-width:600px)" in source
    assert "grid-template-columns:40px minmax(0,1fr) auto" in source
    assert "min-height:56px" in source
    assert ":focus-visible" in source
    assert "100vh" not in source
    assert "line-waves.js" not in source
    assert "magic-bento.js" not in source
    assert "onclick=" not in source
    assert re.search(r'<a\b[^>]+href="\{\{ url_for\(', source)


def test_dashboard_route_allows_authenticated_account_with_active_permissions(monkeypatch):
    from app.routes import main

    app = Flask(__name__, template_folder=str(TEMPLATE_ROOT))
    app.secret_key = "test-secret"
    app.add_url_rule("/login", endpoint="auth.login", view_func=lambda: "login")
    app.register_blueprint(main.main_bp)
    captured = {}

    active_permissions = frozenset({"qa.read"})
    monkeypatch.setattr(main, "account_permissions", lambda account_id: active_permissions)
    monkeypatch.setattr(
        main.DashboardRepository,
        "workspace",
        lambda permissions: captured.update(permissions=permissions) or _workspace(),
        raising=False,
    )
    monkeypatch.setattr(main, "render_template", lambda _name, **context: context["workspace"])

    client = app.test_client()
    assert client.get("/").status_code == 302

    with client.session_transaction() as flask_session:
        flask_session["user_id"] = 7

    response = client.get("/")
    assert response.status_code == 200
    assert captured["permissions"] == active_permissions


def test_base_navigation_includes_permission_gated_reports_and_governance():
    source = (TEMPLATE_ROOT / "base.html").read_text(encoding="utf-8")

    assert "session.get('role') ==" not in source
    assert "url_for('reports.index')" in source
    assert "can('analysis.read')" in source
    for permission in ("system.manage", "audit.read", "model.read", "knowledge.read", "qa.read", "qa.handle"):
        assert f"can('{permission}')" in source


def test_mobile_shell_keeps_workspace_in_the_first_viewport():
    source = (TEMPLATE_ROOT / "base.html").read_text(encoding="utf-8")

    assert "min-height:100dvh" in source
    assert "overflow-x:auto" in source
    assert ".nav-label{display:none}" in source
