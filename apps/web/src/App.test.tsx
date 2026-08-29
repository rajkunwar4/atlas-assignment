import { render, screen } from "@testing-library/react";
import { vi, test, expect } from "vitest";
import App from "./App";
test("renders the reconciliation product shell", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  render(<App />);
  expect(screen.getByText("Ledgerline")).toBeInTheDocument();
  expect(screen.getByText(/Find the differences/)).toBeInTheDocument();
  expect(screen.queryByText("Upload meaning")).not.toBeInTheDocument();
  expect(screen.queryByText("File format")).not.toBeInTheDocument();
  expect(screen.getAllByText("Choose CSV")).toHaveLength(2);
});
