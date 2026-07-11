from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_public_seed_does_not_publish_login_passwords():
    seed = (ROOT / "database" / "v2_seed.sql").read_text(encoding="utf-8")

    assert "gen_random_bytes" in seed
    assert "crypt('" not in seed
    assert "ADMIN_PASSWORD" in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_render_blueprint_bootstraps_database_and_secrets():
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")

    assert "python scripts/bootstrap_cloud.py && python serve.py" in blueprint
    assert "property: connectionString" in blueprint
    assert "key: SECRET_KEY" in blueprint
    assert blueprint.count("sync: false") == 3
    bootstrap = (ROOT / "scripts" / "bootstrap_cloud.py").read_text(encoding="utf-8")
    assert '"--transactions", str(transactions)' in bootstrap
    assert 'transactions < 50000' in (ROOT / "scripts" / "import_demo_data.py").read_text(encoding="utf-8")


def test_gradio_link_is_environment_driven_for_public_hosts():
    template = (ROOT / "app" / "templates" / "algorithms.html").read_text(encoding="utf-8")

    assert "gradio_public_url" in template
    assert "http://127.0.0.1:7860" not in template
