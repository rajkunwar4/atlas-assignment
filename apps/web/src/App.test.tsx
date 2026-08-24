import { render, screen } from "@testing-library/react";
import { vi, test, expect } from "vitest";
import App from "./App";
test("renders the reconciliation product shell", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  render(<App />);
  expect(screen.getByText("Ledgerline")).toBeInTheDocument();
  expect(screen.getByText(/Find the differences/)).toBeInTheDocument();
});
