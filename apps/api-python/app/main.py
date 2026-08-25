import csv
import hashlib
import io
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session
from .db import SessionLocal, get_db
from .domain import DEFAULT_SETTINGS, Transaction, reconcile
from .ingestion import FileValidationError, adapters_for, parse_csv, resolve_adapter
from .models import (
    AuditEvent,
    FieldDifference,
    IngestionFile,
    ManualResolution,
    ReconciliationItem,
    ReconciliationRun,
    SourceTransaction,
    ToleranceSetting,
    TransactionVersion,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Refuse to run against an unmigrated schema, then seed harmless defaults."""
    with SessionLocal() as db:
        version = db.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        if version != "0001":
            raise RuntimeError("database schema is not at Alembic revision 0001")
        if not db.scalar(select(ToleranceSetting).where(ToleranceSetting.active)):
            db.add(ToleranceSetting(settings_json=dict(DEFAULT_SETTINGS), active=True))
            db.commit()
    yield


app = FastAPI(title="Atlas Reconciliation API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    """Keep FastAPI failures identical to the shared Node error envelope."""
    body = (
        exc.detail
        if isinstance(exc.detail, dict)
        else {
            "error": {
                "code": "REQUEST_ERROR",
                "message": str(exc.detail),
                "details": [],
            }
        }
    )
    return JSONResponse(status_code=exc.status_code, content=body)


def iso(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def audit(db, action, entity_type, entity_id, details=None):
    db.add(
        AuditEvent(
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            details_json=details or {},
        )
    )


def active_settings(db):
    row = db.scalar(
        select(ToleranceSetting)
        .where(ToleranceSetting.active)
        .order_by(ToleranceSetting.id.desc())
    )
    return row.settings_json if row else dict(DEFAULT_SETTINGS)


def load_transactions(db):
    rows = db.execute(
        select(SourceTransaction, TransactionVersion)
        .join(
            TransactionVersion,
            SourceTransaction.current_version_id == TransactionVersion.id,
        )
        .where(SourceTransaction.active)
    ).all()
    result = []
    for stable, version in rows:
        data = version.data_json
        result.append(
            Transaction(
                stable_id=stable.id,
                version_id=version.id,
                source=data["source"],
                external_id=data["external_id"],
                executed_at=datetime.fromisoformat(data["executed_at"]),
                instrument=data["instrument"],
                side=data["side"],
                quantity=Decimal(data["quantity"]),
                price=Decimal(data["price"]),
                gross_amount=Decimal(data["gross_amount"]),
                state=data["state"],
                raw=version.raw_json,
            )
        )
    return result


def resolution_state(db, transactions):
    rows = db.scalars(
        select(ManualResolution)
        .where(ManualResolution.active)
        .order_by(ManualResolution.id)
    ).all()
    usable = {
        transaction.stable_id
        for transaction in transactions
        if transaction.state != "CANCELLED"
    }
    for resolution in rows:
        participants = {
            value
            for value in (
                resolution.ledger_transaction_id,
                resolution.counterparty_transaction_id,
                resolution.accepted_transaction_id,
            )
            if value is not None
        }
        resolution.dormant = not participants.issubset(usable)
        resolution.dormant_reason = (
            "TRANSACTION_CANCELLED_OR_INACTIVE" if resolution.dormant else None
        )
    effective = [resolution for resolution in rows if not resolution.dormant]
    pairs = [
        (r.ledger_transaction_id, r.counterparty_transaction_id)
        for r in effective
        if r.resolution_type == "MANUAL_MATCH"
    ]
    accepted = {
        r.accepted_transaction_id
        for r in effective
        if r.resolution_type == "ACCEPT_UNMATCHED"
    }
    accepted_differences = {
        (r.ledger_transaction_id, r.counterparty_transaction_id)
        for r in effective
        if r.resolution_type == "ACCEPT_DIFFERENCES"
    }
    return pairs, accepted, accepted_differences


def populate_run(db, run):
    """Rebuild one run from its snapshotted settings and the current stable identities."""
    if run.status == "CLOSED":
        raise HTTPException(
            409,
            {
                "error": {
                    "code": "RUN_CLOSED",
                    "message": "closed runs are immutable",
                    "details": [],
                }
            },
        )
    db.execute(
        delete(FieldDifference).where(
            FieldDifference.item_id.in_(
                select(ReconciliationItem.id).where(ReconciliationItem.run_id == run.id)
            )
        )
    )
    db.execute(delete(ReconciliationItem).where(ReconciliationItem.run_id == run.id))
    txs = load_transactions(db)
    pairs, accepted, accepted_differences = resolution_state(db, txs)
    results = reconcile(
        [t for t in txs if t.source == "LEDGER"],
        [t for t in txs if t.source == "COUNTERPARTY"],
        pairs,
        accepted,
        run.settings_json,
    )
    summary = {
        key: 0
        for key in [
            "MATCHED",
            "DIFFERENT",
            "UNMATCHED_LEDGER",
            "UNMATCHED_COUNTERPARTY",
            "MANUALLY_MATCHED",
            "ACCEPTED_UNMATCHED",
            "EXCLUDED_CANCELLED",
        ]
    }
    unresolved = 0
    for result in results:
        summary[result["status"]] = summary.get(result["status"], 0) + 1
        pair = (
            (result.get("ledger") or {}).get("id"),
            (result.get("counterparty") or {}).get("id"),
        )
        review_status, resolution_type = "NOT_REQUIRED", None
        if result["status"] == "DIFFERENT":
            if pair in accepted_differences:
                review_status, resolution_type = "ACCEPTED", "ACCEPT_DIFFERENCES"
            else:
                review_status = "PENDING"
        elif result["status"] in ("UNMATCHED_LEDGER", "UNMATCHED_COUNTERPARTY"):
            review_status = "PENDING"
        elif result["status"] == "ACCEPTED_UNMATCHED":
            review_status, resolution_type = "ACCEPTED", "ACCEPT_UNMATCHED"
        elif result["status"] == "MANUALLY_MATCHED":
            review_status, resolution_type = "RESOLVED", "MANUAL_MATCH"
        if review_status == "PENDING":
            unresolved += 1
        result["review_status"] = review_status
        result["resolution_type"] = resolution_type
        item = ReconciliationItem(
            run_id=run.id,
            ledger_transaction_id=result["ledger"]["id"] if result["ledger"] else None,
            counterparty_transaction_id=result["counterparty"]["id"]
            if result["counterparty"]
            else None,
            status=result["status"],
            match_method=result["match_method"],
            score=result["score"],
            review_status=review_status,
            result_json=result,
        )
        db.add(item)
        db.flush()
        for difference in result["differences"]:
            db.add(
                FieldDifference(
                    item_id=item.id,
                    field=difference["field"],
                    difference_json=difference,
                )
            )
    summary["UNRESOLVED"] = unresolved
    run.summary_json = summary
    run.status = "READY_TO_CLOSE" if unresolved == 0 else "OPEN"
    db.flush()
    return results


@app.get("/api/health")
def health():
    return {"status": "ok", "implementation": "python", "version": "1.0.0"}


@app.get("/api/adapters")
def list_adapters(source: str = Query(...)):
    source = source.upper()
    if source not in ("LEDGER", "COUNTERPARTY"):
        raise HTTPException(
            422,
            {
                "error": {
                    "code": "INVALID_SOURCE",
                    "message": "source must be LEDGER or COUNTERPARTY",
                    "details": [],
                }
            },
        )
    return [
        {
            "id": adapter.id,
            "source": adapter.source,
            "description": adapter.description,
            "headers": list(adapter.headers),
        }
        for adapter in adapters_for(source)
    ]


@app.post("/api/files", status_code=201)
async def upload_file(
    source: str = Query(...),
    mode: str = Query("INCREMENTAL"),
    adapter_id: str | None = Query(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    source = source.upper()
    mode = mode.upper()
    if source not in ("LEDGER", "COUNTERPARTY"):
        raise HTTPException(
            422,
            {
                "error": {
                    "code": "INVALID_SOURCE",
                    "message": "source must be LEDGER or COUNTERPARTY",
                    "details": [],
                }
            },
        )
    if mode not in ("INCREMENTAL", "SNAPSHOT"):
        raise HTTPException(
            422,
            {
                "error": {
                    "code": "INVALID_UPLOAD_MODE",
                    "message": "mode must be INCREMENTAL or SNAPSHOT",
                    "details": [],
                }
            },
        )
    content = await file.read()
    if len(content) > 5_000_000:
        raise HTTPException(
            413,
            {
                "error": {
                    "code": "FILE_TOO_LARGE",
                    "message": "maximum file size is 5 MB",
                    "details": [],
                }
            },
        )
    checksum = hashlib.sha256(content).hexdigest()
    try:
        adapter = resolve_adapter(content, source, adapter_id)
    except FileValidationError as exc:
        raise HTTPException(
            422,
            {
                "error": {
                    "code": "INVALID_FILE",
                    "message": "file validation failed",
                    "details": exc.errors,
                }
            },
        )
    duplicate = db.scalar(
        select(IngestionFile).where(
            IngestionFile.source == source,
            IngestionFile.checksum == checksum,
            IngestionFile.upload_mode == mode,
            IngestionFile.adapter_id == adapter.id,
        )
    )
    if duplicate:
        return {
            "id": duplicate.id,
            "source": source,
            "filename": duplicate.filename,
            "checksum": checksum,
            "mode": duplicate.upload_mode,
            "adapter_id": duplicate.adapter_id,
            "row_count": duplicate.row_count,
            "changed_count": duplicate.changed_count,
            "duplicate": True,
            "created_at": iso(duplicate.created_at),
        }
    open_run = db.scalar(
        select(ReconciliationRun).where(ReconciliationRun.status != "CLOSED")
    )
    if open_run:
        raise HTTPException(
            409,
            {
                "error": {
                    "code": "OPEN_RUN_EXISTS",
                    "message": "close the current run before ingesting changed source data",
                    "details": [],
                }
            },
        )
    try:
        rows = parse_csv(content, source, adapter.id)
    except FileValidationError as exc:
        raise HTTPException(
            422,
            {
                "error": {
                    "code": "INVALID_FILE",
                    "message": "file validation failed",
                    "details": exc.errors,
                }
            },
        )
    ingestion = IngestionFile(
        source=source,
        filename=file.filename or "upload.csv",
        checksum=checksum,
        upload_mode=mode,
        adapter_id=adapter.id,
        row_count=len(rows),
        changed_count=0,
    )
    db.add(ingestion)
    db.flush()
    changed = 0
    # Validation finished before the transaction started. Any persistence failure
    # now rolls back the file and every row version together.
    for data, raw, fingerprint in rows:
        stable = db.scalar(
            select(SourceTransaction).where(
                SourceTransaction.source == source,
                SourceTransaction.external_id == data["external_id"],
            )
        )
        if not stable:
            stable = SourceTransaction(source=source, external_id=data["external_id"])
            db.add(stable)
            db.flush()
        current = (
            db.get(TransactionVersion, stable.current_version_id)
            if stable.current_version_id
            else None
        )
        # Unchanged rows do not create noisy versions; changed values remain immutable.
        was_inactive = not stable.active
        stable.active = True
        stable.inactive_reason = None
        stable.last_seen_file_id = ingestion.id
        if current and current.fingerprint == fingerprint and not was_inactive:
            continue
        version_number = (
            db.scalar(
                select(func.max(TransactionVersion.version)).where(
                    TransactionVersion.transaction_id == stable.id
                )
            )
            or 0
        ) + 1
        version = TransactionVersion(
            transaction_id=stable.id,
            ingestion_file_id=ingestion.id,
            version=version_number,
            fingerprint=fingerprint,
            data_json=data,
            raw_json=raw,
        )
        db.add(version)
        db.flush()
        stable.current_version_id = version.id
        changed += 1
    if mode == "SNAPSHOT":
        omitted = db.scalars(
            select(SourceTransaction).where(
                SourceTransaction.source == source,
                SourceTransaction.active,
                or_(
                    SourceTransaction.last_seen_file_id.is_(None),
                    SourceTransaction.last_seen_file_id != ingestion.id,
                ),
            )
        ).all()
        for stable in omitted:
            stable.active = False
            stable.inactive_reason = "ABSENT_FROM_SNAPSHOT"
        changed += len(omitted)
    ingestion.changed_count = changed
    audit(
        db,
        "FILE_INGESTED",
        "ingestion_file",
        ingestion.id,
        {
            "source": source,
            "mode": mode,
            "adapter_id": adapter.id,
            "rows": len(rows),
            "changed": changed,
        },
    )
    db.commit()
    return {
        "id": ingestion.id,
        "source": source,
        "filename": ingestion.filename,
        "checksum": checksum,
        "mode": mode,
        "adapter_id": adapter.id,
        "row_count": len(rows),
        "changed_count": changed,
        "duplicate": False,
        "created_at": iso(ingestion.created_at),
    }


@app.get("/api/files")
def files(db: Session = Depends(get_db)):
    return [
        {
            "id": x.id,
            "source": x.source,
            "filename": x.filename,
            "checksum": x.checksum,
            "mode": x.upload_mode,
            "adapter_id": x.adapter_id,
            "row_count": x.row_count,
            "changed_count": x.changed_count,
            "created_at": iso(x.created_at),
        }
        for x in db.scalars(
            select(IngestionFile).order_by(IngestionFile.id.desc())
        ).all()
    ]


@app.post("/api/runs", status_code=201)
def create_run(db: Session = Depends(get_db)):
    sources = set(
        db.scalars(
            select(SourceTransaction.source).where(SourceTransaction.active)
        ).all()
    )
    if sources != {"LEDGER", "COUNTERPARTY"}:
        raise HTTPException(
            409,
            {
                "error": {
                    "code": "MISSING_SOURCE",
                    "message": "upload at least one file for each source",
                    "details": [],
                }
            },
        )
    run = ReconciliationRun(settings_json=active_settings(db))
    db.add(run)
    db.flush()
    populate_run(db, run)
    audit(db, "RUN_CREATED", "reconciliation_run", run.id, run.summary_json)
    db.commit()
    return {
        "id": run.id,
        "status": run.status,
        "summary": run.summary_json,
        "created_at": iso(run.created_at),
        "closed_at": iso(run.closed_at) if run.closed_at else None,
        "closed_by": run.closed_by,
    }


@app.get("/api/runs")
def list_runs(db: Session = Depends(get_db)):
    return [
        {
            "id": r.id,
            "status": r.status,
            "summary": r.summary_json,
            "created_at": iso(r.created_at),
            "closed_at": iso(r.closed_at) if r.closed_at else None,
            "closed_by": r.closed_by,
        }
        for r in db.scalars(
            select(ReconciliationRun).order_by(ReconciliationRun.id.desc())
        ).all()
    ]


@app.get("/api/runs/{run_id}/results")
def list_results(
    run_id: int,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    run = db.get(ReconciliationRun, run_id)
    if not run:
        raise HTTPException(
            404,
            {"error": {"code": "NOT_FOUND", "message": "run not found", "details": []}},
        )
    query = (
        select(ReconciliationItem)
        .where(ReconciliationItem.run_id == run_id)
        .order_by(ReconciliationItem.id)
    )
    rows = db.scalars(query).all()
    items = []
    for row in rows:
        data = dict(row.result_json)
        data["id"] = row.id
        if status and data["status"] != status:
            continue
        if search and search.lower() not in json.dumps(data).lower():
            continue
        items.append(data)
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": len(items),
        "page": page,
        "page_size": page_size,
        "summary": run.summary_json,
    }


class MatchBody(BaseModel):
    ledger_transaction_id: int
    counterparty_transaction_id: int
    note: str = ""


class AcceptBody(BaseModel):
    transaction_id: int
    note: str = ""


class AcceptDifferencesBody(BaseModel):
    item_id: int
    note: str = ""


class NoteBody(BaseModel):
    note: str = ""


def ensure_unresolved(db, ids):
    active = db.scalars(select(ManualResolution).where(ManualResolution.active)).all()
    occupied = set()
    for r in active:
        occupied.update(
            x
            for x in [
                r.ledger_transaction_id,
                r.counterparty_transaction_id,
                r.accepted_transaction_id,
            ]
            if x
        )
    if occupied.intersection(ids):
        raise HTTPException(
            409,
            {
                "error": {
                    "code": "RESOLUTION_CONFLICT",
                    "message": "a transaction already has an active resolution",
                    "details": [],
                }
            },
        )


def refresh_latest(db):
    run = db.scalar(
        select(ReconciliationRun)
        .where(ReconciliationRun.status != "CLOSED")
        .order_by(ReconciliationRun.id.desc())
    )
    if run:
        populate_run(db, run)


def require_open_run(db):
    run = db.scalar(
        select(ReconciliationRun)
        .where(ReconciliationRun.status != "CLOSED")
        .order_by(ReconciliationRun.id.desc())
    )
    if not run:
        raise HTTPException(
            409,
            {
                "error": {
                    "code": "NO_OPEN_RUN",
                    "message": "create a reconciliation run before recording decisions",
                    "details": [],
                }
            },
        )
    return run


@app.post("/api/resolutions/match", status_code=201)
def manual_match(body: MatchBody, db: Session = Depends(get_db)):
    require_open_run(db)
    left, right = (
        db.get(SourceTransaction, body.ledger_transaction_id),
        db.get(SourceTransaction, body.counterparty_transaction_id),
    )
    if (
        not left
        or not right
        or left.source != "LEDGER"
        or right.source != "COUNTERPARTY"
    ):
        raise HTTPException(
            422,
            {
                "error": {
                    "code": "INVALID_PAIR",
                    "message": "select one transaction from each source",
                    "details": [],
                }
            },
        )
    ensure_unresolved(db, {left.id, right.id})
    resolution = ManualResolution(
        resolution_type="MANUAL_MATCH",
        ledger_transaction_id=left.id,
        counterparty_transaction_id=right.id,
        note=body.note,
    )
    db.add(resolution)
    db.flush()
    audit(
        db,
        "MANUAL_MATCH_CREATED",
        "manual_resolution",
        resolution.id,
        {"ledger": left.id, "counterparty": right.id, "note": body.note},
    )
    refresh_latest(db)
    db.commit()
    return {
        "id": resolution.id,
        "type": "MANUAL_MATCH",
        "active": True,
        "created_at": iso(resolution.created_at),
    }


@app.post("/api/resolutions/accept-unmatched", status_code=201)
def accept_unmatched(body: AcceptBody, db: Session = Depends(get_db)):
    require_open_run(db)
    tx = db.get(SourceTransaction, body.transaction_id)
    if not tx:
        raise HTTPException(
            404,
            {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "transaction not found",
                    "details": [],
                }
            },
        )
    ensure_unresolved(db, {tx.id})
    resolution = ManualResolution(
        resolution_type="ACCEPT_UNMATCHED",
        accepted_transaction_id=tx.id,
        note=body.note,
    )
    db.add(resolution)
    db.flush()
    audit(
        db,
        "UNMATCHED_ACCEPTED",
        "manual_resolution",
        resolution.id,
        {"transaction": tx.id, "note": body.note},
    )
    refresh_latest(db)
    db.commit()
    return {
        "id": resolution.id,
        "type": "ACCEPT_UNMATCHED",
        "active": True,
        "created_at": iso(resolution.created_at),
    }


@app.post("/api/resolutions/accept-differences", status_code=201)
def accept_differences(body: AcceptDifferencesBody, db: Session = Depends(get_db)):
    item = db.get(ReconciliationItem, body.item_id)
    run = db.get(ReconciliationRun, item.run_id) if item else None
    if not item or not run:
        raise HTTPException(
            404,
            {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "item not found",
                    "details": [],
                }
            },
        )
    if run.status == "CLOSED":
        raise HTTPException(
            409,
            {
                "error": {
                    "code": "RUN_CLOSED",
                    "message": "closed runs are immutable",
                    "details": [],
                }
            },
        )
    if (
        item.status != "DIFFERENT"
        or item.ledger_transaction_id is None
        or item.counterparty_transaction_id is None
    ):
        raise HTTPException(
            422,
            {
                "error": {
                    "code": "INVALID_REVIEW_ITEM",
                    "message": "only a matched item with material differences can be accepted",
                    "details": [],
                }
            },
        )
    ensure_unresolved(
        db, {item.ledger_transaction_id, item.counterparty_transaction_id}
    )
    resolution = ManualResolution(
        resolution_type="ACCEPT_DIFFERENCES",
        ledger_transaction_id=item.ledger_transaction_id,
        counterparty_transaction_id=item.counterparty_transaction_id,
        note=body.note,
    )
    db.add(resolution)
    db.flush()
    audit(
        db,
        "DIFFERENCES_ACCEPTED",
        "manual_resolution",
        resolution.id,
        {"item": item.id, "note": body.note},
    )
    populate_run(db, run)
    db.commit()
    return {
        "id": resolution.id,
        "type": "ACCEPT_DIFFERENCES",
        "active": True,
        "created_at": iso(resolution.created_at),
    }


@app.post("/api/runs/{run_id}/close")
def close_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(ReconciliationRun, run_id)
    if not run:
        raise HTTPException(
            404,
            {"error": {"code": "NOT_FOUND", "message": "run not found", "details": []}},
        )
    if run.status == "CLOSED":
        return {
            "id": run.id,
            "status": run.status,
            "summary": run.summary_json,
            "created_at": iso(run.created_at),
            "closed_at": iso(run.closed_at),
            "closed_by": run.closed_by,
        }
    if run.status != "READY_TO_CLOSE":
        raise HTTPException(
            409,
            {
                "error": {
                    "code": "UNRESOLVED_EXCEPTIONS",
                    "message": "resolve every exception before closing the run",
                    "details": [],
                }
            },
        )
    run.status = "CLOSED"
    run.closed_at = datetime.now(timezone.utc)
    run.closed_by = "demo.operator"
    audit(db, "RUN_CLOSED", "reconciliation_run", run.id, run.summary_json)
    db.commit()
    return {
        "id": run.id,
        "status": run.status,
        "summary": run.summary_json,
        "created_at": iso(run.created_at),
        "closed_at": iso(run.closed_at),
        "closed_by": run.closed_by,
    }


@app.post("/api/resolutions/{resolution_id}/supersede", status_code=201)
def supersede_resolution(
    resolution_id: int, body: NoteBody, db: Session = Depends(get_db)
):
    require_open_run(db)
    previous = db.get(ManualResolution, resolution_id)
    if not previous or not previous.active:
        raise HTTPException(
            404,
            {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "active resolution not found",
                    "details": [],
                }
            },
        )
    previous.active = False
    replacement = ManualResolution(
        resolution_type="SUPERSEDE",
        ledger_transaction_id=previous.ledger_transaction_id,
        counterparty_transaction_id=previous.counterparty_transaction_id,
        accepted_transaction_id=previous.accepted_transaction_id,
        note=body.note,
        active=False,
        supersedes_id=previous.id,
    )
    db.add(replacement)
    db.flush()
    audit(
        db,
        "RESOLUTION_SUPERSEDED",
        "manual_resolution",
        replacement.id,
        {"previous_resolution_id": previous.id, "note": body.note},
    )
    refresh_latest(db)
    db.commit()
    return {
        "id": replacement.id,
        "type": "SUPERSEDE",
        "active": False,
        "supersedes_id": previous.id,
        "created_at": iso(replacement.created_at),
    }


@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)):
    return active_settings(db)


@app.put("/api/settings")
def update_settings(body: dict, db: Session = Depends(get_db)):
    allowed = set(DEFAULT_SETTINGS)
    if set(body) != allowed:
        raise HTTPException(
            422,
            {
                "error": {
                    "code": "INVALID_SETTINGS",
                    "message": "all setting fields are required and unknown fields are rejected",
                    "details": [],
                }
            },
        )
    current = db.scalars(select(ToleranceSetting).where(ToleranceSetting.active)).all()
    for row in current:
        row.active = False
    row = ToleranceSetting(settings_json=body, active=True)
    db.add(row)
    db.flush()
    audit(db, "SETTINGS_UPDATED", "tolerance_setting", row.id, body)
    db.commit()
    return body


@app.get("/api/audit")
def get_audit(db: Session = Depends(get_db)):
    return [
        {
            "id": e.id,
            "action": e.action,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "actor": e.actor,
            "details": e.details_json,
            "created_at": iso(e.created_at),
        }
        for e in db.scalars(
            select(AuditEvent).order_by(AuditEvent.id.desc()).limit(100)
        ).all()
    ]


@app.get("/api/transactions/{transaction_id}/history")
def transaction_history(transaction_id: int, db: Session = Depends(get_db)):
    stable = db.get(SourceTransaction, transaction_id)
    if not stable:
        raise HTTPException(
            404,
            {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "transaction not found",
                    "details": [],
                }
            },
        )
    versions = db.scalars(
        select(TransactionVersion)
        .where(TransactionVersion.transaction_id == transaction_id)
        .order_by(TransactionVersion.version.desc())
    ).all()
    return [
        {
            "id": v.id,
            "version": v.version,
            "current": v.id == stable.current_version_id,
            "data": v.data_json,
            "raw": v.raw_json,
            "created_at": iso(v.created_at),
        }
        for v in versions
    ]


@app.get("/api/runs/{run_id}/export")
def export_run(run_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ReconciliationItem)
        .where(ReconciliationItem.run_id == run_id)
        .order_by(ReconciliationItem.id)
    ).all()
    if not rows and not db.get(ReconciliationRun, run_id):
        raise HTTPException(
            404,
            {"error": {"code": "NOT_FOUND", "message": "run not found", "details": []}},
        )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "status",
            "match_method",
            "ledger_id",
            "counterparty_id",
            "material_differences",
        ]
    )
    for row in rows:
        data = row.result_json
        writer.writerow(
            [
                data["status"],
                data["match_method"],
                (data.get("ledger") or {}).get("external_id", ""),
                (data.get("counterparty") or {}).get("external_id", ""),
                "|".join(d["field"] for d in data["differences"] if not d["passed"]),
            ]
        )
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="reconciliation-run-{run_id}.csv"'
        },
    )
