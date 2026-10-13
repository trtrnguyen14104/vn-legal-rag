"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, formatDate, sourceUrl, User } from "../../lib/api";

type Run = {
  id: string;
  source: string;
  status: string;
  started_at: string;
  counts: Record<string, number>;
  error_code: string | null;
};
type Review = {
  id: string;
  title: string;
  number: string;
  source_url: string;
  temporal_status: string;
  metadata: Record<string, unknown>;
};
type Snapshot = { id: string; created_at: string };

export default function Admin() {
  const [user, setUser] = useState<User | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    const [nextRuns, nextReviews, nextSnapshots] = await Promise.all([
      api<Run[]>("/admin/ingestion"),
      api<Review[]>("/admin/reviews"),
      api<Snapshot[]>("/admin/snapshots"),
    ]);
    setRuns(nextRuns);
    setReviews(nextReviews);
    setSnapshots(nextSnapshots);
  }, []);
  useEffect(() => {
    api<User>("/auth/me")
      .then((value) => {
        setUser(value);
        if (value.is_admin) return refresh();
        setMessage("Tài khoản này không có quyền quản trị.");
      })
      .catch((failure) => setMessage(failure.message));
  }, [refresh]);
  async function mutate(path: string, body?: unknown) {
    if (!user || busy) return;
    setBusy(true);
    setMessage("");
    try {
      await api(
        path,
        { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) },
        user.csrf_token,
      );
      await refresh();
      setMessage("Đã ghi nhận thao tác.");
    } catch (failure) {
      setMessage((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function review(event: FormEvent<HTMLFormElement>, id: string) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const approved =
      (event.nativeEvent as SubmitEvent).submitter?.getAttribute("value") ===
      "approve";
    void mutate(`/admin/reviews/${id}`, {
      approved,
      evidence: form.get("evidence"),
      effective_from: form.get("from") || null,
      effective_to: form.get("to") || null,
    });
  }
  return (
    <main className="admin-page">
      <header>
        <div>
          <Link href="/">Về bàn tra cứu</Link>
          <h1>Quản lý dữ liệu pháp luật</h1>
        </div>
        {user && <span>{user.name}</span>}
      </header>
      {message && (
        <p role="status" className="composer-error">
          {message}
        </p>
      )}
      {user?.is_admin && (
        <>
          <section>
            <h2>Nhập và cập nhật dữ liệu</h2>
            <p className="admin-note">
              Cập nhật tự động lúc 02:00 mỗi ngày. Chạy thủ công khi cần; dữ
              liệu lỗi được giữ lại để kiểm tra.
            </p>
            <div className="admin-actions">
              {["vanban", "congbao", "hf", "local"].map((source) => (
                <button
                  key={source}
                  disabled={busy}
                  onClick={() => void mutate("/admin/ingestion", { source })}
                >
                  {
                    {
                      vanban: "Văn bản Chính phủ",
                      congbao: "Công báo",
                      hf: "Hai bucket Hugging Face",
                      local: "Thư mục nhập local",
                    }[source as "vanban" | "congbao" | "hf" | "local"]
                  }
                </button>
              ))}
              <button
                disabled={busy}
                onClick={() =>
                  void refresh().catch((failure) => setMessage(failure.message))
                }
              >
                Làm mới trạng thái
              </button>
            </div>
            <div className="comparison-table">
              <table>
                <thead>
                  <tr>
                    <th>Nguồn</th>
                    <th>Thời điểm</th>
                    <th>Trạng thái</th>
                    <th>Kết quả</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <tr key={run.id}>
                      <td>{run.source}</td>
                      <td>{formatDate(run.started_at)}</td>
                      <td>{run.status}</td>
                      <td>
                        {Object.entries(run.counts)
                          .map(([key, value]) => `${key}: ${value}`)
                          .join(", ") ||
                          run.error_code ||
                          "Đang chờ"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section>
            <h2>Dữ liệu cần đối chiếu ({reviews.length})</h2>
            {!reviews.length && (
              <p className="admin-note">Chưa có văn bản đang chờ duyệt.</p>
            )}
            {reviews.map((item) => (
              <details className="review-item" key={item.id}>
                <summary>
                  {item.number} — {item.title}
                </summary>
                {sourceUrl(item.source_url) && (
                  <a
                    href={sourceUrl(item.source_url)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Mở nguồn chính thức ↗
                  </a>
                )}
                <pre>{JSON.stringify(item.metadata, null, 2)}</pre>
                <form onSubmit={(event) => review(event, item.id)}>
                  <label>
                    Bằng chứng và nội dung đối chiếu
                    <textarea
                      name="evidence"
                      required
                      minLength={10}
                      maxLength={4000}
                      rows={3}
                      placeholder="Ghi nguyên văn căn cứ xác nhận hoặc lý do từ chối."
                    />
                  </label>
                  <label>
                    Ngày bắt đầu hiệu lực đã xác minh
                    <input type="date" name="from" />
                  </label>
                  <label>
                    Ngày hết hiệu lực đã xác minh
                    <input type="date" name="to" />
                  </label>
                  <div className="admin-actions">
                    <button type="submit" value="approve" disabled={busy}>
                      Duyệt và cập nhật snapshot
                    </button>
                    <button type="submit" value="reject" disabled={busy}>
                      Từ chối
                    </button>
                  </div>
                </form>
              </details>
            ))}
          </section>
          <section>
            <h2>Snapshot đã lưu</h2>
            <p className="admin-note">
              Kích hoạt lại snapshot chỉ đổi dữ liệu phục vụ các câu hỏi mới.
            </p>
            <div className="comparison-table">
              <table>
                <thead>
                  <tr>
                    <th>Thời điểm tạo</th>
                    <th>Snapshot</th>
                    <th>Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshots.map((snapshot) => (
                    <tr key={snapshot.id}>
                      <td>{formatDate(snapshot.created_at)}</td>
                      <td>{snapshot.id.slice(0, 8)}</td>
                      <td>
                        <button
                          disabled={busy}
                          onClick={() =>
                            void mutate(
                              `/admin/snapshots/${snapshot.id}/activate`,
                            )
                          }
                        >
                          Kích hoạt
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </main>
  );
}
