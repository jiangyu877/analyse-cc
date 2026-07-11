from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_production_server_and_health_route_exist():
    server = (ROOT / "serve.py").read_text(encoding="utf-8")
    system_route = (ROOT / "app" / "routes" / "system.py").read_text(encoding="utf-8")
    assert "waitress" in server
    assert '"/healthz"' in system_route


def test_browser_never_receives_database_credentials():
    templates = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "app" / "templates").glob("*.html")
    ).lower()
    assert "database_url" not in templates
    assert "db_password" not in templates
    assert "postgresql://" not in templates


def test_no_hardcoded_legacy_database_password():
    compatibility_db = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
    assert '"password"' not in compatibility_db
