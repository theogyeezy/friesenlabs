# worker/ — self-hosted tool-execution worker

Polls the Managed Agents environment queue and executes the custom tools **in your VPC**. Deployed as
an ECS Fargate service in private subnets (`infra/modules/worker`): SG_API to reach Aurora/Cube/Redis,
outbound 443 to api.anthropic.com.

## Two credentials, never confused
- The **environment key** lives here (from Secrets Manager → `UPLIFT_ENV_KEY`) and authenticates the
  worker to the queue.
- The **org API key** creates sessions/agents and reads stats — it must **never** be present on this
  host, or you'd expose an org-scoped credential to tool execution.
- On Claude Platform on AWS, worker auth is IAM SigV4 instead of an env key.

## Tenant isolation during tool exec
The SDK's `tools=` factory is the per-session seam (its `EnvironmentWorker` has **no**
`context_factory` kwarg — verified, see the ratified brief below): `session_tools_factory(clients)`
wraps the tool registry per CLAIMED SESSION; on the first tool call the binding fetches the session's
metadata (`sessions.retrieve` under the environment key — VERIFY on first live run) and every call
gets a fresh `build_context(session_metadata, clients)` that sets `app.current_tenant` before any
DB/Cube call, so Postgres RLS applies while tools run.

## Liveness heartbeat
`heartbeat_loop()` emits `Uplift/Agents:workers_polling=1` every 30s (`WORKER_HEARTBEAT_SECONDS`
to tune; gated on `CLOUDWATCH_METRICS=1`) as a sibling task of the SDK poll loop — never piggybacked
on the tools callable, which fires once per claimed session, not per poll
(`docs/decisions/workers-polling-heartbeat-assumption.md`, ratified #123, Option A). Emit failures
log-and-continue; the heartbeat is cancelled with the poll loop, so missing metric ⇔ worker down
(the `worker_absent` alarm's `treat_missing_data=breaching` contract).

## Status
`worker.py` is authored and import-safe (no network on import; `anthropic`/`boto3` imported lazily).
`run()` is **not** executed against real Anthropic in this build — BLOCKED: needs Nick (env id/key +
beta verify; the worker service deploy is Lane Nick's flip).
Verify connectivity once live: `ant beta:environments:work stats --environment-id "$ENV_ID"`
(expect `workers_polling >= 1`).
