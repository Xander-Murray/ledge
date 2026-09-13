"""Small read-only dashboard over the same durable records as the API."""

from html import escape
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.dependencies import get_current_user_id, get_session
from persistence.models import (
    ExternalTransactionModel,
    InboundEventModel,
    JournalEntryModel,
    TransactionSyncStateModel,
)

router = APIRouter(tags=["dashboard"])


def money(cents: int) -> str:
    return f"{'-' if cents < 0 else ''}${abs(cents) // 100:,}.{abs(cents) % 100:02}"


def page(content: str) -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ledge | Activity & history</title><style>
:root{color-scheme:light;color:#203830;background:#f4f1e8;font:17px Georgia,serif}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#e1e9dc,#faf6ed)}
main{max-width:1100px;margin:auto;padding:40px 24px;min-height:100vh}
header{border-bottom:3px solid #203830;padding-bottom:24px;margin-bottom:32px}
h1{font-size:clamp(36px,7vw,64px);margin:10px 0}h2{margin-top:36px}
a{color:#12634b;text-underline-offset:4px}small,p{line-height:1.6}
.eyebrow,th{font:12px monospace;letter-spacing:2px;text-transform:uppercase}
.panel{background:#ffffffb8;border:1px solid #ccd3c5;padding:20px;margin:16px 0}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}
td,th{text-align:left;padding:14px 10px;border-bottom:1px solid #dce0d4}
td{overflow-wrap:anywhere}th{white-space:nowrap}.amount{white-space:nowrap}
nav{display:flex;gap:24px;margin:24px 0}code{font-size:12px;overflow-wrap:anywhere}
@media(max-width:600px){main{padding:24px 12px}td,th{padding:12px 6px}}
</style><main><header><span class="eyebrow">Ledge / Sandbox</span>
<h1>Your activity, accounted for.</h1><p>Bank updates with a history you can follow.</p>
<a href="/">Activity</a> &nbsp; <a href="/docs">API reference</a></header>"""
        + content
        + "</main></html>"
    )


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    offset: Annotated[int, Query(ge=0)] = 0,
    include_history: bool = False,
) -> HTMLResponse:
    try:
        rows = (
            await session.scalars(
                select(ExternalTransactionModel)
                .where(
                    ExternalTransactionModel.user_id == user_id,
                    True
                    if include_history
                    else ExternalTransactionModel.status == "active",
                )
                .order_by(
                    ExternalTransactionModel.updated_at.desc(),
                    ExternalTransactionModel.id,
                )
                .offset(offset)
                .limit(26)
            )
        ).all()
        states = (
            await session.scalars(
                select(TransactionSyncStateModel).where(
                    TransactionSyncStateModel.user_id == user_id,
                )
            )
        ).all()
        events = (
            await session.scalars(
                select(InboundEventModel)
                .join(TransactionSyncStateModel)
                .where(
                    TransactionSyncStateModel.user_id == user_id,
                )
                .order_by(InboundEventModel.received_at.desc(), InboundEventModel.id)
                .limit(10)
            )
        ).all()
    except SQLAlchemyError as error:
        raise HTTPException(503, "database unavailable") from error
    activity = (
        "".join(
            f'<tr><td><a href="/activity/{row.id}">{escape(row.description)}</a></td>'
            f'<td class="amount">{money(row.amount_cents)}</td>'
            f"<td>{escape(row.status)} / "
            f"{'Pending' if row.is_pending else 'Posted'}</td></tr>"
            for row in rows[:25]
        )
        or '<tr><td colspan="3">No current activity on this page.</td></tr>'
    )
    sync = (
        "".join(
            f"<p>{escape(state.provider_name)}: "
            f"{'Cursor saved' if state.cursor is not None else 'Awaiting first sync'}"
            f" · State updated {escape(str(state.updated_at))}</p>"
            for state in states
        )
        or "<p>No connected accounts yet.</p>"
    )
    attempts = (
        "".join(
            f"<tr><td><code>{event.id}</code></td><td>{escape(event.status)}</td>"
            f"<td>{event.attempt_count}</td>"
            f"<td>{escape(event.last_error_code or '-')}</td></tr>"
            for event in events
        )
        or '<tr><td colspan="4">No notifications received yet.</td></tr>'
    )
    navigation = (
        f'<a href="/?offset={max(0, offset - 25)}">Previous</a>' if offset else ""
    )
    if len(rows) > 25:
        navigation += f'<a href="/?offset={offset + 25}">Next</a>'
    if include_history:
        navigation = navigation.replace("?offset=", "?include_history=true&amp;offset=")
    heading = "Recorded activity" if include_history else "Current activity"
    return page(f"""<h2>{heading}</h2>
<p><a href="/?include_history=true">Include replaced and removed activity</a>
 / <a href="/">Current only</a></p>
<p>Positive amounts are outflows; negative amounts are inflows. USD only.
These are transaction amounts, not account balances.
Ordered by last recorded update.</p>
<div class="scroll"><table><tr><th>Purchase / activity</th>
<th>Amount</th><th>State</th></tr>
{activity}</table></div><nav>{navigation}</nav><h2>Synchronization</h2>
<div class="panel">{sync}</div><h2>Latest 10 notifications</h2>
<p>Attempts include the initial attempt. CLI-only syncs do not create notifications.</p>
<div class="scroll"><table><tr><th>Event</th><th>Outcome</th>
<th>Attempts</th><th>Failure</th></tr>
{attempts}</table></div><p><a href="/">Refresh from database</a></p>""")


@router.get("/activity/{identity}", response_class=HTMLResponse)
async def history(
    identity: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
) -> HTMLResponse:
    try:
        row = await session.scalar(
            select(ExternalTransactionModel).where(
                ExternalTransactionModel.id == identity,
                ExternalTransactionModel.user_id == user_id,
            )
        )
        if row is None:
            raise HTTPException(404, "transaction not found")
        identities = [row.id]
        if row.pending_provider_transaction_id:
            pending = await session.scalar(
                select(ExternalTransactionModel.id).where(
                    ExternalTransactionModel.user_id == user_id,
                    ExternalTransactionModel.provider_transaction_id
                    == row.pending_provider_transaction_id,
                )
            )
            if pending:
                identities.append(pending)
        journals = (
            await session.scalars(
                select(JournalEntryModel)
                .where(
                    JournalEntryModel.external_transaction_id.in_(identities),
                )
                .options(selectinload(JournalEntryModel.postings))
                .order_by(JournalEntryModel.created_at, JournalEntryModel.id)
            )
        ).all()
    except SQLAlchemyError as error:
        raise HTTPException(503, "database unavailable") from error
    cards = ""
    for journal in journals:
        postings = "".join(
            f"<li>{escape(posting.ledger_account)}: {money(posting.amount_cents)}</li>"
            for posting in sorted(journal.postings, key=lambda item: item.line_number)
        )
        cards += (
            '<section class="panel"><h2>'
            f"{'Reversal' if journal.reversal_of_entry_id else 'Entry'}</h2>"
            f"<p>{escape(str(journal.created_at))} "
            f"/ {'Sealed' if journal.sealed_at else 'Unsealed'}</p>"
            f"<code>{journal.id}</code><p>Reverses: "
            f"{journal.reversal_of_entry_id or 'None'}</p>"
            f"<ul>{postings}</ul></section>"
        )
    return page(
        f"<h2>{escape(row.description)}</h2><p>{money(row.amount_cents)} · "
        f"{escape(row.status)} · {'Pending' if row.is_pending else 'Posted'}</p>"
        f"<p>Provider ID: <code>{escape(row.provider_transaction_id)}</code></p>"
        "<p>Replaces pending ID: <code>"
        f"{escape(row.pending_provider_transaction_id or 'None')}</code></p>"
        + (cards or "<p>No journal history recorded.</p>")
    )
