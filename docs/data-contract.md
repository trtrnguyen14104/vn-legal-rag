# Hợp đồng nhập dữ liệu

Hai bucket có adapter riêng; hợp đồng dưới đây dành cho nguồn bổ sung đã chuẩn hóa. JSONL chứa một object/văn bản, JSON chứa object hoặc array hoặc object `documents`, Parquet dùng cùng tên cột.

```json
{
  "title": "Tên đầy đủ của văn bản",
  "number": "Số ký hiệu chính xác",
  "kind": "Nghị định",
  "text": "Nguyên văn, gồm điều/khoản/điểm và xuống dòng",
  "source_url": "https://vbpl.vn/đường-dẫn-văn-bản",
  "source_kind": "legislation",
  "scope": "central",
  "authority": "Cơ quan ban hành",
  "effective_from": null,
  "effective_to": null,
  "effective_from_evidence": null,
  "effective_to_evidence": null,
  "license_evidence": "Thông tin nguồn và quyền tái sử dụng",
  "relations": []
}
```

`source_kind`: `legislation`, `consolidated`, `qa`, `commentary` hoặc `unknown`. Chỉ hai loại đầu và phạm vi trung ương được duyệt làm căn cứ. URL nguồn phải là HTTPS trên host chính thức đã allowlist. Không crawl URL do người dùng chat cung cấp.

Ngày dùng ISO hoặc DD/MM/YYYY. Mỗi ngày khai báo phải có nguyên văn bằng chứng chứa cùng ngày. Thiếu ngày được lưu null; không lấy `fetched_at` làm ngày hiệu lực. Interval phải có ngày hết hiệu lực sau ngày bắt đầu. `verified_at` chỉ dùng khi đã trực tiếp đối chiếu nguồn, không đặt bằng ngày tải snapshot.

Tệp không đúng schema, không có nguyên văn hoặc PDF scan được quarantine với vị trí bản ghi và mã lỗi. Provider/DB lỗi làm job thất bại, giữ snapshot cũ. Chỉ dùng tài liệu đã qua kiểm tra để publish.

Quan hệ gồm `relation`, `target_source_id`, `locator` tùy chọn và `evidence`. Quan hệ chỉ hỗ trợ điều hướng; không tự suy ra bản hợp nhất hay phạm vi hết hiệu lực của từng khoản từ nhãn quan hệ chung.
