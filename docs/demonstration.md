# Demonstration and completion plan

Lambda deployment is paused. Preserve the current worker implementation for
later review; the immediate priority is observable financial correctness.

## Two complementary forms of evidence

Real Plaid Sandbox exercises prove the external API integration. Run with the
existing dynamic Item and configured environment:

```bash
venv/bin/ledge-plaid-sync --iterations 3 --interval 10 --refresh-between --details
```

The first iteration polls the saved cursor. Later iterations request a Sandbox
refresh. Repeated added/removed counts do not imply identical transaction IDs.
Details show committed identities, integer-cent amounts, pending replacement
links, statuses, and journal/posting counts before and after each cycle. An
unchanged poll should produce no financial changes. These snapshots are local
diagnostics: concurrent writers can affect the comparison. They read the mapped
accounts' history and are intended for modest Sandbox datasets, not load tests.
Output contains transaction identifiers; keep evidence private before sharing.

Controlled PostgreSQL scenarios prove specific failure and recovery behavior:

```bash
LEDGE_TEST_DATABASE_URL='postgresql+psycopg://ledge:ledge_local@localhost:5432/ledge_test' \
venv/bin/pytest -q -s tests/application/test_event_processing.py::test_visible_reconciliation_evidence
```

WARNING: this uses the existing integration fixture, which rebuilds the schema.
Use only the disposable database ending in `_test`, never the working database.
Do not run concurrent integration suites against that database.

The scenario verifies a pending $40 restaurant charge, its posted $48 replacement,
duplicate event delivery, failure after a $45 correction is staged, complete
rollback, successful retry, removal, and a final unchanged poll. It checks exact
journal counts, zero-sum postings, sealed history, cursor state, and retry count.
Assertions cause a nonzero exit if an expectation fails. Printed timings measure
this local controlled run; they are not production throughput measurements.

The CLI bypasses HTTP intake, SQS, and Lambda. The controlled scenario starts at
the durable inbox and uses simulated provider pages. Neither is proof of the
complete deployed webhook route. Existing HTTP tests cover intake separately.

## Remaining completion criteria

### Connected local Sandbox acceptance

Load `.env` in both terminals. Start the API with queue publishing disabled for
this local worker experiment, so no competing cloud consumer processes the event:

```bash
LEDGE_EVENT_QUEUE_URL='' venv/bin/uvicorn api.app:create_app --factory
```

In the second terminal, using the same database/user/Item configuration:

```bash
LEDGE_PLAID_TOKEN_FILE=.ledge/plaid-dynamic.json \
venv/bin/python -m commands.plaid_acceptance --interval 10
```

This writes real Sandbox changes into the working database and requests one
Plaid refresh. Run without other workers/sync commands. It sends each manually
constructed normalized notification twice through HTTP, requires the same inbox
identity, processes and reprocesses that event, and compares committed state.
It fails if no pending-to-posted replacement is observed after refreshing. It
does not pretend to receive a signed Plaid webhook or verify AWS transport.
Use `--api-url` only for a trusted API connected to the same database and user.
The failure-injection scenario above remains a separate controlled experiment.

Verified local run on 2026-09-12: two distinct notifications each submitted twice
returned the same respective inbox identity. Reprocessing each event left the
financial snapshot unchanged. A real Sandbox refresh produced six observed
pending-to-posted replacements. Journals grew from 389 to 407 and postings from
778 to 814 for that refresh; worker duration was 583.434 ms. The initial catch-up
worker took 834.360 ms. These are two local observations, not latency percentiles
or AWS measurements. The default token belonged to a different user; this run
explicitly selected the existing dynamic token matching the configured user.

1. Demonstrate the same specific pending-to-posted identity in live Sandbox output
   and inspect its committed history. Record actual results, not invented counts.
2. Implemented: a thin dashboard at `/` for current activity, correction history
   and sync health. Removed/replaced rows are available through the history filter.
3. Connect a verified Plaid notification to the worker and test that whole route.
4. Collect repeatable latency distributions, workload sizes, duplicate outcomes
   and recovery results, with environment and input provenance attached.
5. Add transaction dates and a balance baseline before implementing recurring
   charges, 30-day projections and safe-to-spend. Transaction sums alone are not
   bank balances. These README product goals remain unfinished.
6. Next: finish minimal Lambda deployment and repeat acceptance through SQS,
   including real redelivery. The local command does not verify cloud behavior.

S3 archival, Terraform, custom observability dashboards and complex AWS networking
are outside this completion plan. SQS is implemented; Lambda code is prepared but
deployment is paused. Database hosting remains undecided. Do not add a service
unless its role is necessary and explainable. No production-scale claim follows
from passing tests or a few Sandbox refreshes.
