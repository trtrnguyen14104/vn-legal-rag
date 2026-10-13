from legal_rag.ingestion.datasets import convert_uts, convert_vbpl
from legal_rag.ingestion.sources import record_review_errors


def test_vbpl_join_preserves_source_dates_and_provenance():
    metadata = {
        "id": "123",
        "title": "Nghị định kiểm thử",
        "so_ky_hieu": "1/2020/NĐ-CP",
        "loai_van_ban": "Nghị định",
        "pham_vi": "Trung ương",
        "ngay_co_hieu_luc": "01/01/2021",
    }
    record = convert_vbpl(
        metadata, "<p>Điều 1. Phạm vi</p><p>1. Quy định kiểm thử dài đủ để kiểm tra.</p>", []
    )
    assert record.source_document_id == "123"
    assert record.source_url.endswith("/123")
    assert record.effective_from == "01/01/2021"
    assert not record_review_errors(record)
    assert record.verified_at is None


def test_uts_snapshot_does_not_invent_effective_dates():
    row = {
        "id": "91/2015/QH13",
        "type": "code",
        "title": "Bộ luật",
        "content": "# Bộ luật\n\nĐiều 1. Một quy định chỉ phục vụ kiểm thử kỹ thuật.",
    }
    record = convert_uts(row, {row["id"]: {"doc_num": row["id"], "url_vbpl": "https://vbpl.vn/van-ban/test"}})
    assert record.kind == "Bộ luật"
    assert record.effective_from is None
    assert record.reported_status == "snapshot_2026_unverified_at_query_time"
