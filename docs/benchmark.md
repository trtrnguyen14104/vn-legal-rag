# Benchmark và beta gate

Không có benchmark pháp lý đã được duyệt đi kèm repo. Tạo draft bằng CLI sau khi có 50 văn bản đã publish; draft không phải gold labels.

Mỗi bản ghi cần `id`, `question`, `intent`, `document_group`, `split` (`development` hoặc `held_out`), `reviewed=true`, `reviewed_by`, `snapshot_id`, `relevance` (chunk UUID → grade 0–3), `version_ids` và `as_of_date` nếu cần. Grade >= 2 là nguồn có thể trả lời câu hỏi. Tập held-out phải có đủ năm intent. Các câu so sánh phải chọn đúng hai phiên bản đã publish; câu thiếu phiên bản nên được gán clarification_needed sau duyệt.

Hiệu chỉnh prompt/ngưỡng trên development. Khóa snapshot, model revision và bộ câu hỏi trước khi đo held-out. Không chỉnh nhãn/threshold từ kết quả held-out để làm gate đạt.

Jev AI mặc định `jev-latest` để kiểm tra tích hợp. Trước benchmark đặt `JEV_AI_MODEL=jev-1.13.0` trong env và tạo lại worker; kiểm tra model thực tế. Không dùng kết quả của một alias đã thay phiên bản để tái sử dụng ngưỡng cũ.

Inference mặc định tắt; budget tổng benchmark mặc định 0. Trước khi cho phép chi tiêu, dùng `legal-rag benchmark <draft-or-reviewed.jsonl> --dry-run` để kiểm tra số câu, tình trạng duyệt và giới hạn mà không gọi Jev/Gemini/embedding. Dry-run chấp nhận draft nhưng báo dataset_ready=false; chạy thật vẫn yêu cầu dữ liệu duyệt đầy đủ. Chỉ chạy thật khi chủ động đặt `JEV_AI_INFERENCE_ENABLED=true` và `JEV_AI_MAX_CALLS_PER_BENCHMARK` dương; `JEV_AI_MAX_CALLS_PER_QUERY` giới hạn mỗi câu. Các giới hạn là số POST attempts, không phải tiền hoặc số credit còn lại.

Benchmark tái sử dụng client và các quyết định routing/rerank giống hệt trong cùng câu hỏi. Timeout cũng tiêu thụ một lượt trong budget. Khi hết budget/lỗi provider, report lưu `execution_status=incomplete`, câu bị lỗi và số POST đã thử; report này không thể vượt gate. Không tự resume hay tăng budget. Draft 250 câu vẫn cần người duyệt; không dùng nhãn tự sinh làm gold labels.

Sau `benchmark --baseline`, report có kết quả và hai trường để người duyệt điền cho từng outcome:

```json
{
  "citation_review": {"total_claims": 3, "supported_claims": 3, "reviewed_by": "reviewer-id"},
  "temporal_review": {"errors": 0, "reviewed_by": "reviewer-id"}
}
```

Với abstention không chứa nhận định pháp lý, tổng claims có thể là 0 nhưng vẫn phải ghi người duyệt. Gate yêu cầu macro-F1 >= 0,90; Recall@20 >= 0,90; citation support >= 0,95; temporal errors = 0; Jev nDCG không thấp hơn BGE/RRF. Không có nhãn relevance, baseline hoặc review thì gate không đạt. Giữ report cùng dataset SHA-256 để tái lập.

Benchmark bổ sung: nguyên văn E5 so với tách từ có `_`, CPU FP32 so với CUDA FP16, peak RAM/VRAM, embedding throughput, retrieval p50/p95 và thời gian cập nhật. Không thay representation/index đang phục vụ mà không chạy regression và publish lại snapshot.
