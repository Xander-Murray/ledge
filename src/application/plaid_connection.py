"""Application service for turning a Plaid Sandbox Item into Ledge records."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from persistence.models import (
    FinancialAccountModel,
    ProviderAccountMappingModel,
    TransactionSyncStateModel,
    UserModel,
)
from providers.plaid_sandbox import PlaidAccount, PlaidSandboxClient


class PlaidConnectionExistsError(RuntimeError):
    """The user already owns a Plaid connection."""


class NoSupportedPlaidAccountsError(RuntimeError):
    """The Plaid Item has no account types supported by this Ledge MVP."""


@dataclass(frozen=True, slots=True)
class PlaidConnectionResult:
    item_id: str
    access_token: str
    sync_state_id: UUID
    imported_account_count: int
    skipped_account_count: int


class PlaidSandboxConnector:
    """Create one Plaid Item and atomically persist its local identities."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        plaid: PlaidSandboxClient,
    ) -> None:
        self._session_factory = session_factory
        self._plaid = plaid

    def connect(
        self,
        *,
        user_id: UUID,
        institution_id: str,
    ) -> PlaidConnectionResult:
        self._prepare_user(user_id)
        connection = self._plaid.create_connection(institution_id=institution_id)
        supported_accounts = tuple(
            (account, account_type)
            for account in connection.accounts
            if (account_type := ledge_account_type(account)) is not None
        )
        if not supported_accounts:
            raise NoSupportedPlaidAccountsError(
                "Plaid Item contains no supported checking, savings, or credit accounts"
            )

        sync_state_id = uuid4()
        with self._session_factory() as session, session.begin():
            session.add(
                TransactionSyncStateModel(
                    id=sync_state_id,
                    user_id=user_id,
                    provider_name="plaid",
                    provider_connection_id=connection.item_id,
                    cursor=None,
                )
            )
            for account, account_type in supported_accounts:
                account_id = uuid4()
                session.add(
                    FinancialAccountModel(
                        id=account_id,
                        user_id=user_id,
                        name=account.name,
                        account_type=account_type,
                    )
                )
                session.add(
                    ProviderAccountMappingModel(
                        id=uuid4(),
                        user_id=user_id,
                        transaction_sync_state_id=sync_state_id,
                        financial_account_id=account_id,
                        provider_account_id=account.provider_account_id,
                    )
                )

        return PlaidConnectionResult(
            item_id=connection.item_id,
            access_token=connection.access_token,
            sync_state_id=sync_state_id,
            imported_account_count=len(supported_accounts),
            skipped_account_count=len(connection.accounts) - len(supported_accounts),
        )

    def _prepare_user(self, user_id: UUID) -> None:
        with self._session_factory() as session, session.begin():
            existing_connection = session.scalar(
                select(TransactionSyncStateModel.id).where(
                    TransactionSyncStateModel.user_id == user_id,
                    TransactionSyncStateModel.provider_name == "plaid",
                )
            )
            if existing_connection is not None:
                raise PlaidConnectionExistsError(
                    f"User {user_id} already has a Plaid connection"
                )
            if session.get(UserModel, user_id) is None:
                session.add(UserModel(id=user_id))


def load_plaid_account_ids(
    session: Session,
    *,
    user_id: UUID,
    item_id: str,
) -> dict[str, UUID]:
    """Load the account translation required by PlaidTransactionProvider."""
    rows = session.execute(
        select(
            ProviderAccountMappingModel.provider_account_id,
            ProviderAccountMappingModel.financial_account_id,
        )
        .join(ProviderAccountMappingModel.sync_state)
        .where(
            ProviderAccountMappingModel.user_id == user_id,
            TransactionSyncStateModel.provider_name == "plaid",
            TransactionSyncStateModel.provider_connection_id == item_id,
        )
    )
    return {
        provider_account_id: financial_account_id
        for provider_account_id, financial_account_id in rows.tuples()
    }


def ledge_account_type(account: PlaidAccount) -> str | None:
    if account.account_type == "depository" and account.account_subtype in {
        "checking",
        "savings",
    }:
        return account.account_subtype
    if account.account_type == "credit":
        return "credit"
    return None
