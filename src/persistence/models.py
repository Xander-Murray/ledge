from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    MetaData,
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
