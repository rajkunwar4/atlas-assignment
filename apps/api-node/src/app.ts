import Fastify from "fastify";
import cors from "@fastify/cors";
import multipart from "@fastify/multipart";
import { createHash } from "node:crypto";
import { Decimal } from "decimal.js";
import {
  db as defaultDb,
  ensureSchema,
  DEFAULT_SETTINGS,
  type Knex,
} from "./db.js";
import {
  FileValidationError,
  adaptersFor,
  parseCsv,
  resolveAdapter,
  type Source,
} from "./ingestion.js";
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
const asJson = <T>(value: T | string): T =>
  typeof value === "string" ? JSON.parse(value) : value;
const returnedId = async (query: any) => {
  const [row] = await query.returning("id");
  return Number(row.id);
};

export async function buildApp(database: Knex = defaultDb) {
  await ensureSchema(database);
  const app = Fastify({ logger: false });
  await app.register(cors, {
    origin: [
      "http://localhost:5173",
      "http://127.0.0.1:5173",
      "http://127.0.0.1:5174",
    ],
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
      details_json: details,
    });
  const settings = async (trx: Knex = database) =>
    asJson<Settings>(
      (
        await trx("tolerance_settings")
          .where({ active: true })
          .orderBy("id", "desc")
          .first()
      ).settings_json,
    );
  const loadTransactions = async (trx: Knex = database) => {
    const rows = await trx("source_transactions as s")
      .join("transaction_versions as v", "s.current_version_id", "v.id")
      .where("s.active", true)
      .select(
        "s.id as stable_id",
        "v.id as version_id",
        "v.data_json",
        "v.raw_json",
      );
    return rows.map((row: any) => {
      const d = asJson<any>(row.data_json);
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
        raw: asJson(row.raw_json),
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
        .where({ active: true })
        .orderBy("id");
    const usable = new Set(
      txs.filter((tx) => tx.state !== "CANCELLED").map((tx) => tx.stableId),
    );
    for (const resolution of resolutions) {
      const participants = [
        resolution.ledger_transaction_id,
        resolution.counterparty_transaction_id,
        resolution.accepted_transaction_id,
      ].filter(Boolean);
      const dormant = participants.some((id: number) => !usable.has(id));
      await trx("manual_resolutions")
        .where({ id: resolution.id })
        .update({
          dormant,
          dormant_reason: dormant ? "TRANSACTION_CANCELLED_OR_INACTIVE" : null,
        });
      resolution.dormant = dormant;
    }
    const effective = resolutions.filter(
      (resolution: any) => !resolution.dormant,
    );
    const pairs = effective
        .filter((r: any) => r.resolution_type === "MANUAL_MATCH")
        .map(
          (r: any) =>
            [r.ledger_transaction_id, r.counterparty_transaction_id] as [
              number,
              number,
            ],
        ),
      accepted = new Set<number>(
        effective
          .filter((r: any) => r.resolution_type === "ACCEPT_UNMATCHED")
          .map((r: any) => r.accepted_transaction_id),
      ),
      acceptedDifferences = new Set(
        effective
          .filter((r: any) => r.resolution_type === "ACCEPT_DIFFERENCES")
          .map(
            (r: any) =>
              `${r.ledger_transaction_id}:${r.counterparty_transaction_id}`,
          ),
      );
    const results = reconcile(
      txs.filter((t) => t.source === "LEDGER"),
      txs.filter((t) => t.source === "COUNTERPARTY"),
      pairs,
      accepted,
      asJson(run.settings_json),
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
    let unresolved = 0;
    for (const result of results) {
      summary[result.status] = (summary[result.status] ?? 0) + 1;
      const pair = `${result.ledger?.id ?? ""}:${result.counterparty?.id ?? ""}`;
      let reviewStatus = "NOT_REQUIRED";
      let resolutionType: string | null = null;
      if (result.status === "DIFFERENT") {
        if (acceptedDifferences.has(pair)) {
          reviewStatus = "ACCEPTED";
          resolutionType = "ACCEPT_DIFFERENCES";
        } else reviewStatus = "PENDING";
      } else if (
        ["UNMATCHED_LEDGER", "UNMATCHED_COUNTERPARTY"].includes(result.status)
      )
        reviewStatus = "PENDING";
      else if (result.status === "ACCEPTED_UNMATCHED") {
        reviewStatus = "ACCEPTED";
        resolutionType = "ACCEPT_UNMATCHED";
      } else if (result.status === "MANUALLY_MATCHED") {
        reviewStatus = "RESOLVED";
        resolutionType = "MANUAL_MATCH";
      }
      if (reviewStatus === "PENDING") unresolved++;
      const reviewedResult = {
        ...result,
        review_status: reviewStatus,
        resolution_type: resolutionType,
      };
      const itemId = await returnedId(
        trx("reconciliation_items").insert({
          run_id: run.id,
          ledger_transaction_id: result.ledger?.id ?? null,
          counterparty_transaction_id: result.counterparty?.id ?? null,
          status: result.status,
          match_method: result.match_method,
          score: result.score,
          review_status: reviewStatus,
          result_json: reviewedResult,
        }),
      );
      for (const diff of result.differences)
        await trx("field_differences").insert({
          item_id: itemId,
          field: diff.field,
          difference_json: diff,
        });
    }
    summary.UNRESOLVED = unresolved;
    await trx("reconciliation_runs")
      .where({ id: run.id })
      .update({
        summary_json: summary,
        status: unresolved === 0 ? "READY_TO_CLOSE" : "OPEN",
      });
    return summary;
  };
  const refreshLatest = async (trx: Knex) => {
    const run = await trx("reconciliation_runs")
      .whereNot({ status: "CLOSED" })
      .orderBy("id", "desc")
      .first();
    if (run) await populateRun(trx, run);
  };
  const ensureUnresolved = async (trx: Knex, ids: number[]) => {
    const active = await trx("manual_resolutions").where({ active: true });
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
  const requireOpenRun = async (trx: Knex = database) => {
    const run = await trx("reconciliation_runs")
      .whereNot({ status: "CLOSED" })
      .orderBy("id", "desc")
      .first();
    if (!run)
      throw Object.assign(
        new Error("create a reconciliation run before recording decisions"),
        { statusCode: 409, code: "NO_OPEN_RUN" },
      );
    return run;
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
  app.get("/api/adapters", async (req: any, reply) => {
    const source = String(req.query?.source ?? "").toUpperCase();
    if (!["LEDGER", "COUNTERPARTY"].includes(source))
      return reply
        .code(422)
        .send(error("INVALID_SOURCE", "source must be LEDGER or COUNTERPARTY"));
    return adaptersFor(source as Source).map((adapter) => ({
      id: adapter.id,
      source: adapter.source,
      description: adapter.description,
      headers: adapter.headers,
    }));
  });
  app.get("/api/files", async () => {
    const rows = await database("ingestion_files").orderBy("id", "desc");
    return rows.map(({ upload_mode, ...row }: any) => ({
      ...row,
      mode: upload_mode,
      created_at: iso(row.created_at),
    }));
  });
  app.post("/api/files", async (req: any, reply) => {
    const source = String(req.query?.source ?? "").toUpperCase();
    const mode = String(req.query?.mode ?? "INCREMENTAL").toUpperCase();
    const requestedAdapter = req.query?.adapter_id
      ? String(req.query.adapter_id)
      : undefined;
    if (!["LEDGER", "COUNTERPARTY"].includes(source))
      return reply
        .code(422)
        .send(error("INVALID_SOURCE", "source must be LEDGER or COUNTERPARTY"));
    if (!["INCREMENTAL", "SNAPSHOT"].includes(mode))
      return reply
        .code(422)
        .send(
          error("INVALID_UPLOAD_MODE", "mode must be INCREMENTAL or SNAPSHOT"),
        );
    const upload = await req.file();
    if (!upload)
      return reply.code(422).send(error("MISSING_FILE", "file is required"));
    const content = await upload.toBuffer();
    const adapter = resolveAdapter(content, source as Source, requestedAdapter);
    const checksum = createHash("sha256").update(content).digest("hex");
    const duplicate = await database("ingestion_files")
      .where({
        source,
        checksum,
        upload_mode: mode,
        adapter_id: adapter.id,
      })
      .first();
    if (duplicate)
      return reply.code(200).send({
        ...duplicate,
        mode: duplicate.upload_mode,
        duplicate: true,
        created_at: iso(duplicate.created_at),
      });
    const openRun = await database("reconciliation_runs")
      .whereNot({ status: "CLOSED" })
      .first();
    if (openRun)
      return reply
        .code(409)
        .send(
          error(
            "OPEN_RUN_EXISTS",
            "close the current run before ingesting changed source data",
          ),
        );
    const rows = parseCsv(content, source as Source, adapter.id);
    const response = await database.transaction(async (trx) => {
      const fileId = await returnedId(
        trx("ingestion_files").insert({
          source,
          filename: upload.filename,
          checksum,
          upload_mode: mode,
          adapter_id: adapter.id,
          row_count: rows.length,
          changed_count: 0,
        }),
      );
      let changed = 0;
      for (const row of rows) {
        let stable = await trx("source_transactions")
          .where({ source, external_id: row.data.external_id })
          .first();
        if (!stable) {
          const id = await returnedId(
            trx("source_transactions").insert({
              source,
              external_id: row.data.external_id,
              active: true,
              last_seen_file_id: fileId,
            }),
          );
          stable = { id, current_version_id: null, active: true };
        }
        const current = stable.current_version_id
          ? await trx("transaction_versions")
              .where({ id: stable.current_version_id })
              .first()
          : null;
        const wasInactive = !stable.active;
        await trx("source_transactions").where({ id: stable.id }).update({
          active: true,
          inactive_reason: null,
          last_seen_file_id: fileId,
        });
        if (current?.fingerprint === row.fingerprint && !wasInactive) continue;
        const max = await trx("transaction_versions")
          .where({ transaction_id: stable.id })
          .max({ value: "version" })
          .first();
        const versionId = await returnedId(
          trx("transaction_versions").insert({
            transaction_id: stable.id,
            ingestion_file_id: fileId,
            version: Number(max?.value ?? 0) + 1,
            fingerprint: row.fingerprint,
            data_json: row.data,
            raw_json: row.raw,
          }),
        );
        await trx("source_transactions")
          .where({ id: stable.id })
          .update({ current_version_id: versionId });
        changed++;
      }
      if (mode === "SNAPSHOT") {
        const omitted = await trx("source_transactions")
          .where({ source, active: true })
          .where((builder) =>
            builder
              .whereNull("last_seen_file_id")
              .orWhereNot("last_seen_file_id", fileId),
          )
          .select("id");
        if (omitted.length) {
          await trx("source_transactions")
            .whereIn(
              "id",
              omitted.map((item: any) => item.id),
            )
            .update({
              active: false,
              inactive_reason: "ABSENT_FROM_SNAPSHOT",
            });
          changed += omitted.length;
        }
      }
      await trx("ingestion_files")
        .where({ id: fileId })
        .update({ changed_count: changed });
      await audit(trx, "FILE_INGESTED", "ingestion_file", fileId, {
        source,
        mode,
        adapter_id: adapter.id,
        rows: rows.length,
        changed,
      });
      return {
        id: fileId,
        source,
        filename: upload.filename,
        checksum,
        mode,
        adapter_id: adapter.id,
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
      summary: asJson(r.summary_json),
      created_at: iso(r.created_at),
      closed_at: r.closed_at ? iso(r.closed_at) : null,
      closed_by: r.closed_by,
    }));
  });
  app.post("/api/runs", async (_req, reply) => {
    const sources = new Set(
      (
        await database("source_transactions")
          .where({ active: true })
          .distinct("source")
      ).map((x: any) => x.source),
    );
    if (!sources.has("LEDGER") || !sources.has("COUNTERPARTY"))
      return reply
        .code(409)
        .send(
          error("MISSING_SOURCE", "upload at least one file for each source"),
        );
    const result = await database.transaction(async (trx) => {
      const id = await returnedId(
        trx("reconciliation_runs").insert({
          settings_json: await settings(trx),
          summary_json: {},
          status: "OPEN",
        }),
      );
      const run = await trx("reconciliation_runs").where({ id }).first();
      const summary = await populateRun(trx, run);
      const populated = await trx("reconciliation_runs").where({ id }).first();
      await audit(trx, "RUN_CREATED", "reconciliation_run", id, summary);
      return {
        id,
        status: populated.status,
        summary,
        created_at: iso(populated.created_at),
        closed_at: null,
        closed_by: null,
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
    ).map((r: any) => ({ ...asJson<any>(r.result_json), id: r.id }));
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
      summary: asJson(run.summary_json),
    };
  });
  app.post("/api/resolutions/match", async (req: any, reply) => {
    await requireOpenRun();
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
      const id = await returnedId(
        trx("manual_resolutions").insert({
          resolution_type: "MANUAL_MATCH",
          ledger_transaction_id: lid,
          counterparty_transaction_id: cid,
          note,
        }),
      );
      await audit(trx, "MANUAL_MATCH_CREATED", "manual_resolution", id, {
        ledger: lid,
        counterparty: cid,
        note,
      });
      await refreshLatest(trx);
      return {
        id,
        type: "MANUAL_MATCH",
        active: true,
        created_at: new Date().toISOString(),
      };
    });
    return reply.code(201).send(result);
  });
  app.post("/api/resolutions/accept-unmatched", async (req: any, reply) => {
    await requireOpenRun();
    const { transaction_id: id, note = "" } = req.body ?? {},
      tx = await database("source_transactions").where({ id }).first();
    if (!tx)
      return reply.code(404).send(error("NOT_FOUND", "transaction not found"));
    const result = await database.transaction(async (trx) => {
      await ensureUnresolved(trx, [id]);
      const rid = await returnedId(
        trx("manual_resolutions").insert({
          resolution_type: "ACCEPT_UNMATCHED",
          accepted_transaction_id: id,
          note,
        }),
      );
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
  app.post("/api/resolutions/accept-differences", async (req: any, reply) => {
    const { item_id: itemId, note = "" } = req.body ?? {};
    const item = await database("reconciliation_items")
      .where({ id: itemId })
      .first();
    const run = item
      ? await database("reconciliation_runs").where({ id: item.run_id }).first()
      : null;
    if (!item || !run)
      return reply.code(404).send(error("NOT_FOUND", "item not found"));
    if (run.status === "CLOSED")
      return reply
        .code(409)
        .send(error("RUN_CLOSED", "closed runs are immutable"));
    if (
      item.status !== "DIFFERENT" ||
      !item.ledger_transaction_id ||
      !item.counterparty_transaction_id
    )
      return reply
        .code(422)
        .send(
          error(
            "INVALID_REVIEW_ITEM",
            "only a matched item with material differences can be accepted",
          ),
        );
    const result = await database.transaction(async (trx) => {
      await ensureUnresolved(trx, [
        item.ledger_transaction_id,
        item.counterparty_transaction_id,
      ]);
      const id = await returnedId(
        trx("manual_resolutions").insert({
          resolution_type: "ACCEPT_DIFFERENCES",
          ledger_transaction_id: item.ledger_transaction_id,
          counterparty_transaction_id: item.counterparty_transaction_id,
          note,
        }),
      );
      await audit(trx, "DIFFERENCES_ACCEPTED", "manual_resolution", id, {
        item: item.id,
        note,
      });
      await populateRun(trx, run);
      return {
        id,
        type: "ACCEPT_DIFFERENCES",
        active: true,
        created_at: new Date().toISOString(),
      };
    });
    return reply.code(201).send(result);
  });
  app.post("/api/runs/:runId/close", async (req: any, reply) => {
    const run = await database("reconciliation_runs")
      .where({ id: req.params.runId })
      .first();
    if (!run) return reply.code(404).send(error("NOT_FOUND", "run not found"));
    if (run.status === "CLOSED")
      return {
        id: run.id,
        status: run.status,
        summary: asJson(run.summary_json),
        created_at: iso(run.created_at),
        closed_at: iso(run.closed_at),
        closed_by: run.closed_by,
      };
    if (run.status !== "READY_TO_CLOSE")
      return reply
        .code(409)
        .send(
          error(
            "UNRESOLVED_EXCEPTIONS",
            "resolve every exception before closing the run",
          ),
        );
    const closedAt = new Date();
    await database.transaction(async (trx) => {
      await trx("reconciliation_runs").where({ id: run.id }).update({
        status: "CLOSED",
        closed_at: closedAt,
        closed_by: "demo.operator",
      });
      await audit(
        trx,
        "RUN_CLOSED",
        "reconciliation_run",
        run.id,
        asJson(run.summary_json),
      );
    });
    return {
      id: run.id,
      status: "CLOSED",
      summary: asJson(run.summary_json),
      created_at: iso(run.created_at),
      closed_at: closedAt.toISOString(),
      closed_by: "demo.operator",
    };
  });
  app.post(
    "/api/resolutions/:resolutionId/supersede",
    async (req: any, reply) => {
      await requireOpenRun();
      const previous = await database("manual_resolutions")
        .where({ id: req.params.resolutionId, active: true })
        .first();
      if (!previous)
        return reply
          .code(404)
          .send(error("NOT_FOUND", "active resolution not found"));
      const note = req.body?.note ?? "";
      const result = await database.transaction(async (trx) => {
        await trx("manual_resolutions")
          .where({ id: previous.id })
          .update({ active: false });
        const id = await returnedId(
          trx("manual_resolutions").insert({
            resolution_type: "SUPERSEDE",
            ledger_transaction_id: previous.ledger_transaction_id,
            counterparty_transaction_id: previous.counterparty_transaction_id,
            accepted_transaction_id: previous.accepted_transaction_id,
            note,
            active: false,
            supersedes_id: previous.id,
          }),
        );
        await audit(trx, "RESOLUTION_SUPERSEDED", "manual_resolution", id, {
          previous_resolution_id: previous.id,
          note,
        });
        await refreshLatest(trx);
        return {
          id,
          type: "SUPERSEDE",
          active: false,
          supersedes_id: previous.id,
          created_at: new Date().toISOString(),
        };
      });
      return reply.code(201).send(result);
    },
  );
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
        .where({ active: true })
        .update({ active: false });
      const id = await returnedId(
        trx("tolerance_settings").insert({
          settings_json: body,
          active: true,
        }),
      );
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
      details: asJson(r.details_json),
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
      data: asJson(r.data_json),
      raw: asJson(r.raw_json),
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
      const d = asJson<any>(row.result_json);
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
