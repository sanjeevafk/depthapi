from api.config import Settings, get_settings


def test_database_default_is_local(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    assert Settings(_env_file=None).database_url == "postgresql://depthapi:depthapi@localhost:5432/depthapi"
