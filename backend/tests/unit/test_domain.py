from datetime import date

import pytest

from legal_rag.contracts import Claim, QueryInput
from legal_rag.domain.legal import compare_units, parse_date, temporal_state
from legal_rag.domain.text import legal_units, normalize
from legal_rag.embedding.chunking import chunk_unit
from legal_rag.retrieval.fusion import rrf
from legal_rag.workflow.validation import deterministic_claim_errors


def test_offsets_and_chunks_preserve_every_character():
    text = (
        "Lời mở đầu.\nĐiều 1. Phạm vi\n1. Nội dung thứ nhất.\na) Một điểm.\nĐiều 2. Thi hành\n"
        + "Quy định dài. " * 400
    )
    units = legal_units(text)
    assert "".join(u["raw_text"] for u in units) == text
    assert any("khoản 1 / điểm a" in u["locator"] for u in units)

    # A deterministic stand-in that counts every character plus special tokens.
    def count(value):
        return len(value) + 2

    for unit in units:
        assert text[unit["start_offset"] : unit["end_offset"]] == unit["raw_text"]
        chunks = chunk_unit(unit["raw_text"], "Văn bản", unit["locator"], count)
        assert "".join(c["raw_text"] for c in chunks) == unit["raw_text"]
        assert all(c["embedding_text"].startswith("passage: ") and c["token_count"] <= 512 for c in chunks)


def test_prefix_and_metadata_count_toward_limit():
    with pytest.raises(ValueError, match="metadata"):
        chunk_unit("Nội dung", "x" * 600, "Điều 1", len)


def test_normalization_keeps_legal_identifiers_and_accents():
    assert normalize("  Điều  1.\u00a0Nghị định 123/2020/NĐ-CP  ") == "Điều 1. Nghị định 123/2020/NĐ-CP"


@pytest.mark.parametrize(
    "value, expected", [("31/12/2020", date(2020, 12, 31)), ("2021-01-01", date(2021, 1, 1)), (None, None)]
)
def test_dates(value, expected):
    assert parse_date(value) == expected


def test_invalid_date_cannot_be_used_as_effective_date():
    with pytest.raises(ValueError):
        parse_date("31/02/2025")


def test_missing_expiry_never_proves_current_validity():
    assert temporal_state({"effective_from": date(2020, 1, 1)}, date(2026, 1, 1)) == "unknown"
    assert temporal_state({"effective_from": date(2027, 1, 1)}, date(2026, 1, 1)) == "future"
    assert temporal_state({"effective_to": date(2026, 1, 1)}, date(2026, 1, 1)) == "expired"
    assert temporal_state({"temporal_status": "partially_expired"}, date(2026, 1, 1)) == "partial"
    assert temporal_state({"temporal_status": "conflict"}, date(2026, 1, 1)) == "conflict"


def test_wrong_dates_and_numbers_are_rejected_before_jev():
    evidence = {
        "a": {
            "raw_text": "Văn bản 123/2020/NĐ-CP có hiệu lực từ 01/01/2021.",
            "number": "123/2020/NĐ-CP",
            "temporal_status": "unknown",
        }
    }
    assert deterministic_claim_errors(
        Claim(text="Hiệu lực từ 02/01/2021 theo 124/2020/NĐ-CP", source_ids=["a"]), evidence
    ) == ["wrong_document_number", "unsupported_date"]
    assert deterministic_claim_errors(Claim(text="Văn bản còn hiệu lực.", source_ids=["a"]), evidence) == [
        "unverified_current_validity"
    ]
    assert deterministic_claim_errors(Claim(text="Có căn cứ", source_ids=["fake"]), evidence) == [
        "unknown_source"
    ]


def test_rrf_combines_ranks_without_duplicate_sources():
    results = rrf([{"id": "a"}, {"id": "b"}], [{"id": "b"}, {"id": "c"}])
    assert results[0]["id"] == "b"
    assert len(results) == 3


def test_comparison_records_added_and_removed_units():
    rows = compare_units(
        [{"locator": "Điều 1", "raw_text": "Cũ"}], [{"locator": "Điều 2", "raw_text": "Mới"}]
    )
    assert rows[0]["after"] == ""
    assert rows[1]["before"] == ""


def test_query_rejects_one_or_duplicate_comparison_versions():
    with pytest.raises(ValueError):
        QueryInput(question="So sánh", version_ids=["00000000-0000-0000-0000-000000000001"])
    with pytest.raises(ValueError):
        QueryInput(question="So sánh", version_ids=["00000000-0000-0000-0000-000000000001"] * 2)
