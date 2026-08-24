import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { buildApp } from "../src/app.js";
import { db } from "../src/db.js";

const fixtureDir = fileURLToPath(
  new URL("../../../shared/fixtures/", import.meta.url),
);
const app = await buildApp(db);

for (const [source, filename] of [
  ["LEDGER", "ledger.csv"],
  ["COUNTERPARTY", "counterparty.csv"],
]) {
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
