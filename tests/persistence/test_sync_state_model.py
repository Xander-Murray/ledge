from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    UniqueConstraint,
    inspect,
)

from persistence.models import TransactionSyncStateModel, UserModel


def test_sync_state_has_durable_provider_cursor_fields() -> None:
    columns = TransactionSyncStateModel.__table__.c

    assert set(columns.keys()) == {
        "id",
        "user_id",
        "provider_name",
        "provider_connection_id",
        "cursor",
        "created_at",
        "updated_at",
    }
    assert columns.user_id.nullable is False
    assert columns.provider_name.nullable is False
    assert columns.provider_connection_id.nullable is False
    assert columns.cursor.nullable is True
    for column in (columns.created_at, columns.updated_at):
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True
        assert column.nullable is False
        assert column.server_default is not None


def test_sync_state_enforces_owner_and_provider_connection_identity() -> None:
    table = TransactionSyncStateModel.__table__
    user_foreign_keys = tuple(table.c.user_id.foreign_keys)
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
    indexed_column_sets = {
        tuple(column.name for column in index.columns) for index in table.indexes
    }

    assert len(user_foreign_keys) == 1
    assert user_foreign_keys[0].target_fullname == "users.id"
    assert user_foreign_keys[0].ondelete == "RESTRICT"
    assert unique_constraints["uq_transaction_sync_states_provider_connection"] == (
        "provider_name",
        "provider_connection_id",
    )
    assert check_names == {
        "ck_transaction_sync_states_provider_connection_id_nonempty",
        "ck_transaction_sync_states_provider_name_nonempty",
    }
    assert ("user_id",) in indexed_column_sets


def test_user_and_sync_state_relationships_are_bidirectional() -> None:
    user_sync_states = inspect(UserModel).relationships["transaction_sync_states"]
    sync_state_user = inspect(TransactionSyncStateModel).relationships["user"]

    assert user_sync_states.mapper.class_ is TransactionSyncStateModel
    assert user_sync_states.back_populates == "user"
    assert user_sync_states.uselist is True
    assert sync_state_user.mapper.class_ is UserModel
    assert sync_state_user.back_populates == "transaction_sync_states"
    assert sync_state_user.uselist is False
