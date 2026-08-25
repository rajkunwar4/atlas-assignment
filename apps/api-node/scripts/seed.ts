import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { buildApp } from "../src/app.js";
import { db } from "../src/db.js";

const fixtureDir = fileURLToPath(
  new URL("../../../shared/fixtures/", import.meta.url),
);
const app = await buildApp(db);

// Pass "wide" to load the extended fixture week instead of the brief's example data.
const datasets: Record<string, [string, string][]> = {
  demo: [
    ["LEDGER", "ledger.csv"],
    ["COUNTERPARTY", "counterparty.csv"],
  ],
  wide: [
    ["LEDGER", "ledger-wide.csv"],
    ["COUNTERPARTY", "counterparty-wide.csv"],
  ],
};
const dataset = process.argv[2] ?? "demo";
if (!datasets[dataset])
  throw new Error(`unknown dataset ${dataset}; choose demo or wide`);

for (const [source, filename] of datasets[dataset]) {
  const boundary = `atlas-seed-${source}`;
  const content = await readFile(resolve(fixtureDir, filename));
  const payload = Buffer.concat([
    Buffer.from(
      `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${filename}"\r\nContent-Type: text/csv\r\n\r\n`,
    ),
    content,
    Buffer.from(`\r\n--${boundary}--\r\n`),
  ]);
  const response = await app.inject({
    method: "POST",
    url: `/api/files?source=${source}`,
    headers: { "content-type": `multipart/form-data; boundary=${boundary}` },
    payload,
  });
  if (response.statusCode >= 400) throw new Error(response.body);
  console.log(response.json());
}

await app.close();
await db.destroy();
