import os

# The service modules build a SQLAlchemy engine at import time. Tests only
# exercise pure logic, so point them at a URL that is valid to parse but
# never actually connected to.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test",
)
os.environ.setdefault("OPENAI_API_KEY", "test-key")
