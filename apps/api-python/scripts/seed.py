from pathlib import Path
import sys
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps/api-python"))
from app.main import app  # noqa: E402

with TestClient(app) as client:
    for source, name in [
        ("LEDGER", "ledger.csv"),
        ("COUNTERPARTY", "counterparty.csv"),
    ]:
        path = ROOT / "shared/fixtures" / name
        response = client.post(
            f"/api/files?source={source}",
            files={"file": (name, path.read_bytes(), "text/csv")},
        )
        response.raise_for_status()
        print(response.json())
