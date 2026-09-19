export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}

export type User = { id: string; email: string; created_at: string };
export type Profile = {
  id: string; user_id: string; created_at: string; updated_at: string;
  display_name?: string | null; headline?: string | null; current_role?: string | null;
  location?: string | null; summary?: string | null; career_goal?: string | null;
  job_search_criteria?: string | null; preferred_email?: string | null; phone?: string | null;
  linkedin_url?: string | null; github_url?: string | null; portfolio_url?: string | null;
};
export type OnboardingStatus = {
  profile_exists: boolean; candidate_context_ready: boolean;
  latest_cv_draft: { id: string; state: string; created_at: string; updated_at: string } | null;
  adviser: { intake_exists: boolean; assessment_status: "review_ready" | "confirmed" | "stale" | null; confirmed_clarification_count: number };
};

export class SessionApi {
  private epoch = 0;
  private controllers = new Set<AbortController>();
  constructor(private token: string | null, private onAuthenticated401: () => void) {}
  replaceToken(token: string | null) { this.epoch += 1; this.token = token; this.controllers.forEach((controller) => controller.abort()); this.controllers.clear(); }
  async request<T>(path: string, init: RequestInit = {}, authenticated = true): Promise<T> {
    const epoch = this.epoch; const controller = new AbortController(); this.controllers.add(controller);
    try {
      const headers = new Headers(init.headers);
      if (authenticated && this.token) headers.set("Authorization", `Bearer ${this.token}`);
      if (init.body) headers.set("Content-Type", "application/json");
      const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers, signal: controller.signal });
      if (authenticated && response.status === 401 && epoch === this.epoch) this.onAuthenticated401();
      if (!response.ok) throw new ApiError(response.status, "Request could not be completed.");
      if (epoch !== this.epoch) throw new DOMException("Superseded auth session", "AbortError");
      return response.json() as Promise<T>;
    } finally { this.controllers.delete(controller); }
  }
}
