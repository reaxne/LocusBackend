import pytest
import settings


def test_env_loaded_from_project_directory(tmp_path, monkeypatch):
    config = tmp_path / '.env'
    config.write_text('DATABASE_URL=postgresql://example@localhost/locus\nCOOKIE_SECURE=false\n')
    monkeypatch.setattr(settings,'ENV_FILE',config)
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.delenv('COOKIE_SECURE',raising=False)
    monkeypatch.chdir(tmp_path.parent)
    assert settings.database_url() == 'postgresql://example@localhost/locus'


def test_explicit_and_environment_override_env_file(tmp_path, monkeypatch):
    config = tmp_path / '.env'
    config.write_text('DATABASE_URL=postgresql://from-file/unused\n')
    monkeypatch.setattr(settings,'ENV_FILE',config)
    monkeypatch.setenv('DATABASE_URL','postgresql://from-env/locus')
    assert settings.database_url() == 'postgresql://from-env/locus'
    assert settings.database_url('postgresql://explicit/locus') == 'postgresql://explicit/locus'


def test_missing_configuration_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(settings,'ENV_FILE',tmp_path / 'missing.env')
    monkeypatch.delenv('DATABASE_URL',raising=False)
    with pytest.raises(RuntimeError,match='setup_local.py'):
        settings.database_url()
