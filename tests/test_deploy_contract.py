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
    assert blueprint.count("sync: false") == 5
    for key in (
        "AI_API_KEY", "AI_MODEL", "ADMIN_PASSWORD", "OPERATOR_PASSWORD",
        "ANALYST_PASSWORD",
    ):
        assert f"key: {key}" in blueprint
    bootstrap = (ROOT / "scripts" / "bootstrap_cloud.py").read_text(encoding="utf-8")
    assert '"--transactions", str(transactions)' in bootstrap
    assert 'transactions < 50000' in (ROOT / "scripts" / "import_demo_data.py").read_text(encoding="utf-8")


def test_gradio_link_is_environment_driven_for_public_hosts():
    template = (ROOT / "app" / "templates" / "algorithms.html").read_text(encoding="utf-8")

    assert "gradio_public_url" in template
    assert "http://127.0.0.1:7860" not in template


def test_existing_repository_uses_dashboard_navigation_commit_message():
    push_script = (ROOT / "push_to_github.cmd").read_text(encoding="utf-8")

    assert 'set "COMMIT_MESSAGE=Add Magic Bento dashboard navigation"' in push_script
    assert 'set "COMMIT_MESSAGE=Make LineWaves background self-contained"' not in push_script


def test_local_worker_is_packaged_without_a_public_port():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")

    assert "COPY serve.py run.py worker.py ./" in dockerfile
    assert "\n  worker:\n" in compose
    worker = compose.partition("\n  worker:\n")[2].partition("\nvolumes:\n")[0]
    assert "build: ." in worker
    assert 'command: ["python", "worker.py"]' in worker
    for variable in (
        "FLASK_ENV",
        "SECRET_KEY",
        "DATABASE_URL",
        "ADMIN_PASSWORD",
        "OPERATOR_PASSWORD",
        "ANALYST_PASSWORD",
    ):
        assert f"{variable}:" in worker
    assert "ports:" not in worker
    assert "web:\n        condition: service_healthy" in worker
    assert "127.0.0.1:5000/readyz" in compose
    assert "127.0.0.1:5000/healthz" not in compose
    assert "type: worker" not in blueprint
    assert "healthCheckPath: /healthz" in blueprint


def test_ci_uses_postgres_18_and_short_timeout_without_pgvector():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "image: postgres:18" in ci
    assert "timeout-minutes: 15" in ci
    assert "pgvector" not in ci.lower()
