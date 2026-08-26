import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

test("completes a run and preserves a manual match after correction", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Source files" }),
  ).toBeVisible();

  const uploads = page.locator('input[type="file"]');
  await uploads.nth(0).setInputFiles(resolve("shared/fixtures/ledger.csv"));
  await expect(page.getByText("ledger.csv", { exact: true })).toBeVisible();
  await uploads
    .nth(1)
    .setInputFiles(resolve("shared/fixtures/counterparty.csv"));
  await expect(
    page.getByText("counterparty.csv", { exact: true }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Start run" }).click();
  await expect(
    page.getByRole("heading", { name: "Exception workspace" }),
  ).toBeVisible();
  await page.getByRole("row", { name: /T-1011/ }).click();
  await expect(page.getByRole("heading", { name: "T-1011" })).toBeVisible();
  await expect(page.getByText(/Delta 170/)).toBeVisible();
  await page.getByLabel("Review note").fill("Confirmed with counterparty");
  await page.getByRole("button", { name: "Accept differences" }).click();
  await page.getByRole("button", { name: "Close detail" }).click();

  await page.getByRole("row", { name: /T-1015/ }).click();
  await page.getByLabel("Review note").fill("Clock difference confirmed");
  await page.getByRole("button", { name: "Accept differences" }).click();
  await page.getByRole("button", { name: "Close detail" }).click();

  await page.getByRole("row", { name: /T-1016/ }).click();
  await page
    .getByLabel("Match with")
    .selectOption({ label: "C-9001 · BTC-USD" });
  await page
    .getByLabel("Resolution note")
    .fill("Matched using broker evidence");
  await page.getByRole("button", { name: "Save manual match" }).click();
  await page.getByRole("button", { name: "Close detail" }).click();

  await expect(page.getByRole("button", { name: "Close run" })).toBeVisible();
  await page.getByRole("button", { name: "Close run" }).click();
  await expect(page.getByText("Closed", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Overview" }).click();
  await uploads
    .nth(0)
    .setInputFiles(resolve("shared/fixtures/ledger-correction.csv"));
  await expect(
    page.getByText("ledger-correction.csv", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page.getByRole("button", { name: "Close run" })).toBeVisible();
  await page.getByRole("row", { name: /T-1016.*C-9001/ }).click();
  await expect(
    page.locator(".drawer").getByText("Manually Matched", { exact: true }),
  ).toBeVisible();
});
