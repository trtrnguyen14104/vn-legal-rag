# Jev AI: cấu hình và kiểm chứng

Backend hiện dùng adapter HTTPX trong `backend/src/legal_rag/llm/jev.py`; dự án không có client `@typesafe-ai/sdk`. Adapter giữ định dạng typed questions/answers và gửi tới dịch vụ Jev AI, theo [tài liệu tích hợp](https://jev-ai.pro/docs#official-sdk).

## Local

Khai báo trong `.env` ở thư mục gốc dự án; `.env.example` không có key thật:

```dotenv
JEV_AI_API_KEY=<key tạo tại https://jev-ai.pro/jev-api>
JEV_AI_BASE_URL=https://jev-ai.pro/api
JEV_AI_MODEL=jev-latest
JEV_AI_INFERENCE_ENABLED=false
JEV_AI_MAX_CALLS_PER_QUERY=32
JEV_AI_MAX_CALLS_PER_BENCHMARK=0
GEMINI_API_KEY=<Google Gemini key>
GEMINI_MODEL=gemini-2.5-flash
```

Settings chặn URL khác để key không bị gửi sang dịch vụ khác do env cũ hoặc sai đường dẫn. Không dùng lại các biến `TYPESAFE_*`. Mặc định alias `jev-latest`; khi benchmark/release, chọn `JEV_AI_MODEL=jev-1.13.0` để cố định phiên bản. Adapter ghi nhận model thực tế qua metrics và metadata probe, không coi alias là phiên bản cố định.

API có thể trả model upstream `typesafe/jev-1.13-20260917` hoặc alias `typesafe/jev-1.13`. Adapter nhận diện chính xác hai ID này là `jev-1.13.0`, vẫn kiểm tra phiên bản khi cấu hình model cố định. Metadata `reported_model` giữ tên gốc; `model` và metrics dùng tên đã chuẩn hóa. Không tự bỏ namespace hoặc chấp nhận mọi model khác.

Inference mặc định tắt, kể cả khi có key. Chỉ bật `JEV_AI_INFERENCE_ENABLED=true` khi người vận hành chủ động cho phép chi tiêu. GET models vẫn dùng được khi inference tắt; metadata lookup có bằng chứng không cần Jev. Luồng giải thích cần validation sẽ trả unavailable nếu Jev bị tắt.

Giới hạn đếm số POST đã thử gửi, bao gồm request lỗi/timeout; không hoàn lại lượt khi outcome chưa rõ. Một client được dùng trong suốt truy vấn và cache quyết định có body giống hệt, chỉ trong vòng đời truy vấn. Không cache chung giữa tài khoản. Đây là giới hạn call, không phải giới hạn tiền/tokens/credit tài khoản và không phải quota tổng toàn dịch vụ. Muốn mở dịch vụ công khai cần thêm budget tổng theo tài khoản provider/ngày.

Benchmark mặc định bị chặn bởi giới hạn tổng `0`. Đặt giới hạn dương rõ ràng trong env trước khi chạy benchmark có inference; giới hạn mỗi câu hỏi và tổng lượt chạy cùng được kiểm tra trước POST. Probe decision có giới hạn riêng một POST. Adapter không tự bật inference hoặc tăng giới hạn khi hết lượt.

Chạy từ thư mục gốc bằng Python đã cài dependencies backend:

```powershell
# GET https://jev-ai.pro/api/v1/models, không inference
backend\.venv\Scripts\python.exe scripts/probe_jev.py

# Chỉ chạy sau khi chủ động bật inference trong env; dùng balance
# GET models trước, rồi tối đa một POST decision
backend\.venv\Scripts\python.exe scripts/probe_jev.py --decision
```

Trên Linux dùng `backend/.venv/bin/python`. Script chạy adapter thật của ứng dụng, in URL đã resolve, model, answers, usage và các billing header được chọn; không in key hoặc Authorization. Report ở `data/reports/jev-probe.json` được gitignore. Khi inference tắt, `--decision` báo `inference_disabled` và số decision attempt bằng 0. Mỗi lần chạy `--decision` với inference bật là một lần chi tiêu mới; không chạy lặp nếu chỉ cần xem lại report. `doctor` và `smoke_providers.py` là kiểm thử inference rộng hơn; Gemini có thể dùng token ngay cả khi Jev bị tắt. Chúng không phải lệnh kiểm tra kết nối miễn inference.

Request nhỏ dùng trong probe:

```json
{
  "model": "jev-latest",
  "state": "Nghị định 123/2020/NĐ-CP có hiệu lực từ ngày nào?",
  "questions": {
    "intent": {
      "type": "choice",
      "instructions": "Classify this Vietnamese legal question.",
      "criteria": {
        "legal_lookup": null,
        "legal_explanation": null,
        "compare_versions": null,
        "out_of_scope": null,
        "clarification_needed": null
      }
    }
  }
}
```

Đích chính xác là `POST https://jev-ai.pro/api/v1/systemone`, header `Authorization: Bearer <giá trị env JEV_AI_API_KEY>`. HTTPX tự thêm Content-Type JSON. Giới hạn adapter: tối đa 64 câu hỏi, ID 64 ký tự, body tối đa 256.000 byte; request quá lớn bị chặn trước khi gọi inference. Backend kiểm tra schema câu trả lời và ghi token usage. `Noul` là xác suất trả lời có; không hiển thị như xác suất đúng pháp luật.

## Khi triển khai

Đặt cùng các biến `JEV_AI_*` và `GEMINI_*` trong env/secret store của backend trên máy chủ. Compose đọc `.env` trên máy chủ qua `env_file`; giới hạn quyền đọc file cho tài khoản vận hành. Không commit file này, không thêm prefix `NEXT_PUBLIC_`, không truyền key qua props hoặc browser bundle.

Sau khi đổi env hoặc adapter, cần build/recreate các tiến trình backend đang dùng cấu hình đã nạp:

```powershell
docker compose build api query-worker ingestion-worker scheduler migrate
docker compose up -d --no-build --force-recreate api query-worker ingestion-worker scheduler
```

Production thêm `-f compose.yaml -f compose.production.yaml` vào các lệnh tương ứng. Nếu một hệ thống server khác dùng SDK TypeSafe chính thức, client phải đặt `baseURL: 'https://jev-ai.pro/api'` và `apiKey: process.env.JEV_AI_API_KEY`; SDK tự thêm `/v1/systemone`. Không đổi chỉ key rồi giữ URL TypeSafe mặc định.

## Lỗi và retry

Theo [hướng dẫn failure handling](https://jev-ai.pro/docs#errors), adapter không tự gửi lại decision POST, kể cả khi 429/502/503/504, timeout hoặc mất kết nối. Điều này tránh replay một request có outcome/tính phí chưa rõ. Kiểm tra API usage trước khi chạy lại; chờ `Retry-After` nếu server trả header này.

- 401: kiểm tra key và host/path; không fallback sang TypeSafe.
- 402: balance không đủ hoặc spending bị tạm ngừng; không retry unchanged.
- 404/422: sửa path, model hoặc body trước khi thử lại.
- 429: chờ theo Retry-After rồi chủ động chạy lại khi phù hợp.
- 502/503: chỉ retry có giới hạn sau khi xác nhận request đã thất bại.
- 504/timeout/mất kết nối: kiểm tra outcome trên tài khoản trước khi gửi lại.

GET model lookup có retry giới hạn vì không inference. `ProviderError.retry_after_seconds` giữ thời gian chờ từ Retry-After (giây hoặc HTTP-date), probe hiển thị giá trị này khi có lỗi. Lỗi được ghi bằng mã lỗi; không log response body, key hoặc nội dung chat. Smoke thành công chỉ chứng minh tích hợp kỹ thuật; benchmark pháp luật và beta gate vẫn cần nghiệm thu riêng.

Nếu routing trả `unavailable`, worker ghi `query_unavailable query_id=... code=jev:...` vào log và lưu mã vào `queries.error_code`, kể cả khi query có trạng thái `completed` để trả kết quả cho giao diện. Mã chẩn đoán nội bộ không nằm trong JSON result gửi về trình duyệt. Kiểm tra bằng `docker compose logs --tail 100 query-worker`; không kết luận provider đang ngừng hoạt động chỉ từ thông báo chung trên giao diện.
