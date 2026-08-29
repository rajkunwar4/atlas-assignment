# Take-Home Assignment B: The Reconciliation Problem

Transcribed verbatim from `2026-08-11 Assignment B — Transaction Reconciliation.pdf` (the original
brief) so it can be used as a fact-check reference without reopening the PDF.

**Time:** Suggested timeline is Monday 24 August 2026. If you need a little longer because of work
or other commitments, just let us know. Allow approximately 5 to 6 hours of work.
**Stack:** any Python web framework, any database, any way of building the UI.
**Data:** make up your own.
**Anything unclear:** decide for yourself and record the decision in the README.

## The problem

Two systems recorded the same transactions. Your own ledger says one thing. The other company's
statement says another. They disagree on amounts, on times, and on which transactions happened at
all. Until someone works out where and why, the accounts cannot be closed.

The two systems were built by different companies and were never designed to agree. They use
different column names for the same field, write dates in different formats, and use different
words for the same value, so where one says BUY the other says B. Tomorrow there may be a third
company sending a third format.

Not every difference is a real problem. Amounts drift apart slightly because of rounding and fees,
and recorded times drift apart slightly because two clocks are never quite the same. A tiny
difference is normal. A large one means something is wrong, and the person looking at it needs to
know which fields differ and by how much. Some rows will have nothing matching them on the other
side at all, and this happens in both directions. Cancelled transactions also appear in the files,
and these were never meant to be compared at all.

Files keep arriving. Sometimes the same file is sent twice. Sometimes a file is a correction, where
most rows are unchanged but a few amounts have been fixed, and the fixed values are the ones that
count from then on, although people will still ask what the row used to say.

The process runs every morning, and between runs people resolve things by hand. They match two rows
the system could not match, or they accept that a row genuinely has no pair. Whatever they decide
must still hold tomorrow.

**Build the screen someone opens each morning to find what does not match, and to resolve it.**

## What to build

- **Database.** Design the tables.
- **Backend.** Loading the files, matching, and comparing. The comparison logic should be testable
  without a database and without a browser.
- **UI.** Enough to be usable: start a run, see the results, inspect what differs on a row that does
  not agree, and match an unmatched row by hand. Plain server-rendered pages are fine.
- **Tests.** Cover the logic that matters.
- **README.** How to run it, what you decided and why, what you left out, and what you would do
  next.

## Example data (illustration only — make your own, and more of it)

Your own ledger:

```csv
trade_id,traded_at,instrument,side,quantity,price,gross_amount,state
T-1001,2025-07-01T09:15:00Z,BTC-USD,BUY,0.50,62000.00,31000.00,SETTLED
T-1011,2025-07-04T10:15:00Z,ETH-USD,BUY,10.00,3400.00,34000.00,SETTLED
T-1015,2025-07-05T10:00:00Z,SOL-USD,SELL,300.00,146.00,43800.00,SETTLED
T-1016,2025-07-06T09:00:00Z,BTC-USD,BUY,0.20,63200.00,12640.00,SETTLED
T-1018,2025-07-06T15:00:00Z,SOL-USD,BUY,100.00,149.00,14900.00,CANCELLED
```

The other company's statement, same period:

```csv
reference,executed_at,symbol,direction,qty,unit_price,total,status
T-1001,2025-07-01 09:15:00,BTC-USD,B,0.5,62000,31000.00,SETTLED
T-1011,2025-07-04 10:15:00,ETH-USD,B,10,3417,34170.00,SETTLED
T-1015,2025-07-05 10:40:00,SOL-USD,S,300,146,43800.00,SETTLED
C-9001,2025-07-06 11:20:00,BTC-USD,B,0.15,63100,9465.00,SETTLED
```

Create data of your own that covers the rest.

## Sending it back

Push your work to a public GitHub repository and reply to the assignment email with the link.
Commit as you normally would, as it will be reviewed via the commit history.

Please also send a short video, 3 to 5 minutes, of the application running. A screen recording with
narration. It need not be edited. Show a run from start to finish and talk through the cases judged
worth handling. A Loom link, a Drive link, or an mp4 in the repository are all fine.
