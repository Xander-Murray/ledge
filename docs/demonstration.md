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

1. Demonstrate the same specific pending-to-posted identity in live Sandbox output
   and inspect its committed history. Record actual results, not invented counts.
2. Add a thin dashboard for current activity, correction history and sync health.
3. Connect a verified Plaid notification to the worker and test that whole route.
4. Collect repeatable latency distributions, workload sizes, duplicate outcomes
   and recovery results, with environment and input provenance attached.
5. Add transaction dates and a balance baseline before implementing recurring
   charges, 30-day projections and safe-to-spend. Transaction sums alone are not
   bank balances. These README product goals remain unfinished.
6. Revisit minimal deployment after the local demonstration is understandable.

S3 archival, Terraform, custom observability dashboards and complex AWS networking
are outside this completion plan. SQS is implemented; Lambda code is prepared but
deployment is paused. Database hosting remains undecided. Do not add a service
unless its role is necessary and explainable. No production-scale claim follows
from passing tests or a few Sandbox refreshes.
