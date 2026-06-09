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
`build_context(session_metadata, clients)` sets `app.current_tenant` from the session metadata before
any DB/Cube call, so Postgres RLS applies while tools run.

## Status
`worker.py` is authored and import-safe (no network on import; `anthropic` imported lazily). `run()` is
**not** executed against real Anthropic in this build — BLOCKED: needs Nick (env id/key + beta verify).
Verify connectivity once live: `ant beta:environments:work stats --environment-id "$ENV_ID"`
(expect `workers_polling >= 1`).
