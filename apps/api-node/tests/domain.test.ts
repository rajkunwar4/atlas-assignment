import test from "node:test";
import assert from "node:assert/strict";
import { Decimal } from "decimal.js";
import { candidateScore, reconcile, type Transaction } from "../src/domain.js";
const tx = (
  id: number,
  source: "LEDGER" | "COUNTERPARTY" = "LEDGER",
  external = `T-${id}`,
  minutes = 0,
  gross = "100",
  state: "SETTLED" | "CANCELLED" = "SETTLED",
): Transaction => ({
  stableId: id,
  source,
  externalId: external,
  executedAt: new Date(Date.UTC(2025, 0, 1, 0, minutes)),
  instrument: "BTC-USD",
  side: "BUY",
  quantity: new Decimal(1),
  price: new Decimal(100),
  grossAmount: new Decimal(gross),
  state,
});
test("exact IDs compare material differences", () => {
  const items = reconcile(
    [tx(1, "LEDGER", "A")],
    [tx(2, "COUNTERPARTY", "A", 0, "110")],
    [],
    new Set(),
  );
  assert.equal(items[0].status, "DIFFERENT");
});
test("candidate gates are conservative", () => {
  assert.ok(candidateScore(tx(1), tx(2, "COUNTERPARTY", "X", 4)));
  const statuses = new Set(
    reconcile([tx(1)], [tx(2, "COUNTERPARTY", "X", 20)], [], new Set()).map(
      (x) => x.status,
    ),
  );
  assert.deepEqual(
    statuses,
    new Set(["UNMATCHED_LEDGER", "UNMATCHED_COUNTERPARTY"]),
  );
});
test("cancelled rows are excluded", () =>
  assert.equal(
    reconcile(
      [tx(1, "LEDGER", "A", 0, "100", "CANCELLED")],
      [],
      [],
      new Set(),
    )[0].status,
    "EXCLUDED_CANCELLED",
  ));
test("manual pair remains explicit", () =>
  assert.equal(
    reconcile([tx(1)], [tx(2, "COUNTERPARTY", "X", 30)], [[1, 2]], new Set())[0]
      .status,
    "MANUALLY_MATCHED",
  ));
