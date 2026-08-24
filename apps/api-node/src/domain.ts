import { Decimal } from "decimal.js";
import { DEFAULT_SETTINGS } from "./db.js";

export type Settings = typeof DEFAULT_SETTINGS;
export type Transaction = {
  stableId: number;
  versionId?: number;
  source: "LEDGER" | "COUNTERPARTY";
  externalId: string;
  executedAt: Date;
  instrument: string;
  side: "BUY" | "SELL";
  quantity: Decimal;
  price: Decimal;
  grossAmount: Decimal;
  state: "SETTLED" | "CANCELLED";
  raw?: Record<string, string>;
};
export type ResultItem = {
  status: string;
  match_method: string;
  score: string | null;
  ledger: ReturnType<typeof publicTx> | null;
  counterparty: ReturnType<typeof publicTx> | null;
  differences: any[];
};

export function publicTx(t: Transaction) {
  return {
    id: t.stableId,
    version_id: t.versionId,
    source: t.source,
    external_id: t.externalId,
    executed_at: t.executedAt.toISOString(),
    instrument: t.instrument,
    side: t.side,
    quantity: t.quantity.toString(),
    price: t.price.toString(),
    gross_amount: t.grossAmount.toString(),
    state: t.state,
    raw: t.raw ?? {},
  };
}
// A symmetric denominator guarantees that swapping sources cannot change a result.
const relative = (a: Decimal, b: Decimal) =>
  Decimal.max(a.abs(), b.abs()).eq(0)
    ? new Decimal(0)
    : a.minus(b).abs().div(Decimal.max(a.abs(), b.abs()));
export function compare(
  left: Transaction,
  right: Transaction,
  s: Settings = DEFAULT_SETTINGS,
) {
  const differences: any[] = [];
  const add = (
    field: string,
    lv: any,
    rv: any,
    passed: boolean,
    absolute: any = null,
    rel: any = null,
    tolerance: any = "exact",
  ) =>
    differences.push({
      field,
      left: String(lv),
      right: String(rv),
      absolute_delta: absolute === null ? null : String(absolute),
      relative_delta: rel === null ? null : String(rel),
      tolerance,
      passed,
    });
  const seconds = new Decimal(
    Math.abs(left.executedAt.getTime() - right.executedAt.getTime()),
  ).div(1000);
  add(
    "executed_at",
    left.executedAt.toISOString(),
    right.executedAt.toISOString(),
    seconds.lte(s.time_tolerance_seconds),
    seconds,
    null,
    `${s.time_tolerance_seconds} seconds`,
  );
  for (const field of ["instrument", "side", "state"] as const)
    add(field, left[field], right[field], left[field] === right[field]);
  const qd = left.quantity.minus(right.quantity).abs();
  add(
    "quantity",
    left.quantity,
    right.quantity,
    qd.lte(s.quantity_abs_tolerance),
    qd,
    relative(left.quantity, right.quantity),
    s.quantity_abs_tolerance,
  );
  for (const field of ["price", "grossAmount"] as const) {
    const a = left[field],
      b = right[field],
      absolute = a.minus(b).abs(),
      rel = relative(a, b);
    add(
      field === "grossAmount" ? "gross_amount" : field,
      a,
      b,
      absolute.lte(s.money_abs_tolerance) || rel.lte(s.money_rel_tolerance),
      absolute,
      rel,
      `max(${s.money_abs_tolerance} absolute, ${s.money_rel_tolerance} relative)`,
    );
  }
  return differences;
}
export function candidateScore(
  left: Transaction,
  right: Transaction,
  s: Settings = DEFAULT_SETTINGS,
) {
  // Textual identity is required before numeric closeness can influence a match.
  if (left.instrument !== right.instrument || left.side !== right.side)
    return null;
  const deltas = [
    new Decimal(
      Math.abs(left.executedAt.getTime() - right.executedAt.getTime()),
    ).div(1000),
    relative(left.quantity, right.quantity),
    relative(left.grossAmount, right.grossAmount),
  ];
  const gates = [
    new Decimal(s.candidate_time_seconds),
    new Decimal(s.candidate_quantity_rel),
    new Decimal(s.candidate_gross_rel),
  ];
  if (deltas.some((d, i) => d.gt(gates[i]))) return null;
  return new Decimal(1)
    .minus(deltas[0].div(gates[0]))
    .mul(0.5)
    .plus(new Decimal(1).minus(deltas[1].div(gates[1])).mul(0.25))
    .plus(new Decimal(1).minus(deltas[2].div(gates[2])).mul(0.25));
}
export function reconcile(
  ledger: Transaction[],
  counterparty: Transaction[],
  manualPairs: [number, number][],
  accepted: Set<number>,
  s: Settings = DEFAULT_SETTINGS,
) {
  const items: ResultItem[] = [],
    usedL = new Set<number>(),
    usedC = new Set<number>(),
    byL = new Map(ledger.map((t) => [t.stableId, t])),
    byC = new Map(counterparty.map((t) => [t.stableId, t]));
  const paired = (
    l: Transaction,
    c: Transaction,
    method: string,
    score: Decimal | null = null,
  ) => {
    const differences = compare(l, c, s);
    items.push({
      status:
        method === "MANUAL"
          ? "MANUALLY_MATCHED"
          : differences.every((d) => d.passed)
            ? "MATCHED"
            : "DIFFERENT",
      match_method: method,
      score: score?.toDecimalPlaces(4).toString() ?? null,
      ledger: publicTx(l),
      counterparty: publicTx(c),
      differences,
    });
    usedL.add(l.stableId);
    usedC.add(c.stableId);
  };
  // Human decisions bind to stable identities and therefore survive corrected versions.
  for (const [lid, cid] of manualPairs) {
    const l = byL.get(lid),
      c = byC.get(cid);
    if (l && c && l.state !== "CANCELLED" && c.state !== "CANCELLED")
      paired(l, c, "MANUAL");
  }
  const group = (xs: Transaction[]) => {
      const m = new Map<string, Transaction[]>();
      for (const x of xs) {
        const k = x.externalId.toUpperCase();
        m.set(k, [...(m.get(k) ?? []), x]);
      }
      return m;
    },
    lg = group(ledger),
    cg = group(counterparty);
  for (const key of [...lg.keys()].filter((k) => cg.has(k)).sort()) {
    const ls = lg.get(key)!,
      cs = cg.get(key)!;
    if (
      ls.length === 1 &&
      cs.length === 1 &&
      !usedL.has(ls[0].stableId) &&
      !usedC.has(cs[0].stableId) &&
      ls[0].state !== "CANCELLED" &&
      cs[0].state !== "CANCELLED"
    )
      paired(ls[0], cs[0], "EXACT_ID");
  }
  const rl = ledger.filter(
      (t) => !usedL.has(t.stableId) && t.state !== "CANCELLED",
    ),
    rc = counterparty.filter(
      (t) => !usedC.has(t.stableId) && t.state !== "CANCELLED",
    );
  const scores = new Map<string, Decimal>();
  for (const l of rl)
    for (const c of rc) {
      const score = candidateScore(l, c, s);
      if (score && score.gte(s.candidate_min_score))
        scores.set(`${l.stableId}:${c.stableId}`, score);
    }
  // Mutual-best pairing prevents two rows from competing for the same counter-row.
  // Equal scores remain unmatched rather than being broken by insertion order.
  const bestFor = (entries: [string, Decimal][]) => {
    const ranked = entries.sort((a, b) => b[1].cmp(a[1]));
    return ranked.length > 1 && ranked[0][1].eq(ranked[1][1])
      ? undefined
      : ranked[0];
  };
  for (const l of rl) {
    const lb = bestFor(
      [...scores].filter(([k]) => k.startsWith(`${l.stableId}:`)),
    );
    if (!lb) continue;
    const cid = Number(lb[0].split(":")[1]),
      cb = bestFor([...scores].filter(([k]) => k.endsWith(`:${cid}`)));
    if (cb?.[0] === lb[0] && !usedL.has(l.stableId) && !usedC.has(cid))
      paired(l, byC.get(cid)!, "CANDIDATE_SCORE", lb[1]);
  }
  for (const t of ledger)
    if (t.state === "CANCELLED")
      items.push({
        status: "EXCLUDED_CANCELLED",
        match_method: "EXCLUDED",
        score: null,
        ledger: publicTx(t),
        counterparty: null,
        differences: [],
      });
    else if (!usedL.has(t.stableId))
      items.push({
        status: accepted.has(t.stableId)
          ? "ACCEPTED_UNMATCHED"
          : "UNMATCHED_LEDGER",
        match_method: accepted.has(t.stableId) ? "MANUAL_ACCEPT" : "NONE",
        score: null,
        ledger: publicTx(t),
        counterparty: null,
        differences: [],
      });
  for (const t of counterparty)
    if (t.state === "CANCELLED")
      items.push({
        status: "EXCLUDED_CANCELLED",
        match_method: "EXCLUDED",
        score: null,
        ledger: null,
        counterparty: publicTx(t),
        differences: [],
      });
    else if (!usedC.has(t.stableId))
      items.push({
        status: accepted.has(t.stableId)
          ? "ACCEPTED_UNMATCHED"
          : "UNMATCHED_COUNTERPARTY",
        match_method: accepted.has(t.stableId) ? "MANUAL_ACCEPT" : "NONE",
        score: null,
        ledger: null,
        counterparty: publicTx(t),
        differences: [],
      });
  return items.sort(
    (a, b) =>
      (a.ledger ?? a.counterparty)!.external_id.localeCompare(
        (b.ledger ?? b.counterparty)!.external_id,
      ) || a.status.localeCompare(b.status),
  );
}
