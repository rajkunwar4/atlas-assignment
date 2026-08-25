import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { Decimal } from "decimal.js";
import { parseCsv, FileValidationError } from "../src/ingestion.js";
import { reconcile, type Transaction } from "../src/domain.js";

const shared = (path: string) =>
  fileURLToPath(new URL(`../../../shared/${path}`, import.meta.url));
const fixture = (name: string) => readFileSync(shared(`fixtures/${name}`));
const expected = JSON.parse(
  readFileSync(shared("expected-results/wide.json"), "utf8"),
);
const invalid = JSON.parse(
  readFileSync(shared("expected-results/invalid-files.json"), "utf8"),
);

const transactions = (
  name: string,
  source: "LEDGER" | "COUNTERPARTY",
  start: number,
): Transaction[] =>
  parseCsv(fixture(name), source).map(({ data, raw }, index) => ({
    stableId: start + index,
    versionId: start + index,
    source,
    externalId: data.external_id,
    executedAt: new Date(data.executed_at),
    instrument: data.instrument,
    side: data.side,
    quantity: new Decimal(data.quantity),
    price: new Decimal(data.price),
    grossAmount: new Decimal(data.gross_amount),
    state: data.state,
    raw,
  }));

const outcome = (scenario: any) => {
  const items = reconcile(
    transactions(scenario.ledger_file, "LEDGER", 1000),
    transactions(scenario.counterparty_file, "COUNTERPARTY", 2000),
    [],
    new Set(),
  );
  return new Map(
    items.map((item) => [
      `${item.ledger?.external_id ?? ""}|${item.counterparty?.external_id ?? ""}`,
      {
        status: item.status,
        method: item.match_method,
        score: item.score,
        failing_fields: item.differences
          .filter((d: any) => !d.passed)
          .map((d: any) => d.field),
      },
    ]),
  );
};

for (const name of ["base", "corrected"]) {
  test(`extended fixtures reconcile as documented (${name})`, () => {
    const scenario = expected[name];
    const actual = outcome(scenario);
    assert.deepEqual(
      [...actual.keys()].sort(),
      Object.keys(scenario.items).sort(),
    );
    const summary: Record<string, number> = Object.fromEntries(
      Object.keys(scenario.summary).map((key) => [key, 0]),
    );
    for (const [key, item] of Object.entries<any>(scenario.items)) {
      assert.deepEqual(
        actual.get(key),
        {
          status: item.status,
          method: item.method,
          score: item.score,
          failing_fields: item.failing_fields,
        },
        `${key}: ${item.case}`,
      );
      summary[item.status] += 1;
    }
    assert.deepEqual(summary, scenario.summary);
  });
}

// A byte-order mark, CRLF endings, quoting and blank lines are not corrections.
for (const [variant, base, source] of [
  ["ledger-wide-reformatted.csv", "ledger-wide.csv", "LEDGER"],
  [
    "counterparty-wide-reformatted.csv",
    "counterparty-wide.csv",
    "COUNTERPARTY",
  ],
] as const) {
  test(`cosmetic reformatting changes no rows (${variant})`, () => {
    const fingerprints = new Map(
      parseCsv(fixture(base), source).map((row) => [
        row.data.external_id,
        row.fingerprint,
      ]),
    );
    const rows = parseCsv(fixture(variant), source);
    assert.ok(rows.length > 0);
    for (const row of rows)
      assert.equal(fingerprints.get(row.data.external_id), row.fingerprint);
  });
}

test("a row repeated identically inside one file is collapsed", () => {
  const identifiers = parseCsv(fixture("ledger-wide.csv"), "LEDGER").map(
    (row) => row.data.external_id,
  );
  assert.equal(identifiers.filter((id) => id === "T-2015").length, 1);
  assert.equal(new Set(identifiers).size, identifiers.length);
});

for (const item of invalid.files) {
  test(`rejects ${item.path}`, () => {
    assert.throws(
      () => parseCsv(fixture(item.path), item.source),
      (error: unknown) => {
        assert.ok(error instanceof FileValidationError, item.case);
        const reported = new Map(
          error.errors.map((e: any) => [e.row, String(e.reason).toLowerCase()]),
        );
        for (const expectedError of item.errors) {
          assert.ok(reported.has(expectedError.row), item.case);
          for (const fragment of expectedError.contains)
            assert.ok(
              reported.get(expectedError.row)!.includes(fragment.toLowerCase()),
              `${item.path} row ${expectedError.row}: ${fragment}`,
            );
        }
        return true;
      },
    );
  });
}
