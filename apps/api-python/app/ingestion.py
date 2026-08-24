import csv
import hashlib
import io
import json
from dataclasses import dataclass
from .domain import decimal_value, parse_time

HEADERS = {
    "LEDGER": [
        "trade_id",
        "traded_at",
        "instrument",
        "side",
        "quantity",
        "price",
        "gross_amount",
        "state",
    ],
    "COUNTERPARTY": [
        "reference",
        "executed_at",
        "symbol",
        "direction",
        "qty",
        "unit_price",
        "total",
        "status",
    ],
}


@dataclass
class FileValidationError(Exception):
    errors: list[dict]


def parse_csv(content: bytes, source: str):
    """Validate a complete source file and return canonical rows without side effects."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FileValidationError(
            [{"row": 0, "column": "file", "value": "", "reason": "must be UTF-8"}]
        )
    reader = csv.DictReader(io.StringIO(text))
    expected = HEADERS[source]
    if reader.fieldnames != expected:
        raise FileValidationError(
            [
                {
                    "row": 1,
                    "column": "header",
                    "value": ",".join(reader.fieldnames or []),
                    "reason": f"expected {','.join(expected)}",
                }
            ]
        )
    rows, errors, seen = [], [], {}
    for number, raw in enumerate(reader, start=2):
        try:
            if source == "LEDGER":
                external_id = raw["trade_id"].strip()
                executed = parse_time(raw["traded_at"])
                instrument = raw["instrument"].strip().upper()
                side = raw["side"].strip().upper()
                quantity = decimal_value(raw["quantity"], "quantity")
                price = decimal_value(raw["price"], "price")
                gross = decimal_value(raw["gross_amount"], "gross_amount")
                state = raw["state"].strip().upper()
            else:
                external_id = raw["reference"].strip()
                executed = parse_time(raw["executed_at"], True)
                instrument = raw["symbol"].strip().upper()
                side = {"B": "BUY", "S": "SELL", "BUY": "BUY", "SELL": "SELL"}.get(
                    raw["direction"].strip().upper(), ""
                )
                quantity = decimal_value(raw["qty"], "qty")
                price = decimal_value(raw["unit_price"], "unit_price")
                gross = decimal_value(raw["total"], "total")
                state = raw["status"].strip().upper()
            if not external_id:
                raise ValueError("transaction ID is required")
            if not instrument:
                raise ValueError("instrument is required")
            if side not in ("BUY", "SELL"):
                raise ValueError("side must be BUY/SELL or B/S")
            if state not in ("SETTLED", "CANCELLED"):
                raise ValueError("state must be SETTLED or CANCELLED")
            data = {
                "source": source,
                "external_id": external_id,
                "executed_at": executed.isoformat(),
                "instrument": instrument,
                "side": side,
                "quantity": str(quantity),
                "price": str(price),
                "gross_amount": str(gross),
                "state": state,
            }
            # The canonical fingerprint ignores source formatting while preserving
            # every value that can affect reconciliation.
            canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
            if external_id in seen and seen[external_id] != canonical:
                raise ValueError("conflicting duplicate transaction ID")
            if external_id not in seen:
                rows.append((data, raw, hashlib.sha256(canonical.encode()).hexdigest()))
            seen[external_id] = canonical
        except (ValueError, KeyError) as exc:
            errors.append(
                {
                    "row": number,
                    "column": "row",
                    "value": json.dumps(raw),
                    "reason": str(exc),
                }
            )
    if errors:
        raise FileValidationError(errors)
    if not rows:
        raise FileValidationError(
            [
                {
                    "row": 0,
                    "column": "file",
                    "value": "",
                    "reason": "file contains no data rows",
                }
            ]
        )
    return rows
