import { createHash } from "node:crypto";
import { Decimal } from "decimal.js";

export class FileValidationError extends Error {
  constructor(public errors: any[]) {
    super("file validation failed");
  }
}
const headers = {
  LEDGER: [
    "trade_id",
    "traded_at",
    "instrument",
    "side",
    "quantity",
    "price",
    "gross_amount",
    "state",
  ],
  COUNTERPARTY: [
    "reference",
    "executed_at",
    "symbol",
    "direction",
    "qty",
    "unit_price",
    "total",
    "status",
  ],
};
function decimal(value: string, field: string) {
  try {
    const d = new Decimal(value.trim());
    if (d.isNegative() || !d.isFinite()) throw 0;
    return d.toString();
  } catch {
    throw new Error(`${field} must be a non-negative finite decimal`);
  }
}
function parseTime(value: string, assumeUtc = false) {
  const text = value.trim();
  const normalized =
    assumeUtc && !/[zZ]|[+-]\d\d:\d\d$/.test(text)
      ? text.replace(" ", "T") + "Z"
      : text;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime()))
    throw new Error("must be an ISO-8601 date-time");
  if (!assumeUtc && !/[zZ]|[+-]\d\d:\d\d$/.test(text))
    throw new Error("must include a timezone");
  return date.toISOString();
}
// This small RFC-4180 row parser is sufficient for generated take-home fixtures;
// production ingestion would use a streaming parser with explicit size controls.
function csvLine(line: string) {
  const out: string[] = [];
  let cur = "",
    quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"' && line[i + 1] === '"' && quoted) {
      cur += '"';
      i++;
    } else if (ch === '"') quoted = !quoted;
    else if (ch === "," && !quoted) {
      out.push(cur);
      cur = "";
    } else cur += ch;
  }
  out.push(cur);
  return out;
}
export function parseCsv(content: Buffer, source: "LEDGER" | "COUNTERPARTY") {
  const text = content.toString("utf8").replace(/^\uFEFF/, "");
  const lines = text.split(/\r?\n/).filter(Boolean),
    actual = csvLine(lines[0] ?? "");
  if (JSON.stringify(actual) !== JSON.stringify(headers[source]))
    throw new FileValidationError([
      {
        row: 1,
        column: "header",
        value: actual.join(","),
        reason: `expected ${headers[source].join(",")}`,
      },
    ]);
  const rows: any[] = [],
    errors: any[] = [],
    seen = new Map<string, string>();
  for (let i = 1; i < lines.length; i++) {
    const values = csvLine(lines[i]),
      raw = Object.fromEntries(
        headers[source].map((h, j) => [h, values[j] ?? ""]),
      );
    try {
      let data: any;
      if (source === "LEDGER")
        data = {
          source,
          external_id: raw.trade_id.trim(),
          executed_at: parseTime(raw.traded_at),
          instrument: raw.instrument.trim().toUpperCase(),
          side: raw.side.trim().toUpperCase(),
          quantity: decimal(raw.quantity, "quantity"),
          price: decimal(raw.price, "price"),
          gross_amount: decimal(raw.gross_amount, "gross_amount"),
          state: raw.state.trim().toUpperCase(),
        };
      else
        data = {
          source,
          external_id: raw.reference.trim(),
          executed_at: parseTime(raw.executed_at, true),
          instrument: raw.symbol.trim().toUpperCase(),
          side:
            ({ B: "BUY", S: "SELL", BUY: "BUY", SELL: "SELL" } as any)[
              raw.direction.trim().toUpperCase()
            ] ?? "",
          quantity: decimal(raw.qty, "qty"),
          price: decimal(raw.unit_price, "unit_price"),
          gross_amount: decimal(raw.total, "total"),
          state: raw.status.trim().toUpperCase(),
        };
      if (!data.external_id) throw new Error("transaction ID is required");
      if (!data.instrument) throw new Error("instrument is required");
      if (!["BUY", "SELL"].includes(data.side))
        throw new Error("side must be BUY/SELL or B/S");
      if (!["SETTLED", "CANCELLED"].includes(data.state))
        throw new Error("state must be SETTLED or CANCELLED");
      const canonical = JSON.stringify(
        Object.fromEntries(Object.entries(data).sort()),
      );
      if (
        seen.has(data.external_id) &&
        seen.get(data.external_id) !== canonical
      )
        throw new Error("conflicting duplicate transaction ID");
      if (!seen.has(data.external_id))
        rows.push({
          data,
          raw,
          fingerprint: createHash("sha256").update(canonical).digest("hex"),
        });
      seen.set(data.external_id, canonical);
    } catch (e: any) {
      errors.push({
        row: i + 1,
        column: "row",
        value: JSON.stringify(raw),
        reason: e.message,
      });
    }
  }
  if (errors.length) throw new FileValidationError(errors);
  if (!rows.length)
    throw new FileValidationError([
      {
        row: 0,
        column: "file",
        value: "",
        reason: "file contains no data rows",
      },
    ]);
  return rows;
}
