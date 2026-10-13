# Vận hành

## Khởi động và chẩn đoán

`model-init` chỉ tải VnCoreNLP. Lần khởi động embedding đầu tải E5 vào volume `/models/huggingface`; có thể mất thời gian. Health API kiểm tra PostgreSQL/Redis; `doctor` kiểm tra cả embedding/Jev/Gemini. Đừng coi API readiness là toàn bộ pipeline đã sẵn sàng.

401: kiểm tra key trong env và đúng provider Jev AI (`https://jev-ai.pro/api`), không dán key vào log. Jev decision POST không tự retry: 402 cần kiểm tra balance; 429 cần chờ Retry-After; timeout/504 cần kiểm tra outcome trước khi gửi lại. Các GET không inference và provider khác có retry giới hạn. 422: kiểm tra schema/ngân sách token. Quá thời gian query: maintenance đánh dấu failed, giải phóng lock bằng token ownership; kết quả đến muộn không ghi đè trạng thái đã kết thúc. Chi tiết ở [docs Jev AI](jev-ai.md).

Ingestion `partial`: xem JSON `/data/reports/<run_id>.json`, checkpoint và hàng duyệt admin. Batch HF cần chạy tiếp; schema/robots/pagination/OCR cần sửa connector hoặc bổ sung bản text. Ingestion crash không thay active snapshot. Phiên bản đã được ghi trước crash sẽ được nhận lại mà không embedding lần nữa khi retry.

Nhập mẫu từ profile đã tải dùng các script `ingest_profile_sample.py` và `import_metadata_sample.py`, với manifest artifact và job `partial/sample_only`. Chúng không đại diện cho nhập đủ bucket hoặc cập nhật nguồn trực tiếp. Đợt mẫu CPU 50 UTS mất khoảng 21 phút; `/prepare` có timeout riêng `EMBEDDING_DOCUMENT_TIMEOUT_SECONDS=600` cho tài liệu dài, không tăng timeout truy vấn người dùng.

## Backup và phục hồi

```powershell
python scripts/backup.py --project vietnamese-legal-rag
python scripts/backup.py --project legal-rag-test --verify-restore
```

Script dùng pg_dump custom format, ghi file vào `data/backups` và SHA-256 sidecar. `--verify-restore` chỉ cho project test: tạo database tạm, restore, kiểm tra schema, rồi xóa chính database tạm đó. Không phục hồi trực tiếp lên dữ liệu production trong script.

Khi phục hồi máy chủ: dừng API/workers/scheduler, restore database vào database mới, restore volume artifact cùng snapshot, kiểm tra `doctor` và số lượng snapshot/chunk, cập nhật DATABASE_URL trong env, rồi chạy stack. Redis session có thể bỏ để yêu cầu đăng nhập lại; các truy vấn đang chờ cần đánh dấu failed trước khi mở dịch vụ. Kích hoạt snapshot cũ từ admin nếu một đợt cập nhật có lỗi nội dung.

## Theo dõi

Prometheus metrics ở `/metrics` dùng Bearer credential service trong env, không được gateway expose. Mỗi tiến trình worker giữ metrics riêng; để tổng hợp production cần scrape từng tiến trình hoặc cấu hình Prometheus multiprocess, không suy từ metrics API rằng đã đo toàn bộ worker.

Theo dõi source freshness, ingestion failed/partial, tuổi snapshot, provider error, latency và tỷ lệ abstention/regeneration. Khi nguồn chính thức không truy cập được, giữ dữ liệu cũ và thông báo ngày cập nhật; không gắn nhãn hiện hành.

`JEV_AI_INFERENCE_ENABLED=false` chặn mọi Jev decision POST trong adapter, không ảnh hưởng embedding local. Sau khi đổi env cần tạo lại API/workers để nạp cấu hình. Budget theo query/benchmark đếm cả POST timeout; không phải hạn mức chi tiêu tổng của account provider. Cache decision chỉ sống trong một truy vấn; benchmark không gửi lại routing/rerank giống hệt để đo lần hai.

Docker volume và report có thể chứa dữ liệu hội thoại. Hạn chế quyền backup; không đưa chúng vào repo. Source nguyên bản là dữ liệu công bố; các tài khoản chỉ thấy hội thoại của mình.
