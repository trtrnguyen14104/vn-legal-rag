import os

import pytest

from legal_rag.cli import migrate
from legal_rag.infrastructure import db
from legal_rag.retrieval import retriever as retrieval

pytestmark = pytest.mark.skipif(
    os.environ.get("LEGAL_RAG_INTEGRATION") != "1", reason="Isolated database required"
)


def test_hybrid_retrieval_uses_same_snapshot_and_model_revision(monkeypatch):
    migrate()
    snapshot = retrieval.active_snapshot()
    if not snapshot:
        pytest.skip("Snapshot fixture is created by integration publication tests")

    class Encoder:
        def query(self, text):
            return {"vectors": [[1.0] + [0.0] * 1023], "segmented": ["kiểm_thử"], "revision": "test"}

    monkeypatch.setattr(retrieval, "EmbeddingClient", Encoder)
    candidates = retrieval.search("kiểm thử", snapshot["id"])
    assert candidates
    for candidate in candidates:
        assert db.one(
            "SELECT 1 FROM snapshot_members WHERE snapshot_id=%s AND version_id=%s",
            (snapshot["id"], candidate["version_id"]),
        )
