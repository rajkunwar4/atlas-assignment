import os
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./python-test.db"
from fastapi.testclient import TestClient
from app.db import Base, engine
from app.main import app

ROOT = Path(__file__).resolve().parents[3]


def setup_function():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def teardown_function():
    Base.metadata.drop_all(engine)
    Path("python-test.db").unlink(missing_ok=True)


def upload(client, source, name):
    data = (ROOT / "shared/fixtures" / name).read_bytes()
    return client.post(
        f"/api/files?source={source}", files={"file": (name, data, "text/csv")}
    )


def test_upload_duplicate_run_and_correction_history():
    with TestClient(app) as client:
        first = upload(client, "LEDGER", "ledger.csv")
        assert first.status_code == 201
        duplicate = upload(client, "LEDGER", "ledger.csv")
        assert duplicate.json()["duplicate"] is True
        assert upload(client, "COUNTERPARTY", "counterparty.csv").status_code == 201
        run = client.post("/api/runs")
        assert run.status_code == 201
        results = client.get(f"/api/runs/{run.json()['id']}/results").json()
        assert results["summary"]["DIFFERENT"] == 2
        correction = upload(client, "LEDGER", "ledger-correction.csv")
        assert correction.json()["changed_count"] == 1
        t1011 = next(
            x
            for x in results["items"]
            if (x.get("ledger") or {}).get("external_id") == "T-1011"
        )
        history = client.get(
            f"/api/transactions/{t1011['ledger']['id']}/history"
        ).json()
        assert len(history) == 2
