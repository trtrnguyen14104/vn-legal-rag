export type User = {
  id: string;
  name: string;
  csrf_token: string;
  is_admin: boolean;
};
export type Conversation = { id: string; title: string; created_at: string };
export type Citation = {
  source_id: string;
  document_id: string;
  version_id: string;
  title: string;
  number: string;
  locator: string;
  quote: string;
  url: string;
  verified_at: string | null;
  effective_from: string | null;
  effective_to: string | null;
  temporal_status: string;
};
export type QueryResult = {
  intent: string;
  status: string;
  answer: string;
  clarification: string | null;
  citations: Citation[];
  as_of_date: string;
  corpus_snapshot_id: string | null;
  freshness: { snapshot_at?: string; scope?: string };
  comparison: {
    locator: string;
    before: string;
    after: string;
    diff: string;
  }[];
};
export type Message = {
  id: string;
  question: string;
  result?: QueryResult;
  error?: string;
};
export type Corpus = {
  document_count: number;
  snapshot_id: string | null;
  updated_at: string | null;
  scope: string;
  sources: { source: string; checked_at: string | null; status: string }[];
};

export async function api<T>(
  path: string,
  options: RequestInit = {},
  csrf?: string,
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    credentials: "same-origin",
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    let message =
      response.status === 401
        ? "Vui lòng đăng nhập để tiếp tục."
        : "Chưa kết nối được dịch vụ. Vui lòng thử lại.";
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      /* The proxy may return a non-JSON error. */
    }
    throw new Error(message);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "Chưa xác minh";
  const day = new Date(value);
  return Number.isNaN(day.getTime())
    ? value
    : day.toLocaleDateString("vi-VN", { timeZone: "Asia/Saigon" });
}

export function sourceUrl(url: string) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" &&
      [
        "vanban.chinhphu.vn",
        "congbao.chinhphu.vn",
        "vbpl.vn",
        "vbpl-bientap-gateway.moj.gov.vn",
      ].includes(parsed.hostname)
      ? parsed.href
      : undefined;
  } catch {
    return undefined;
  }
}
