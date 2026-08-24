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
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from .db import Base, SessionLocal, engine, get_db
from .domain import DEFAULT_SETTINGS, Transaction, reconcile
from .ingestion import FileValidationError, parse_csv
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
    """Initialize local defaults while keeping migrations available to reviewers."""
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if not db.scalar(select(ToleranceSetting).where(ToleranceSetting.active)):
            db.add(
                ToleranceSetting(
                    settings_json=json.dumps(DEFAULT_SETTINGS), active=True
                )
            )
            db.commit()
    yield


app = FastAPI(title="Atlas Reconciliation API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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
            details_json=json.dumps(details or {}),
        )
    )


def active_settings(db):
    row = db.scalar(
        select(ToleranceSetting)
        .where(ToleranceSetting.active)
        .order_by(ToleranceSetting.id.desc())
    )
    return json.loads(row.settings_json) if row else dict(DEFAULT_SETTINGS)


def load_transactions(db):
    rows = db.execute(
        select(SourceTransaction, TransactionVersion).join(
            TransactionVersion,
            SourceTransaction.current_version_id == TransactionVersion.id,
        )
    ).all()
    result = []
    for stable, version in rows:
        data = json.loads(version.data_json)
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
                raw=json.loads(version.raw_json),
            )
        )
    return result


def resolution_state(db):
    rows = db.scalars(
        select(ManualResolution)
        .where(ManualResolution.active)
        .order_by(ManualResolution.id)
    ).all()
    pairs = [
        (r.ledger_transaction_id, r.counterparty_transaction_id)
        for r in rows
        if r.resolution_type == "MATCH"
    ]
    accepted = {
        r.accepted_transaction_id
        for r in rows
        if r.resolution_type == "ACCEPT_UNMATCHED"
    }
    return pairs, accepted


def populate_run(db, run):
    """Rebuild one run from its snapshotted settings and the current stable identities."""
    db.execute(
        delete(FieldDifference).where(
            FieldDifference.item_id.in_(
                select(ReconciliationItem.id).where(ReconciliationItem.run_id == run.id)
            )
        )
    )
    db.execute(delete(ReconciliationItem).where(ReconciliationItem.run_id == run.id))
    txs = load_transactions(db)
    pairs, accepted = resolution_state(db)
    results = reconcile(
        [t for t in txs if t.source == "LEDGER"],
        [t for t in txs if t.source == "COUNTERPARTY"],
        pairs,
        accepted,
        json.loads(run.settings_json),
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
    for result in results:
        summary[result["status"]] = summary.get(result["status"], 0) + 1
        item = ReconciliationItem(
            run_id=run.id,
            ledger_transaction_id=result["ledger"]["id"] if result["ledger"] else None,
            counterparty_transaction_id=result["counterparty"]["id"]
            if result["counterparty"]
            else None,
            status=result["status"],
            match_method=result["match_method"],
            score=result["score"],
            result_json=json.dumps(result),
        )
        db.add(item)
        db.flush()
        for difference in result["differences"]:
            db.add(
                FieldDifference(
                    item_id=item.id,
                    field=difference["field"],
                    difference_json=json.dumps(difference),
                )
            )
    run.summary_json = json.dumps(summary)
    db.flush()
    return results


@app.get("/api/health")
def health():
    return {"status": "ok", "implementation": "python", "version": "1.0.0"}


@app.post("/api/files", status_code=201)
async def upload_file(
    source: str = Query(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
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
    duplicate = db.scalar(
        select(IngestionFile).where(
            IngestionFile.source == source, IngestionFile.checksum == checksum
        )
    )
    if duplicate:
        return {
            "id": duplicate.id,
            "source": source,
            "filename": duplicate.filename,
            "checksum": checksum,
            "row_count": duplicate.row_count,
            "changed_count": duplicate.changed_count,
            "duplicate": True,
            "created_at": iso(duplicate.created_at),
        }
    try:
        rows = parse_csv(content, source)
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
        if current and current.fingerprint == fingerprint:
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
            data_json=json.dumps(data),
            raw_json=json.dumps(raw),
        )
        db.add(version)
        db.flush()
        stable.current_version_id = version.id
        changed += 1
    ingestion.changed_count = changed
    audit(
        db,
        "FILE_INGESTED",
        "ingestion_file",
        ingestion.id,
        {"source": source, "rows": len(rows), "changed": changed},
    )
    db.commit()
    return {
        "id": ingestion.id,
        "source": source,
        "filename": ingestion.filename,
        "checksum": checksum,
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
    sources = set(db.scalars(select(SourceTransaction.source)).all())
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
    run = ReconciliationRun(settings_json=json.dumps(active_settings(db)))
    db.add(run)
    db.flush()
    populate_run(db, run)
    audit(
        db, "RUN_COMPLETED", "reconciliation_run", run.id, json.loads(run.summary_json)
    )
    db.commit()
    return {
        "id": run.id,
        "status": run.status,
        "summary": json.loads(run.summary_json),
        "created_at": iso(run.created_at),
    }


@app.get("/api/runs")
def list_runs(db: Session = Depends(get_db)):
    return [
        {
            "id": r.id,
            "status": r.status,
            "summary": json.loads(r.summary_json),
            "created_at": iso(r.created_at),
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
        data = json.loads(row.result_json)
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
        "summary": json.loads(run.summary_json),
    }


class MatchBody(BaseModel):
    ledger_transaction_id: int
    counterparty_transaction_id: int
    note: str = ""


class AcceptBody(BaseModel):
    transaction_id: int
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
    run = db.scalar(select(ReconciliationRun).order_by(ReconciliationRun.id.desc()))
    if run:
        populate_run(db, run)


@app.post("/api/resolutions/match", status_code=201)
def manual_match(body: MatchBody, db: Session = Depends(get_db)):
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
        resolution_type="MATCH",
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
        "type": "MATCH",
        "active": True,
        "created_at": iso(resolution.created_at),
    }


@app.post("/api/resolutions/accept-unmatched", status_code=201)
def accept_unmatched(body: AcceptBody, db: Session = Depends(get_db)):
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
    row = ToleranceSetting(settings_json=json.dumps(body), active=True)
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
            "details": json.loads(e.details_json),
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
            "data": json.loads(v.data_json),
            "raw": json.loads(v.raw_json),
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
        data = json.loads(row.result_json)
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
