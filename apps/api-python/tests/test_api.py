import os
from pathlib import Path

os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://atlas:atlas@localhost:55432/atlas_test?sslmode=disable",
)
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from app.db import engine
from app.main import app
from app.models import SourceTransaction, TransactionVersion

ROOT = Path(__file__).resolve().parents[3]


def setup_function():
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE audit_events, tolerance_settings, manual_resolutions, "
                "field_differences, reconciliation_items, reconciliation_runs, "
                "transaction_versions, source_transactions, ingestion_files "
                "RESTART IDENTITY CASCADE"
            )
        )


def teardown_function():
    pass


def upload(client, source, name):
    data = (ROOT / "shared/fixtures" / name).read_bytes()
    return client.post(
        f"/api/files?source={source}", files={"file": (name, data, "text/csv")}
    )


def upload_text(client, source, content, *, mode="INCREMENTAL", adapter_id=None):
    query = f"source={source}&mode={mode}"
    if adapter_id:
        query += f"&adapter_id={adapter_id}"
    return client.post(
        f"/api/files?{query}",
        files={"file": ("rows.csv", content.encode(), "text/csv")},
    )


def test_upload_duplicate_run_and_correction_history():
    with TestClient(app) as client:
        first = upload(client, "LEDGER", "ledger.csv")
        assert first.status_code == 201
        duplicate = upload(client, "LEDGER", "ledger.csv")
        assert duplicate.json()["duplicate"] is True
        correction = upload(client, "LEDGER", "ledger-correction.csv")
        assert correction.json()["changed_count"] == 1
        assert upload(client, "COUNTERPARTY", "counterparty.csv").status_code == 201
        run = client.post("/api/runs")
        assert run.status_code == 201
        results = client.get(f"/api/runs/{run.json()['id']}/results").json()
        assert results["summary"]["DIFFERENT"] == 1
        t1011 = next(
            x
            for x in results["items"]
            if (x.get("ledger") or {}).get("external_id") == "T-1011"
        )
        history = client.get(
            f"/api/transactions/{t1011['ledger']['id']}/history"
        ).json()
        assert len(history) == 2


def test_adapter_detection_and_snapshot_retire_omitted_rows():
    header = (
        "transaction_id,executed_at,instrument,side,quantity,unit_price,"
        "gross_amount,state,desk_note\n"
    )
    first = header + (
        "A,2026-08-11T10:00:00Z,AAPL,BUY,1,10,10,SETTLED,alpha\n"
        "B,2026-08-11T10:01:00Z,MSFT,SELL,2,20,40,SETTLED,beta\n"
    )
    second = header + (
        "A,2026-08-11T10:00:00Z,AAPL,BUY,1,10,10,SETTLED,alpha\n"
        "C,2026-08-11T10:02:00Z,NVDA,BUY,3,30,90,SETTLED,gamma\n"
    )
    with TestClient(app) as client:
        adapters = client.get("/api/adapters?source=LEDGER").json()
        assert {item["id"] for item in adapters} >= {
            "ledger-v1",
            "ledger-canonical-v1",
        }
        accepted = upload_text(client, "LEDGER", first)
        assert accepted.status_code == 201
        assert accepted.json()["adapter_id"] == "ledger-canonical-v1"
        snapshot = upload_text(client, "LEDGER", second, mode="SNAPSHOT")
        assert snapshot.status_code == 201

    with engine.connect() as connection:
        rows = connection.execute(
            select(
                SourceTransaction.external_id,
                SourceTransaction.active,
                SourceTransaction.inactive_reason,
            ).order_by(SourceTransaction.external_id)
        ).all()
    assert rows == [
        ("A", True, None),
        ("B", False, "ABSENT_FROM_SNAPSHOT"),
        ("C", True, None),
    ]
    with engine.connect() as connection:
        raw = connection.execute(
            select(TransactionVersion.raw_json)
            .join(
                SourceTransaction,
                SourceTransaction.current_version_id == TransactionVersion.id,
            )
            .where(SourceTransaction.external_id == "A")
        ).scalar_one()
    assert raw["desk_note"] == "alpha"


def test_differences_require_review_before_run_can_close():
    ledger = (
        "trade_id,traded_at,instrument,side,quantity,price,gross_amount,state\n"
        "R-1,2026-08-11T10:00:00Z,AAPL,BUY,1,10,10,SETTLED\n"
    )
    counterparty = (
        "reference,executed_at,symbol,direction,qty,unit_price,total,status\n"
        "R-1,2026-08-11T10:00:00Z,AAPL,B,1,11,11,SETTLED\n"
    )
    with TestClient(app) as client:
        assert upload_text(client, "LEDGER", ledger).status_code == 201
        assert upload_text(client, "COUNTERPARTY", counterparty).status_code == 201
        run = client.post("/api/runs").json()
        assert run["status"] == "OPEN"
        assert run["summary"]["UNRESOLVED"] == 1
        changed = ledger.replace(",10,10,SETTLED", ",12,12,SETTLED")
        assert upload_text(client, "LEDGER", changed).status_code == 409
        assert client.post(f"/api/runs/{run['id']}/close").status_code == 409

        item = client.get(f"/api/runs/{run['id']}/results").json()["items"][0]
        assert item["review_status"] == "PENDING"
        accepted = client.post(
            "/api/resolutions/accept-differences",
            json={"item_id": item["id"], "note": "confirmed with broker"},
        )
        assert accepted.status_code == 201
        refreshed = client.get("/api/runs").json()[0]
        assert refreshed["status"] == "READY_TO_CLOSE"
        assert refreshed["summary"]["UNRESOLVED"] == 0

        superseded = client.post(
            f"/api/resolutions/{accepted.json()['id']}/supersede",
            json={"note": "review needs to be repeated"},
        )
        assert superseded.status_code == 201
        assert client.get("/api/runs").json()[0]["status"] == "OPEN"
        new_item = client.get(f"/api/runs/{run['id']}/results").json()["items"][0]
        assert (
            client.post(
                "/api/resolutions/accept-differences",
                json={"item_id": new_item["id"], "note": "reviewed again"},
            ).status_code
            == 201
        )

        closed = client.post(f"/api/runs/{run['id']}/close")
        assert closed.status_code == 200
        assert closed.json()["status"] == "CLOSED"
        assert client.post(f"/api/runs/{run['id']}/close").status_code == 200


def test_only_one_run_may_be_open_at_a_time():
    with TestClient(app) as client:
        assert upload(client, "LEDGER", "ledger.csv").status_code == 201
        assert upload(client, "COUNTERPARTY", "counterparty.csv").status_code == 201
        run = client.post("/api/runs").json()
        assert run["status"] == "OPEN"

        duplicate = upload(client, "LEDGER", "ledger.csv")
        assert duplicate.status_code == 201
        assert duplicate.json()["duplicate"] is True
        changed = upload(client, "LEDGER", "ledger-correction.csv")
        assert changed.status_code == 409
        assert changed.json()["error"]["code"] == "OPEN_RUN_EXISTS"

        blocked = client.post("/api/runs")
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "OPEN_RUN_EXISTS"
        assert client.get("/api/runs").json() == [run]

        # Resolving repopulates the run's items, so re-fetch after every decision
        # instead of iterating a stale snapshot of item ids.
        for _ in range(50):
            items = client.get(f"/api/runs/{run['id']}/results").json()["items"]
            pending = next((x for x in items if x["review_status"] == "PENDING"), None)
            if pending is None:
                break
            if pending["status"] == "DIFFERENT":
                resolved = client.post(
                    "/api/resolutions/accept-differences",
                    json={"item_id": pending["id"], "note": "reviewed"},
                )
            else:
                side = (
                    "ledger"
                    if pending["status"] == "UNMATCHED_LEDGER"
                    else "counterparty"
                )
                resolved = client.post(
                    "/api/resolutions/accept-unmatched",
                    json={"transaction_id": pending[side]["id"], "note": "reviewed"},
                )
            assert resolved.status_code == 201
        assert client.get("/api/runs").json()[0]["summary"]["UNRESOLVED"] == 0
        assert client.post(f"/api/runs/{run['id']}/close").status_code == 200

        second = client.post("/api/runs")
        assert second.status_code == 201


def test_manual_match_persists_after_correction_and_into_next_run():
    ledger = (
        "trade_id,traded_at,instrument,side,quantity,price,gross_amount,state\n"
        "L-1,2026-08-11T10:00:00Z,BTC-USD,BUY,1,100,100,SETTLED\n"
    )
    corrected = ledger.replace(",100,100,SETTLED", ",105,105,SETTLED")
    counterparty = (
        "reference,executed_at,symbol,direction,qty,unit_price,total,status\n"
        "C-1,2026-08-11 11:00:00,BTC-USD,B,1,100,100,SETTLED\n"
    )
    with TestClient(app) as client:
        assert upload_text(client, "LEDGER", ledger).status_code == 201
        assert upload_text(client, "COUNTERPARTY", counterparty).status_code == 201
        first_run = client.post("/api/runs").json()
        first_items = client.get(f"/api/runs/{first_run['id']}/results").json()["items"]
        left = next(item["ledger"] for item in first_items if item.get("ledger"))
        right = next(
            item["counterparty"] for item in first_items if item.get("counterparty")
        )

        matched = client.post(
            "/api/resolutions/match",
            json={
                "ledger_transaction_id": left["id"],
                "counterparty_transaction_id": right["id"],
                "note": "confirmed manually",
            },
        )
        assert matched.status_code == 201
        conflict = client.post(
            "/api/resolutions/accept-unmatched",
            json={"transaction_id": left["id"], "note": "duplicate decision"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "RESOLUTION_CONFLICT"

        refreshed = client.get(f"/api/runs/{first_run['id']}/results").json()
        assert refreshed["summary"]["UNRESOLVED"] == 0
        assert refreshed["items"][0]["status"] == "MANUALLY_MATCHED"
        assert client.post(f"/api/runs/{first_run['id']}/close").status_code == 200

        correction = upload_text(client, "LEDGER", corrected)
        assert correction.status_code == 201
        assert correction.json()["changed_count"] == 1
        second_run = client.post("/api/runs")
        assert second_run.status_code == 201
        second_items = client.get(
            f"/api/runs/{second_run.json()['id']}/results"
        ).json()["items"]
        assert len(second_items) == 1
        assert second_items[0]["status"] == "MANUALLY_MATCHED"
        assert second_items[0]["match_method"] == "MANUAL"
        assert second_items[0]["ledger"]["gross_amount"] == "105"
        assert second_items[0]["ledger"]["version_id"] != left["version_id"]
        history = client.get(f"/api/transactions/{left['id']}/history").json()
        assert [version["version"] for version in history] == [2, 1]


def test_accepted_unmatched_decisions_persist_into_next_run():
    ledger = (
        "trade_id,traded_at,instrument,side,quantity,price,gross_amount,state\n"
        "L-ONLY,2026-08-11T10:00:00Z,BTC-USD,BUY,1,100,100,SETTLED\n"
    )
    counterparty = (
        "reference,executed_at,symbol,direction,qty,unit_price,total,status\n"
        "C-ONLY,2026-08-11 10:00:00,ETH-USD,S,2,50,100,SETTLED\n"
    )
    with TestClient(app) as client:
        assert upload_text(client, "LEDGER", ledger).status_code == 201
        assert upload_text(client, "COUNTERPARTY", counterparty).status_code == 201
        first_run = client.post("/api/runs").json()
        first_items = client.get(f"/api/runs/{first_run['id']}/results").json()["items"]
        transaction_ids = [
            (item.get("ledger") or item.get("counterparty"))["id"]
            for item in first_items
        ]

        for transaction_id in transaction_ids:
            accepted = client.post(
                "/api/resolutions/accept-unmatched",
                json={"transaction_id": transaction_id, "note": "confirmed absent"},
            )
            assert accepted.status_code == 201

        assert client.post(f"/api/runs/{first_run['id']}/close").status_code == 200
        second_run = client.post("/api/runs").json()
        second = client.get(f"/api/runs/{second_run['id']}/results").json()
        assert second["summary"]["UNRESOLVED"] == 0
        assert {item["status"] for item in second["items"]} == {"ACCEPTED_UNMATCHED"}
        assert {item["review_status"] for item in second["items"]} == {"ACCEPTED"}


def test_invalid_upload_is_atomic_and_invalid_states_are_rejected():
    invalid = (ROOT / "shared/fixtures/invalid/ledger-multiple-errors.csv").read_bytes()
    with TestClient(app) as client:
        rejected = client.post(
            "/api/files?source=LEDGER",
            files={"file": ("invalid.csv", invalid, "text/csv")},
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "INVALID_FILE"
        assert client.get("/api/files").json() == []

        bad_source = client.post(
            "/api/files?source=UNKNOWN",
            files={"file": ("ledger.csv", b"header\n", "text/csv")},
        )
        assert bad_source.status_code == 422
        assert bad_source.json()["error"]["code"] == "INVALID_SOURCE"

        bad_mode = client.post(
            "/api/files?source=LEDGER&mode=REPLACE",
            files={"file": ("ledger.csv", b"header\n", "text/csv")},
        )
        assert bad_mode.status_code == 422
        assert bad_mode.json()["error"]["code"] == "INVALID_UPLOAD_MODE"

        assert upload(client, "LEDGER", "ledger.csv").status_code == 201
        missing_source = client.post("/api/runs")
        assert missing_source.status_code == 409
        assert missing_source.json()["error"]["code"] == "MISSING_SOURCE"
