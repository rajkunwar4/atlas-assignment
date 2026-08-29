import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, vi, test, expect } from "vitest";
import App from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("renders the reconciliation product shell", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  render(<App />);
  expect(screen.getByText("Ledgerline")).toBeInTheDocument();
  expect(screen.getByText(/Find the differences/)).toBeInTheDocument();
  expect(screen.queryByText("Upload meaning")).not.toBeInTheDocument();
  expect(screen.queryByText("File format")).not.toBeInTheDocument();
  expect(screen.getAllByText("Choose CSV")).toHaveLength(2);
});

test("explains why source uploads are unavailable during an open run", async () => {
  const reply = (body: unknown) =>
    Promise.resolve({ ok: true, json: async () => body } as Response);
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/health")) {
        return reply({ status: "ok", implementation: "python", version: "1" });
      }
      if (url.endsWith("/api/files")) return reply([]);
      if (url.includes("/api/runs/1/results")) {
        return reply({
          items: [],
          total: 0,
          page: 1,
          page_size: 50,
          summary: {},
        });
      }
      if (url.endsWith("/api/runs")) {
        return reply([
          {
            id: 1,
            status: "OPEN",
            summary: {},
            created_at: "2026-08-29T00:00:00Z",
          },
        ]);
      }
      if (url.endsWith("/api/audit")) return reply([]);
      if (url.endsWith("/api/settings")) return reply({});
      throw new Error(`Unexpected request: ${url}`);
    }),
  );

  render(<App />);

  expect(
    await screen.findAllByText("Uploads resume when run #1 closes"),
  ).toHaveLength(2);
  expect(screen.queryByText("Choose CSV")).not.toBeInTheDocument();
});
