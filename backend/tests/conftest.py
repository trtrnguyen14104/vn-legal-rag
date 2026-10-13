import os

import pytest

for key, value in {
    "DATABASE_URL": "postgresql://test:test@localhost:55432/test",
    "REDIS_URL": "redis://localhost:56379/0",
    "SESSION_SECRET": "test-session-secret-only-" + "x" * 32,
    "EMBEDDING_API_KEY": "test-embedding-key-only-" + "x" * 32,
}.items():
    os.environ.setdefault(key, value)


@pytest.fixture(autouse=True)
def clear_settings():
    from legal_rag.config import settings

    settings.cache_clear()
    yield
    settings.cache_clear()
