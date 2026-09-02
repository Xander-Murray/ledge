from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    inspect,
)

from persistence.models import ExternalTransactionModel, FinancialAccountModel


def test_external_transaction_has_required_current_projection_fields() -> None:
    columns = ExternalTransactionModel.__table__.c

    assert set(columns.keys()) == {
        "id",
        "user_id",
        "financial_account_id",
        "provider_transaction_id",
        "amount_cents",
        "description",
        "is_pending",
        "pending_provider_transaction_id",
        "status",
        "created_at",
        "updated_at",
    }
    assert isinstance(columns.provider_transaction_id.type, String)
    assert isinstance(columns.amount_cents.type, BigInteger)
    assert isinstance(columns.is_pending.type, Boolean)
    assert isinstance(columns.pending_provider_transaction_id.type, String)
    assert columns.is_pending.nullable is False
    assert columns.is_pending.server_default is not None
    assert columns.pending_provider_transaction_id.nullable is True
    assert isinstance(columns.status.type, String)
    assert columns.status.server_default is not None


def test_external_transaction_timestamps_are_database_generated_and_utc() -> None:
    columns = ExternalTransactionModel.__table__.c

    for column in (columns.created_at, columns.updated_at):
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True
        assert column.nullable is False
        assert column.server_default is not None


def test_financial_account_exposes_a_composite_ownership_key() -> None:
    ownership_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in FinancialAccountModel.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ownership_constraints["uq_financial_accounts_ownership"] == (
        "id",
        "user_id",
    )


def test_external_transaction_enforces_ownership_and_provider_identity() -> None:
    table = ExternalTransactionModel.__table__
    foreign_keys = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ]
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    foreign_key_targets = {
        tuple(element.target_fullname for element in constraint.elements): constraint
        for constraint in foreign_keys
    }

    assert set(foreign_key_targets) == {
        ("financial_accounts.id", "financial_accounts.user_id"),
        (
            "external_transactions.user_id",
            "external_transactions.provider_transaction_id",
        ),
    }
    assert all(
        constraint.ondelete == "RESTRICT" for constraint in foreign_key_targets.values()
    )
    assert unique_constraints["uq_external_transactions_provider_identity"] == (
        "user_id",
        "provider_transaction_id",
    )
    assert unique_constraints["uq_external_transactions_pending_replacement"] == (
        "user_id",
        "pending_provider_transaction_id",
    )


def test_external_transaction_checks_status_and_provider_identity_values() -> None:
    check_names = {
        constraint.name
        for constraint in ExternalTransactionModel.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert check_names == {
        "ck_external_transactions_not_self_pending_replacement",
        "ck_external_transactions_only_pending_can_be_replaced",
        "ck_external_transactions_pending_has_no_replacement_source",
        "ck_external_transactions_provider_id_nonempty",
        "ck_external_transactions_status",
    }


def test_account_and_external_transaction_relationship_is_bidirectional() -> None:
    account_relationship = inspect(ExternalTransactionModel).relationships["account"]
    transaction_relationship = inspect(FinancialAccountModel).relationships[
        "transactions"
    ]

    assert account_relationship.mapper.class_ is FinancialAccountModel
    assert account_relationship.back_populates == "transactions"
    assert account_relationship.uselist is False
    assert transaction_relationship.mapper.class_ is ExternalTransactionModel
    assert transaction_relationship.back_populates == "account"
    assert transaction_relationship.uselist is True
