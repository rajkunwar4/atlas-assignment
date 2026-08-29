import csv
import hashlib
import io
import json
from dataclasses import dataclass
from typing import Callable

from .domain import decimal_value, parse_time


@dataclass(frozen=True)
class Adapter:
    id: str
    source: str
    description: str
    headers: tuple[str, ...]
    normalize: Callable[[dict[str, str]], dict]


@dataclass
class FileValidationError(Exception):
    errors: list[dict]


def _mapped_normalizer(mapping: dict[str, str], *, assume_utc: bool = False):
    """Build a declarative adapter for formats that only rename columns."""

    def normalize(raw: dict[str, str]) -> dict:
        def value(field: str) -> str:
            return raw[mapping[field]].strip()

        return {
            "external_id": value("external_id"),
            "executed_at": parse_time(value("executed_at"), assume_utc).isoformat(),
            "instrument": value("instrument").upper(),
            "side": value("side").upper(),
            "quantity": str(decimal_value(value("quantity"), mapping["quantity"])),
            "price": str(decimal_value(value("price"), mapping["price"])),
            "gross_amount": str(
                decimal_value(value("gross_amount"), mapping["gross_amount"])
            ),
            "state": value("state").upper(),
        }

    return normalize


def _counterparty_v1(raw: dict[str, str]) -> dict:
    """The counterparty format needs a code adapter for B/S side aliases."""
    side = {"B": "BUY", "S": "SELL", "BUY": "BUY", "SELL": "SELL"}.get(
        raw["direction"].strip().upper(), ""
    )
    return {
        "external_id": raw["reference"].strip(),
        "executed_at": parse_time(raw["executed_at"], True).isoformat(),
        "instrument": raw["symbol"].strip().upper(),
        "side": side,
        "quantity": str(decimal_value(raw["qty"], "qty")),
        "price": str(decimal_value(raw["unit_price"], "unit_price")),
        "gross_amount": str(decimal_value(raw["total"], "total")),
        "state": raw["status"].strip().upper(),
    }


CANONICAL_HEADERS = (
    "transaction_id",
    "executed_at",
    "instrument",
    "side",
    "quantity",
    "unit_price",
    "gross_amount",
    "state",
)
CANONICAL_MAPPING = {
    "external_id": "transaction_id",
    "executed_at": "executed_at",
    "instrument": "instrument",
    "side": "side",
    "quantity": "quantity",
    "price": "unit_price",
    "gross_amount": "gross_amount",
    "state": "state",
}

ADAPTERS = (
    Adapter(
        "ledger-v1",
        "LEDGER",
        "Original ledger export",
        (
            "trade_id",
            "traded_at",
            "instrument",
            "side",
            "quantity",
            "price",
            "gross_amount",
            "state",
        ),
        _mapped_normalizer(
            {
                "external_id": "trade_id",
                "executed_at": "traded_at",
                "instrument": "instrument",
                "side": "side",
                "quantity": "quantity",
                "price": "price",
                "gross_amount": "gross_amount",
                "state": "state",
            }
        ),
    ),
    Adapter(
        "counterparty-v1",
        "COUNTERPARTY",
        "Original counterparty statement",
        (
            "reference",
            "executed_at",
            "symbol",
            "direction",
            "qty",
            "unit_price",
            "total",
            "status",
        ),
        _counterparty_v1,
    ),
    Adapter(
        "ledger-canonical-v1",
        "LEDGER",
        "Canonical column export",
        CANONICAL_HEADERS,
        _mapped_normalizer(CANONICAL_MAPPING),
    ),
    Adapter(
        "counterparty-canonical-v1",
        "COUNTERPARTY",
        "Canonical column export",
        CANONICAL_HEADERS,
        _mapped_normalizer(CANONICAL_MAPPING),
    ),
)


def adapters_for(source: str) -> list[Adapter]:
    return [adapter for adapter in ADAPTERS if adapter.source == source]


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FileValidationError(
            [{"row": 0, "column": "file", "value": "", "reason": "must be UTF-8"}]
        )


def resolve_adapter(content: bytes, source: str) -> Adapter:
    candidates = adapters_for(source)
    header = tuple(next(csv.reader(io.StringIO(_decode(content))), []))
    matches = [
        item for item in candidates if header[: len(item.headers)] == item.headers
    ]
    if len(matches) != 1:
        expected = " or ".join(",".join(item.headers) for item in candidates)
        reason = (
            "format is ambiguous"
            if matches
            else f"unsupported file format; expected {expected}"
        )
        raise FileValidationError(
            [
                {
                    "row": 1,
                    "column": "header",
                    "value": ",".join(header),
                    "reason": reason,
                }
            ]
        )
    return matches[0]


def parse_csv(content: bytes, source: str):
    """Validate a complete file and return canonical rows without side effects."""
    text = _decode(content)
    adapter = resolve_adapter(content, source)
    reader = csv.DictReader(io.StringIO(text))
    actual_headers = tuple(reader.fieldnames or ())
    if actual_headers[: len(adapter.headers)] != adapter.headers:
        raise FileValidationError(
            [
                {
                    "row": 1,
                    "column": "header",
                    "value": ",".join(reader.fieldnames or []),
                    "reason": f"expected {','.join(adapter.headers)}",
                }
            ]
        )

    rows, errors, seen = [], [], {}
    for number, parsed in enumerate(reader, start=2):
        # Preserve every source column while making ragged rows ordinary errors.
        raw = {column: (parsed.get(column) or "") for column in actual_headers}
        try:
            data = {"source": source, **adapter.normalize(raw)}
            if not data["external_id"]:
                raise ValueError("transaction ID is required")
            if not data["instrument"]:
                raise ValueError("instrument is required")
            if data["side"] not in ("BUY", "SELL"):
                raise ValueError("side must be BUY/SELL or B/S")
            if data["state"] not in ("SETTLED", "CANCELLED"):
                raise ValueError("state must be SETTLED or CANCELLED")
            canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
            external_id = data["external_id"]
            if external_id in seen and seen[external_id] != canonical:
                raise ValueError("conflicting duplicate transaction ID")
            if external_id not in seen:
                fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
                rows.append((data, raw, fingerprint))
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
