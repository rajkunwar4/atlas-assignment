import type { Knex } from "knex";

export async function up(db: Knex) {
  await db.schema.createTable("ingestion_files", (table) => {
    table.increments("id");
    table.string("source").notNullable().index();
    table.string("filename").notNullable();
    table.string("checksum").notNullable();
    table.integer("row_count").notNullable();
    table.integer("changed_count").notNullable();
    table.timestamp("created_at").notNullable().defaultTo(db.fn.now());
    table.unique(["source", "checksum"]);
  });
  await db.schema.createTable("source_transactions", (table) => {
    table.increments("id");
    table.string("source").notNullable();
    table.string("external_id").notNullable();
    table.integer("current_version_id");
    table.timestamp("created_at").notNullable().defaultTo(db.fn.now());
    table.unique(["source", "external_id"]);
    table.index(["source", "external_id"], "idx_source_transactions_lookup");
  });
  await db.schema.createTable("transaction_versions", (table) => {
    table.increments("id");
    table
      .integer("transaction_id")
      .notNullable()
      .references("source_transactions.id");
    table
      .integer("ingestion_file_id")
      .notNullable()
      .references("ingestion_files.id");
    table.integer("version").notNullable();
    table.string("fingerprint").notNullable();
    table.text("data_json").notNullable();
    table.text("raw_json").notNullable();
    table.timestamp("created_at").notNullable().defaultTo(db.fn.now());
    table.unique(["transaction_id", "version"]);
    table.index("transaction_id");
  });
  await db.schema.createTable("reconciliation_runs", (table) => {
    table.increments("id");
    table.text("settings_json").notNullable();
    table.text("summary_json").notNullable().defaultTo("{}");
    table.string("status").notNullable().defaultTo("COMPLETED");
    table.timestamp("created_at").notNullable().defaultTo(db.fn.now());
  });
  await db.schema.createTable("reconciliation_items", (table) => {
    table.increments("id");
    table
      .integer("run_id")
      .notNullable()
      .references("reconciliation_runs.id")
      .index();
    table.integer("ledger_transaction_id");
    table.integer("counterparty_transaction_id");
    table.string("status").notNullable().index();
    table.string("match_method").notNullable();
    table.string("score");
    table.text("result_json").notNullable();
  });
  await db.schema.createTable("field_differences", (table) => {
    table.increments("id");
    table
      .integer("item_id")
      .notNullable()
      .references("reconciliation_items.id")
      .index();
    table.string("field").notNullable();
    table.text("difference_json").notNullable();
  });
  await db.schema.createTable("manual_resolutions", (table) => {
    table.increments("id");
    table.string("resolution_type").notNullable();
    table.integer("ledger_transaction_id");
    table.integer("counterparty_transaction_id");
    table.integer("accepted_transaction_id");
    table.text("note").notNullable().defaultTo("");
    table.string("actor").notNullable().defaultTo("demo.operator");
    table.boolean("active").notNullable().defaultTo(true).index();
    table.integer("supersedes_id");
    table.timestamp("created_at").notNullable().defaultTo(db.fn.now());
  });
  await db.schema.createTable("tolerance_settings", (table) => {
    table.increments("id");
    table.text("settings_json").notNullable();
    table.boolean("active").notNullable().defaultTo(true);
    table.timestamp("created_at").notNullable().defaultTo(db.fn.now());
  });
  await db.schema.createTable("audit_events", (table) => {
    table.increments("id");
    table.string("action").notNullable().index();
    table.string("entity_type").notNullable();
    table.string("entity_id").notNullable();
    table.string("actor").notNullable().defaultTo("demo.operator");
    table.text("details_json").notNullable().defaultTo("{}");
    table.timestamp("created_at").notNullable().defaultTo(db.fn.now());
  });
}

export async function down(db: Knex) {
  for (const table of [
    "audit_events",
    "tolerance_settings",
    "manual_resolutions",
    "field_differences",
    "reconciliation_items",
    "reconciliation_runs",
    "transaction_versions",
    "source_transactions",
    "ingestion_files",
  ]) {
    await db.schema.dropTableIfExists(table);
  }
}
