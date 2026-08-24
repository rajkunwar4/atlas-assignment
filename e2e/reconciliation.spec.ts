import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

test("uploads, reconciles, and explains a material difference", async ({
  page,
}, testInfo) => {
  await page.goto("/");
  const backend = String(testInfo.project.metadata.backend);
  await page
    .getByLabel("Backend implementation")
    .selectOption({ label: backend });
  await expect(page.getByText("Source files")).toBeVisible();

  const uploads = page.locator('input[type="file"]');
  await uploads.nth(0).setInputFiles(resolve("shared/fixtures/ledger.csv"));
  await expect(page.getByText("ledger.csv")).toBeVisible();
  await uploads
    .nth(1)
    .setInputFiles(resolve("shared/fixtures/counterparty.csv"));
  await expect(page.getByText("counterparty.csv")).toBeVisible();

  await page.getByRole("button", { name: "Start run" }).click();
  await expect(
    page.getByRole("heading", { name: "Exception workspace" }),
  ).toBeVisible();
  await page.getByRole("row", { name: /Different T-1011/ }).click();
  await expect(page.getByRole("heading", { name: "T-1011" })).toBeVisible();
  await expect(page.getByText(/Delta 170/)).toBeVisible();
});
