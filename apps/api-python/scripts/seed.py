from pathlib import Path
import sys
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps/api-python"))
from app.main import app  # noqa: E402

# Pass "wide" to load the extended fixture week instead of the brief's example data.
DATASETS = {
    "demo": [("LEDGER", "ledger.csv"), ("COUNTERPARTY", "counterparty.csv")],
    "wide": [
        ("LEDGER", "ledger-wide.csv"),
        ("COUNTERPARTY", "counterparty-wide.csv"),
    ],
}
dataset = sys.argv[1] if len(sys.argv) > 1 else "demo"
if dataset not in DATASETS:
    raise SystemExit(f"unknown dataset {dataset}; choose demo or wide")

with TestClient(app) as client:
    for source, name in DATASETS[dataset]:
        path = ROOT / "shared/fixtures" / name
        response = client.post(
            f"/api/files?source={source}",
            files={"file": (name, path.read_bytes(), "text/csv")},
        )
        response.raise_for_status()
        print(response.json())
