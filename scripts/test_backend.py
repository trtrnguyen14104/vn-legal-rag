"""Run tests against the isolated legal-rag-test Compose project; do not print secrets."""

import os
import subprocess
from pathlib import Path
from urllib.parse import quote

from dotenv import dotenv_values


def main():
    root = Path(__file__).resolve().parent.parent
    config = dotenv_values(root / ".env")
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": "postgresql://"
            + quote(config["POSTGRES_USER"], safe="")
            + ":"
            + quote(config["POSTGRES_PASSWORD"], safe="")
            + "@127.0.0.1:55432/"
            + quote(config["POSTGRES_DB"], safe=""),
            "REDIS_URL": "redis://:"
            + quote(config["REDIS_PASSWORD"], safe="")
            + "@127.0.0.1:56379/0",
            "SESSION_SECRET": "test-only-secret-" + "x" * 48,
            "EMBEDDING_API_KEY": "test-only-embedding-" + "x" * 48,
            "LEGAL_RAG_INTEGRATION": "1",
            "ENVIRONMENT": "testing",
            "PUBLIC_ORIGIN": "http://localhost:3000",
        }
    )
    python_path = root / (
        "backend/.venv/Scripts/python.exe"
        if os.name == "nt"
        else "backend/.venv/bin/python"
    )
    return subprocess.call(
        [str(python_path), "-m", "pytest", "-q"], cwd=root / "backend", env=environment
    )


if __name__ == "__main__":
    raise SystemExit(main())
