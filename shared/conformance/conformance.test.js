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
  score: item.score,
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
  // Each backend independently materializes a run over the same current rows.
  const pRunResponse = await fetch(`${python}/api/runs`, { method: "POST" });
  const nRunResponse = await fetch(`${node}/api/runs`, { method: "POST" });
  assert.equal(pRunResponse.status, 201);
  assert.equal(nRunResponse.status, 201);
  const [pRun, nRun] = await Promise.all([
    pRunResponse.json(),
    nRunResponse.json(),
  ]);
  const [p, n] = await Promise.all([
    fetch(`${python}/api/runs/${pRun.id}/results`).then((r) => r.json()),
    fetch(`${node}/api/runs/${nRun.id}/results`).then((r) => r.json()),
  ]);
  assert.deepEqual(p.summary, n.summary);
  assert.deepEqual(p.items.map(normalize), n.items.map(normalize));
});
