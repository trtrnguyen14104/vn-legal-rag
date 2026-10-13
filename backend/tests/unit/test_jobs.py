import logging
from datetime import date
from unittest.mock import Mock

import pytest

from legal_rag.contracts import QueryResult
from legal_rag.tasks import jobs


@pytest.mark.parametrize("code", [None, "jev:unexpected_model", "jev:402"])
def test_completed_query_persists_diagnostic_and_redacts_public_result(monkeypatch, caplog, code):
    private_question = "Private test question that must never appear in logs"
    monkeypatch.setattr(
        jobs.db,
        "one",
        lambda *args: {
            "snapshot_id": None,
            "request": {"question": private_question},
            "user_id": "user-test",
        },
    )
    result = QueryResult(
        intent="legal_lookup",
        status="unavailable" if code else "insufficient_evidence",
        as_of_date=date(2026, 10, 13),
        error_code=code,
    )
    monkeypatch.setattr(jobs, "run", lambda *args: result)
    execute = Mock()
    redis = Mock()
    monkeypatch.setattr(jobs.db, "execute", execute)
    monkeypatch.setattr(jobs, "redis_client", lambda: redis)
    with caplog.at_level(logging.WARNING, logger=jobs.__name__):
        jobs.answer_query.run("query-test")
    sql, params = execute.call_args.args
    assert "error_code=%s" in sql
    assert params[1:] == (code, "query-test")
    assert "error_code" not in params[0].obj
    assert private_question not in caplog.text
    if code:
        assert f"query_unavailable query_id=query-test code={code}" in caplog.text
    else:
        assert "query_unavailable" not in caplog.text
    redis.eval.assert_called_once_with(jobs.RELEASE_LOCK, 1, "active-query:user-test", "query-test")
