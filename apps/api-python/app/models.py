from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def now():
    return datetime.now(timezone.utc)


class IngestionFile(Base):
    __tablename__ = "ingestion_files"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    checksum: Mapped[str] = mapped_column(String(64))
    upload_mode: Mapped[str] = mapped_column(
        String(20), default="INCREMENTAL", server_default="INCREMENTAL"
    )
    adapter_id: Mapped[str] = mapped_column(String(80))
    row_count: Mapped[int] = mapped_column(Integer)
    changed_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint(
            "source",
            "checksum",
            "upload_mode",
            "adapter_id",
            name="uq_file_source_checksum_mode_adapter",
        ),
    )


class SourceTransaction(Base):
    __tablename__ = "source_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(120))
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), index=True
    )
    inactive_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_seen_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingestion_files.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_source_external"),
        Index("idx_source_transactions_lookup", "source", "external_id"),
    )


class TransactionVersion(Base):
    __tablename__ = "transaction_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("source_transactions.id"), index=True
    )
    ingestion_file_id: Mapped[int] = mapped_column(
        ForeignKey("ingestion_files.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    data_json: Mapped[dict] = mapped_column(JSONB)
    raw_json: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint("transaction_id", "version", name="uq_transaction_version"),
    )


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    settings_json: Mapped[dict] = mapped_column(JSONB)
    summary_json: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20), default="OPEN", server_default="OPEN"
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, server_default=func.now()
    )


class ReconciliationItem(Base):
    __tablename__ = "reconciliation_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("reconciliation_runs.id"), index=True
    )
    ledger_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_transactions.id"), nullable=True
    )
    counterparty_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_transactions.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(40), index=True)
    match_method: Mapped[str] = mapped_column(String(30))
    score: Mapped[str | None] = mapped_column(String(30), nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(30), default="NOT_REQUIRED", server_default="NOT_REQUIRED"
    )
    result_json: Mapped[dict] = mapped_column(JSONB)


class FieldDifference(Base):
    __tablename__ = "field_differences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("reconciliation_items.id"), index=True
    )
    field: Mapped[str] = mapped_column(String(40))
    difference_json: Mapped[dict] = mapped_column(JSONB)


class ManualResolution(Base):
    __tablename__ = "manual_resolutions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resolution_type: Mapped[str] = mapped_column(String(30))
    ledger_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_transactions.id"), nullable=True
    )
    counterparty_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_transactions.id"), nullable=True
    )
    accepted_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_transactions.id"), nullable=True
    )
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    actor: Mapped[str] = mapped_column(
        String(80), default="demo.operator", server_default="demo.operator"
    )
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), index=True
    )
    dormant: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )
    dormant_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    supersedes_id: Mapped[int | None] = mapped_column(
        ForeignKey("manual_resolutions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, server_default=func.now()
    )


class ToleranceSetting(Base):
    __tablename__ = "tolerance_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    settings_json: Mapped[dict] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, server_default=func.now()
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str] = mapped_column(
        String(80), default="demo.operator", server_default="demo.operator"
    )
    details_json: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, server_default=func.now()
    )
