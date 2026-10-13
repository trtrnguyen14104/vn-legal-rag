"""Guard process boundaries and public contracts during backend restructuring."""

import ast
import json
import subprocess
import sys
from pathlib import Path

from legal_rag.api.app import app
from legal_rag.infrastructure.migrations import migration_files


def test_public_api_contract_is_preserved():
    expected = Path(__file__).parents[1] / "fixtures" / "openapi-v1.json"
    assert app.openapi() == json.loads(expected.read_text(encoding="utf-8"))


def test_sql_migrations_are_available_and_unchanged():
    project = Path(__file__).parents[2]
    originals = sorted((project / "migrations").glob("*.sql"))
    resolved = migration_files()
    assert [path.name for path in resolved] == [path.name for path in originals]
    assert [path.read_text(encoding="utf-8") for path in resolved] == [
        path.read_text(encoding="utf-8") for path in originals
    ]


def test_core_process_imports_do_not_load_embedding_runtime():
    code = """
import sys
from legal_rag.api.app import app
from legal_rag.ingestion.pipeline import ingest
from legal_rag.tasks.celery_app import celery
celery.loader.import_default_modules()
assert not {'torch', 'numpy', 'sentence_transformers', 'py_vncorenlp', 'jnius'} & sys.modules.keys()
assert 'legal_rag.embedding.encoder' not in sys.modules
assert 'legal_rag.embedding.service' not in sys.modules
assert set(celery.conf.task_routes) == {
    'legal_rag.tasks.answer_query', 'legal_rag.tasks.ingest_source',
    'legal_rag.tasks.daily_update', 'legal_rag.tasks.maintenance'
}
assert set(celery.conf.task_routes) <= celery.tasks.keys()
assert celery.conf.timezone == 'Asia/Saigon'
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True, timeout=20)


def test_domain_and_infrastructure_have_no_reverse_layer_dependencies():
    source = Path(__file__).parents[2] / "src" / "legal_rag"
    for layer, forbidden in {
        "domain": (
            "api",
            "ingestion",
            "retrieval",
            "embedding",
            "llm",
            "workflow",
            "tasks",
            "infrastructure",
        ),
        "infrastructure": ("api", "ingestion", "retrieval", "embedding", "llm", "workflow", "tasks"),
        "ingestion": ("api",),
        "tasks": ("api",),
    }.items():
        for path in (source / layer).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    imports = [node.module or ""]
                elif isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                else:
                    continue
                assert not any(
                    name == f"legal_rag.{dependency}" or name.startswith(f"legal_rag.{dependency}.")
                    for name in imports
                    for dependency in forbidden
                ), path
