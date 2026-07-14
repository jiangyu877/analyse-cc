from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backup_script_contract():
    text = (ROOT / "scripts" / "backup_db.ps1").read_text(encoding="utf-8")
    assert "PG_BIN_DIR" in text and "pg_dump" in text
    assert ".partial" in text
    assert "SHA256" in text
    assert "BACKUP_RETENTION_DAYS" in text
    assert "BACKUP_DIR" in text
    assert "Remove-Item" in text
    assert "DATABASE_URL" in text
    assert "PGPASSWORD" in text
    assert "--format=custom" in text or "-Fc" in text
    assert "--file" in text or "-f" in text
    for token in ("DatabaseUrl", "EnvFile", "PgBinDir", "BackupDir", "RetentionDays", "PGHOST", "PGPORT", "PGUSER", "PGDATABASE"):
        assert token in text
    assert "&$dump" in text and " $DatabaseUrl" not in text


def test_restore_script_contract_and_dangerous_tokens():
    text = (ROOT / "scripts" / "restore_db.ps1").read_text(encoding="utf-8")
    for token in ("BackupFile", "TargetDatabase", "Get-FileHash", "pg_restore", "--list", "psql", "PGPASSWORD"):
        assert token in text
    for token in ("--exit-on-error", "--single-transaction", "--no-owner", "--no-privileges"):
        assert token in text
    for token in ("--clean", "--create", "DROP DATABASE", "CREATE DATABASE"):
        assert token not in text
    assert "PRODUCTION_DB_NAME" in text
    assert "DATABASE_URL" in text
    for token in ("DatabaseUrl", "EnvFile", "PgBinDir", "PGHOST", "PGPORT", "PGUSER", "PGDATABASE", "PRODUCTION_DB_NAME"):
        assert token in text
    assert "&$restore" in text and " $DatabaseUrl" not in text


def test_scripts_fail_fast_for_missing_pg_bin_dir_and_dangerous_production_target():
    backup = (ROOT / "scripts" / "backup_db.ps1").read_text(encoding="utf-8")
    restore = (ROOT / "scripts" / "restore_db.ps1").read_text(encoding="utf-8")
    for text in (backup, restore):
        assert "Test-Path" in text
        assert "PG_BIN_DIR" in text
    assert "PRODUCTION_DB_NAME" in restore
    assert "TargetDatabase" in restore


def test_env_example_exposes_backup_settings_without_secrets():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("PG_BIN_DIR", "BACKUP_DIR=.cache/backups", "BACKUP_RETENTION_DAYS=30", "PRODUCTION_DB_NAME"):
        assert key in text
    assert "postgresql://user:password" not in text
