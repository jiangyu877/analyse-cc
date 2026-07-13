from pathlib import Path

from app import create_app


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = (
    "from users",
    "join users",
    "from roles",
    "spending_record",
    "from budgets",
)


def test_registered_runtime_is_v2_only():
    app = create_app()
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    assert "main.dashboard" in endpoints
    assert "customers.index" in endpoints
    assert "algorithms.index" in endpoints

    runtime_files = [
        ROOT / "gradio_app.py",
        *sorted((ROOT / "app").rglob("*.py")),
        *sorted((ROOT / "app" / "templates").rglob("*.html")),
    ]
    violations = []
    for path in runtime_files:
        source = path.read_text(encoding="utf-8").lower()
        for marker in FORBIDDEN:
            if marker in source:
                violations.append(f"{path.relative_to(ROOT)}: {marker}")

    assert violations == []
