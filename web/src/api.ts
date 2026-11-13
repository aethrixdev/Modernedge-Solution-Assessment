// Small typed wrappers around the FastAPI endpoints.

export type Row = Record<string, string | number | boolean | null>;

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export interface KpiResponse { buildings: Row[]; ar_aging: Row[]; ar_items: Row[] }
export interface BuildingViewResponse { posts: Row[]; people: Row[]; dates: string[] }
export interface ChatResponse {
  answer: string;
  queries: { sql: string; result_preview: string }[];
  citations: { source: string; ref: string }[];
}

export const api = {
  kpis: () => get<KpiResponse>("/api/kpis"),
  kpiFacts: (building: string, kpi: string) =>
    get<Row[]>(`/api/kpis/${encodeURIComponent(building)}/${encodeURIComponent(kpi)}`),
  buildings: () => get<Row[]>("/api/buildings"),
  buildingView: (building: string, day: string) =>
    get<BuildingViewResponse>(`/api/building-view?building=${encodeURIComponent(building)}&day=${day}`),
  source: (source: string, ref: string) =>
    get<Row[]>(`/api/source?source=${encodeURIComponent(source)}&ref=${encodeURIComponent(ref)}`),
  reviewQueue: () => get<Row[]>("/api/review-queue"),
  decide: (id: string, action: "approve" | "reject", code?: string) =>
    post<Row>(`/api/review-queue/${encodeURIComponent(id)}`, { action, code }),
  payroll: () => get<{ submissions: Row[]; audit: Row[] }>("/api/payroll"),
  exceptions: () => get<Row[]>("/api/exceptions"),
  chat: (question: string, history: { role: string; content: string }[]) =>
    post<ChatResponse>("/api/chat", { question, history }),
};

// Money with 2 decimals; "–" when there is no value.
export const money = (v: unknown) =>
  v === null || v === undefined ? "–" : Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
// A rate like 0.123 shown as "12.3%".
export const pct = (v: unknown) => (v === null || v === undefined ? "–" : `${(Number(v) * 100).toFixed(1)}%`);
