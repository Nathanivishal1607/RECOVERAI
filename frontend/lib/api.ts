// Server-side API client. Runs inside the Next.js server (never the
// browser), so it talks to the backend over the Docker network directly —
// no CORS, no public API URL needed. See docs/frontend/dashboard.md.
import type {
  DashboardRead,
  RecoveryCaseDetailRead,
  RecoveryCaseListResponse,
} from "./types";

const BASE_URL = process.env.BACKEND_API_URL || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function apiFetch<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, { cache: "no-store" });
  } catch (err) {
    throw new ApiError(
      0,
      `Could not reach the RecoverAI API at ${BASE_URL}. Is the backend running?`
    );
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore — use statusText
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export function getDashboard(): Promise<DashboardRead> {
  return apiFetch<DashboardRead>("/api/dashboard");
}

export function listRecoveryCases(params: {
  limit?: number;
  offset?: number;
  status?: string;
}): Promise<RecoveryCaseListResponse> {
  const q = new URLSearchParams();
  q.set("limit", String(params.limit ?? 25));
  q.set("offset", String(params.offset ?? 0));
  if (params.status) q.set("status", params.status);
  return apiFetch<RecoveryCaseListResponse>(`/api/recovery-cases?${q.toString()}`);
}

export function getRecoveryCaseDetail(caseId: string): Promise<RecoveryCaseDetailRead> {
  return apiFetch<RecoveryCaseDetailRead>(`/api/recovery-cases/${caseId}`);
}
