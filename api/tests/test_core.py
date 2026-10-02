from api.config import Settings, get_settings
from api.main import app


def test_database_default_is_local(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    assert Settings(_env_file=None).database_url == "postgresql://depthapi:depthapi@localhost:5432/depthapi"

def test_core_endpoints_are_registered():
    paths = set(app.openapi()["paths"])
    assert "/api/query" in paths
    assert "/api/ingest" in paths
    assert "/api/ingest/file" in paths
    assert "/api/health" in paths



def test_security_controls_are_registered():
    middleware_names = {middleware.cls.__name__ for middleware in app.user_middleware}
    assert "CORSMiddleware" in middleware_names
    assert "SlowAPIMiddleware" in middleware_names
