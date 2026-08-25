import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain import Transaction, reconcile
from app.ingestion import FileValidationError, parse_csv

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "shared/fixtures"
EXPECTED = json.loads((ROOT / "shared/expected-results/wide.json").read_text())
INVALID = json.loads((ROOT / "shared/expected-results/invalid-files.json").read_text())


def transactions(name, source, start):
    rows = parse_csv((FIXTURES / name).read_bytes(), source)
    return [
        Transaction(
            stable_id=index,
            version_id=index,
            source=data["source"],
            external_id=data["external_id"],
            executed_at=datetime.fromisoformat(data["executed_at"]),
            instrument=data["instrument"],
            side=data["side"],
            quantity=Decimal(data["quantity"]),
            price=Decimal(data["price"]),
            gross_amount=Decimal(data["gross_amount"]),
            state=data["state"],
            raw=raw,
        )
        for index, (data, raw, _) in enumerate(rows, start=start)
    ]


def outcome(scenario):
    items = reconcile(
        transactions(scenario["ledger_file"], "LEDGER", 1000),
        transactions(scenario["counterparty_file"], "COUNTERPARTY", 2000),
        [],
        set(),
    )
    return {
        "{}|{}".format(
            item["ledger"]["external_id"] if item["ledger"] else "",
            item["counterparty"]["external_id"] if item["counterparty"] else "",
        ): {
            "status": item["status"],
            "method": item["match_method"],
            "score": item["score"],
            "failing_fields": [
                d["field"] for d in item["differences"] if not d["passed"]
            ],
        }
        for item in items
    }


@pytest.mark.parametrize("scenario_name", ["base", "corrected"])
def test_wide_fixture_scenarios_match_expected_results(scenario_name):
    scenario = EXPECTED[scenario_name]
    actual = outcome(scenario)
    assert set(actual) == set(scenario["items"])
    for key, expected in scenario["items"].items():
        observed = actual[key]
        assert observed["status"] == expected["status"], f"{key}: {expected['case']}"
        assert observed["method"] == expected["method"], f"{key}: {expected['case']}"
        assert observed["score"] == expected["score"], f"{key}: {expected['case']}"
        assert observed["failing_fields"] == expected["failing_fields"], (
            f"{key}: {expected['case']}"
        )
    summary = {key: 0 for key in scenario["summary"]}
    for observed in actual.values():
        summary[observed["status"]] = summary.get(observed["status"], 0) + 1
    assert summary == scenario["summary"]


@pytest.mark.parametrize(
    "variant,base,source",
    [
        ("ledger-wide-reformatted.csv", "ledger-wide.csv", "LEDGER"),
        (
            "counterparty-wide-reformatted.csv",
            "counterparty-wide.csv",
            "COUNTERPARTY",
        ),
    ],
)
def test_cosmetic_reformatting_produces_no_row_changes(variant, base, source):
    """A byte-order mark, CRLF endings, quoting and blank lines are not corrections."""
    fingerprints = {
        data["external_id"]: fingerprint
        for data, _, fingerprint in parse_csv((FIXTURES / base).read_bytes(), source)
    }
    rows = parse_csv((FIXTURES / variant).read_bytes(), source)
    assert rows
    for data, _, fingerprint in rows:
        assert fingerprints[data["external_id"]] == fingerprint


def test_duplicate_row_inside_one_file_is_collapsed():
    rows = parse_csv((FIXTURES / "ledger-wide.csv").read_bytes(), "LEDGER")
    identifiers = [data["external_id"] for data, _, _ in rows]
    assert identifiers.count("T-2015") == 1
    assert len(identifiers) == len(set(identifiers))


@pytest.mark.parametrize("case", INVALID["files"], ids=lambda case: case["path"])
def test_invalid_fixtures_are_rejected_atomically(case):
    with pytest.raises(FileValidationError) as raised:
        parse_csv((FIXTURES / case["path"]).read_bytes(), case["source"])
    reported = {error["row"]: error["reason"].lower() for error in raised.value.errors}
    for expected in case["errors"]:
        assert expected["row"] in reported, case["case"]
        for fragment in expected["contains"]:
            assert fragment.lower() in reported[expected["row"]], case["case"]
