# Deployment checkpoint

Audited 2026-10-02 against commit `b141fe9`. This describes implemented code and
recorded service checks; it does not assert that previously created AWS resources
have been rechecked today.

## Implemented and verified

- FastAPI commits an inbound event before optional SQS publication.
- `LEDGE_EVENT_QUEUE_URL` enables the publisher through the application factory.
- The queue message contains only `event_id` and `schema_version` (currently 1).
- A real local HTTP-to-inbox-to-SQS publication was verified on 2026-09-12.
- `lambda_handler.handler` lazily creates and reuses a worker runtime.
- The runtime loads Plaid credentials and Item identity from Secrets Manager,
  validates durable account mappings, and calls `InboundEventProcessor`.
- Each SQS record is handled independently. The returned `batchItemFailures`
  lists only failed message IDs; the event-source mapping must enable
  `ReportBatchItemFailures` for this to control retries.
- A Python 3.14 x86_64 Linux zip was built and its imports checked locally.
  No deployed Lambda invocation has been recorded.

## Required configuration

| Setting | Purpose |
| --- | --- |
| `LEDGE_DATABASE_URL` | SQLAlchemy PostgreSQL URL using `postgresql+psycopg://` |
| `LEDGE_USER_ID` | User owning the configured Item and account mappings |
| `LEDGE_PLAID_SECRET_ID` | Secrets Manager secret name or ARN |
| AWS region | Region used by the SDK's standard configuration chain |

The Plaid secret JSON has exactly four fields: `client_id`, `secret`,
`access_token`, `item_id`. The Item must match the connection stored in the
database. A token from another local Sandbox profile will not work merely
because it is a valid Plaid token. The database URL currently comes from the
environment; secure database credential delivery remains deployment work.

The local `ledge-dev` profile was created for SQS publishing. It does not have
general deployment permissions; an earlier read-only VPC inspection was denied.
Lambda will use an execution role rather than those local access keys.

## Remaining steps

1. Finalize low-cost database hosting. Neon was recommended; neither a Neon
   project nor an RDS deployment has been confirmed. Localhost PostgreSQL is
   not reachable from deployed Lambda.
2. Set up schema and connection/account mappings in the hosted database. Keep
   disposable integration tests local; their fixtures rebuild the schema.
3. Configure credential storage, TLS, and bounded database connections.
4. Create the Lambda execution role with queue consumption, the specific secret,
   and logging permissions. Select Python 3.14, x86_64 and the packaged handler.
5. Verify a manual invocation and provider/database connectivity before enabling
   queue consumption. Align Lambda timeout, SQS visibility and processing leases;
   constrain initial concurrency and enable partial batch failures.
6. Run acceptance through SQS/Lambda, including duplicates, failed attempts,
   retries and dead-letter handling. Preserve input provenance and measured
   timings. The current local command directly invokes the processor and cannot
   verify cloud transport.

EC2 hosting for the API/dashboard is an eventual option, not a deployed capability.
S3 archival, Terraform and custom CloudWatch dashboards are outside current scope.
