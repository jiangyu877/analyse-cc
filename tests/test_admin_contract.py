from pathlib import Path

from app import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_v2_admin_routes_and_templates_are_registered():
    app = create_app()
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    assert {
        "admin.accounts",
        "admin.create_account",
        "admin.toggle_account",
        "admin.reset_password",
        "admin.logs",
    } <= endpoints
    assert (ROOT / "app" / "templates" / "admin" / "users.html").exists()
    assert (ROOT / "app" / "templates" / "admin" / "logs.html").exists()


def test_admin_repository_never_selects_password_hash_in_list():
    path = ROOT / "app" / "repositories" / "auth.py"
    assert path.exists(), "V2 auth repository is required"
    source = path.read_text(encoding="utf-8").lower()
    list_query = source.split("def list_accounts", 1)[1].split("def ", 1)[0]
    assert "password_hash" not in list_query
    assert "auth.account_role" in source
    assert "auth.role" in source
