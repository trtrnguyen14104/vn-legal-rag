"""Resolve unchanged SQL migrations in both editable installs and built wheels."""

from importlib.resources import files
from pathlib import Path


def migration_files():
    packaged = files("legal_rag").joinpath("_migrations")
    if packaged.is_dir():
        folder = packaged
    else:
        # Hatch editable installs expose src/ without materializing force-included resources.
        project = Path(__file__).resolve().parents[3]
        if not (project / "pyproject.toml").is_file():
            raise RuntimeError("migration_resources_missing")
        folder = project / "migrations"
    migrations = sorted(
        (path for path in folder.iterdir() if path.name.endswith(".sql")), key=lambda path: path.name
    )
    if not migrations:
        raise RuntimeError("migration_resources_missing")
    return migrations
