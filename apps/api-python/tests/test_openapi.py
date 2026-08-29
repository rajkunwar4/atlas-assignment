from pathlib import Path

import yaml


def test_shared_contract_covers_public_routes():
    contract_path = Path(__file__).resolve().parents[3] / "shared/openapi/openapi.yaml"
    contract = yaml.safe_load(contract_path.read_text())
    operations = {
        operation["operationId"]
        for path in contract["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    assert operations == {
        "getHealth",
        "listFiles",
        "uploadFile",
        "listRuns",
        "createRun",
        "listResults",
        "exportRun",
        "closeRun",
        "createManualMatch",
        "acceptUnmatched",
        "acceptDifferences",
        "supersedeResolution",
        "getSettings",
        "updateSettings",
        "getTransactionHistory",
        "listAudit",
    }
