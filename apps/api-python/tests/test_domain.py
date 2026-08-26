from datetime import datetime, timezone, timedelta
from decimal import Decimal
from app.domain import Transaction, candidate_score, compare, reconcile


def tx(
    i,
    source="LEDGER",
    external=None,
    minutes=0,
    seconds=0,
    quantity="1",
    price="100",
    gross="100",
    state="SETTLED",
):
    return Transaction(
        i,
        source,
        external or f"T-{i}",
        datetime(2025, 1, 1, tzinfo=timezone.utc)
        + timedelta(minutes=minutes, seconds=seconds),
        "BTC-USD",
        "BUY",
        Decimal(quantity),
        Decimal(price),
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


def test_accepted_unmatched_decision_prevents_future_automatic_pairing():
    items = reconcile(
        [tx(1, external="A")],
        [tx(2, "COUNTERPARTY", external="A")],
        [],
        {1},
    )
    assert {item["status"] for item in items} == {
        "ACCEPTED_UNMATCHED",
        "UNMATCHED_COUNTERPARTY",
    }


def test_comparison_tolerance_boundaries_are_inclusive():
    at_boundary = compare(
        tx(1),
        tx(
            2,
            "COUNTERPARTY",
            seconds=120,
            quantity="1.00000001",
            price="100.01",
            gross="100.01",
        ),
    )
    assert all(item["passed"] for item in at_boundary)

    outside = compare(
        tx(1),
        tx(
            2,
            "COUNTERPARTY",
            seconds=121,
            quantity="1.00000002",
            price="100.02",
            gross="100.02",
        ),
    )
    assert {item["field"] for item in outside if not item["passed"]} == {
        "executed_at",
        "quantity",
        "price",
        "gross_amount",
    }


def test_equal_candidate_scores_remain_unmatched():
    items = reconcile(
        [tx(1, external="LEDGER")],
        [
            tx(2, "COUNTERPARTY", external="A"),
            tx(3, "COUNTERPARTY", external="B"),
        ],
        [],
        set(),
    )
    assert {item["status"] for item in items} == {
        "UNMATCHED_LEDGER",
        "UNMATCHED_COUNTERPARTY",
    }
    assert len(items) == 3


def test_manual_pair_does_not_override_cancellation():
    items = reconcile(
        [tx(1, state="CANCELLED")],
        [tx(2, "COUNTERPARTY", external="OTHER")],
        [(1, 2)],
        set(),
    )
    assert {item["status"] for item in items} == {
        "EXCLUDED_CANCELLED",
        "UNMATCHED_COUNTERPARTY",
    }
