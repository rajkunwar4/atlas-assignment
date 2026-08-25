import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import knex from "knex";
import { buildApp } from "../src/app.js";

const database = knex({
  client: "pg",
  connection:
    process.env.TEST_DATABASE_URL ??
    "postgresql://atlas:atlas@localhost:55432/atlas_test?sslmode=disable",
});

function multipart(filename: string, content: Buffer) {
  const boundary = "atlas-test-boundary";
  return {
    headers: { "content-type": `multipart/form-data; boundary=${boundary}` },
    payload: Buffer.concat([
      Buffer.from(
        `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${filename}"\r\nContent-Type: text/csv\r\n\r\n`,
      ),
      content,
      Buffer.from(`\r\n--${boundary}--\r\n`),
    ]),
  };
}

const uploadText = (app: any, source: string, content: string, query = "") =>
  app.inject({
    method: "POST",
    url: `/api/files?source=${source}${query}`,
    ...multipart("rows.csv", Buffer.from(content)),
  });

const fixture = (filename: string) =>
  fileURLToPath(
    new URL(`../../../shared/fixtures/${filename}`, import.meta.url),
  );

test("ingests both sources and creates the expected run", async () => {
  await database.raw(
    "TRUNCATE audit_events, tolerance_settings, manual_resolutions, " +
      "field_differences, reconciliation_items, reconciliation_runs, " +
      "transaction_versions, source_transactions, ingestion_files " +
      "RESTART IDENTITY CASCADE",
  );
  const app = await buildApp(database);
  for (const [source, filename] of [
    ["LEDGER", "ledger.csv"],
    ["COUNTERPARTY", "counterparty.csv"],
  ]) {
    const content = await readFile(fixture(filename));
    const upload = multipart(filename, content);
    const response = await app.inject({
      method: "POST",
      url: `/api/files?source=${source}`,
      ...upload,
    });
    assert.equal(response.statusCode, 201, response.body);
  }

  const run = await app.inject({ method: "POST", url: "/api/runs" });
  assert.equal(run.statusCode, 201, run.body);
  assert.deepEqual(run.json().summary, {
    MATCHED: 1,
    DIFFERENT: 2,
    UNMATCHED_LEDGER: 1,
    UNMATCHED_COUNTERPARTY: 1,
    MANUALLY_MATCHED: 0,
    ACCEPTED_UNMATCHED: 0,
    EXCLUDED_CANCELLED: 1,
    UNRESOLVED: 4,
  });

  await database.raw(
    "TRUNCATE audit_events, manual_resolutions, field_differences, " +
      "reconciliation_items, reconciliation_runs, transaction_versions, " +
      "source_transactions, ingestion_files RESTART IDENTITY CASCADE",
  );
  const canonicalHeader =
    "transaction_id,executed_at,instrument,side,quantity,unit_price," +
    "gross_amount,state,desk_note\n";
  const first =
    canonicalHeader +
    "A,2026-08-11T10:00:00Z,AAPL,BUY,1,10,10,SETTLED,alpha\n" +
    "B,2026-08-11T10:01:00Z,MSFT,SELL,2,20,40,SETTLED,beta\n";
  const second =
    canonicalHeader +
    "A,2026-08-11T10:00:00Z,AAPL,BUY,1,10,10,SETTLED,alpha\n" +
    "C,2026-08-11T10:02:00Z,NVDA,BUY,3,30,90,SETTLED,gamma\n";
  const detected = await uploadText(app, "LEDGER", first);
  assert.equal(detected.statusCode, 201, detected.body);
  assert.equal(detected.json().adapter_id, "ledger-canonical-v1");
  const snapshot = await uploadText(app, "LEDGER", second, "&mode=SNAPSHOT");
  assert.equal(snapshot.statusCode, 201, snapshot.body);
  const identities = await database("source_transactions")
    .select("external_id", "active", "inactive_reason")
    .orderBy("external_id");
  assert.deepEqual(identities, [
    { external_id: "A", active: true, inactive_reason: null },
    {
      external_id: "B",
      active: false,
      inactive_reason: "ABSENT_FROM_SNAPSHOT",
    },
    { external_id: "C", active: true, inactive_reason: null },
  ]);
  const raw = await database("transaction_versions as version")
    .join(
      "source_transactions as transaction",
      "transaction.current_version_id",
      "version.id",
    )
    .where("transaction.external_id", "A")
    .select("version.raw_json")
    .first();
  assert.equal(raw.raw_json.desk_note, "alpha");

  await database.raw(
    "TRUNCATE audit_events, manual_resolutions, field_differences, " +
      "reconciliation_items, reconciliation_runs, transaction_versions, " +
      "source_transactions, ingestion_files RESTART IDENTITY CASCADE",
  );
  const ledger =
    "trade_id,traded_at,instrument,side,quantity,price,gross_amount,state\n" +
    "R-1,2026-08-11T10:00:00Z,AAPL,BUY,1,10,10,SETTLED\n";
  const counterparty =
    "reference,executed_at,symbol,direction,qty,unit_price,total,status\n" +
    "R-1,2026-08-11T10:00:00Z,AAPL,B,1,11,11,SETTLED\n";
  assert.equal((await uploadText(app, "LEDGER", ledger)).statusCode, 201);
  assert.equal(
    (await uploadText(app, "COUNTERPARTY", counterparty)).statusCode,
    201,
  );
  const reviewRun = await app.inject({ method: "POST", url: "/api/runs" });
  assert.equal(reviewRun.json().status, "OPEN");
  const runId = reviewRun.json().id;
  const changedLedger = ledger.replace(",10,10,SETTLED", ",12,12,SETTLED");
  assert.equal(
    (await uploadText(app, "LEDGER", changedLedger)).statusCode,
    409,
  );
  assert.equal(
    (await app.inject({ method: "POST", url: `/api/runs/${runId}/close` }))
      .statusCode,
    409,
  );
  const results = await app.inject({
    method: "GET",
    url: `/api/runs/${runId}/results`,
  });
  const item = results.json().items[0];
  assert.equal(item.review_status, "PENDING");
  const accepted = await app.inject({
    method: "POST",
    url: "/api/resolutions/accept-differences",
    payload: { item_id: item.id, note: "confirmed with broker" },
  });
  assert.equal(accepted.statusCode, 201, accepted.body);
  const superseded = await app.inject({
    method: "POST",
    url: `/api/resolutions/${accepted.json().id}/supersede`,
    payload: { note: "review needs to be repeated" },
  });
  assert.equal(superseded.statusCode, 201, superseded.body);
  const refreshedResults = await app.inject({
    method: "GET",
    url: `/api/runs/${runId}/results`,
  });
  const newItem = refreshedResults.json().items[0];
  const acceptedAgain = await app.inject({
    method: "POST",
    url: "/api/resolutions/accept-differences",
    payload: { item_id: newItem.id, note: "reviewed again" },
  });
  assert.equal(acceptedAgain.statusCode, 201, acceptedAgain.body);
  const closed = await app.inject({
    method: "POST",
    url: `/api/runs/${runId}/close`,
  });
  assert.equal(closed.statusCode, 200, closed.body);
  assert.equal(closed.json().status, "CLOSED");

  await app.close();
  await database.destroy();
});
