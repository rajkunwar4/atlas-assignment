# Atlas Reconciliation

Atlas Reconciliation is a small operations console for reconciling transactions recorded by two companies. It ingests mismatched CSV formats, preserves corrections, performs conservative and explainable matching, highlights material differences, and remembers manual decisions across runs.

The project intentionally implements the same product twice: Python/FastAPI is the primary learning implementation, while TypeScript/Fastify demonstrates that the domain model and API contract are portable. Both backends are consumed by one React UI and verified with shared fixtures.

## Features

- Atomic CSV validation and source-specific normalization
- Idempotent uploads using file checksums
- Immutable row version history for corrections
- Auto-detected, extensible CSV format adapters
- Explicit incremental and complete-snapshot uploads
- Exact-reference and explainable candidate matching
- Configurable financial and time tolerances
- Side-by-side field differences and match explanations
- Persistent manual matches, exception decisions, and run sign-off
- Immutable reconciliation run snapshots and audit events
- One PostgreSQL history shared by both backend implementations
- CSV export and a shared API contract

See [docs/architecture.md](docs/architecture.md) for the design, data model, matching rules, and decision log.

## Prerequisites

- Node.js 20 or newer
- Python 3.11 or newer
- PostgreSQL 15 or newer, or a Neon PostgreSQL database

## Install

```bash
npm run install:all
cp .env.example .env
npm run db:up
npm run migrate
```

The example environment uses the Docker PostgreSQL service on port `55432`. To use Neon,
replace `DATABASE_URL` with its pooled connection string and `DIRECT_URL` with the direct
non-pooler connection string. Keep `TEST_DATABASE_URL` pointed at a disposable local
database. Never commit `.env` or use the Neon database for destructive tests.

## Run with the Python backend

```bash
npm run reset:db
npm run seed:python
npm run dev:python
```

In another terminal:

```bash
VITE_API_URL=http://localhost:8000 npm run dev:web
```

## Run with the Node backend

```bash
npm run reset:db
npm run seed:node
npm run dev:node
```

In another terminal:

```bash
VITE_API_URL=http://localhost:8001 npm run dev:web
```

Open `http://localhost:5173`. The development backend switcher can move between the Python API on port 8000 and Node API on port 8001 without changing frontend code. Both APIs show the same PostgreSQL-backed files, runs, resolutions, and audit history.

Alembic is the only schema migration authority. Always run `npm run migrate` before either
backend; `npm run check:schema:node` verifies that Node recognizes the migrated schema.

## Demo workflow

1. Upload `shared/fixtures/ledger.csv` as **Ledger**, using incremental mode and automatic format detection.
2. Upload `shared/fixtures/counterparty.csv` as **Counterparty**.
3. Start a reconciliation run.
4. Inspect the amount difference on `T-1011` and time difference on `T-1015`.
5. Accept the explained differences, then manually match `T-1016` to `C-9001` or accept rows as genuinely unmatched.
6. Close the run after its unresolved count reaches zero.
7. Upload `shared/fixtures/ledger-correction.csv` to demonstrate immutable correction history.
8. Start another run and verify the manual decision persists.

The seed commands perform the first two uploads automatically.

## Extended data set

`shared/fixtures/ledger-wide.csv` and `shared/fixtures/counterparty-wide.csv` hold a full
trading week that exercises every outcome the system can produce: tolerance-sized rounding
and clock differences, material breaks on each field, both directions of unmatched rows,
cancellations on one side and on both, similarity matches with no shared reference, a
refused ambiguous tie, partial fills the system deliberately does not aggregate, and rows
sitting immediately on either side of each tolerance and scoring threshold. Two correction
files restate a few amounts, two reformatted files restate none, and `shared/fixtures/invalid`
holds twelve files that must be rejected whole.

Load it with `npm run seed:python -- wide` or `npm run seed:node -- wide` after `npm run reset:db`, or
upload the two files through the screen.

Every expected outcome is recorded in `shared/expected-results/wide.json` and
`shared/expected-results/invalid-files.json`, and both backends are tested against them.
[shared/fixtures/README.md](shared/fixtures/README.md) catalogues each row and what it covers.

## Tests and verification

```bash
npm test
npm run build
npm run quality
```

Run individual suites with `npm run test:python`, `npm run test:node`, or `npm run test:web`. The backend suites automatically migrate `TEST_DATABASE_URL`. With both APIs running and one fixture set seeded, run `npm run test:conformance`; each backend independently creates a run and the test compares their normalized outcomes.

Install a Playwright browser once with `npx playwright install chromium`, then run `npm run test:e2e` to reset the safe configured database and execute the same upload, reconciliation, and difference-inspection workflow against both backends.

Useful database commands:

```bash
npm run db:up              # start local PostgreSQL
npm run migrate            # apply the Alembic schema
npm run check:schema:node  # confirm Node compatibility
npm run reset:db           # local/test databases only; remote targets are refused
npm run db:down            # stop local PostgreSQL
```

## Repository structure

```text
apps/web          Shared React and TypeScript interface
apps/api-python   FastAPI, SQLAlchemy, Alembic, and PostgreSQL
apps/api-node     Fastify, Knex, and PostgreSQL
shared/openapi    Contract-first API description
shared/fixtures   Generated demonstration CSV data
shared/conformance Black-box backend parity checks
docs              Architecture and operational documentation
```

## Intentional boundaries

This is a single-operator take-home application. Authentication, background jobs, object storage, multi-tenancy, and deployment automation are intentionally omitted. Runs execute synchronously and files are held in memory while being validated. AI is deliberately excluded from matching and approval: financial outcomes remain deterministic and attributable to rules or a named human decision.

## Future work

- Expose the existing resolution-supersession API in a dedicated history UI
- Stream very large files and execute reconciliation in background workers
- Add an operator UI for registering declarative adapter mappings
- Add object-storage deployment profiles
- Add authentication and per-action operator identities

## Demo video

Follow [docs/demo-script.md](docs/demo-script.md), then add the final 3-5 minute walkthrough URL here before submission.
