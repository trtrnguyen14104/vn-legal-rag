# Pháp lý Việt — Vietnamese Legal RAG Assistant

Next.js + FastAPI, PostgreSQL/pgvector, Redis/Celery, E5 và VnCoreNLP. Jev quyết định routing, chấm retrieval và kiểm tra nhận định; Gemini 2.5 Flash tạo câu trả lời. Nội dung chỉ được gửi về trình duyệt sau khi kiểm tra nguồn.

Trạng thái thực hiện và các mục chưa nghiệm thu: [docs/validation-status.md](docs/validation-status.md).

Cấu trúc backend, ranh giới module và các entrypoint: [docs/backend-architecture.md](docs/backend-architecture.md).

## Chạy local bằng Docker

Yêu cầu: Docker Compose >= 2.24.4, khoảng 12 GB RAM cho toàn bộ stack CPU; đây là cấu hình đề xuất, chưa phải số liệu benchmark. Model E5 cần tải khoảng 2,2 GB và được cache trong volume. Chạy từ thư mục repo.

```powershell
python scripts/init_env.py
# Nếu đã có .env: python scripts/init_env.py --complete-missing
```

Điền `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GEMINI_API_KEY`, `JEV_AI_API_KEY`, `HF_TOKEN` vào `.env`. Jev AI dùng `JEV_AI_BASE_URL=https://jev-ai.pro/api` và mặc định `JEV_AI_MODEL=jev-latest`. Không gửi key vào chat hoặc commit. Google OAuth dùng callback `http://localhost:3000/api/v1/auth/callback`; thêm origin `http://localhost:3000` trong Google Cloud Console. Quyền admin được cấp bằng Google `sub` trong `ADMIN_GOOGLE_SUBJECTS`, phân cách bằng dấu phẩy. Xem [hướng dẫn kết nối Jev AI](docs/jev-ai.md) để kiểm tra không inference và chạy đúng một decision nhỏ.

`init_env.py` sinh credential local. `--complete-missing` chỉ thêm cấu hình thiếu và sinh secret local đang trống; không thay credential đã điền. Đảm bảo `DATABASE_URL`/`REDIS_URL` khớp credential của Compose và host nội bộ `db`/`redis`.

Jev inference mặc định tắt (`JEV_AI_INFERENCE_ENABLED=false`); có key không tự cho phép chi tiêu. Tra cứu metadata có bằng chứng vẫn hoạt động; giải thích cần Jev validation sẽ báo unavailable. Chỉ bật inference khi người vận hành chủ động cho phép và đặt giới hạn call phù hợp; xem [budget Jev](docs/jev-ai.md).

```powershell
docker compose build api embedding client
docker compose --profile setup run --rm model-init
docker compose up -d
docker compose run --rm api legal-rag doctor
docker compose run --rm api legal-rag discover
docker compose run --rm api legal-rag ingest hf --enqueue
```

Mở **http://localhost:3000**. Migration chạy qua service `migrate`; không tạo schema ngầm trong API. `doctor` gọi provider thật với một mẫu kỹ thuật nhỏ và trả exit code khác 0 khi chưa đạt. Không tự đổi Gemini model khi key/model không khả dụng.

### Dữ liệu khởi tạo đã khảo sát

| Bucket | Schema active | Chính sách nhập |
|---|---|---|
| `trtrnguyen14104/vietnamese-legal-documents-bucket` | 171.556 metadata, 170.824 HTML, 1.033.255 quan hệ; join ID string | Join metadata/content/relationships bằng SQLite staging, chỉ văn bản trung ương; giữ ghi công CC BY 4.0 |
| `trtrnguyen14104/UTS_VLC-bucket` | 306 văn bản trong split `2026`, content Markdown và bảng nguồn | Nhập split `2026` đã sửa và provenance `metadata/2026_sources.json`; giữ ghi công Underthesea/MIT |

Không tải `legacy` 3,5 GB hoặc tự coi các split cũ là hiện hành. Ngày hiệu lực của UTS được để chưa biết nếu nguồn không cung cấp. Metadata snapshot không được đánh dấu là đã kiểm tra trực tiếp nguồn hôm nay. Khi hai nguồn có nội dung/hiệu lực khác nhau, phiên bản mới cần duyệt thay vì ghi đè âm thầm.

`HF_DOCUMENT_LIMIT=100` giới hạn số văn bản xử lý mỗi đợt/bucket để phù hợp phát triển CPU. Chạy lại ingestion `hf` để tiếp tục từ checkpoint. Raw artifact được giữ theo SHA-256, không sửa khi bucket đổi dữ liệu. Dữ liệu đã parse nhưng chưa publish được khôi phục khi chạy lại.

```powershell
docker compose run --rm api legal-rag ingest vanban --enqueue
docker compose run --rm api legal-rag ingest congbao --enqueue
```

Celery Beat chạy hằng ngày lúc **02:00 Asia/Saigon**. Chỉ chạy một scheduler. Connector dùng cửa sổ chồng lấn bảy ngày, rà lại URL đã nhập vào Chủ nhật, giới hạn dung lượng, tuân thủ robots và kiểm tra host của từng redirect. Phân trang postback chưa nhận diện sẽ báo `partial`, không công bố độ bao phủ đầy đủ. PDF scan báo `ocr_required`; không tự suy đoán nội dung.

### Nhập tài liệu đã chuẩn hóa

Đặt JSON/JSONL/Parquet vào `/data/import` của volume `legal-data`. Với PDF/DOCX/TXT/HTML, thêm `<filename>.metadata.json`. Hợp đồng nằm ở `docs/data-contract.md`.

```powershell
docker compose run --rm api legal-rag ingest local --enqueue
```

## Kiểm thử và benchmark

```powershell
cd backend
uv sync --frozen --extra dev
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests
uv run --extra dev ruff format --check src tests
cd ..
docker compose -p legal-rag-test -f compose.yaml -f compose.test.yaml up -d db redis
backend\.venv\Scripts\python.exe scripts/test_backend.py
cd frontend
npm ci
npm run lint
npm run build
```

Các kiểm thử tích hợp chỉ chạy trên project `legal-rag-test`, port loopback 55432/56379. Kiểm tra transaction publish bị lỗi, snapshot cũ, quota đồng thời, ownership, CSRF, dates/citations, regenerate một lần và abstention. Không gọi LLM thật trong pytest.

```powershell
docker compose run --rm api legal-rag benchmark-draft
# Duyệt và bổ sung nhãn vào /data/benchmark/draft.jsonl
# Chạy benchmark trong môi trường có extra benchmark + embedding + cpu:
legal-rag benchmark /data/benchmark/reviewed.jsonl --dry-run
# Lệnh sau sử dụng provider trả phí, chỉ chạy khi đã bật inference và chọn budget tổng:
legal-rag benchmark /data/benchmark/reviewed.jsonl --baseline
legal-rag benchmark-gate /data/reports/benchmark.json
```

`benchmark-draft` tạo 250 câu hỏi từ ít nhất 50 văn bản đã publish, tất cả `reviewed=false`. Người duyệt phải chỉnh câu hỏi, gán intent, relevance theo chunk ID và xác nhận development/held-out không chung nhóm văn bản. Benchmark tính macro-F1, Recall@20, nDCG@10, latency và abstention; BGE chỉ là baseline offline. Kết quả citation/temporal cần người duyệt xác nhận trong report trước khi chạy `benchmark-gate`. Chưa có dữ liệu duyệt thì **beta gate bị chặn**. Xem `docs/benchmark.md`.

## CUDA và môi trường công khai

CPU là mặc định, PyTorch CPU được khóa riêng để tránh tải CUDA libraries vào image CPU. CUDA cần NVIDIA Container Toolkit và driver tương thích CUDA 12.8:

```powershell
docker compose -f compose.yaml -f compose.cuda.yaml up -d --build embedding
```

CUDA dùng FP16; batch giảm khi OOM. Prefix E5 và normalization luôn được giữ. Không cam kết batch 32/64 trên GPU 4 GB. Chọn `EMBEDDING_REVISION` cố định khi release; pipeline lưu commit model thực tế trên chunk.

Trước triển khai công khai, đặt `ENVIRONMENT=production`, `PUBLIC_ORIGIN=https://<domain>`, `SITE_ADDRESS=<domain>` và callback OAuth HTTPS tương ứng trong env. Cần mở TCP 80/443 và cấu hình DNS của máy chủ.

```powershell
docker compose -f compose.yaml -f compose.production.yaml up -d --build
```

Caddy cấp TLS. Database, Redis và embedding không expose port công khai. Endpoint `/metrics` chỉ truy cập mạng nội bộ với credential service từ env. Log mặc định không ghi nội dung chat; lịch sử cá nhân hết hạn sau 90 ngày, xóa hội thoại xóa kèm câu hỏi/phản hồi. Readiness `/api/v1/health`, freshness `/api/v1/corpus`, OpenAPI `/api/docs`.

## Vận hành và giới hạn nghiệm thu

- Admin `/admin`: xem ingestion, chạy lại, duyệt xung đột và kích hoạt snapshot cũ. Không cho sửa phiên bản đã publish.
- Thông tin hiệu lực `unknown` không có nghĩa là còn hiệu lực. Hết hiệu lực một phần cần kiểm chứng đơn vị pháp lý trước khi giải thích hiện hành.
- Không tự tạo bản hợp nhất hoặc đưa nhận định chắc chắn về kết quả vụ việc cá nhân.
- API key thật và Google OAuth hợp lệ là điều kiện nghiệm thu luồng người dùng. HTTP 401 là lỗi xác thực, không retry hay tự đổi provider/model.
- Chỉ mở beta khi benchmark đạt gate, kiểm tra cập nhật theo nguồn thực tế và phục hồi backup đạt. Không tuyên bố SLA/chất lượng pháp lý từ test kỹ thuật.

Sao lưu database bằng `python scripts/backup.py`. Backup artifact volume `legal-data` cùng thời điểm để giữ nguyên nguồn chứng cứ. Cache model có thể tải lại. Chi tiết backup/restore và xử lý sự cố ở `docs/operations.md`.
