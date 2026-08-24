import test from "node:test";
import assert from "node:assert/strict";
import { readFile, unlink } from "node:fs/promises";
import { resolve } from "node:path";
import knex from "knex";
import { buildApp } from "../src/app.js";

const databasePath = `/tmp/atlas-node-api-${process.pid}.db`;
const database = knex({
  client: "better-sqlite3",
  connection: { filename: databasePath },
  useNullAsDefault: true,
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

test("ingests both sources and creates the expected run", async () => {
  const app = await buildApp(database);
  for (const [source, filename] of [
    ["LEDGER", "ledger.csv"],
    ["COUNTERPARTY", "counterparty.csv"],
  ]) {
    const content = await readFile(resolve("../../shared/fixtures", filename));
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
  });

  await app.close();
  await database.destroy();
  await unlink(databasePath).catch(() => undefined);
});
