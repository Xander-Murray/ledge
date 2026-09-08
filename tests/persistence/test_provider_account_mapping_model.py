from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, inspect

from persistence.models import (
    FinancialAccountModel,
    ProviderAccountMappingModel,
    TransactionSyncStateModel,
)


def test_provider_account_mapping_has_durable_identity_fields() -> None:
    columns = ProviderAccountMappingModel.__table__.c

    assert set(columns.keys()) == {
        "id",
        "user_id",
        "transaction_sync_state_id",
        "financial_account_id",
        "provider_account_id",
        "created_at",
    }
    assert all(not column.nullable for column in columns)
    assert columns.created_at.server_default is not None


def test_provider_account_mapping_enforces_ownership_and_uniqueness() -> None:
    table = ProviderAccountMappingModel.__table__
    foreign_keys = {
        tuple(column.name for column in constraint.columns): tuple(
            element.target_fullname for element in constraint.elements
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert foreign_keys[("transaction_sync_state_id", "user_id")] == (
        "transaction_sync_states.id",
        "transaction_sync_states.user_id",
    )
    assert foreign_keys[("financial_account_id", "user_id")] == (
        "financial_accounts.id",
        "financial_accounts.user_id",
    )
    assert unique_constraints["uq_provider_account_mappings_provider_account"] == (
        "transaction_sync_state_id",
        "provider_account_id",
    )
    assert unique_constraints["uq_provider_account_mappings_financial_account"] == (
        "financial_account_id",
    )
    assert check_names == {
        "ck_provider_account_mappings_provider_account_id_nonempty"
    }


def test_provider_mapping_relationships_are_bidirectional() -> None:
    mapping_relationships = inspect(ProviderAccountMappingModel).relationships

    assert (
        mapping_relationships["sync_state"].mapper.class_
        is TransactionSyncStateModel
    )
    assert mapping_relationships["sync_state"].back_populates == "account_mappings"
    assert (
        mapping_relationships["financial_account"].mapper.class_
        is FinancialAccountModel
    )
    assert (
        mapping_relationships["financial_account"].back_populates
        == "provider_mappings"
    )
