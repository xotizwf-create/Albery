from pathlib import Path


def test_local_backup_is_atomic_and_validated():
    source = Path("scripts/backup_postgres.sh").read_text(encoding="utf-8")
    assert ".dump.partial" in source
    assert "flock -n" in source
    assert 'pg_restore --list "$partial_path"' in source
    assert "sha256sum" in source
    assert 'mv -- "$partial_path" "$backup_path"' in source


def test_restore_helper_cannot_target_production():
    source = Path("scripts/restore_postgres.sh").read_text(encoding="utf-8")
    assert "albery_restore_" in source
    assert 'target_database" = "albery"' in source
    assert "--clean" not in source
    assert "--exit-on-error" in source


def test_offsite_transfer_is_atomic_and_uses_sha256():
    source = Path("scripts/backup_offsite.py").read_text(encoding="utf-8")
    assert 'partial = remote + ".partial"' in source
    assert "sha256sum" in source
    assert "md5sum" not in source
    assert "pg_restore --list" in source
    assert "OFFSITE_STATUS_PATH" in source


def test_selfcheck_covers_backup_chain():
    source = Path("scripts/albery_selfcheck.py").read_text(encoding="utf-8")
    assert "inspect_backup_health" in source
    assert "PostgreSQL backup chain" in source
