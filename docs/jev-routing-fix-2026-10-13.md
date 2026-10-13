# Báo cáo sửa lỗi đánh giá Jev — 13/10/2026

Lỗi `unexpected_model` đã được tái hiện, sửa và cập nhật vào API/query worker local. Backend hiện chấp nhận đúng các ID tương đương của Jev 1.13.0 thay vì từ chối phản hồi thành công do tên model có namespace upstream.

## Triệu chứng và nguyên nhân

Giao diện hiển thị “Dịch vụ đánh giá tạm thời không khả dụng. Vui lòng thử lại sau.” ở bước phân loại câu hỏi. Cấu hình worker đã bật inference, có API key và giới hạn 32 calls/query; GET model list trước đó trả HTTP 200. Đây không còn là trường hợp inference bị tắt đã phát hiện ở lần kiểm tra đầu.

Hai POST chẩn đoán bằng câu hỏi tổng hợp đều nhận HTTP 200 nhưng adapter báo `jev:unexpected_model`. Lượt thứ hai ghi nhận phản hồi:

```json
{
  "model": "typesafe/jev-1.13-20260917",
  "answers": {
    "intent": {
      "type": "choice",
      "choice": "clarification_needed",
      "probabilities": {
        "legal_lookup": 0.49,
        "legal_explanation": 0,
        "compare_versions": 0,
        "out_of_scope": 0,
        "clarification_needed": 0.51
      },
      "confidence": 0.39
    }
  },
  "usage": {"input_tokens": 464, "output_tokens": 65}
}
```

Adapter cũ yêu cầu tên model khớp `jev-[\w.-]{1,64}`. Tên trả về không khớp vì có namespace `typesafe/`, dù có đủ answers. [Tài liệu Jev](https://jev-ai.pro/docs) mô tả `typesafe/jev-1.13` và `typesafe/jev-1.13-20260917` là ID tương đương với `jev-1.13.0` trong phần Decisions compatibility. Quan sát HTTP thực tế xác nhận `/v1/systemone` cũng có thể trả ID này.

Routing bắt `ProviderError` rồi trả kết quả `unavailable`. Worker coi việc tạo được kết quả này là query `completed`, nhưng trước đây không lưu hoặc log mã lỗi đã bị bắt. Hai query cũ trong database vì vậy có `error_code=null`. Không thể xác nhận hồi tố mã lỗi của từng query cũ; nguyên nhân trên được xác nhận bằng tái hiện trực tiếp, không phải suy đoán từ ảnh.

## Thay đổi triển khai

| File | Thay đổi |
| --- | --- |
| `backend/src/legal_rag/llm/jev.py` | Nhận diện chính xác hai ID TypeSafe đã biết là `jev-1.13.0`; lưu `reported_model` gốc, giữ `requested_model`, dùng model chuẩn hóa cho so sánh phiên bản và metrics. |
| `backend/src/legal_rag/contracts.py` | Thêm `QueryResult.error_code` nội bộ, loại khỏi JSON result bằng `exclude=True`. |
| `backend/src/legal_rag/workflow/rag_flow.py` | Giữ mã `provider:code` khi routing gặp `ProviderError`. |
| `backend/src/legal_rag/tasks/jobs.py` | Lưu mã vào cột `queries.error_code` sẵn có và log `query_unavailable query_id=... code=...`. Kết quả unavailable vẫn đến được giao diện. |
| `backend/tests/unit/test_providers.py` | Kiểm tra alias với cả cấu hình latest/pinned và từ chối model khác, namespace lạ, phiên bản không nhận diện, kiểu dữ liệu sai. |
| `backend/tests/unit/test_workflow.py` | Kiểm tra phản hồi thực đã ghi lại; kiểm tra mã lỗi được giữ và không xuất ra JSON hoặc bỏ qua validation. |
| `backend/tests/unit/test_jobs.py` | Kiểm tra worker giữ mã lỗi, log có query ID, không log nội dung câu hỏi và giải phóng khóa. |
| `backend/tests/integration/test_integration.py` | Kiểm tra lỗi được lưu thật trong PostgreSQL, result không lộ diagnostic, khóa Redis được giải phóng. |
| `backend/tests/fixtures/jev_typesafe_response.json` | Fixture từ phản hồi HTTP của câu hỏi tổng hợp, dùng được offline. |
| `docs/jev-ai.md` | Cập nhật cách nhận diện model và tra cứu lỗi routing. |

Không bỏ toàn bộ kiểm tra model và không tự đổi provider. Cấu hình pinned vẫn phải khớp phiên bản sau khi nhận diện alias. POST Jev vẫn chỉ được gửi một lần cho mỗi quyết định, giữ nguyên giới hạn call và không tự retry khi outcome chưa rõ. Không sửa `.env`, `config.py`, frontend, Compose hoặc SQL migrations trong đợt này.

## Kiểm chứng

1. `pytest -q tests/unit`: **85 passed**.
2. `backend/.venv/Scripts/python.exe scripts/test_backend.py`: **95 passed**, gồm unit/API và integration trên project riêng `legal-rag-test`, port 55432/56379. Các tests không gọi LLM thật. Đợt này bổ sung 18 trường hợp kiểm thử.
3. Ruff lint: **All checks passed**. Ruff format: **59 files already formatted**.
4. Có một cảnh báo deprecation của Starlette TestClient/httpx. Không có test thất bại; chưa thay dependency để xử lý cảnh báo vì không liên quan đến lỗi này.
5. Đã build image `api`, `query-worker` và cập nhật hai container bằng `docker compose up -d --no-deps --no-build api query-worker`. Không rebuild frontend hoặc embedding.
6. Kiểm tra Jev thật trên worker mới bằng câu hỏi tổng hợp: **HTTP 200**, intent `legal_lookup`, model trả về `jev-1.13.0`, đúng **một POST**.
7. Phát lại fixture model `typesafe/jev-1.13-20260917` trong worker mới: **passed**, intent `clarification_needed`, model chuẩn hóa `jev-1.13.0`, **không gọi HTTP**. Kết quả hỏi làm rõ là đúng với confidence thấp của fixture.
8. `GET /api/v1/health`: HTTP 200, `{"status":"ready"}`. `GET /api/v1/auth/login`: HTTP 307. Chưa chạy bước đăng nhập Google bằng tài khoản thật.

Report kỹ thuật được giữ tại `data/reports/jev-routing-fix.json` (gitignored), không chứa API key hoặc nội dung hội thoại riêng tư.

## Chi phí và giới hạn

Đã gửi tổng cộng **ba POST Jev với dữ liệu tổng hợp**: hai lượt xác định lỗi và một lượt xác nhận sau sửa. Mỗi lượt có billing header `credits_charged=1`, `input_tokens_charged=0`; tổng cộng **3 credit**. Không tự gửi lại POST; pytest và phát lại fixture không phát sinh inference. Không gọi Gemini trong đợt này.

Kiểm tra phê duyệt tự động đã từ chối kế hoạch phát lại câu hỏi gần nhất từ database vì thao tác đó gửi dữ liệu riêng tư sang provider. Kế hoạch đó không được thực thi; các kiểm tra HTTP đã dùng câu hỏi tổng hợp, không đọc database. Việc kiểm tra model/health và tests vẫn hoàn thành bằng phương án này.

Đã xác nhận sửa lỗi nhận diện model và routing trên worker chạy thực tế. Chưa kiểm chứng toàn bộ luồng retrieval → Gemini → Jev validation cho câu hỏi pháp luật của người dùng. Giới hạn dữ liệu, độ mới của corpus và khả năng của Gemini model đang cấu hình vẫn là các vấn đề riêng; bản sửa không chứng minh câu hỏi về thay đổi luật giao thông đến 13/10/2026 sẽ có đủ bằng chứng trả lời.

Lịch sử cũ giữ nguyên kết quả đã lưu. Để kiểm tra trải nghiệm hiện tại, gửi một câu hỏi mới. Nếu vẫn có `unavailable`, kiểm tra `docker compose logs --tail 100 query-worker` hoặc `queries.error_code` để xác định lỗi mới trước khi lặp lại request trả phí.
