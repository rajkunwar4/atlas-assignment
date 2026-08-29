import os
from pathlib import Path

os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://atlas:atlas@localhost:55432/atlas_test?sslmode=disable",
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from app.db import engine
from app.main import app
from app.models import AuditEvent, IngestionFile, SourceTransaction, TransactionVersion

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


def upload_story(client, source, name):
    data = (ROOT / "shared/fixtures/story" / name).read_bytes()
    return client.post(
        f"/api/files?source={source}", files={"file": (name, data, "text/csv")}
    )


def upload_text(client, source, content):
    return client.post(
        f"/api/files?source={source}",
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
        assert [version["data"]["gross_amount"] for version in history] == [
            "34170.00",
            "34000.00",
        ]


def test_every_registered_format_is_detected_automatically():
    canonical_header = (
        "transaction_id,executed_at,instrument,side,quantity,unit_price,"
        "gross_amount,state\n"
    )
    with TestClient(app) as client:
        assert upload(client, "LEDGER", "ledger.csv").status_code == 201
        assert upload(client, "COUNTERPARTY", "counterparty.csv").status_code == 201
        assert (
            upload_text(
                client,
                "LEDGER",
                canonical_header
                + "LC-1,2026-08-11T10:00:00Z,AAPL,BUY,1,10,10,SETTLED\n",
            ).status_code
            == 201
        )
        assert (
            upload_text(
                client,
                "COUNTERPARTY",
                canonical_header
                + "CC-1,2026-08-11T10:00:00Z,AAPL,BUY,1,10,10,SETTLED\n",
            ).status_code
            == 201
        )

    with engine.connect() as connection:
        detected = set(connection.execute(select(IngestionFile.adapter_id)).scalars())
    assert detected == {
        "ledger-v1",
        "counterparty-v1",
        "ledger-canonical-v1",
        "counterparty-canonical-v1",
    }


def test_incremental_upload_keeps_omissions_and_versions_only_corrections():
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
    corrected = second.replace(",1,10,10,SETTLED", ",1,11,11,SETTLED")
    with TestClient(app) as client:
        accepted = upload_text(client, "LEDGER", first)
        assert accepted.status_code == 201
        assert "adapter_id" not in accepted.json()
        assert "mode" not in accepted.json()
        incremental = upload_text(client, "LEDGER", second)
        assert incremental.status_code == 201
        assert incremental.json()["changed_count"] == 1
        correction = upload_text(client, "LEDGER", corrected)
        assert correction.status_code == 201
        assert correction.json()["changed_count"] == 1
        assert all(
            "adapter_id" not in item and "mode" not in item
            for item in client.get("/api/files").json()
        )

    with engine.connect() as connection:
        rows = connection.execute(
            select(SourceTransaction.external_id).order_by(SourceTransaction.external_id)
        ).all()
        version_counts = connection.execute(
            select(SourceTransaction.external_id, func.count(TransactionVersion.id))
            .join(TransactionVersion)
            .group_by(SourceTransaction.external_id)
            .order_by(SourceTransaction.external_id)
        ).all()
        adapter_id = connection.execute(select(IngestionFile.adapter_id).limit(1)).scalar_one()
        audit_adapter = connection.execute(
            select(AuditEvent.details_json["adapter_id"].astext)
            .where(AuditEvent.action == "FILE_INGESTED")
            .limit(1)
        ).scalar_one()
    assert rows == [("A",), ("B",), ("C",)]
    assert version_counts == [("A", 2), ("B", 1), ("C", 1)]
    assert adapter_id == "ledger-canonical-v1"
    assert audit_adapter == "ledger-canonical-v1"
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

        assert upload(client, "LEDGER", "ledger.csv").status_code == 201
        missing_source = client.post("/api/runs")
        assert missing_source.status_code == 409
        assert missing_source.json()["error"]["code"] == "MISSING_SOURCE"


def test_guided_story_sequence_proves_corrections_formats_and_decision_persistence():
    def results(client, run_id):
        return client.get(f"/api/runs/{run_id}/results").json()

    def item_for(items, external_id):
        return next(
            item
            for item in items
            if external_id
            in {
                (item.get("ledger") or {}).get("external_id"),
                (item.get("counterparty") or {}).get("external_id"),
            }
        )

    def assert_counts(summary, **expected):
        for status, count in expected.items():
            assert summary[status] == count, status

    with TestClient(app) as client:
        ledger = upload_story(client, "LEDGER", "01-ledger-baseline.csv")
        counterparty = upload_story(
            client, "COUNTERPARTY", "02-counterparty-baseline.csv"
        )
        assert ledger.json()["changed_count"] == 7
        assert counterparty.json()["changed_count"] == 7

        run1 = client.post("/api/runs").json()
        run1_results = results(client, run1["id"])
        assert_counts(
            run1_results["summary"],
            MATCHED=3,
            DIFFERENT=1,
            UNMATCHED_LEDGER=2,
            UNMATCHED_COUNTERPARTY=2,
            EXCLUDED_CANCELLED=2,
            UNRESOLVED=5,
        )
        items = run1_results["items"]
        difference = item_for(items, "ST-1003")
        assert (
            client.post(
                "/api/resolutions/accept-differences",
                json={"item_id": difference["id"], "note": "broker confirmed"},
            ).status_code
            == 201
        )
        ledger_manual = item_for(items, "L-MANUAL-1")["ledger"]
        counterparty_manual = item_for(items, "C-MANUAL-1")["counterparty"]
        assert (
            client.post(
                "/api/resolutions/match",
                json={
                    "ledger_transaction_id": ledger_manual["id"],
                    "counterparty_transaction_id": counterparty_manual["id"],
                    "note": "external ticket",
                },
            ).status_code
            == 201
        )
        for external_id, side in (
            ("L-ONLY-1", "ledger"),
            ("C-ONLY-1", "counterparty"),
        ):
            transaction = item_for(items, external_id)[side]
            assert (
                client.post(
                    "/api/resolutions/accept-unmatched",
                    json={"transaction_id": transaction["id"], "note": "confirmed"},
                ).status_code
                == 201
            )
        assert client.get("/api/runs").json()[0]["summary"]["UNRESOLVED"] == 0
        assert client.post(f"/api/runs/{run1['id']}/close").status_code == 200

        ledger_fix = upload_story(
            client, "LEDGER", "03-ledger-corrections-incremental.csv"
        )
        counterparty_fix = upload_story(
            client,
            "COUNTERPARTY",
            "04-counterparty-corrections-incremental.csv",
        )
        assert ledger_fix.json()["changed_count"] == 1
        assert counterparty_fix.json()["changed_count"] == 1
        run2 = client.post("/api/runs").json()
        assert_counts(
            run2["summary"],
            MATCHED=4,
            DIFFERENT=0,
            MANUALLY_MATCHED=1,
            ACCEPTED_UNMATCHED=2,
            EXCLUDED_CANCELLED=2,
            UNRESOLVED=0,
        )
        corrected = item_for(results(client, run2["id"])["items"], "ST-1003")
        history = client.get(
            f"/api/transactions/{corrected['ledger']['id']}/history"
        ).json()
        assert [version["data"]["price"] for version in history] == [
            "152.00",
            "150.00",
        ]
        assert client.post(f"/api/runs/{run2['id']}/close").status_code == 200

        canonical_ledger = upload_story(
            client, "LEDGER", "05-ledger-full-canonical.csv"
        )
        canonical_counterparty = upload_story(
            client, "COUNTERPARTY", "06-counterparty-full-canonical.csv"
        )
        assert canonical_ledger.json()["changed_count"] == 4
        assert canonical_counterparty.json()["changed_count"] == 5
        run3 = client.post("/api/runs").json()
        assert_counts(
            run3["summary"],
            MATCHED=6,
            DIFFERENT=1,
            MANUALLY_MATCHED=1,
            ACCEPTED_UNMATCHED=2,
            UNMATCHED_LEDGER=1,
            UNMATCHED_COUNTERPARTY=2,
            EXCLUDED_CANCELLED=2,
            UNRESOLVED=4,
        )
        items = results(client, run3["id"])["items"]
        difference = item_for(items, "ST-3002")
        assert (
            client.post(
                "/api/resolutions/accept-differences",
                json={"item_id": difference["id"], "note": "known adjustment"},
            ).status_code
            == 201
        )
        tie_ledger = item_for(items, "L-TIE-1")["ledger"]
        tie_a = item_for(items, "C-TIE-A")["counterparty"]
        tie_b = item_for(items, "C-TIE-B")["counterparty"]
        assert (
            client.post(
                "/api/resolutions/match",
                json={
                    "ledger_transaction_id": tie_ledger["id"],
                    "counterparty_transaction_id": tie_a["id"],
                    "note": "external ticket identifies A",
                },
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/resolutions/accept-unmatched",
                json={"transaction_id": tie_b["id"], "note": "separate record"},
            ).status_code
            == 201
        )
        assert client.get("/api/runs").json()[0]["summary"]["UNRESOLVED"] == 0
        assert client.post(f"/api/runs/{run3['id']}/close").status_code == 200

        final_ledger = upload_story(
            client, "LEDGER", "07-ledger-final-correction.csv"
        )
        final_counterparty = upload_story(
            client, "COUNTERPARTY", "08-counterparty-new-rows.csv"
        )
        assert final_ledger.json()["changed_count"] == 2
        assert final_counterparty.json()["changed_count"] == 1
        run4 = client.post("/api/runs").json()
        assert_counts(
            run4["summary"],
            MATCHED=8,
            DIFFERENT=0,
            MANUALLY_MATCHED=2,
            ACCEPTED_UNMATCHED=3,
            EXCLUDED_CANCELLED=2,
            UNRESOLVED=0,
        )
        assert client.post(f"/api/runs/{run4['id']}/close").status_code == 200

        file_count = len(client.get("/api/files").json())
        invalid = upload_story(client, "LEDGER", "90-ledger-invalid-atomic.csv")
        assert invalid.status_code == 422
        assert len(client.get("/api/files").json()) == file_count
        duplicate = upload_story(
            client, "COUNTERPARTY", "08-counterparty-new-rows.csv"
        )
        assert duplicate.json()["duplicate"] is True
        assert len(client.get("/api/files").json()) == file_count

    with engine.connect() as connection:
        assert connection.scalar(select(func.count(IngestionFile.id))) == 8
        assert connection.scalar(select(func.count(TransactionVersion.id))) == 28
        assert set(connection.execute(select(IngestionFile.adapter_id)).scalars()) == {
            "ledger-v1",
            "counterparty-v1",
            "ledger-canonical-v1",
            "counterparty-canonical-v1",
        }
