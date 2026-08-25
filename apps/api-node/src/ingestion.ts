import { createHash } from "node:crypto";
import { Decimal } from "decimal.js";

export type Source = "LEDGER" | "COUNTERPARTY";
type CanonicalData = Record<string, string>;
type Normalizer = (raw: Record<string, string>) => CanonicalData;

export type Adapter = {
  id: string;
  source: Source;
  description: string;
  headers: readonly string[];
  normalize: Normalizer;
};

export class FileValidationError extends Error {
  constructor(public errors: any[]) {
    super("file validation failed");
  }
}

function decimal(value: string, field: string) {
  try {
    const result = new Decimal(value.trim());
    if (result.isNegative() || !result.isFinite()) throw new Error();
    return result.toString();
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

function mappedNormalizer(
  mapping: Record<string, string>,
  assumeUtc = false,
): Normalizer {
  return (raw) => {
    const value = (field: string) => raw[mapping[field]].trim();
    return {
      external_id: value("external_id"),
      executed_at: parseTime(value("executed_at"), assumeUtc),
      instrument: value("instrument").toUpperCase(),
      side: value("side").toUpperCase(),
      quantity: decimal(value("quantity"), mapping.quantity),
      price: decimal(value("price"), mapping.price),
      gross_amount: decimal(value("gross_amount"), mapping.gross_amount),
      state: value("state").toUpperCase(),
    };
  };
}

const ledgerHeaders = [
  "trade_id",
  "traded_at",
  "instrument",
  "side",
  "quantity",
  "price",
  "gross_amount",
  "state",
] as const;
const counterpartyHeaders = [
  "reference",
  "executed_at",
  "symbol",
  "direction",
  "qty",
  "unit_price",
  "total",
  "status",
] as const;
const canonicalHeaders = [
  "transaction_id",
  "executed_at",
  "instrument",
  "side",
  "quantity",
  "unit_price",
  "gross_amount",
  "state",
] as const;
const canonicalMapping = {
  external_id: "transaction_id",
  executed_at: "executed_at",
  instrument: "instrument",
  side: "side",
  quantity: "quantity",
  price: "unit_price",
  gross_amount: "gross_amount",
  state: "state",
};

export const adapters: readonly Adapter[] = [
  {
    id: "ledger-v1",
    source: "LEDGER",
    description: "Original ledger export",
    headers: ledgerHeaders,
    normalize: mappedNormalizer({
      external_id: "trade_id",
      executed_at: "traded_at",
      instrument: "instrument",
      side: "side",
      quantity: "quantity",
      price: "price",
      gross_amount: "gross_amount",
      state: "state",
    }),
  },
  {
    id: "counterparty-v1",
    source: "COUNTERPARTY",
    description: "Original counterparty statement",
    headers: counterpartyHeaders,
    // This format needs code because its compact B/S values are not simple renames.
    normalize: (raw) => ({
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
    }),
  },
  {
    id: "ledger-canonical-v1",
    source: "LEDGER",
    description: "Canonical column export",
    headers: canonicalHeaders,
    normalize: mappedNormalizer(canonicalMapping),
  },
  {
    id: "counterparty-canonical-v1",
    source: "COUNTERPARTY",
    description: "Canonical column export",
    headers: canonicalHeaders,
    normalize: mappedNormalizer(canonicalMapping),
  },
];

export const adaptersFor = (source: Source) =>
  adapters.filter((adapter) => adapter.source === source);

// This small RFC-4180 row parser is sufficient for generated take-home fixtures.
function csvLine(line: string) {
  const output: string[] = [];
  let current = "";
  let quoted = false;
  for (let index = 0; index < line.length; index++) {
    const character = line[index];
    if (character === '"' && line[index + 1] === '"' && quoted) {
      current += '"';
      index++;
    } else if (character === '"') quoted = !quoted;
    else if (character === "," && !quoted) {
      output.push(current);
      current = "";
    } else current += character;
  }
  output.push(current);
  return output;
}

export function resolveAdapter(
  content: Buffer,
  source: Source,
  requested?: string,
) {
  const candidates = adaptersFor(source);
  if (requested) {
    const selected = candidates.find((adapter) => adapter.id === requested);
    if (!selected)
      throw new FileValidationError([
        {
          row: 1,
          column: "adapter_id",
          value: requested,
          reason: "adapter is not registered for this source",
        },
      ]);
    return selected;
  }
  const actual = csvLine(
    content
      .toString("utf8")
      .replace(/^\uFEFF/, "")
      .split(/\r?\n/)[0] ?? "",
  );
  const matches = candidates.filter(
    (adapter) =>
      JSON.stringify(adapter.headers) ===
      JSON.stringify(actual.slice(0, adapter.headers.length)),
  );
  if (matches.length !== 1) {
    const expected = candidates
      .map((item) => item.headers.join(","))
      .join(" or ");
    throw new FileValidationError([
      {
        row: 1,
        column: "header",
        value: actual.join(","),
        reason: matches.length
          ? "format is ambiguous"
          : `unsupported file format; expected ${expected}`,
      },
    ]);
  }
  return matches[0];
}

export function parseCsv(content: Buffer, source: Source, adapterId?: string) {
  const text = content.toString("utf8").replace(/^\uFEFF/, "");
  const adapter = resolveAdapter(content, source, adapterId);
  const lines = text.split(/\r?\n/).filter(Boolean);
  const actual = csvLine(lines[0] ?? "");
  if (
    JSON.stringify(actual.slice(0, adapter.headers.length)) !==
    JSON.stringify(adapter.headers)
  )
    throw new FileValidationError([
      {
        row: 1,
        column: "header",
        value: actual.join(","),
        reason: `expected ${adapter.headers.join(",")}`,
      },
    ]);

  const rows: any[] = [];
  const errors: any[] = [];
  const seen = new Map<string, string>();
  for (let index = 1; index < lines.length; index++) {
    const values = csvLine(lines[index]);
    const raw = Object.fromEntries(
      actual.map((header, column) => [header, values[column] ?? ""]),
    );
    try {
      const data: CanonicalData & { source: Source } = {
        source,
        ...adapter.normalize(raw),
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
    } catch (error: any) {
      errors.push({
        row: index + 1,
        column: "row",
        value: JSON.stringify(raw),
        reason: error.message,
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
