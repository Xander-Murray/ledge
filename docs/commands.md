# Commands and database guide

This is the quick reference for running Ledge locally. Run commands from the
repository root. The virtual environment is expected at `venv/`.

## Load local configuration

`.env` is ignored and contains local credentials. Load it before running Plaid
or database commands:

```bash
set -a
source .env
set +a
```

The current dynamic Sandbox profile uses `.ledge/plaid-dynamic.json` and the
matching isolated `LEDGE_USER_ID` in `.env`.

## PostgreSQL and migrations

Start or inspect PostgreSQL:

```bash
docker compose up -d postgres
docker compose ps
```

Apply all migrations:

```bash
venv/bin/alembic upgrade head
```

Create a migration after intentionally changing SQLAlchemy models:

```bash
venv/bin/alembic revision --autogenerate -m "describe schema change"
```

Review generated migrations before applying them. Check the current revision
with `venv/bin/alembic current` and history with `venv/bin/alembic history`.

## Plaid Sandbox

Create a Sandbox Item and persist its protected access token. The output file
must not already exist:

```bash
venv/bin/ledge-plaid-sandbox --dynamic-transactions
```

Use a separate profile without overwriting an existing token:

```bash
venv/bin/ledge-plaid-sandbox \
  --dynamic-transactions \
  --token-file .ledge/plaid-dynamic-2.json \
  --user-id dddddddd-dddd-dddd-dddd-dddddddddddd
```

Select a token file for synchronization:

```bash
export LEDGE_PLAID_TOKEN_FILE=.ledge/plaid-dynamic.json
```

Run one cursor-based synchronization:

```bash
venv/bin/ledge-plaid-sync
```

Run repeated cycles. `--refresh-between` asks Plaid Sandbox to simulate an
institution refresh before later cycles; Plaid may still return no changes.

```bash
venv/bin/ledge-plaid-sync \
  --iterations 3 \
  --interval 10 \
  --refresh-between
```

The output reports provider pages, added/modified/removed counts, cursor
status, and elapsed time. `+0 ~0 -0` with an unchanged cursor means Plaid
reported no new changes; it is not reading stale data from the database.

## Tests and checks

Run the complete PostgreSQL-backed test suite:

```bash
LEDGE_TEST_DATABASE_URL='postgresql+psycopg://ledge:ledge_local@localhost:5432/ledge_test' \
  venv/bin/pytest -q
```

Run one test with a full traceback:

```bash
venv/bin/pytest -vv -x --tb=long --showlocals path/to/test.py
```

Run static checks:

```bash
venv/bin/ruff check .
venv/bin/ruff format --check .
```

## FastAPI

Start the development API:

```bash
venv/bin/uvicorn api.app:create_app --factory --reload
```

Open `http://127.0.0.1:8000/docs` for interactive OpenAPI documentation.
Useful read endpoints include `/accounts`, `/transactions`, and `/sync-status`.

## View PostgreSQL data

Open an interactive SQL session inside the container:

```bash
docker compose exec postgres psql -U ledge -d ledge
```

Useful `psql` commands:

```sql
\dt
\d external_transactions
SELECT COUNT(*) FROM external_transactions;
SELECT COUNT(*) FROM journal_entries;
SELECT COUNT(*) FROM postings;
SELECT provider_connection_id, cursor FROM transaction_sync_states;
\q
```

Run a one-off query without entering the interactive shell:

```bash
docker compose exec postgres psql -U ledge -d ledge \
  -c "SELECT provider_transaction_id, amount_cents, is_pending, status FROM external_transactions ORDER BY created_at DESC LIMIT 10;"
```

Important tables:

- `external_transactions`: current provider transaction projection.
- `journal_entries` and `postings`: immutable double-entry accounting history.
- `transaction_sync_states`: one committed cursor per provider connection.
- `provider_account_mappings`: Plaid account IDs mapped to Ledge accounts.
- `inbox_events`: durable webhook events and processing status.

