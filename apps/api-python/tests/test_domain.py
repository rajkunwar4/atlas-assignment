from datetime import datetime, timezone, timedelta
from decimal import Decimal
from app.domain import Transaction, candidate_score, reconcile


def tx(i, source="LEDGER", external=None, minutes=0, gross="100", state="SETTLED"):
    return Transaction(
        i,
        source,
        external or f"T-{i}",
        datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes),
        "BTC-USD",
        "BUY",
        Decimal("1"),
        Decimal("100"),
        Decimal(gross),
        state,
    )


def test_exact_id_records_material_difference():
    items = reconcile(
        [tx(1, external="A")], [tx(2, "COUNTERPARTY", "A", gross="110")], [], set()
    )
    assert items[0]["status"] == "DIFFERENT"
    assert any(
        d["field"] == "gross_amount" and not d["passed"]
        for d in items[0]["differences"]
    )


def test_candidate_matching_is_conservative():
    assert (
        candidate_score(tx(1), tx(2, "COUNTERPARTY", external="OTHER", minutes=4))
        is not None
    )
    items = reconcile(
        [tx(1)], [tx(2, "COUNTERPARTY", external="OTHER", minutes=20)], [], set()
    )
    assert {x["status"] for x in items} == {
        "UNMATCHED_LEDGER",
        "UNMATCHED_COUNTERPARTY",
    }


def test_cancelled_is_excluded():
    items = reconcile([tx(1, state="CANCELLED")], [], [], set())
    assert items[0]["status"] == "EXCLUDED_CANCELLED"


def test_manual_pair_has_manual_status():
    items = reconcile(
        [tx(1)], [tx(2, "COUNTERPARTY", external="X", minutes=30)], [(1, 2)], set()
    )
    assert items[0]["status"] == "MANUALLY_MATCHED"
