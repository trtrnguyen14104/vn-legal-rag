from datetime import date

import pytest

from legal_rag.domain.models import DocumentRecord
from legal_rag.ingestion.sources import OfficialConnector, SourceError, record_review_errors


def record(**kwargs):
    return DocumentRecord(
        title="Nghị định thử nghiệm",
        number="1/2020/NĐ-CP",
        kind="Nghị định",
        text="Điều 1. Nội dung thử nghiệm chỉ phục vụ kiểm thử.",
        source_url="https://vbpl.vn/test",
        source_kind="legislation",
        license_evidence="Nguồn công bố chính thức",
        **kwargs,
    )


def test_qa_corpus_cannot_become_legal_authority():
    item = record().model_copy(update={"source_kind": "qa"})
    assert "unverified_source_kind" in record_review_errors(item)


def test_effective_date_requires_matching_source_evidence():
    assert "effective_from_without_evidence" in record_review_errors(record(effective_from="2021-01-01"))
    assert not record_review_errors(
        record(effective_from="2021-01-01", effective_from_evidence="Ngày hiệu lực: 01/01/2021")
    )


def test_effective_date_accepts_vietnamese_date_wording():
    assert not record_review_errors(
        record(
            effective_from="2021-01-01",
            effective_from_evidence="Văn bản có hiệu lực từ ngày 01 tháng 01 năm 2021.",
        )
    )


def test_local_scope_is_quarantined():
    assert "outside_central_scope" in record_review_errors(
        record().model_copy(update={"number": "1/2020/QĐ-UBND"})
    )


def test_official_connector_accepts_only_document_details():
    connector = OfficialConnector("vanban")
    assert connector.is_detail("https://vanban.chinhphu.vn/?pageid=27160&docid=123")
    assert not connector.is_detail("https://evil.test/?docid=123")
    with pytest.raises(SourceError, match="unsafe_source"):
        connector.fetch("http://127.0.0.1/private")


def test_changed_source_markup_is_failure_not_success(monkeypatch):
    connector = OfficialConnector("vanban")
    monkeypatch.setattr(connector, "fetch", lambda _: b"<html><h1>Different page</h1></html>")
    with pytest.raises(SourceError, match="schema_changed"):
        connector.listings(date(2026, 1, 1))
