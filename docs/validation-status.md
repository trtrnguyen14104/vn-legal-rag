# Trạng thái kiểm chứng — 13/10/2026

Ứng dụng local có implementation các workflow và hạ tầng v1. Đây chưa phải kết quả nghiệm thu beta.

## Đã kiểm chứng

- Backend: **77 kiểm thử** unit/API/integration đạt trên PostgreSQL/Redis tách biệt; Ruff lint và format đạt. Có kiểm thử OAuth state/nonce/PKCE/session/CSRF/logout/replay với Google transport giả lập. Một cảnh báo deprecation của Starlette TestClient còn tồn tại.
- Đã refactor sang `backend/src/legal_rag`, tách HTTP routers, domain, ingestion, retrieval, embedding, providers, workflow, infrastructure, Celery và evals. OpenAPI khớp trước refactor; API/worker không nạp embedding runtime. Wheel/sdist build đạt; CLI và SQL migration trong wheel kiểm tra được ngoài thư mục repo. Xem [cấu trúc backend](backend-architecture.md).
- Đã rebuild và khởi động lại API/embedding/worker/scheduler local theo entrypoint mới. Trước/sau refactor đều có một tài khoản, 51 văn bản/phiên bản, 4.204 chunk, năm snapshot và một migration; active snapshot không đổi. Smoke E5/VnCoreNLP sau refactor đạt với năm vector, mất 6,34 giây. Không gọi inference trả phí trong đợt refactor.
- Google login/callback thực đã được người dùng xác nhận thành công; database local có một tài khoản. Không gọi Google thật trong pytest.
- Frontend: lint, TypeScript và production build đạt trên Windows và Docker Linux. Edge headless kiểm tra desktop 1.440 px/mobile 390 px, composer, câu hỏi gợi ý, chặn gửi khi chưa đăng nhập và không tràn ngang.
- Docker: API, Next.js, gateway, PostgreSQL, Redis, embedding, query worker, ingestion worker và scheduler hoạt động local. Health/corpus trả 200; lịch sử chưa đăng nhập trả 401; `/metrics` không được gateway expose.
- E5/VnCoreNLP thực dùng model revision `3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3`. Smoke năm vector có 1.024 chiều, unit norm và giữ nguyên nội dung mẫu.
- Corpus hiện có **51 văn bản**: 50 UTS và một bản VBPL. Có **4.204 chunk**, lớn nhất **384 token**; kiểm tra tái ghép chunk trên cả 51 phiên bản khớp nguyên văn được lưu.
- Đợt nhập 50 UTS mất **1.285,83 giây CPU** (khoảng 21 phút), truy xuất được 50 ứng viên hybrid. Đây là mẫu giới hạn, chưa phải benchmark toàn corpus hoặc tải đồng thời. Job giữ trạng thái `partial/sample_only` để không công bố độ bao phủ đầy đủ.
- Tra cứu metadata của `123/2020/NĐ-CP` trả `answered` khi Jev inference tắt, không gọi Gemini. Đây là đối chiếu metadata trong dataset; không xác nhận trực tiếp hiệu lực pháp lý hiện tại.
- Đã tạo **250 câu hỏi nháp** từ 50 nhóm văn bản: 205 development, 45 held-out. Chưa có câu nào được duyệt. Dry-run báo `dataset_ready=false`, `maximum_post_attempts=0`.
- Backup/restore đã được kiểm tra trước đó trong database tạm thuộc project test. Chưa diễn tập phục hồi toàn bộ artifact volume trên máy chủ mới.
- `.env`, raw data, benchmark draft, report và cache được gitignore. Log/report không chứa key hoặc nội dung chat cá nhân.

## Kiểm soát inference hiện tại

Jev mặc định tắt trong settings và `.env` local. Giới hạn mỗi query là 32 POST attempts; tổng benchmark mặc định 0. Quyết định giống hệt được tái sử dụng trong cùng query; timeout vẫn tính một lượt. Metadata lookup chạy trước routing và không cần Jev. Đã xác minh adapter trong API container trả `inference_disabled` với `post_attempts=0`.

Không dùng thêm Jev/Gemini trong đợt nhập mẫu và kiểm thử này. Các smoke trước đó xác nhận Jev AI/Gemini đã kết nối được; một decision trước đây đã trừ một credit, không được chạy lại trong đợt này. Giới hạn call hiện tại chưa phải hạn mức tiền hoặc budget tổng theo provider/ngày.

Benchmark không gửi lại routing/rerank giống hệt để đo lần hai. Khi hết budget/lỗi provider, report giữ `execution_status=incomplete` và số POST đã thử; report chưa hoàn tất không thể vượt beta gate. Gate cũng kiểm tra số nhận định support và đủ số câu held-out.

## Điều kiện nghiệm thu còn thiếu

1. Chưa nhập toàn bộ hai bucket, chưa có corpus đủ độ bao phủ cho public beta. Metadata chưa biết/xung đột và phạm vi hiệu lực vẫn cần duyệt.
2. Connector Chính phủ gặp `robots_unavailable`, Công báo gặp `ConnectError`. Lịch 02:00 Asia/Saigon đã cấu hình; chưa chứng minh cập nhật nguồn thật thành công hằng ngày.
3. Chưa có gold labels thủ công cho 250 câu; chưa đo Jev/BGE/RRF hoặc đạt các ngưỡng macro-F1/Recall/citation support/temporal errors.
4. Chưa kiểm chứng trọn luồng giải thích/so sánh có validation bằng provider thật với budget được người vận hành chủ động cho phép. Trước public beta cần thêm budget tổng theo account provider/ngày.
5. Chưa đo GPU, tải đồng thời, SLA hoặc triển khai HTTPS trên domain công khai.
6. Cây dependency development có năm cảnh báo high qua `braces` trong audit đã khảo sát; runtime audit lúc kiểm tra không báo vulnerability. Không dùng `npm audit fix --force` để hạ major Next.js ESLint config.
7. CI đã bổ sung PostgreSQL/Redis integration checks; workflow GitHub chưa chạy vì mã nguồn chưa commit/push.

Report nằm trong `data/reports`; draft ở `data/benchmark/draft.jsonl`; fixture nguồn ở `data/fixtures/official`. Dùng README để chạy lại kiểm chứng, không dùng số liệu kỹ thuật này làm cam kết chất lượng pháp lý.
