# Guided reconciliation story

This folder is a self-contained demonstration of the reconciliation system. Use the files in
numeric order from an empty database. The sequence is intentionally split into four closed runs so
that corrections and persistent human decisions are visible instead of merely described.

The system always has two logical sources: **Ledger** and **Counterparty**. Files 05 and 06 use a
third physical CSV layout, the shared canonical export format. They demonstrate format
extensibility; they do not introduce a third company into a two-sided reconciliation.

## What the story proves

- automatic detection of three physical CSV layouts;
- atomic validation and complete-file rejection;
- exact-ID matching and field comparison as separate decisions;
- inclusive financial and timestamp tolerances;
- conservative scored matching when identifiers differ;
- refusal to guess when two candidates tie;
- material-difference review;
- manual matching with a side-by-side candidate preview;
- accepted-unmatched decisions on both sources;
- cancellation exclusion;
- immutable correction versions and no noisy versions for unchanged rows;
- incremental behavior: rows omitted from a correction file remain current;
- persistence of manual matches, accepted differences, and accepted-unmatched decisions;
- a full restatement in a different format without changing unchanged transactions;
- one-open-run protection and immutable closed-run history.

## Before starting

Reset the local database, start the API, then start the web app in another terminal:

```bash
npm run reset:db
npm run dev:python
npm run dev:web
```

Open `http://localhost:5173`. Select files from this directory; the source to use is stated for
every upload below. Do not upload the next cycle's files until the current run is closed. While a
run is open, the source cards intentionally replace **Choose CSV** with an upload-lock message.

## Run 1 — establish the baseline and human decisions

Upload in this order:

1. `01-ledger-baseline.csv` as **Ledger** — 7 rows, 7 changed.
2. `02-counterparty-baseline.csv` as **Counterparty** — 7 rows, 7 changed.
3. Click **Start run**.

Expected initial summary:

| Result | Count |
| --- | ---: |
| Matched | 3 |
| Different | 1 |
| Unmatched Ledger | 2 |
| Unmatched Counterparty | 2 |
| Excluded Cancelled | 2 |
| Unresolved | 5 |

Inspect these examples:

| Transactions | Expected result | Why it matters |
| --- | --- | --- |
| `ST-1001 ↔ ST-1001` | Matched / Exact ID | Same reference and values. |
| `ST-1002 ↔ ST-1002` | Matched / Exact ID | A 90-second clock difference and one-cent money differences are inside tolerance. |
| `ST-1003 ↔ ST-1003` | Different / Exact ID | Same identity, but price and gross amount materially differ. |
| `L-AUTO-1 ↔ C-AUTO-1` | Matched / Candidate score | Different identifiers, but the unique safe candidate is one minute apart with matching economics. |
| `ST-CANCEL-1` on both sides | Excluded Cancelled | Cancelled evidence is retained but not matched. |

Resolve every exception in this order:

1. Open `ST-1003`, enter `Confirmed against broker statement`, and choose
   **Accept differences**.
2. Open `L-MANUAL-1`, select `C-MANUAL-1 · AVAX-USD`, inspect the side-by-side values, enter
   `Same trade confirmed by external ticket`, and choose **Save manual match**. The records are
   economically identical but 30 minutes apart, so the automatic candidate gate correctly left
   them for a person.
3. Open `L-ONLY-1`, enter `Internal-only fee allocation`, and choose **Accept unmatched**.
4. Open `C-ONLY-1`, enter `Counterparty-only adjustment`, and choose **Accept unmatched**.
5. Confirm **Unresolved** is zero, then click **Close run**.

This run creates all three reusable decision types: accepted differences, a manual pair, and
accepted-unmatched transactions.

## Run 2 — incremental corrections and decision persistence

Upload in this order:

1. `03-ledger-corrections-incremental.csv` as **Ledger** — 2 rows, only 1 changed.
2. `04-counterparty-corrections-incremental.csv` as **Counterparty** — 2 rows, only 1 changed.
3. Click **Start run**.

These are deliberately small correction files, not full snapshots. Every omitted baseline row
remains current. Each file also repeats one unchanged row, proving that unchanged normalized data
does not create another transaction version.

Expected summary:

| Result | Count |
| --- | ---: |
| Matched | 4 |
| Manually Matched | 1 |
| Accepted Unmatched | 2 |
| Excluded Cancelled | 2 |
| Unresolved | 0 |

What changed:

- Ledger `ST-1003` now agrees with the counterparty, so the earlier material break becomes clean.
- Counterparty `ST-1002` is restated closer in time with exact money values.
- `L-MANUAL-1 ↔ C-MANUAL-1` is reapplied automatically.
- `L-ONLY-1` and `C-ONLY-1` remain accepted unmatched.
- The run is immediately ready to close; no human decision needs to be repeated.

Close the run.

## Run 3 — switch to the third format and add new cases

Upload in this order:

1. `05-ledger-full-canonical.csv` as **Ledger** — 11 rows, only 4 new rows changed.
2. `06-counterparty-full-canonical.csv` as **Counterparty** — 12 rows, only 5 new rows changed.
3. Click **Start run**.

Both files now use the canonical header:

```text
transaction_id,executed_at,instrument,side,quantity,unit_price,gross_amount,state
```

The backend detects the canonical Ledger and Counterparty adapters from the same header plus the
selected logical source. Although these are full restatements in a different layout, unchanged
normalized rows create no new versions. Only the newly introduced rows count as changed.

Expected initial summary:

| Result | Count |
| --- | ---: |
| Matched | 6 |
| Different | 1 |
| Manually Matched | 1 |
| Accepted Unmatched | 2 |
| Unmatched Ledger | 1 |
| Unmatched Counterparty | 2 |
| Excluded Cancelled | 2 |
| Unresolved | 4 |

Inspect and resolve the new cases:

1. `ST-3001 ↔ ST-3001` is a new clean exact-ID match.
2. `L-CANDIDATE-3 ↔ C-CANDIDATE-3` is another safe scored match across different identifiers.
3. Open `ST-3002`, inspect its price and gross difference, enter `Known execution adjustment`,
   and choose **Accept differences**.
4. `L-TIE-1` has two economically identical candidates, `C-TIE-A` and `C-TIE-B`. The engine
   refuses the equal-score tie instead of choosing arbitrarily. Open `L-TIE-1`, select
   `C-TIE-A · DOGE-USD`, inspect the comparison, enter `External ticket identifies candidate A`,
   and save the manual match.
5. Open the remaining `C-TIE-B`, enter `Separate counterparty-only record`, and accept it as
   unmatched.
6. Confirm **Unresolved** is zero and close the run.

The final Run 3 summary has 6 matched, 1 different, 2 manually matched, 3 accepted unmatched, 2
excluded cancellations, and no unresolved exceptions.

## Run 4 — prove the newer decisions survive too

Upload in this order:

1. `07-ledger-final-correction.csv` as **Ledger** — 2 rows, 2 changed.
2. `08-counterparty-new-rows.csv` as **Counterparty** — 1 row, 1 changed.
3. Click **Start run**.

Expected summary:

| Result | Count |
| --- | ---: |
| Matched | 8 |
| Manually Matched | 2 |
| Accepted Unmatched | 3 |
| Excluded Cancelled | 2 |
| Unresolved | 0 |

`ST-3002` is corrected to a clean match, and the new `ST-4001` pair passes despite a harmless
30-second clock difference. Both manual pairs and all accepted-unmatched decisions are reapplied.
The old accepted-difference decision remains in the audit trail, but it is no longer needed because
`ST-3002` now compares cleanly. Close the run.

## Optional proof points

These are useful in a longer technical walkthrough but can distract from a short product demo.

### Atomic rejection

After closing a run, upload `90-ledger-invalid-atomic.csv` as **Ledger**. The first row is valid and
the second has a negative gross amount. The complete file is rejected, and
`SHOULD-NOT-IMPORT` never appears in a later run.

### Duplicate-file idempotency

Upload `08-counterparty-new-rows.csv` again after Run 4 closes. The checksum, source, and detected
adapter identify the existing ingestion, so it creates neither a new file record nor a transaction
version.

### Open-run protection

Return to Overview before closing any run. The accepted source evidence remains visible, but file
selection is unavailable until the run closes. This prevents a changed upload from altering the
data underneath an active review.

## Recommended presentation length

- **Short 5-minute demo:** Run 1 decisions, Run 2 persistence, then explain Run 3's third format
  without resolving every tie on camera.
- **Full 10-minute walkthrough:** Complete all four runs and finish with the atomic-rejection file.
- **Technical review:** Add the duplicate-file check and inspect Activity after each cycle to show
  the durable audit trail.
