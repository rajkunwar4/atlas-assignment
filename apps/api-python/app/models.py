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
)
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
    row_count: Mapped[int] = mapped_column(Integer)
    changed_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        UniqueConstraint("source", "checksum", name="uq_file_source_checksum"),
    )


class SourceTransaction(Base):
    __tablename__ = "source_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(120))
    current_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
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
    data_json: Mapped[str] = mapped_column(Text)
    raw_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        UniqueConstraint("transaction_id", "version", name="uq_transaction_version"),
    )


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    settings_json: Mapped[str] = mapped_column(Text)
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="COMPLETED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


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
    result_json: Mapped[str] = mapped_column(Text)


class FieldDifference(Base):
    __tablename__ = "field_differences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("reconciliation_items.id"), index=True
    )
    field: Mapped[str] = mapped_column(String(40))
    difference_json: Mapped[str] = mapped_column(Text)


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
    note: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="demo.operator")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    supersedes_id: Mapped[int | None] = mapped_column(
        ForeignKey("manual_resolutions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ToleranceSetting(Base):
    __tablename__ = "tolerance_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    settings_json: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str] = mapped_column(String(80), default="demo.operator")
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
