import knex, { type Knex } from "knex";

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

export const databaseUrl =
  process.env.DATABASE_URL ??
  "postgresql://atlas:atlas@localhost:55432/atlas?sslmode=disable";

export const db: Knex = knex({
  client: "pg",
  connection: databaseUrl,
  pool: { min: 0, max: 5 },
});

export async function ensureSchema(database: Knex = db) {
  const revision = await database("alembic_version")
    .select("version_num")
    .first();
  if (revision?.version_num !== "0001") {
    throw new Error(
      "database schema is not at Alembic revision 0001; run npm run migrate",
    );
  }
  const activeSettings = await database("tolerance_settings")
    .where({ active: true })
    .first();
  if (!activeSettings) {
    await database("tolerance_settings").insert({
      settings_json: DEFAULT_SETTINGS,
      active: true,
    });
  }
}
