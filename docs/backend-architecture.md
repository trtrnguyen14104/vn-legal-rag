# Cấu trúc backend

Backend dùng src layout và modular monolith. API, worker và embedding là các tiến trình riêng, cùng sử dụng package `legal_rag`.

```text
backend/
├── src/legal_rag/
│   ├── config.py
│   ├── contracts.py
│   ├── cli.py
│   ├── domain/
│   │   ├── models.py
│   │   ├── legal.py
│   │   └── text.py
│   ├── api/
│   │   ├── app.py
│   │   ├── security.py
│   │   ├── schemas.py
│   │   └── routes/
│   │       ├── auth.py
│   │       ├── chat.py
│   │       ├── documents.py
│   │       └── admin.py
│   ├── ingestion/
│   │   ├── pipeline.py
│   │   ├── sources.py
│   │   └── datasets.py
│   ├── retrieval/
│   │   ├── retriever.py
│   │   └── fusion.py
│   ├── embedding/
│   │   ├── client.py
│   │   ├── service.py
│   │   ├── encoder.py
│   │   ├── chunking.py
│   │   └── model_setup.py
│   ├── llm/
│   │   ├── jev.py
│   │   └── gemini.py
│   ├── workflow/
│   │   ├── rag_flow.py
│   │   └── validation.py
│   ├── infrastructure/
│   │   ├── db.py
│   │   ├── redis.py
│   │   ├── http.py
│   │   └── migrations.py
│   ├── tasks/
│   │   ├── celery_app.py
│   │   └── jobs.py
│   └── evals/
│       └── benchmark.py
├── migrations/
└── tests/
    ├── conftest.py
    ├── fixtures/
    ├── unit/
    └── integration/
```

Mỗi thư mục Python là package với `__init__.py` nhẹ. `domain` chứa schema văn bản và hàm thuần cho định danh, thời gian, cấu trúc pháp lý. `contracts.py` chứa DTO dùng chung giữa API, worker và workflow; `api/schemas.py` chỉ chứa input của endpoint feedback/admin.

`api/app.py` lắp ứng dụng, middleware, health/metrics và router. `api/security.py` xử lý OAuth, session và CSRF. Kết nối Redis và Lua cho quota/khóa nằm ở `infrastructure/redis.py`, nên ingestion và worker không phải import HTTP layer. `infrastructure/http.py` giữ transport, lỗi provider, retry và metrics dùng chung; logic Jev/Gemini nằm trong `llm`.

`embedding/client.py` gọi dịch vụ nội bộ và không import PyTorch, NumPy hoặc JVM. Chỉ `embedding/service.py` dùng `encoder.py`; model được nạp một lần trong lifespan của tiến trình embedding. Ingestion dùng `domain.models.DocumentRecord`, gọi dịch vụ chuẩn bị chunk/vector rồi publish snapshot. Hybrid retrieval và RRF nằm trong `retrieval`; điều phối và kiểm tra câu trả lời nằm trong `workflow`.

Benchmark và model setup vẫn nằm trong package để CLI và wheel có thể import được. Các script vận hành nằm ở `scripts/` tại gốc repo.

## Entrypoint và packaging

| Tiến trình | Entrypoint |
|---|---|
| API | `uvicorn legal_rag.api.app:app --host 0.0.0.0 --port 8000 --no-access-log` |
| Embedding | `uvicorn legal_rag.embedding.service:app --host 0.0.0.0 --port 8001 --no-access-log` |
| Worker | `celery -A legal_rag.tasks.celery_app:celery worker -Q queries` hoặc `-Q ingestion` |
| Scheduler | `celery -A legal_rag.tasks.celery_app:celery beat` |
| CLI | `legal-rag` hoặc `python -m legal_rag.cli` |

Sau khi chuyển cấu trúc, chạy `uv sync --frozen --extra dev` trong `backend` để cài lại editable package. Không cần đặt `PYTHONPATH`. Dockerfile và Compose đã dùng các entrypoint mới. Tên task trên broker vẫn là `legal_rag.tasks.answer_query`, `ingest_source`, `daily_update`, `maintenance`; worker đăng ký qua `include=["legal_rag.tasks.jobs"]`.

SQL có nguồn duy nhất ở `backend/migrations`. Hatch đưa SQL vào `legal_rag/_migrations` trong wheel. Resolver dùng package resources khi cài wheel và thư mục migration của dự án khi cài editable; thiếu migration sẽ báo lỗi. Refactor không thêm migration, không đổi schema hoặc yêu cầu embedding lại.

Kiểm tra wheel ngoài thư mục repo:

```powershell
cd backend
uv build --wheel --sdist --out-dir ../data/refactor-build
.venv\Scripts\python.exe ../scripts/check_backend_package.py ../data/refactor-build/vn_legal_rag-0.1.0-py3-none-any.whl
```

Kiểm thử kiến trúc bảo vệ hợp đồng OpenAPI trước refactor, nội dung SQL, hướng phụ thuộc và việc API/worker không nạp embedding runtime. Các kiểm thử nghiệp vụ vẫn dùng provider giả lập; chạy pytest và kiểm tra packaging không tiêu credit Jev/Gemini.
