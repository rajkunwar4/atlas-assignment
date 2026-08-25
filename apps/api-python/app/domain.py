from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

DEFAULT_SETTINGS = {
    "time_tolerance_seconds": 120,
    "quantity_abs_tolerance": "0.00000001",
    "money_abs_tolerance": "0.01",
    "money_rel_tolerance": "0.0001",
    "candidate_time_seconds": 900,
    "candidate_quantity_rel": "0.001",
    "candidate_gross_rel": "0.01",
    "candidate_min_score": "0.75",
}


@dataclass(frozen=True)
class Transaction:
    stable_id: int
    source: str
    external_id: str
    executed_at: datetime
    instrument: str
    side: str
    quantity: Decimal
    price: Decimal
    gross_amount: Decimal
    state: str
    version_id: int | None = None
    raw: dict[str, Any] | None = None

    def public(self):
        def decimal_text(value: Decimal):
            text = format(value.normalize(), "f")
            return "0" if text in ("-0", "") else text

        timestamp = (
            self.executed_at.astimezone(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        return {
            "id": self.stable_id,
            "version_id": self.version_id,
            "source": self.source,
            "external_id": self.external_id,
            "executed_at": timestamp,
            "instrument": self.instrument,
            "side": self.side,
            "quantity": decimal_text(self.quantity),
            "price": decimal_text(self.price),
            "gross_amount": decimal_text(self.gross_amount),
            "state": self.state,
            "raw": self.raw or {},
        }


def parse_time(value: str, assume_utc: bool = False) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("must be an ISO-8601 date-time") from exc
    if parsed.tzinfo is None:
        if not assume_utc:
            raise ValueError("must include a timezone")
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def decimal_value(value: str, field: str) -> Decimal:
    try:
        result = Decimal(value.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"{field} must be a decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field} must be a non-negative finite decimal")
    return result


def relative_delta(left: Decimal, right: Decimal) -> Decimal:
    """Return a symmetric relative delta so source ordering cannot change a result."""
    denominator = max(abs(left), abs(right))
    return Decimal("0") if denominator == 0 else abs(left - right) / denominator


def compare(left: Transaction, right: Transaction, settings=DEFAULT_SETTINGS):
    """Describe every field comparison, including passing fields, for UI explainability."""
    differences = []

    def text(value):
        if isinstance(value, Decimal):
            normalized = format(value.normalize(), "f")
            return "0" if normalized in ("-0", "") else normalized
        return str(value)

    def add(field, lv, rv, passed, absolute=None, relative=None, tolerance=None):
        differences.append(
            {
                "field": field,
                "left": text(lv),
                "right": text(rv),
                "absolute_delta": None if absolute is None else text(absolute),
                "relative_delta": None if relative is None else text(relative),
                "tolerance": tolerance,
                "passed": passed,
            }
        )

    seconds = Decimal(str(abs((left.executed_at - right.executed_at).total_seconds())))
    add(
        "executed_at",
        left.public()["executed_at"],
        right.public()["executed_at"],
        seconds <= Decimal(str(settings["time_tolerance_seconds"])),
        seconds,
        tolerance=f"{settings['time_tolerance_seconds']} seconds",
    )
    for field in ("instrument", "side", "state"):
        lv, rv = getattr(left, field), getattr(right, field)
        add(field, lv, rv, lv == rv, tolerance="exact")
    qd = abs(left.quantity - right.quantity)
    add(
        "quantity",
        left.quantity,
        right.quantity,
        qd <= Decimal(settings["quantity_abs_tolerance"]),
        qd,
        relative_delta(left.quantity, right.quantity),
        settings["quantity_abs_tolerance"],
    )
    for field in ("price", "gross_amount"):
        lv, rv = getattr(left, field), getattr(right, field)
        absolute, relative = abs(lv - rv), relative_delta(lv, rv)
        passed = absolute <= Decimal(
            settings["money_abs_tolerance"]
        ) or relative <= Decimal(settings["money_rel_tolerance"])
        add(
            field,
            lv,
            rv,
            passed,
            absolute,
            relative,
            f"max({settings['money_abs_tolerance']} absolute, {settings['money_rel_tolerance']} relative)",
        )
    return differences


def candidate_score(left: Transaction, right: Transaction, settings=DEFAULT_SETTINGS):
    """Score only candidates that pass conservative identity-independent safety gates."""
    if left.instrument != right.instrument or left.side != right.side:
        return None
    time_delta = Decimal(
        str(abs((left.executed_at - right.executed_at).total_seconds()))
    )
    qty_delta = relative_delta(left.quantity, right.quantity)
    gross_delta = relative_delta(left.gross_amount, right.gross_amount)
    gates = (
        Decimal(str(settings["candidate_time_seconds"])),
        Decimal(settings["candidate_quantity_rel"]),
        Decimal(settings["candidate_gross_rel"]),
    )
    if time_delta > gates[0] or qty_delta > gates[1] or gross_delta > gates[2]:
        return None
    closeness = [
        Decimal("1") - time_delta / gates[0],
        Decimal("1") - qty_delta / gates[1],
        Decimal("1") - gross_delta / gates[2],
    ]
    return (
        closeness[0] * Decimal("0.50")
        + closeness[1] * Decimal("0.25")
        + closeness[2] * Decimal("0.25")
    )


def reconcile(
    ledger: list[Transaction],
    counterparty: list[Transaction],
    manual_pairs: list[tuple[int, int]],
    accepted: set[int],
    settings=DEFAULT_SETTINGS,
):
    """Produce deterministic one-to-one results without persistence or HTTP dependencies."""
    items, used_l, used_c = [], set(), set()
    by_l, by_c = (
        {t.stable_id: t for t in ledger},
        {t.stable_id: t for t in counterparty},
    )

    def add_pair(left, right, method, score=None):
        diffs = compare(left, right, settings)
        status = (
            "MANUALLY_MATCHED"
            if method == "MANUAL"
            else ("MATCHED" if all(d["passed"] for d in diffs) else "DIFFERENT")
        )
        items.append(
            {
                "status": status,
                "match_method": method,
                "score": None
                if score is None
                else str(score.quantize(Decimal("0.0001"))),
                "ledger": left.public(),
                "counterparty": right.public(),
                "differences": diffs,
            }
        )
        used_l.add(left.stable_id)
        used_c.add(right.stable_id)

    # Human decisions take precedence and bind to stable identities, not row versions.
    for lid, cid in manual_pairs:
        if (
            lid in by_l
            and cid in by_c
            and by_l[lid].state != "CANCELLED"
            and by_c[cid].state != "CANCELLED"
        ):
            add_pair(by_l[lid], by_c[cid], "MANUAL")
    ids_l, ids_c = {}, {}
    for t in ledger:
        ids_l.setdefault(t.external_id.upper(), []).append(t)
    for t in counterparty:
        ids_c.setdefault(t.external_id.upper(), []).append(t)
    # Exact references may still contain material field differences; pairing and
    # comparison are deliberately separate concerns.
    for external_id in sorted(set(ids_l) & set(ids_c)):
        ls, cs = ids_l[external_id], ids_c[external_id]
        if (
            len(ls) == len(cs) == 1
            and ls[0].stable_id not in used_l
            and cs[0].stable_id not in used_c
            and ls[0].stable_id not in accepted
            and cs[0].stable_id not in accepted
            and ls[0].state != "CANCELLED"
            and cs[0].state != "CANCELLED"
        ):
            add_pair(ls[0], cs[0], "EXACT_ID")
    remaining_l = [
        t
        for t in ledger
        if t.stable_id not in used_l
        and t.stable_id not in accepted
        and t.state != "CANCELLED"
    ]
    remaining_c = [
        t
        for t in counterparty
        if t.stable_id not in used_c
        and t.stable_id not in accepted
        and t.state != "CANCELLED"
    ]
    scores = {
        (left.stable_id, right.stable_id): candidate_score(left, right, settings)
        for left in remaining_l
        for right in remaining_c
    }
    scores = {
        k: v
        for k, v in scores.items()
        if v is not None and v >= Decimal(settings["candidate_min_score"])
    }

    def unique_best(options):
        """Reject equal top scores instead of resolving financial ambiguity arbitrarily."""
        ranked = sorted(options, reverse=True)
        if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
            return (None, None)
        return ranked[0]

    best_l = {
        left.stable_id: unique_best(
            [
                (value, cid)
                for (lid, cid), value in scores.items()
                if lid == left.stable_id
            ]
        )
        for left in remaining_l
    }
    best_c = {
        c.stable_id: unique_best(
            [(value, lid) for (lid, cid), value in scores.items() if cid == c.stable_id]
        )
        for c in remaining_c
    }
    for left in remaining_l:
        score, cid = best_l[left.stable_id]
        if (
            score is not None
            and best_c.get(cid) == (score, left.stable_id)
            and left.stable_id not in used_l
            and cid not in used_c
        ):
            add_pair(left, by_c[cid], "CANDIDATE_SCORE", score)
    for t in ledger:
        if t.state == "CANCELLED":
            items.append(
                {
                    "status": "EXCLUDED_CANCELLED",
                    "match_method": "EXCLUDED",
                    "score": None,
                    "ledger": t.public(),
                    "counterparty": None,
                    "differences": [],
                }
            )
        elif t.stable_id not in used_l:
            items.append(
                {
                    "status": "ACCEPTED_UNMATCHED"
                    if t.stable_id in accepted
                    else "UNMATCHED_LEDGER",
                    "match_method": "MANUAL_ACCEPT"
                    if t.stable_id in accepted
                    else "NONE",
                    "score": None,
                    "ledger": t.public(),
                    "counterparty": None,
                    "differences": [],
                }
            )
    for t in counterparty:
        if t.state == "CANCELLED":
            items.append(
                {
                    "status": "EXCLUDED_CANCELLED",
                    "match_method": "EXCLUDED",
                    "score": None,
                    "ledger": None,
                    "counterparty": t.public(),
                    "differences": [],
                }
            )
        elif t.stable_id not in used_c:
            items.append(
                {
                    "status": "ACCEPTED_UNMATCHED"
                    if t.stable_id in accepted
                    else "UNMATCHED_COUNTERPARTY",
                    "match_method": "MANUAL_ACCEPT"
                    if t.stable_id in accepted
                    else "NONE",
                    "score": None,
                    "ledger": None,
                    "counterparty": t.public(),
                    "differences": [],
                }
            )
    return sorted(
        items,
        key=lambda x: (
            (x.get("ledger") or x.get("counterparty"))["external_id"],
            x["status"],
        ),
    )
