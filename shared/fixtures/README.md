# Fixture data

Every file here is generated demonstration data. The files fall into four groups.

| Group | Files | Purpose |
| --- | --- | --- |
| Original demo | `ledger.csv`, `counterparty.csv`, `ledger-correction.csv` | The small worked example from the assignment brief. Used by the seed scripts, the API tests and the Playwright workflow. |
| Extended week | `ledger-wide.csv`, `counterparty-wide.csv` | One trading week (4–8 August 2025) covering every reconciliation outcome and both sides of every tolerance boundary. |
| Corrections | `ledger-wide-correction.csv`, `counterparty-wide-correction.csv` | Full resends of the extended week with a few restated amounts, plus `ledger-wide-reformatted.csv` and `counterparty-wide-reformatted.csv`, which restate nothing at all. |
| Rejections | `invalid/*.csv` | Twelve files that must be refused whole, one defect class per file. |

The expected outcome of every row is recorded in
[`shared/expected-results/wide.json`](../expected-results/wide.json) and
[`shared/expected-results/invalid-files.json`](../expected-results/invalid-files.json).
The backend is held to those files by `apps/api-python/tests/test_fixtures.py`, so this
catalogue cannot drift away from the code.

## Using the extended week

The extended data uses its own identifiers, so it can be loaded alongside the original
demo data or on its own. Load it on its own for a clean summary:

```bash
npm run reset:python && npm run seed:python -- wide
```

Uploading `ledger-wide.csv` as **Ledger** and `counterparty-wide.csv` as **Counterparty**
through the screen does the same thing. Start a run once both files are in. The summary is 20 matched, 11 different, 7 unmatched ledger rows,
8 unmatched counterparty rows and 5 excluded cancellations.

Four rows are deliberately left for a human: `T-2034` has two equally close counterparty
rows and the tie is refused, `T-2035` and `C-9108` sit just outside the candidate gate and
want a manual match, `T-2038` is one order the counterparty reported as two partial fills,
and `T-2029` is a trade the counterparty cancelled after the ledger settled it.

Uploading the two correction files afterwards restates five amounts: three ledger breaks
close, the AVAX pair starts matching on similarity without ever sharing a reference, and
the LTC pair keeps its genuine 5-minute time break. The summary becomes 24 matched,
8 different, 6 unmatched ledger rows, 7 unmatched counterparty rows and 5 exclusions.

Uploading either `*-reformatted.csv` file demonstrates the opposite: a byte-order mark,
CRLF line endings, quoted fields, padded values and a blank line produce a new checksum,
so the file is ingested, but not one row version changes.

## Scenarios in the extended week

| Ledger | Counterparty | Outcome | Method | Fields outside tolerance | What it exercises |
| --- | --- | --- | --- | --- | --- |
| `—` | `C-9101` | EXCLUDED_CANCELLED | EXCLUDED | — | counterparty-only cancelled row |
| `—` | `C-9102` | UNMATCHED_COUNTERPARTY | — | — | counterparty-only trade with no ledger row |
| `—` | `C-9103` | UNMATCHED_COUNTERPARTY | — | — | counterparty-only adjustment row |
| `—` | `C-9106` | UNMATCHED_COUNTERPARTY | — | — | one half of the refused ambiguous tie |
| `—` | `C-9107` | UNMATCHED_COUNTERPARTY | — | — | the other half of the refused ambiguous tie |
| `—` | `C-9108` | UNMATCHED_COUNTERPARTY | — | — | the counterparty half of the manual-match pair |
| `—` | `C-9109` | UNMATCHED_COUNTERPARTY | — | — | the counterparty half of the below-threshold pair |
| `—` | `C-9111` | UNMATCHED_COUNTERPARTY | — | — | first partial fill, never aggregated automatically |
| `—` | `C-9112` | UNMATCHED_COUNTERPARTY | — | — | second partial fill, never aggregated automatically |
| `T-2001` | `T-2001` | MATCHED | EXACT_ID | — | identical rows on both sides |
| `T-2002` | `T-2002` | MATCHED | EXACT_ID | — | counterparty timestamp carries a +02:00 offset for the same instant |
| `T-2003` | `T-2003` | MATCHED | EXACT_ID | — | 90-second clock skew, inside the 120-second time tolerance |
| `T-2004` | `T-2004` | MATCHED | EXACT_ID | — | one-cent rounding difference, inside the absolute money tolerance |
| `T-2005` | `T-2005` | MATCHED | EXACT_ID | — | large notional 0.9 basis points apart, inside the relative money tolerance |
| `T-2006` | `T-2006` | MATCHED | EXACT_ID | — | same values written with a different decimal scale |
| `T-2007` | `T-2007` | MATCHED | EXACT_ID | — | lower-case instrument and side in both files |
| `T-2008` | `T-2008` | MATCHED | EXACT_ID | — | counterparty row padded with surrounding whitespace |
| `T-2009` | `t-2009` | MATCHED | EXACT_ID | — | counterparty reference differs only by letter case |
| `T-2010` | `T-2010` | MATCHED | EXACT_ID | — | sub-second timestamps 250 ms apart |
| `T-2011` | `T-2011` | MATCHED | EXACT_ID | — | exactly 120 seconds apart, the inclusive edge of the time tolerance |
| `T-2012` | `T-2012` | MATCHED | EXACT_ID | — | gross 9.7 basis points apart, just inside the relative money tolerance |
| `T-2013` | `T-2013` | MATCHED | EXACT_ID | — | quantity 0.00000001 apart, the inclusive edge of the quantity tolerance |
| `T-2014` | `T-2014` | MATCHED | EXACT_ID | — | zero price and zero gross, guarding relative comparison against divide-by-zero |
| `T-2015` | `T-2015` | MATCHED | EXACT_ID | — | row repeated identically in the ledger file and deduplicated on ingest |
| `T-2016` | `T-2016` | MATCHED | EXACT_ID | — | eight-decimal crypto quantity preserved exactly |
| `T-2017` | `T-2017` | MATCHED | EXACT_ID | — | counterparty row written with every field quoted |
| `T-2018` | `T-2018` | DIFFERENT | EXACT_ID | price, gross_amount | counterparty applied a fee, so price and gross differ materially |
| `T-2019` | `T-2019` | DIFFERENT | EXACT_ID | executed_at | same trade booked 40 minutes apart |
| `T-2020` | `T-2020` | DIFFERENT | EXACT_ID | quantity, gross_amount | counterparty recorded a smaller quantity |
| `T-2021` | `T-2021` | DIFFERENT | EXACT_ID | side | counterparty recorded the opposite side |
| `T-2022` | `T-2022` | DIFFERENT | EXACT_ID | instrument | counterparty settled the same reference in a different instrument |
| `T-2023` | `T-2023` | DIFFERENT | EXACT_ID | executed_at, price, gross_amount | time, price and gross all drift at once |
| `T-2024` | `T-2024` | DIFFERENT | EXACT_ID | gross_amount | gross 10.5 basis points apart, just outside the relative money tolerance |
| `T-2025` | `T-2025` | DIFFERENT | EXACT_ID | executed_at | 121 seconds apart, just outside the time tolerance |
| `T-2026` | `T-2026` | DIFFERENT | EXACT_ID | quantity | quantity 0.00000002 apart, just outside the quantity tolerance |
| `T-2027` | `—` | EXCLUDED_CANCELLED | EXCLUDED | — | cancelled on both sides and never compared |
| `—` | `T-2027` | EXCLUDED_CANCELLED | EXCLUDED | — | cancelled on both sides and never compared |
| `T-2028` | `—` | EXCLUDED_CANCELLED | EXCLUDED | — | cancelled in the ledger with no counterparty row |
| `—` | `T-2029` | EXCLUDED_CANCELLED | EXCLUDED | — | the ledger settled a trade the counterparty cancelled |
| `T-2029` | `—` | UNMATCHED_LEDGER | — | — | the ledger settled a trade the counterparty cancelled |
| `T-2030` | `—` | UNMATCHED_LEDGER | — | — | ledger-only trade with no counterparty row |
| `T-2031` | `—` | UNMATCHED_LEDGER | — | — | ledger trade booked at 23:58 on the last day of the period |
| `T-2032` | `C-9104` | MATCHED | CANDIDATE_SCORE (0.9667) | — | different references one minute apart, matched on similarity |
| `T-2033` | `C-9105` | DIFFERENT | CANDIDATE_SCORE (0.7834) | executed_at, price, gross_amount | different references matched on similarity, with a real fee and time break |
| `T-2034` | `—` | UNMATCHED_LEDGER | — | — | two equally close counterparty rows, so the tie is refused rather than guessed |
| `T-2035` | `—` | UNMATCHED_LEDGER | — | — | gross 1.26 percent apart, outside the candidate gate, left for a manual match |
| `T-2036` | `—` | UNMATCHED_LEDGER | — | — | 468 seconds apart, scoring 0.74 and below the 0.75 threshold |
| `T-2037` | `C-9110` | DIFFERENT | CANDIDATE_SCORE (0.7500) | executed_at | 450 seconds apart, scoring exactly 0.75, the inclusive threshold |
| `T-2038` | `—` | UNMATCHED_LEDGER | — | — | one ledger order the counterparty reported as two partial fills |
| `T-2039` | `T-2039` | MATCHED | EXACT_ID | — | identical twin trade matched by reference rather than by similarity |
| `T-2040` | `T-2040` | MATCHED | EXACT_ID | — | identical twin trade matched by reference rather than by similarity |

## Formatting differences carried by the counterparty file

- `T-2002` uses a `+02:00` offset for the same instant the ledger records in UTC.
- `T-2006` writes whole numbers where the ledger writes two decimal places.
- `T-2007` writes the instrument and direction in lower case in both files.
- `T-2008` pads every field with spaces.
- `t-2009` differs from the ledger reference only by letter case.
- `T-2010` carries milliseconds; the two clocks are 250 ms apart.
- `T-2017` quotes every field.
- Most counterparty timestamps have no timezone at all and are read as UTC, which is the
  documented assumption for that source.

## Rejected files

Each file is refused as a whole; nothing in it is imported.

| File | Source | Defect |
| --- | --- | --- |
| `invalid/ledger-bad-header.csv` | Ledger | A column is renamed. |
| `invalid/counterparty-bad-header-order.csv` | Counterparty | The columns are reordered. |
| `invalid/ledger-header-only.csv` | Ledger | Header present, no data rows. |
| `invalid/ledger-missing-timezone.csv` | Ledger | A ledger timestamp has no timezone. |
| `invalid/ledger-negative-amount.csv` | Ledger | A gross amount is negative. |
| `invalid/ledger-invalid-state.csv` | Ledger | An unknown state, `PENDING`. |
| `invalid/ledger-truncated-row.csv` | Ledger | A row is cut short by a broken export. |
| `invalid/ledger-conflicting-duplicate.csv` | Ledger | One trade ID appears twice with different amounts. |
| `invalid/ledger-multiple-errors.csv` | Ledger | Five defects at once: blank ID, unknown side, non-ISO date, non-numeric quantity, missing instrument. |
| `invalid/counterparty-invalid-direction.csv` | Counterparty | A direction outside `B`/`S`/`BUY`/`SELL`. |
| `invalid/counterparty-thousands-separator.csv` | Counterparty | A grouped number, `15,250.00`. |
| `invalid/counterparty-not-utf8.csv` | Counterparty | A byte that is not valid UTF-8. |

Both services reject all twelve. The wording of a few reasons differs: Python refuses the
non-UTF-8 file at decode time while Node substitutes the byte and refuses the row that
contains it, and Node reports `04/08/2025 09:45` as a missing timezone where Python reports
it as a malformed ISO-8601 date-time. The shared expectations file asserts the row numbers
and the wording the two services agree on.

## Tolerances these files were built against

The boundary rows above assume the default settings in `app/domain.py` and `src/domain.ts`:
120 seconds of clock skew, `0.00000001` absolute on quantity, `0.01` absolute or one basis
point relative on money, and for candidate matching a 900-second window, `0.001` relative
quantity, `0.01` relative gross and a minimum score of `0.75`. Change any of those and the
rows named "just inside" or "just outside" will move, which is the point of having them.
