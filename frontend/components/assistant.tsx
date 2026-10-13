"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  api,
  Citation,
  Conversation,
  Corpus,
  formatDate,
  Message,
  QueryResult,
  sourceUrl,
  User,
} from "../lib/api";

function BookMark({ small = false }: { small?: boolean }) {
  return (
    <svg
      width={small ? 25 : 38}
      height={small ? 25 : 38}
      viewBox="0 0 40 40"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M7 9C12 7 16 8 20 11C24 8 28 7 33 9V29C28 27 24 28 20 31C16 28 12 27 7 29V9Z"
        stroke="currentColor"
        strokeWidth="1.7"
      />
      <path
        d="M20 11V31M11 14H16M24 14H29M11 19H16M24 19H29"
        stroke="currentColor"
        strokeWidth="1.5"
      />
    </svg>
  );
}

const suggestions = [
  {
    title: "Tra cứu văn bản",
    text: "Nghị định 123/2020/NĐ-CP có hiệu lực từ ngày nào?",
  },
  {
    title: "Hiểu một quy định",
    text: "Giải thích điều kiện giao kết hợp đồng lao động bằng ngôn ngữ dễ hiểu.",
  },
  {
    title: "Đối chiếu thay đổi",
    text: "Tôi muốn so sánh hai phiên bản của một văn bản pháp luật.",
  },
];

const resultLabels: Record<string, string> = {
  answered: "Có nguồn trích dẫn",
  clarification_needed: "Cần làm rõ",
  insufficient_evidence: "Chưa đủ căn cứ",
  out_of_scope: "Ngoài phạm vi hỗ trợ",
  unavailable: "Dịch vụ tạm thời gián đoạn",
};

export default function Assistant() {
  const [user, setUser] = useState<User | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [corpus, setCorpus] = useState<Corpus | null>(null);
  const [history, setHistory] = useState<Conversation[]>([]);
  const [conversation, setConversation] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [hasOlderMessages, setHasOlderMessages] = useState(false);
  const [question, setQuestion] = useState("");
  const [asOf, setAsOf] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const [sources, setSources] = useState<Citation[]>([]);
  const [selectedSource, setSelectedSource] = useState(0);
  const [mobileHistory, setMobileHistory] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);
  const [documentSearch, setDocumentSearch] = useState("");
  const [documents, setDocuments] = useState<
    { id: string; title: string; number: string }[]
  >([]);
  const [versions, setVersions] = useState<
    { id: string; created_at: string; effective_from: string | null }[]
  >([]);
  const [versionIds, setVersionIds] = useState<string[]>([]);
  const stream = useRef<EventSource | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);
  const input = useRef<HTMLTextAreaElement | null>(null);
  const compareDialog = useRef<HTMLElement | null>(null);
  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await api<Conversation[]>("/conversations"));
    } catch {
      /* Expired sessions are handled on submission. */
    }
  }, []);
  useEffect(() => {
    let active = true;
    api<User>("/auth/me")
      .then((value) => {
        if (active) {
          setUser(value);
          void refreshHistory();
        }
      })
      .catch(() => {})
      .finally(() => {
        if (active) setAuthLoading(false);
      });
    api<Corpus>("/corpus")
      .then((value) => {
        if (active) setCorpus(value);
      })
      .catch(() => {});
    return () => {
      active = false;
      stream.current?.close();
    };
  }, [refreshHistory]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, progress]);

  useEffect(() => {
    if (!compareOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = compareDialog.current;
    dialog?.querySelector<HTMLElement>("button, input")?.focus();
    const trapFocus = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !dialog) return;
      const controls = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), [tabindex="0"]',
        ),
      );
      const first = controls[0];
      const last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", trapFocus);
    return () => {
      document.removeEventListener("keydown", trapFocus);
      previous?.focus();
    };
  }, [compareOpen]);

  async function openConversation(id: string, older = false) {
    if (busy) return;
    try {
      const rows = await api<
        {
          id: string;
          request: { question: string };
          status: string;
          result: QueryResult | null;
        }[]
      >(`/conversations/${id}?offset=${older ? historyOffset : 0}`);
      setConversation(id);
      setHistoryOffset((older ? historyOffset : 0) + rows.length);
      setHasOlderMessages(rows.length === 200);
      setMessages((previous) => [
        ...rows.map((row) => ({
          id: row.id,
          question: row.request.question,
          result: row.result || undefined,
          error:
            row.status === "failed"
              ? "Câu hỏi này chưa hoàn tất."
              : row.status !== "completed"
                ? "Câu hỏi đang xử lý; mở lại hội thoại sau ít phút."
                : undefined,
        })),
        ...(older ? previous : []),
      ]);
      setSources(
        rows.filter((row) => row.result).at(-1)?.result?.citations || [],
      );
      setSelectedSource(0);
      setError("");
      setMobileHistory(false);
    } catch (failure) {
      setError((failure as Error).message);
    }
  }
  async function removeConversation(id: string) {
    if (!user || busy) return;
    try {
      await api(`/conversations/${id}`, { method: "DELETE" }, user.csrf_token);
      if (conversation === id) {
        setConversation(null);
        setMessages([]);
        setSources([]);
      }
      await refreshHistory();
    } catch (failure) {
      setError((failure as Error).message);
    }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !question.trim()) return;
    if (!user) {
      setError("Đăng nhập Google để đặt câu hỏi và lưu lịch sử tra cứu.");
      return;
    }
    const text = question.trim();
    setBusy(true);
    setError("");
    setProgress("Đang nhận câu hỏi");
    try {
      const created = await api<{ query_id: string; conversation_id: string }>(
        "/queries",
        {
          method: "POST",
          body: JSON.stringify({
            question: text,
            conversation_id: conversation,
            as_of_date: asOf || null,
            version_ids: versionIds.length === 2 ? versionIds : [],
          }),
        },
        user.csrf_token,
      );
      setConversation(created.conversation_id);
      setMessages((old) => [...old, { id: created.query_id, question: text }]);
      setQuestion("");
      setSources([]);
      stream.current?.close();
      const events = new EventSource(
        `/api/v1/queries/${created.query_id}/events`,
      );
      stream.current = events;
      const finish = () => {
        events.close();
        setBusy(false);
        setProgress("");
        void refreshHistory();
      };
      events.addEventListener("progress", (event) =>
        setProgress(JSON.parse((event as MessageEvent).data).message),
      );
      events.addEventListener("result", (event) => {
        const result: QueryResult = JSON.parse((event as MessageEvent).data);
        setMessages((old) =>
          old.map((m) => (m.id === created.query_id ? { ...m, result } : m)),
        );
        setSources(result.citations);
        setSelectedSource(0);
        finish();
      });
      events.addEventListener("failure", (event) => {
        const message = JSON.parse((event as MessageEvent).data).message;
        setMessages((old) =>
          old.map((m) =>
            m.id === created.query_id ? { ...m, error: message } : m,
          ),
        );
        finish();
      });
      events.onerror = () => {
        setError(
          "Kết nối tiến độ bị gián đoạn. Mở lại hội thoại để xem kết quả.",
        );
        finish();
      };
    } catch (failure) {
      setError((failure as Error).message);
      setBusy(false);
      setProgress("");
    }
  }
  async function searchDocuments(event: FormEvent) {
    event.preventDefault();
    try {
      setDocuments(
        await api(`/documents?q=${encodeURIComponent(documentSearch)}`),
      );
    } catch (failure) {
      setError((failure as Error).message);
    }
  }
  async function selectDocument(id: string) {
    try {
      setVersions(await api(`/documents/${id}/versions`));
      setVersionIds([]);
    } catch (failure) {
      setError((failure as Error).message);
    }
  }
  async function feedback(id: string, helpful: boolean) {
    if (!user) return;
    try {
      await api(
        `/queries/${id}/feedback`,
        { method: "POST", body: JSON.stringify({ helpful }) },
        user.csrf_token,
      );
      setError("Đã ghi nhận phản hồi của bạn.");
    } catch (failure) {
      setError((failure as Error).message);
    }
  }
  async function logout() {
    if (!user) return;
    try {
      await api("/auth/logout", { method: "POST" }, user.csrf_token);
      setUser(null);
      setHistory([]);
      setMessages([]);
      setSources([]);
      setConversation(null);
    } catch (failure) {
      setError((failure as Error).message);
    }
  }
  const source = sources[selectedSource];
  const ready = Boolean(corpus?.snapshot_id);

  return (
    <div className="workspace">
      <aside
        className={`sidebar ${mobileHistory ? "is-open" : ""}`}
        aria-label="Lịch sử tra cứu"
      >
        <button
          className="mobile-close"
          aria-label="Đóng lịch sử"
          onClick={() => setMobileHistory(false)}
        >
          ×
        </button>
        <Link href="/" className="brand">
          <BookMark />
          <span>
            Pháp lý Việt<small>Tra cứu có căn cứ</small>
          </span>
        </Link>
        <button
          className="new-chat"
          disabled={busy}
          onClick={() => {
            setConversation(null);
            setMessages([]);
            setHasOlderMessages(false);
            setHistoryOffset(0);
            setSources([]);
            setMobileHistory(false);
            input.current?.focus();
          }}
        >
          <span aria-hidden="true">＋</span> Tra cứu mới
        </button>
        <div className="history-title">Lịch sử của bạn</div>
        <nav className="history-list">
          {history.length ? (
            history.map((item) => (
              <div
                key={item.id}
                className={`history-row ${item.id === conversation ? "selected" : ""}`}
              >
                <button
                  disabled={busy}
                  onClick={() => void openConversation(item.id)}
                >
                  <span>{item.title}</span>
                  <small>{formatDate(item.created_at)}</small>
                </button>
                <button
                  className="delete-chat"
                  disabled={busy}
                  aria-label={`Xóa hội thoại ${item.title}`}
                  onClick={() => void removeConversation(item.id)}
                >
                  ×
                </button>
              </div>
            ))
          ) : (
            <p className="sidebar-empty">
              Các lần tra cứu sẽ được lưu ở đây sau khi bạn đăng nhập.
            </p>
          )}
        </nav>
        <div className="sidebar-bottom">
          <div className="scope-marker">
            <span className={`status-dot ${ready ? "ready" : ""}`} />
            {ready ? "Có bộ dữ liệu để tra cứu" : "Chưa có dữ liệu đã xác minh"}
          </div>
          <p>
            Phạm vi văn bản trung ương
            <br />
            Đã nhập: {corpus?.document_count ?? 0} văn bản
            <br />
            Cập nhật: {formatDate(corpus?.updated_at)}
          </p>
          {user?.is_admin && (
            <Link className="admin-link" href="/admin">
              Quản lý dữ liệu
            </Link>
          )}
          {user ? (
            <div className="user-row">
              <span className="avatar">{user.name.slice(0, 1)}</span>
              <span>{user.name}</span>
              <button onClick={() => void logout()} disabled={busy}>
                Thoát
              </button>
            </div>
          ) : (
            <a className="google-login" href="/api/v1/auth/login">
              {authLoading ? "Đang kiểm tra tài khoản…" : "Đăng nhập Google"}
            </a>
          )}
        </div>
      </aside>
      <main className="main-panel">
        <header className="workspace-header">
          <div>
            <button
              className="mobile-menu"
              aria-label="Mở lịch sử"
              onClick={() => setMobileHistory(!mobileHistory)}
            >
              ☰
            </button>
            <span>Bàn tra cứu pháp luật</span>
          </div>
          <span className="header-note">Văn bản, thời điểm và căn cứ</span>
        </header>
        <div className="chat-scroll">
          {!messages.length ? (
            <section className="welcome">
              <div className="welcome-symbol">
                <BookMark />
              </div>
              <h1>
                Hiểu quy định.
                <br />
                Có nguồn để đối chiếu.
              </h1>
              <p>
                Đặt câu hỏi bằng tiếng Việt để tìm văn bản, hiểu một điều khoản
                hoặc đối chiếu những thay đổi.
              </p>
              <div className="suggestions">
                {suggestions.map((item, i) => (
                  <button
                    key={item.title}
                    onClick={() => {
                      setQuestion(item.text);
                      input.current?.focus();
                    }}
                  >
                    <span className="suggestion-icon" aria-hidden="true">
                      {["§", "?", "⇄"][i]}
                    </span>
                    <span>
                      <strong>{item.title}</strong>
                      <small>{item.text}</small>
                    </span>
                    <span className="suggestion-action" aria-hidden="true">
                      ＋
                    </span>
                  </button>
                ))}
              </div>
              <div className="evidence-note">
                <span aria-hidden="true">▤</span>
                <p>
                  Mỗi câu trả lời có căn cứ đi kèm trích dẫn. Khi nguồn chưa đủ,
                  trợ lý sẽ nêu rõ thông tin còn thiếu.
                </p>
              </div>
            </section>
          ) : (
            <div className="message-list">
              {hasOlderMessages && conversation && (
                <button
                  disabled={busy}
                  onClick={() => void openConversation(conversation, true)}
                >
                  Xem câu hỏi cũ hơn
                </button>
              )}
              {messages.map((message) => (
                <article className="exchange" key={message.id}>
                  <div className="question">
                    <span>Bạn</span>
                    <p>{message.question}</p>
                  </div>
                  {message.result && (
                    <div className="answer">
                      <div className="answer-heading">
                        <BookMark small />
                        <strong>Pháp lý Việt</strong>
                        <span
                          className={`answer-status ${message.result.status === "answered" ? "supported" : ""}`}
                        >
                          {resultLabels[message.result.status] ||
                            "Chưa đủ căn cứ"}
                        </span>
                      </div>
                      <div className="answer-text">
                        {message.result.answer || message.result.clarification}
                      </div>
                      {message.result.comparison?.length > 0 && (
                        <div className="comparison-table">
                          <table>
                            <caption>
                              Đối chiếu nguyên văn hai phiên bản
                            </caption>
                            <thead>
                              <tr>
                                <th>Điều khoản</th>
                                <th>Phiên bản trước</th>
                                <th>Phiên bản sau</th>
                              </tr>
                            </thead>
                            <tbody>
                              {message.result.comparison.map((row, i) => (
                                <tr key={i}>
                                  <td>{row.locator}</td>
                                  <td>{row.before || "Không có"}</td>
                                  <td>{row.after || "Không có"}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                      {message.result.citations.length > 0 && (
                        <div className="citation-chips">
                          {message.result.citations.map((cite, i) => (
                            <button
                              key={cite.source_id}
                              onClick={() => {
                                setSources(message.result!.citations);
                                setSelectedSource(i);
                              }}
                            >
                              <span>{i + 1}</span>
                              {cite.number}
                              <small>{cite.locator}</small>
                            </button>
                          ))}
                        </div>
                      )}
                      <div className="answer-footer">
                        <span>
                          Thời điểm tra cứu:{" "}
                          {formatDate(message.result.as_of_date)}
                        </span>
                        <div>
                          <button
                            onClick={() => void feedback(message.id, true)}
                          >
                            Hữu ích
                          </button>
                          <button
                            onClick={() => void feedback(message.id, false)}
                          >
                            Cần cải thiện
                          </button>
                        </div>
                      </div>
                    </div>
                  )}
                  {message.error && (
                    <p className="inline-error" role="alert">
                      {message.error}
                    </p>
                  )}
                </article>
              ))}
            </div>
          )}
          {busy && (
            <div className="progress" role="status">
              <span className="working-indicator" />
              {progress}
              <small>Câu trả lời sẽ xuất hiện sau khi kiểm tra nguồn.</small>
            </div>
          )}
          <div ref={bottom} />
        </div>
        <div className="composer-area">
          {error && (
            <p className="composer-error" role="status">
              {error}
              <button aria-label="Đóng thông báo" onClick={() => setError("")}>
                ×
              </button>
            </p>
          )}
          <div className="query-tools">
            <label>
              Thời điểm tra cứu
              <input
                type="date"
                value={asOf}
                onChange={(e) => setAsOf(e.target.value)}
                disabled={busy}
                aria-label="Ngày tra cứu; để trống để dùng hôm nay"
              />
            </label>
            <button
              className={versionIds.length === 2 ? "active-tool" : ""}
              disabled={busy || !user}
              onClick={() => setCompareOpen(true)}
            >
              ⇄{" "}
              {versionIds.length === 2
                ? "Đã chọn hai phiên bản"
                : "So sánh phiên bản"}
            </button>
            {versionIds.length > 0 && (
              <button disabled={busy} onClick={() => setVersionIds([])}>
                Bỏ chọn
              </button>
            )}
          </div>
          <form className="composer" onSubmit={submit}>
            <label className="sr-only" htmlFor="question">
              Câu hỏi pháp luật
            </label>
            <textarea
              ref={input}
              id="question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Bạn muốn tìm hiểu quy định nào?"
              maxLength={4000}
              rows={2}
              disabled={busy}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  e.currentTarget.form?.requestSubmit();
                }
              }}
            />
            <button
              className="send"
              type="submit"
              disabled={busy || question.trim().length < 3}
              aria-label="Gửi câu hỏi"
            >
              {busy ? "…" : "↑"}
            </button>
          </form>
          <p className="composer-caption">
            Tra cứu và giải thích quy định. Kết quả phụ thuộc phạm vi và thời
            điểm của nguồn dữ liệu.
          </p>
        </div>
      </main>
      <aside className="source-panel" aria-label="Nguồn đối chiếu">
        <header>
          <span>Nguồn đối chiếu</span>
          <span className="source-count">{sources.length}</span>
        </header>
        {source ? (
          <div className="source-content">
            <div className="source-tabs">
              {sources.map((cite, i) => (
                <button
                  key={cite.source_id}
                  aria-pressed={selectedSource === i}
                  onClick={() => setSelectedSource(i)}
                >
                  {i + 1}
                </button>
              ))}
            </div>
            <p className="document-kind">Văn bản được trích dẫn</p>
            <h2>{source.number}</h2>
            <p className="document-title">{source.title}</p>
            <dl className="document-meta">
              <div>
                <dt>Bắt đầu hiệu lực</dt>
                <dd>{formatDate(source.effective_from)}</dd>
              </div>
              <div>
                <dt>Hết hiệu lực</dt>
                <dd>{formatDate(source.effective_to)}</dd>
              </div>
              <div>
                <dt>Đối chiếu nguồn</dt>
                <dd>{formatDate(source.verified_at)}</dd>
              </div>
            </dl>
            <div className="source-warning">
              Ngày bắt đầu hiệu lực không tự xác nhận văn bản còn hiệu lực hiện
              nay.
            </div>
            <h3>{source.locator}</h3>
            <blockquote>{source.quote}</blockquote>
            {sourceUrl(source.url) && (
              <a
                className="original-source"
                href={sourceUrl(source.url)}
                target="_blank"
                rel="noopener noreferrer"
              >
                Xem tại nguồn chính thức ↗
              </a>
            )}
          </div>
        ) : (
          <div className="source-empty">
            <div className="source-illustration" aria-hidden="true">
              <span>§</span>
              <div />
              <div />
              <div />
            </div>
            <h2>Đọc cùng văn bản gốc</h2>
            <p>
              Nguồn trích dẫn, điều khoản và thông tin hiệu lực sẽ xuất hiện ở
              đây khi có kết quả tra cứu.
            </p>
          </div>
        )}
        <footer>
          <span className="status-dot" />
          <p>
            Phạm vi: văn bản trung ương
            <br />
            Chỉ trả lời từ nguồn đã nhập và đối chiếu.
          </p>
        </footer>
      </aside>
      {compareOpen && (
        <div className="dialog-backdrop">
          <section
            ref={compareDialog}
            className="compare-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="compare-title"
            onKeyDown={(e) => {
              if (e.key === "Escape") setCompareOpen(false);
            }}
          >
            <div className="dialog-heading">
              <h2 id="compare-title">Chọn phiên bản để đối chiếu</h2>
              <button
                onClick={() => setCompareOpen(false)}
                aria-label="Đóng so sánh"
              >
                ×
              </button>
            </div>
            <p>
              Tìm một văn bản rồi chọn hai phiên bản đã được kiểm tra trong dữ
              liệu.
            </p>
            <form className="document-search" onSubmit={searchDocuments}>
              <input
                aria-label="Tên hoặc số văn bản"
                value={documentSearch}
                onChange={(e) => setDocumentSearch(e.target.value)}
                placeholder="Số văn bản hoặc tên văn bản"
              />
              <button type="submit">Tìm</button>
            </form>
            <div className="document-results">
              {documents.map((doc) => (
                <button
                  key={doc.id}
                  onClick={() => void selectDocument(doc.id)}
                >
                  <strong>{doc.number}</strong> {doc.title}
                </button>
              ))}
            </div>
            <div className="version-list">
              {versions.map((version, i) => (
                <label key={version.id}>
                  <input
                    type="checkbox"
                    checked={versionIds.includes(version.id)}
                    disabled={
                      versionIds.length === 2 &&
                      !versionIds.includes(version.id)
                    }
                    onChange={(e) =>
                      setVersionIds((old) =>
                        e.target.checked
                          ? [...old, version.id]
                          : old.filter((id) => id !== version.id),
                      )
                    }
                  />
                  <span>
                    Phiên bản {i + 1}
                    <small>
                      Lưu ngày {formatDate(version.created_at)}; hiệu lực từ{" "}
                      {formatDate(version.effective_from)}
                    </small>
                  </span>
                </label>
              ))}
              {versions.length === 1 && (
                <p>
                  Hiện chỉ có một phiên bản đã kiểm tra. Chưa đủ căn cứ để so
                  sánh.
                </p>
              )}
            </div>
            <button
              className="primary-button"
              disabled={versionIds.length !== 2}
              onClick={() => {
                setCompareOpen(false);
                setQuestion(
                  question ||
                    "So sánh hai phiên bản đã chọn và chỉ rõ các thay đổi có căn cứ.",
                );
                input.current?.focus();
              }}
            >
              Dùng hai phiên bản này
            </button>
          </section>
        </div>
      )}
    </div>
  );
}
