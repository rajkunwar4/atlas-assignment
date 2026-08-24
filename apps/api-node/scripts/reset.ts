import { db, migrate } from "../src/db.js";
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
  "knex_migrations_lock",
  "knex_migrations",
])
  if (await db.schema.hasTable(table)) await db.schema.dropTable(table);
await migrate();
await db.destroy();
console.log("Node database reset");
