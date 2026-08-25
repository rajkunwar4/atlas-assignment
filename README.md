# Atlas Reconciliation

Atlas Reconciliation is a small operations console for reconciling transactions recorded by two companies. It ingests mismatched CSV formats, preserves corrections, performs conservative and explainable matching, highlights material differences, and remembers manual decisions across runs.

The project intentionally implements the same product twice: Python/FastAPI is the primary learning implementation, while TypeScript/Fastify demonstrates that the domain model and API contract are portable. Both backends are consumed by one React UI and verified with shared fixtures.

## Features

- Atomic CSV validation and source-specific normalization
- Idempotent uploads using file checksums
- Immutable row version history for corrections
- Exact-reference and explainable candidate matching
- Configurable financial and time tolerances
- Side-by-side field differences and match explanations
- Persistent manual matches and accepted-unmatched decisions
- Immutable reconciliation run snapshots and audit events
- CSV export and a shared API contract

See [docs/architecture.md](docs/architecture.md) for the design, data model, matching rules, and decision log.

## Prerequisites

- Node.js 20 or newer
- Python 3.11 or newer

## Install

```bash
npm run install:all
```

## Run with the Python backend

```bash
npm run reset:python
npm run seed:python
npm run dev:python
```

In another terminal:

```bash
VITE_API_URL=http://localhost:8000 npm run dev:web
```

## Run with the Node backend

```bash
npm run reset:node
npm run seed:node
npm run dev:node
```

In another terminal:

```bash
VITE_API_URL=http://localhost:8001 npm run dev:web
```

Open `http://localhost:5173`. The development backend switcher can move between the Python API on port 8000 and Node API on port 8001 without changing frontend code.

## Demo workflow

1. Upload `shared/fixtures/ledger.csv` as **Ledger**.
2. Upload `shared/fixtures/counterparty.csv` as **Counterparty**.
3. Start a reconciliation run.
4. Inspect the amount difference on `T-1011` and time difference on `T-1015`.
5. Manually match `T-1016` to `C-9001`, or accept either row as genuinely unmatched.
6. Upload `shared/fixtures/ledger-correction.csv` to demonstrate immutable correction history.
7. Start another run and verify the manual decision persists.

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

Load it with `npm run seed:python -- wide` or `npm run seed:node -- wide` after a reset, or
upload the two files through the screen.

Every expected outcome is recorded in `shared/expected-results/wide.json` and
`shared/expected-results/invalid-files.json`, and both backends are tested against them.
[shared/fixtures/README.md](shared/fixtures/README.md) catalogues each row and what it covers.

## Tests and verification

```bash
npm test
npm run build
```

Run individual suites with `npm run test:python`, `npm run test:node`, or `npm run test:web`. With both APIs running and seeded, run `npm run test:conformance` to compare their observable results.

Install a Playwright browser once with `npx playwright install chromium`, then run `npm run test:e2e` to execute the same upload, reconciliation, and difference-inspection workflow against both backends. Run `npm run quality` for Python linting and repository formatting checks.

## Repository structure

```text
apps/web          Shared React and TypeScript interface
apps/api-python   FastAPI, SQLAlchemy, Alembic, and SQLite
apps/api-node     Fastify, Knex, and SQLite
shared/openapi    Contract-first API description
shared/fixtures   Generated demonstration CSV data
shared/conformance Black-box backend parity checks
docs              Architecture and operational documentation
```

## Intentional boundaries

This is a single-operator take-home application. Authentication, background jobs, object storage, multi-tenancy, and hosted deployment are intentionally omitted. Runs execute synchronously and files are held in memory while being validated. A production system would add identity-aware authorization, malware scanning, durable object storage, job queues, metrics, and retention controls.

## Future work

- Review and supersede manual resolutions from the UI
- Stream very large files and execute reconciliation in background workers
- Add source adapter configuration rather than code-defined formats
- Add PostgreSQL and object-storage deployment profiles
- Add authentication and per-action operator identities

## Demo video

Follow [docs/demo-script.md](docs/demo-script.md), then add the final 3-5 minute walkthrough URL here before submission.
