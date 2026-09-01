from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    MetaData,
    SmallInteger,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base for SQLAlchemy persistence models and Alembic metadata."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UserModel(Base):
    """Persistence identity that will eventually map to an authenticated user."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    accounts: Mapped[list[FinancialAccountModel]] = relationship(back_populates="user")

    transaction_sync_states: Mapped[list[TransactionSyncStateModel]] = relationship(
        back_populates="user"
    )


class FinancialAccountModel(Base):
    """A checking, savings, or credit account owned by one Ledge user."""

    __tablename__ = "financial_accounts"

    __table_args__ = (
        CheckConstraint(
            "account_type IN ('checking', 'savings', 'credit')",
            name="account_type",
        ),
        CheckConstraint(
            "length(trim(name)) > 0",
            name="name_nonempty",
        ),
        UniqueConstraint(
            "id",
            "user_id",
            name="uq_financial_accounts_ownership",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(nullable=False)

    account_type: Mapped[str] = mapped_column(nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[UserModel] = relationship(back_populates="accounts")

    transactions: Mapped[list[ExternalTransactionModel]] = relationship(
        back_populates="account"
    )


class TransactionSyncStateModel(Base):
    """Durable progress for one provider transaction-update stream."""

    __tablename__ = "transaction_sync_states"

    __table_args__ = (
        CheckConstraint(
            "length(trim(provider_name)) > 0",
            name="provider_name_nonempty",
        ),
        CheckConstraint(
            "length(trim(provider_connection_id)) > 0",
            name="provider_connection_id_nonempty",
        ),
        UniqueConstraint(
            "provider_name",
            "provider_connection_id",
            name="uq_transaction_sync_states_provider_connection",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    provider_name: Mapped[str] = mapped_column(nullable=False)

    provider_connection_id: Mapped[str] = mapped_column(nullable=False)

    cursor: Mapped[str | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped[UserModel] = relationship(back_populates="transaction_sync_states")


class ExternalTransactionModel(Base):
    """The latest known state of one provider transaction."""

    __tablename__ = "external_transactions"

    __table_args__ = (
        CheckConstraint(
            "length(trim(provider_transaction_id)) > 0",
            name="provider_id_nonempty",
        ),
        CheckConstraint(
            "status IN ('active', 'removed')",
            name="status",
        ),
        ForeignKeyConstraint(
            ["financial_account_id", "user_id"],
            ["financial_accounts.id", "financial_accounts.user_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "user_id",
            "provider_transaction_id",
            name="uq_external_transactions_provider_identity",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)

    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)

    financial_account_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)

    provider_transaction_id: Mapped[str] = mapped_column(nullable=False)

    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)

    description: Mapped[str] = mapped_column(nullable=False)

    status: Mapped[str] = mapped_column(nullable=False, server_default="active")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    account: Mapped[FinancialAccountModel] = relationship(back_populates="transactions")

    journal_entries: Mapped[list[JournalEntryModel]] = relationship(
        back_populates="external_transaction"
    )


class JournalEntryModel(Base):
    """An immutable accounting event for one external transaction."""

    __tablename__ = "journal_entries"

    __table_args__ = (
        CheckConstraint(
            "reversal_of_entry_id IS NULL OR reversal_of_entry_id <> id",
            name="not_self_reversal",
        ),
        UniqueConstraint(
            "id",
            "external_transaction_id",
            name="uq_journal_entries_identity",
        ),
        ForeignKeyConstraint(
            ["reversal_of_entry_id", "external_transaction_id"],
            ["journal_entries.id", "journal_entries.external_transaction_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "reversal_of_entry_id",
            name="uq_journal_entries_reversal",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)

    description: Mapped[str] = mapped_column(nullable=False)

    external_transaction_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("external_transactions.id", ondelete="RESTRICT"),
        nullable=False,
    )

    reversal_of_entry_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    sealed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    external_transaction: Mapped[ExternalTransactionModel] = relationship(
        back_populates="journal_entries"
    )

    postings: Mapped[list[PostingModel]] = relationship(back_populates="journal_entry")


class PostingModel(Base):
    """One ordered debit or credit line in a journal entry."""

    __tablename__ = "postings"

    __table_args__ = (
        CheckConstraint(
            "line_number >= 0",
            name="line_number_nonnegative",
        ),
        CheckConstraint(
            "length(trim(ledger_account)) > 0",
            name="ledger_account_nonempty",
        ),
        UniqueConstraint(
            "journal_entry_id",
            "line_number",
            name="uq_postings_journal_line",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    journal_entry_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("journal_entries.id", ondelete="RESTRICT"),
        nullable=False,
    )

    line_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    ledger_account: Mapped[str] = mapped_column(nullable=False)

    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)

    journal_entry: Mapped[JournalEntryModel] = relationship(back_populates="postings")
