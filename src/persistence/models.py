from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    MetaData,
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
