import test from "node:test";
import assert from "node:assert/strict";

const python = process.env.PYTHON_API_URL ?? "http://localhost:8000";
const node = process.env.NODE_API_URL ?? "http://localhost:8001";
async function available(url) {
  try {
    return (await fetch(`${url}/api/health`)).ok;
  } catch {
    return false;
  }
}
const normalize = (item) => ({
  status: item.status,
  match_method: item.match_method,
  ledger: item.ledger?.external_id ?? null,
  counterparty: item.counterparty?.external_id ?? null,
  differences: item.differences.map((d) => ({
    field: d.field,
    left: d.left,
    right: d.right,
    passed: d.passed,
  })),
});

test("Python and Node expose equivalent reconciliation outcomes", async (t) => {
  if (!(await available(python)) || !(await available(node))) {
    t.skip("start and seed both APIs to run conformance");
    return;
  }
  const [pr, nr] = await Promise.all([
    fetch(`${python}/api/runs`),
    fetch(`${node}/api/runs`),
  ]);
  const [pRuns, nRuns] = await Promise.all([pr.json(), nr.json()]);
  assert.ok(
    pRuns[0] && nRuns[0],
    "both backends must be seeded and reconciled",
  );
  const [p, n] = await Promise.all([
    fetch(`${python}/api/runs/${pRuns[0].id}/results`).then((r) => r.json()),
    fetch(`${node}/api/runs/${nRuns[0].id}/results`).then((r) => r.json()),
  ]);
  assert.deepEqual(p.summary, n.summary);
  assert.deepEqual(p.items.map(normalize), n.items.map(normalize));
});
