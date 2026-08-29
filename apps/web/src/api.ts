import type { Audit, Health, Ingestion, Results, Run, Source } from "./types";
export class ApiClient {
  constructor(public baseUrl: string) {}
  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, init);
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(
        body?.detail?.error?.message ??
          body?.error?.message ??
          `Request failed (${response.status})`,
      );
    }
    return response.json();
  }
  health = () => this.request<Health>("/api/health");
  files = () => this.request<Ingestion[]>("/api/files");
  upload = (source: Source, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return this.request<Ingestion>(`/api/files?source=${source}`, {
      method: "POST",
      body: form,
    });
  };
  runs = () => this.request<Run[]>("/api/runs");
  createRun = () => this.request<Run>("/api/runs", { method: "POST" });
  results = (runId: number, status = "", search = "") =>
    this.request<Results>(
      `/api/runs/${runId}/results?status=${encodeURIComponent(status)}&search=${encodeURIComponent(search)}`,
    );
  match = (
    ledger_transaction_id: number,
    counterparty_transaction_id: number,
    note: string,
  ) =>
    this.request("/api/resolutions/match", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ledger_transaction_id,
        counterparty_transaction_id,
        note,
      }),
    });
  accept = (transaction_id: number, note: string) =>
    this.request("/api/resolutions/accept-unmatched", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transaction_id, note }),
    });
  acceptDifferences = (item_id: number, note: string) =>
    this.request("/api/resolutions/accept-differences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id, note }),
    });
  closeRun = (runId: number) =>
    this.request<Run>(`/api/runs/${runId}/close`, { method: "POST" });
  audit = () => this.request<Audit[]>("/api/audit");
  settings = () =>
    this.request<Record<string, string | number>>("/api/settings");
  updateSettings = (body: Record<string, string | number>) =>
    this.request<Record<string, string | number>>("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
}
