from __future__ import annotations

from sqlalchemy import CheckConstraint, DateTime, inspect

from persistence.models import Base, FinancialAccountModel, UserModel


def test_identity_tables_are_registered_with_uuid_primary_keys() -> None:
    assert {"users", "financial_accounts"} <= set(Base.metadata.tables)
    assert tuple(UserModel.__table__.primary_key.columns) == (UserModel.__table__.c.id,)
    assert tuple(FinancialAccountModel.__table__.primary_key.columns) == (
        FinancialAccountModel.__table__.c.id,
    )


def test_user_has_a_database_generated_utc_creation_time() -> None:
    columns = UserModel.__table__.c

    assert set(columns.keys()) == {"id", "created_at"}
    assert isinstance(columns.created_at.type, DateTime)
    assert columns.created_at.type.timezone is True
    assert columns.created_at.nullable is False
    assert columns.created_at.server_default is not None


def test_financial_account_has_required_current_fields() -> None:
    columns = FinancialAccountModel.__table__.c

    assert set(columns.keys()) == {
        "id",
        "user_id",
        "name",
        "account_type",
        "created_at",
    }
    assert columns.user_id.nullable is False
    assert columns.name.nullable is False
    assert columns.account_type.nullable is False
    assert isinstance(columns.created_at.type, DateTime)
    assert columns.created_at.type.timezone is True
    assert columns.created_at.nullable is False
    assert columns.created_at.server_default is not None


def test_financial_account_enforces_ownership_and_valid_values() -> None:
    table = FinancialAccountModel.__table__
    user_id_foreign_keys = tuple(table.c.user_id.foreign_keys)

    assert len(user_id_foreign_keys) == 1
    assert user_id_foreign_keys[0].target_fullname == "users.id"
    assert user_id_foreign_keys[0].ondelete == "RESTRICT"

    indexed_column_sets = {
        tuple(column.name for column in index.columns) for index in table.indexes
    }
    assert ("user_id",) in indexed_column_sets

    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert check_names == {
        "ck_financial_accounts_account_type",
        "ck_financial_accounts_name_nonempty",
    }


def test_user_and_financial_account_relationships_are_bidirectional() -> None:
    user_relationship = inspect(UserModel).relationships["accounts"]
    account_relationship = inspect(FinancialAccountModel).relationships["user"]

    assert user_relationship.mapper.class_ is FinancialAccountModel
    assert user_relationship.back_populates == "user"
    assert user_relationship.uselist is True
    assert account_relationship.mapper.class_ is UserModel
    assert account_relationship.back_populates == "accounts"
    assert account_relationship.uselist is False
