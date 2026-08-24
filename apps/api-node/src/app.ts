import Fastify from "fastify";
import cors from "@fastify/cors";
import multipart from "@fastify/multipart";
import { createHash } from "node:crypto";
import { Decimal } from "decimal.js";
import { db as defaultDb, migrate, DEFAULT_SETTINGS, type Knex } from "./db.js";
import { FileValidationError, parseCsv } from "./ingestion.js";
import { reconcile, type Transaction, type Settings } from "./domain.js";

const iso = (value: any) =>
  new Date(
    String(value).includes("Z") || String(value).includes("+")
      ? value
      : `${String(value).replace(" ", "T")}Z`,
  ).toISOString();
const error = (code: string, message: string, details: any[] = []) => ({
  error: { code, message, details },
});

export async function buildApp(database: Knex = defaultDb) {
  await migrate(database);
  const app = Fastify({ logger: false });
  await app.register(cors, {
    origin: ["http://localhost:5173", "http://127.0.0.1:5173"],
  });
  await app.register(multipart, { limits: { fileSize: 5_000_000, files: 1 } });
  const audit = (
    trx: Knex,
    action: string,
    entityType: string,
    entityId: any,
    details: any = {},
  ) =>
    trx("audit_events").insert({
      action,
      entity_type: entityType,
      entity_id: String(entityId),
      details_json: JSON.stringify(details),
    });
  const settings = async (trx: Knex = database) =>
    JSON.parse(
      (
        await trx("tolerance_settings")
          .where({ active: 1 })
          .orderBy("id", "desc")
          .first()
      ).settings_json,
    ) as Settings;
  const loadTransactions = async (trx: Knex = database) => {
    const rows = await trx("source_transactions as s")
      .join("transaction_versions as v", "s.current_version_id", "v.id")
      .select(
        "s.id as stable_id",
        "v.id as version_id",
        "v.data_json",
        "v.raw_json",
      );
    return rows.map((row: any) => {
      const d = JSON.parse(row.data_json);
      return {
        stableId: row.stable_id,
        versionId: row.version_id,
        source: d.source,
        externalId: d.external_id,
        executedAt: new Date(d.executed_at),
        instrument: d.instrument,
        side: d.side,
        quantity: new Decimal(d.quantity),
        price: new Decimal(d.price),
        grossAmount: new Decimal(d.gross_amount),
        state: d.state,
        raw: JSON.parse(row.raw_json),
      } as Transaction;
    });
  };
  // Run items are materialized snapshots. Rebuilding the latest run after a human
  // decision updates the current workspace without changing older runs.
  const populateRun = async (trx: Knex, run: any) => {
    const ids = (
      await trx("reconciliation_items").where({ run_id: run.id }).select("id")
    ).map((x: any) => x.id);
    if (ids.length)
      await trx("field_differences").whereIn("item_id", ids).del();
    await trx("reconciliation_items").where({ run_id: run.id }).del();
    const txs = await loadTransactions(trx),
      resolutions = await trx("manual_resolutions")
        .where({ active: 1 })
        .orderBy("id"),
      pairs = resolutions
        .filter((r: any) => r.resolution_type === "MATCH")
        .map(
          (r: any) =>
            [r.ledger_transaction_id, r.counterparty_transaction_id] as [
              number,
              number,
            ],
        ),
      accepted = new Set<number>(
        resolutions
          .filter((r: any) => r.resolution_type === "ACCEPT_UNMATCHED")
          .map((r: any) => r.accepted_transaction_id),
      );
    const results = reconcile(
      txs.filter((t) => t.source === "LEDGER"),
      txs.filter((t) => t.source === "COUNTERPARTY"),
      pairs,
      accepted,
      JSON.parse(run.settings_json),
    );
    const summary: any = {
      MATCHED: 0,
      DIFFERENT: 0,
      UNMATCHED_LEDGER: 0,
      UNMATCHED_COUNTERPARTY: 0,
      MANUALLY_MATCHED: 0,
      ACCEPTED_UNMATCHED: 0,
      EXCLUDED_CANCELLED: 0,
    };
    for (const result of results) {
      summary[result.status] = (summary[result.status] ?? 0) + 1;
      const [itemId] = await trx("reconciliation_items").insert({
        run_id: run.id,
        ledger_transaction_id: result.ledger?.id ?? null,
        counterparty_transaction_id: result.counterparty?.id ?? null,
        status: result.status,
        match_method: result.match_method,
        score: result.score,
        result_json: JSON.stringify(result),
      });
      for (const diff of result.differences)
        await trx("field_differences").insert({
          item_id: itemId,
          field: diff.field,
          difference_json: JSON.stringify(diff),
        });
    }
    await trx("reconciliation_runs")
      .where({ id: run.id })
      .update({ summary_json: JSON.stringify(summary) });
    return summary;
  };
  const refreshLatest = async (trx: Knex) => {
    const run = await trx("reconciliation_runs").orderBy("id", "desc").first();
    if (run) await populateRun(trx, run);
  };
  const ensureUnresolved = async (trx: Knex, ids: number[]) => {
    const active = await trx("manual_resolutions").where({ active: 1 });
    const occupied = new Set(
      active.flatMap((r: any) =>
        [
          r.ledger_transaction_id,
          r.counterparty_transaction_id,
          r.accepted_transaction_id,
        ].filter(Boolean),
      ),
    );
    if (ids.some((id) => occupied.has(id)))
      throw Object.assign(
        new Error("a transaction already has an active resolution"),
        { statusCode: 409, code: "RESOLUTION_CONFLICT" },
      );
  };

  app.setErrorHandler((err: any, _req, reply) => {
    if (err instanceof FileValidationError)
      return reply
        .code(422)
        .send(error("INVALID_FILE", err.message, err.errors));
    const status = err.statusCode ?? 500;
    return reply
      .code(status)
      .send(
        error(
          err.code ?? (status === 500 ? "INTERNAL_ERROR" : "REQUEST_ERROR"),
          status === 500 ? "unexpected server error" : err.message,
        ),
      );
  });
  app.get("/api/health", async () => ({
    status: "ok",
    implementation: "node",
    version: "1.0.0",
  }));
  app.get("/api/files", async () => {
    const rows = await database("ingestion_files").orderBy("id", "desc");
    return rows.map((r: any) => ({ ...r, created_at: iso(r.created_at) }));
  });
  app.post("/api/files", async (req: any, reply) => {
    const source = String(req.query?.source ?? "").toUpperCase();
    if (!["LEDGER", "COUNTERPARTY"].includes(source))
      return reply
        .code(422)
        .send(error("INVALID_SOURCE", "source must be LEDGER or COUNTERPARTY"));
    const upload = await req.file();
    if (!upload)
      return reply.code(422).send(error("MISSING_FILE", "file is required"));
    const content = await upload.toBuffer(),
      checksum = createHash("sha256").update(content).digest("hex"),
      duplicate = await database("ingestion_files")
        .where({ source, checksum })
        .first();
    if (duplicate)
      return reply.code(200).send({
        ...duplicate,
        duplicate: true,
        created_at: iso(duplicate.created_at),
      });
    const rows = parseCsv(content, source as any);
    const response = await database.transaction(async (trx) => {
      const [fileId] = await trx("ingestion_files").insert({
        source,
        filename: upload.filename,
        checksum,
        row_count: rows.length,
        changed_count: 0,
      });
      let changed = 0;
      for (const row of rows) {
        let stable = await trx("source_transactions")
          .where({ source, external_id: row.data.external_id })
          .first();
        if (!stable) {
          const [id] = await trx("source_transactions").insert({
            source,
            external_id: row.data.external_id,
          });
          stable = { id, current_version_id: null };
        }
        const current = stable.current_version_id
          ? await trx("transaction_versions")
              .where({ id: stable.current_version_id })
              .first()
          : null;
        if (current?.fingerprint === row.fingerprint) continue;
        const max = await trx("transaction_versions")
          .where({ transaction_id: stable.id })
          .max({ value: "version" })
          .first();
        const [versionId] = await trx("transaction_versions").insert({
          transaction_id: stable.id,
          ingestion_file_id: fileId,
          version: Number(max?.value ?? 0) + 1,
          fingerprint: row.fingerprint,
          data_json: JSON.stringify(row.data),
          raw_json: JSON.stringify(row.raw),
        });
        await trx("source_transactions")
          .where({ id: stable.id })
          .update({ current_version_id: versionId });
        changed++;
      }
      await trx("ingestion_files")
        .where({ id: fileId })
        .update({ changed_count: changed });
      await audit(trx, "FILE_INGESTED", "ingestion_file", fileId, {
        source,
        rows: rows.length,
        changed,
      });
      return {
        id: fileId,
        source,
        filename: upload.filename,
        checksum,
        row_count: rows.length,
        changed_count: changed,
        duplicate: false,
        created_at: new Date().toISOString(),
      };
    });
    return reply.code(201).send(response);
  });
  app.get("/api/runs", async () => {
    const rows = await database("reconciliation_runs").orderBy("id", "desc");
    return rows.map((r: any) => ({
      id: r.id,
      status: r.status,
      summary: JSON.parse(r.summary_json),
      created_at: iso(r.created_at),
    }));
  });
  app.post("/api/runs", async (_req, reply) => {
    const sources = new Set(
      (await database("source_transactions").distinct("source")).map(
        (x: any) => x.source,
      ),
    );
    if (!sources.has("LEDGER") || !sources.has("COUNTERPARTY"))
      return reply
        .code(409)
        .send(
          error("MISSING_SOURCE", "upload at least one file for each source"),
        );
    const result = await database.transaction(async (trx) => {
      const [id] = await trx("reconciliation_runs").insert({
        settings_json: JSON.stringify(await settings(trx)),
        summary_json: "{}",
        status: "COMPLETED",
      });
      const run = await trx("reconciliation_runs").where({ id }).first(),
        summary = await populateRun(trx, run);
      await audit(trx, "RUN_COMPLETED", "reconciliation_run", id, summary);
      return {
        id,
        status: "COMPLETED",
        summary,
        created_at: iso(run.created_at),
      };
    });
    return reply.code(201).send(result);
  });
  app.get("/api/runs/:runId/results", async (req: any, reply) => {
    const run = await database("reconciliation_runs")
      .where({ id: req.params.runId })
      .first();
    if (!run) return reply.code(404).send(error("NOT_FOUND", "run not found"));
    const page = Math.max(1, Number(req.query.page ?? 1)),
      pageSize = Math.min(100, Math.max(1, Number(req.query.page_size ?? 50)));
    let items = (
      await database("reconciliation_items")
        .where({ run_id: run.id })
        .orderBy("id")
    ).map((r: any) => ({ ...JSON.parse(r.result_json), id: r.id }));
    if (req.query.status)
      items = items.filter((x: any) => x.status === req.query.status);
    if (req.query.search)
      items = items.filter((x: any) =>
        JSON.stringify(x)
          .toLowerCase()
          .includes(String(req.query.search).toLowerCase()),
      );
    return {
      items: items.slice((page - 1) * pageSize, page * pageSize),
      total: items.length,
      page,
      page_size: pageSize,
      summary: JSON.parse(run.summary_json),
    };
  });
  app.post("/api/resolutions/match", async (req: any, reply) => {
    const {
        ledger_transaction_id: lid,
        counterparty_transaction_id: cid,
        note = "",
      } = req.body ?? {},
      left = await database("source_transactions").where({ id: lid }).first(),
      right = await database("source_transactions").where({ id: cid }).first();
    if (
      !left ||
      !right ||
      left.source !== "LEDGER" ||
      right.source !== "COUNTERPARTY"
    )
      return reply
        .code(422)
        .send(error("INVALID_PAIR", "select one transaction from each source"));
    const result = await database.transaction(async (trx) => {
      await ensureUnresolved(trx, [lid, cid]);
      const [id] = await trx("manual_resolutions").insert({
        resolution_type: "MATCH",
        ledger_transaction_id: lid,
        counterparty_transaction_id: cid,
        note,
      });
      await audit(trx, "MANUAL_MATCH_CREATED", "manual_resolution", id, {
        ledger: lid,
        counterparty: cid,
        note,
      });
      await refreshLatest(trx);
      return {
        id,
        type: "MATCH",
        active: true,
        created_at: new Date().toISOString(),
      };
    });
    return reply.code(201).send(result);
  });
  app.post("/api/resolutions/accept-unmatched", async (req: any, reply) => {
    const { transaction_id: id, note = "" } = req.body ?? {},
      tx = await database("source_transactions").where({ id }).first();
    if (!tx)
      return reply.code(404).send(error("NOT_FOUND", "transaction not found"));
    const result = await database.transaction(async (trx) => {
      await ensureUnresolved(trx, [id]);
      const [rid] = await trx("manual_resolutions").insert({
        resolution_type: "ACCEPT_UNMATCHED",
        accepted_transaction_id: id,
        note,
      });
      await audit(trx, "UNMATCHED_ACCEPTED", "manual_resolution", rid, {
        transaction: id,
        note,
      });
      await refreshLatest(trx);
      return {
        id: rid,
        type: "ACCEPT_UNMATCHED",
        active: true,
        created_at: new Date().toISOString(),
      };
    });
    return reply.code(201).send(result);
  });
  app.get("/api/settings", async () => settings());
  app.put("/api/settings", async (req: any, reply) => {
    const body = req.body ?? {};
    if (
      JSON.stringify(Object.keys(body).sort()) !==
      JSON.stringify(Object.keys(DEFAULT_SETTINGS).sort())
    )
      return reply
        .code(422)
        .send(
          error(
            "INVALID_SETTINGS",
            "all setting fields are required and unknown fields are rejected",
          ),
        );
    await database.transaction(async (trx) => {
      await trx("tolerance_settings")
        .where({ active: 1 })
        .update({ active: 0 });
      const [id] = await trx("tolerance_settings").insert({
        settings_json: JSON.stringify(body),
        active: 1,
      });
      await audit(trx, "SETTINGS_UPDATED", "tolerance_setting", id, body);
    });
    return body;
  });
  app.get("/api/audit", async () => {
    const rows = await database("audit_events")
      .orderBy("id", "desc")
      .limit(100);
    return rows.map((r: any) => ({
      id: r.id,
      action: r.action,
      entity_type: r.entity_type,
      entity_id: r.entity_id,
      actor: r.actor,
      details: JSON.parse(r.details_json),
      created_at: iso(r.created_at),
    }));
  });
  app.get("/api/transactions/:id/history", async (req: any, reply) => {
    const stable = await database("source_transactions")
      .where({ id: req.params.id })
      .first();
    if (!stable)
      return reply.code(404).send(error("NOT_FOUND", "transaction not found"));
    const rows = await database("transaction_versions")
      .where({ transaction_id: stable.id })
      .orderBy("version", "desc");
    return rows.map((r: any) => ({
      id: r.id,
      version: r.version,
      current: r.id === stable.current_version_id,
      data: JSON.parse(r.data_json),
      raw: JSON.parse(r.raw_json),
      created_at: iso(r.created_at),
    }));
  });
  app.get("/api/runs/:runId/export", async (req: any, reply) => {
    const run = await database("reconciliation_runs")
      .where({ id: req.params.runId })
      .first();
    if (!run) return reply.code(404).send(error("NOT_FOUND", "run not found"));
    const rows = await database("reconciliation_items")
        .where({ run_id: run.id })
        .orderBy("id"),
      quote = (s: any) => `"${String(s ?? "").replaceAll('"', '""')}"`,
      lines = [
        [
          "status",
          "match_method",
          "ledger_id",
          "counterparty_id",
          "material_differences",
        ]
          .map(quote)
          .join(","),
      ];
    for (const row of rows) {
      const d = JSON.parse(row.result_json);
      lines.push(
        [
          d.status,
          d.match_method,
          d.ledger?.external_id ?? "",
          d.counterparty?.external_id ?? "",
          d.differences
            .filter((x: any) => !x.passed)
            .map((x: any) => x.field)
            .join("|"),
        ]
          .map(quote)
          .join(","),
      );
    }
    return reply
      .header("Content-Type", "text/csv")
      .header(
        "Content-Disposition",
        `attachment; filename="reconciliation-run-${run.id}.csv"`,
      )
      .send(lines.join("\n"));
  });
  return app;
}
