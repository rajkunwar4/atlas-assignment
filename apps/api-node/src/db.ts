import knex, { type Knex } from "knex";
import { fileURLToPath } from "node:url";

export type { Knex } from "knex";

export const DEFAULT_SETTINGS = {
  time_tolerance_seconds: 120,
  quantity_abs_tolerance: "0.00000001",
  money_abs_tolerance: "0.01",
  money_rel_tolerance: "0.0001",
  candidate_time_seconds: 900,
  candidate_quantity_rel: "0.001",
  candidate_gross_rel: "0.01",
  candidate_min_score: "0.75",
};

export const databasePath =
  process.env.DATABASE_PATH ??
  fileURLToPath(new URL("../reconciliation.db", import.meta.url));

export const db: Knex = knex({
  client: "better-sqlite3",
  connection: { filename: databasePath },
  useNullAsDefault: true,
});

export async function migrate(database: Knex = db) {
  const directory = fileURLToPath(new URL("../migrations", import.meta.url));
  await database.migrate.latest({ directory, extension: "ts" });

  const activeSettings = await database("tolerance_settings")
    .where({ active: 1 })
    .first();
  if (!activeSettings) {
    await database("tolerance_settings").insert({
      settings_json: JSON.stringify(DEFAULT_SETTINGS),
      active: 1,
    });
  }
  await database.raw("PRAGMA optimize");
}
