<!-- Decision brief — produced by the QA+DECISIONS lane (2026-06-10).
     Research: parallel agents over repo code + current Anthropic docs; claims adversarially
     spot-checked by an independent critic agent. Status: DRAFT until ratified by Nick + Matt. -->

# BRIEF: The `workers_polling` heartbeat assumption is wrong — and `run()` has a second startup-fatal bug

## Context (what the code does today)

**The heartbeat.** `worker/worker.py:36-52` defines `emit_polling_metric()`: when `CLOUDWATCH_METRICS=1` (set on the worker task def by REQ-001, `infra/modules/worker/main.tf:75`), it does a `PutMetricData` of `Uplift/Agents:workers_polling = 1`. It is wired in two places inside `run()`:

- `worker/worker.py:149` — one explicit emit at startup ("so the alarm clears as soon as we start serving").
- `worker/worker.py:138-142` — `_tools_for_poll(env)`, passed as `tools=` to `EnvironmentWorker` at line 156, with the comment: *"The SDK requests the tool list each poll iteration; piggyback the heartbeat metric here."*

**The alarm.** `infra/modules/observability/main.tf:93-105` (`worker_absent`): `workers_polling` Maximum < 1 over **2 × 60s periods**, `treat_missing_data = "breaching"` — missing data *means* "no worker" by design. It is count-gated behind `var.worker_deployed` (default `false`, lines 88-91), and per `CLAUDE.md` the worker is not yet deployed (parked on the Console-generated env key). REQ-001 (`infra/REQUESTS.md:49-81`) is DONE @6d5a210; the dashboard also charts this metric (`observability/main.tf:166-173`).

**What the SDK actually does.** The repo venv has `anthropic` 0.109.0 (`requirements-api.lock.txt:39` pins 0.109.1) and ships the real implementation at `.venv/lib/python3.14/site-packages/anthropic/lib/environments/`:

- The poll loop is `aiter_work()` (`_poller.py:189-266`): `work.poll(block_ms=999)` long-poll, 1–3s jittered sleep on empty, ack on claim. **Nothing in the poll path touches the tools callable.**
- The tools callable is invoked at `_worker.py:448` — `tools = self._tools_for(env)` — inside `_handle_item()`, i.e. **once per claimed session work item**, after claim+ack, with that session's `AgentToolContext`. The `EnvironmentWorkerTools` type doc (`_worker.py:72-78`) says it outright: *"a factory invoked once per claimed session."* The claude-api skill's self-hosted-sandboxes doc matches.

So the comment at `worker.py:139` is **false for the SDK we ship**. Consequence on an idle queue (no chat sessions — i.e. most nights, and all of the pre-launch period): the metric is emitted **exactly once at startup, then never again**. ~2–3 minutes later the alarm fires and stays in ALARM until a session happens to be claimed. The alarm becomes a tenant-traffic detector, not a worker-death detector — permanent false positives, SNS noise to the confirmed email subscriber, and no remaining signal when the worker actually dies.

**The second bug, same function, worse.** `worker.py:160` passes `context_factory=_context_for` to `EnvironmentWorker(...)`. The SDK constructor (`_worker.py:253-266`) accepts `client, environment_id, environment_key, tools, workdir, unrestricted_paths, max_file_bytes, max_idle, worker_id, extra_headers` — **there is no `context_factory` kwarg**. `run()` raises `TypeError` at construction. The repo's own `# VERIFY` comment (lines 157-159) flagged this; verified against the installed SDK: it does not exist. The perverse interaction: line 149's emit runs *before* the crash, so a crash-looping ECS task emits one heartbeat per restart — a dead-on-arrival worker intermittently feeds the very metric that's supposed to prove it's serving. Depending on Fargate restart cadence (~1–3 min) the alarm flaps instead of firing clean. Note `tests/` has zero coverage of `emit_polling_metric` / `_tools_for_poll`, so nothing catches either bug offline.

## Options

### Option A — Explicit in-process heartbeat task, alarm unchanged (recommended)
Drop the piggyback. In `run()`, start an asyncio task alongside `worker.run()` (e.g. `asyncio.gather` / TaskGroup) that emits `workers_polling=1` every **30s** for as long as the poll-loop task is alive; structured concurrency guarantees the heartbeat dies when `run()` dies (TypeError, fatal 4xx from `aiter_work`, cancelled task). Semantics become exactly what the alarm promises: *metric present ⇔ worker process up and its poll loop not crashed*. Move the boto3 client out of the per-emit path (create once), wrap the emit in try/except so a CloudWatch blip can't kill the worker, and run it as `asyncio.to_thread` (boto3 is blocking) — total ~20 lines in `worker/worker.py`, zero infra change, alarm and dashboard keep working as written.
- **Cost:** 1 custom metric ($0.30/mo) + ~86K PutMetricData/mo (~$0.86) ≈ **$1/mo** — already budgeted in `shared/COST.md`.
- **Risk:** Low. The one semantic gap: a worker whose event loop is alive but whose connectivity to api.anthropic.com is black-holed keeps heartbeating while serving nothing. (Mitigable later: gate the emit on "last poll attempt < 90s ago" by wrapping the iterator, or layer Option C.)
- **Effort:** ~half a day including a unit test that fakes the SDK and asserts the heartbeat is poll-loop-independent. **Must ship together with deleting `context_factory=`** — without that, `run()` doesn't start at all (see Recommendation for where tenant binding goes instead).

### Option B — Alarm on ECS `RunningTaskCount` instead of a custom metric
Delete the heartbeat entirely; alarm on Container Insights `RunningTaskCount < 1` for the `uplift-worker` service (Insights is already on the cluster per `observability/main.tf:2-3`). Zero app code, zero custom-metric cost, and the deployment circuit breaker (`worker/main.tf:125-128`) already auto-rolls-back bad task defs.
- **Cost:** $0 incremental.
- **Risk:** Measures "Fargate says a container exists," not "worker is polling." A worker wedged in Python (deadlock, env-key revoked → fatal 401 only after the task somehow stays up, crash-loop where the task is 'running' most of each cycle) looks healthy. It's strictly weaker than what REQ-001/the Build Guide intended the alarm to mean.
- **Effort:** ~1 hour of Terraform.

### Option C — Probe Anthropic's authoritative server-side count
`GET /v1/environments/{env_id}/work/stats` returns a server-side `workers_polling` field — almost certainly where the repo's metric name came from (`worker/README.md:21-22` already says to verify liveness with `ant beta:environments:work stats`). A 1-minute EventBridge-scheduled probe (Lambda, or a thread in the API task which already holds the org key per REQ-001's asymmetry rule — the worker must never make this call) republishes Anthropic's count into `Uplift/Agents:workers_polling`. This is the only option that measures the **end-to-end truth**: worker → network → Anthropic queue, as Anthropic sees it.
- **Cost:** ~$0 (Lambda free tier) + the same ~$1/mo metric.
- **Risk:** New moving part that can itself die (now you need an alarm on the prober — `treat_missing_data=breaching` covers it, but it's turtles); puts org-key-bearing code on another runtime if done as a Lambda; depends on a beta endpoint's response shape staying stable.
- **Effort:** ~1–2 days including IAM + REQ for the schedule.

## Recommendation

**Ship Option A now, in the same PR that deletes the `context_factory` kwarg — both are blockers for the worker deploy, and the deploy is already parked on the env key so the window is open.** A is the only option that keeps the alarm's intended meaning ("a worker is alive and polling") with a code-only change, and the heartbeat-dies-with-the-loop property comes free from structured concurrency rather than from a hook whose call cadence we misread once already. B is a downgrade dressed as simplification; C is the right *second* layer once there's real tenant traffic, not the first.

For the tenant-binding flow that `context_factory` was trying to do: the SDK's supported seam is the **tools factory itself** — `_tools_for_poll(env)` already receives the per-session `AgentToolContext` (carrying `session_id` and an env-key-scoped client). Build the per-session `ToolContext` there (fetch session metadata by `session_id`, then `build_context(...)` as today) and return tenant-bound tool wrappers. That keeps the THE-TRUST-RULE path (tenant from API-stamped session metadata) intact without inventing SDK kwargs — but verify the session-retrieval call works under the environment key on first live run, exactly as `CLAUDE.md` hard-constraint #4 demands. Leave `worker_deployed=false` until the fixed worker has run against the live queue and the dashboard shows a flat `workers_polling=1` line through an idle hour.

## What would flip this

- **A future SDK version that documents per-poll tool resolution or a real per-invocation context hook** — recheck `lib/environments/_worker.py` on every `anthropic` bump (the repo floats `>=0.40` in `requirements-api.txt:5`; only the lock pins 0.109.1). If a `context_factory`-equivalent lands, the piggyback design becomes legal again — though A is still simpler.
- **Moving the worker to Claude Platform on AWS** (the HIPAA/Bedrock seam in `agents/runtime.py`): self-hosted sandboxes are *not available* there, so the whole worker+heartbeat construct disappears and this brief is moot.
- **Webhook-driven wake replacing the always-on loop** (`run_one()` per `session.status_run_started`): "polling" stops being the liveness invariant; the right alarm becomes webhook-delivery failures + Option C's queue-depth/oldest-queued-at, and A's heartbeat should be retired rather than faked.
- **Evidence the stats endpoint is cheap and stable in beta** would promote C from "later layer" to "primary," with A kept only as the fast-twitch (60s vs probe-interval) signal.

## Sources

- `worker/worker.py:36-52, 138-161` — heartbeat, the false per-poll comment, the `context_factory` kwarg.
- `infra/modules/observability/main.tf:85-105, 166-173` — `worker_absent` alarm + dashboard widget.
- `infra/modules/worker/main.tf:68-97, 117-137` — task-def env (CLOUDWATCH_METRICS=1), service + circuit breaker.
- `infra/REQUESTS.md:49-81` (REQ-001), `worker/README.md`, `CLAUDE.md` (worker parked on env key; runtime-seam hard constraint #4).
- **SDK source (ground truth, installed in repo venv, anthropic 0.109.0; lock pins 0.109.1):** `.venv/lib/python3.14/site-packages/anthropic/lib/environments/_worker.py` — `__init__` kwargs (253-266), `EnvironmentWorkerTools` "once per session" doc (72-78), `_tools_for` call site inside `_handle_item` (448); `_poller.py:189-266` — `aiter_work` poll loop (no tools access). N.B. the SDK's own "heartbeat" (`_heartbeat_loop`, `_worker.py:90-151`) is the Anthropic work-item *lease* heartbeat — unrelated to CloudWatch; don't conflate the two when reading the code.
- **claude-api skill** (`shared/managed-agents-self-hosted-sandboxes.md`, API reference): tools factory "invoked once per claimed session with that session's AgentToolContext"; `GET /v1/environments/{id}/work/stats` returns server-side `workers_polling`; control-plane stats calls use the org API key, never from the worker host.


## Critic-noted gaps (non-blocking)
- All six claims verified against the installed SDK and repo: tools callable invoked only at _worker.py:448 inside _handle_item (per claimed work item; _poller.py never touches it); worker.py:138-142 comment and line-149 one-shot emit confirmed; alarm config (period 60, eval 2, <1, treat_missing_data=breaching, count-gated on worker_deployed default false) confirmed at observability/main.tf:88-105; EnvironmentWorker.__init__ (keyword-only, no **kwargs, lines 253-266) has no context_factory → TypeError at construction with line 149 firing first; work/stats returns workers_polling per docs + worker/README.md:21-22.
- Minor: the heartbeat is also gated on CLOUDWATCH_METRICS=1 (set in the task def, so fine) — keep that env var when moving the emit into the run() loop.
- The server-side work/stats workers_polling count requires the org x-api-key and the docs say to call control-plane endpoints from outside the worker host — so layering it onto the alarm means a separate poller (e.g. a scheduled Lambda), not the worker itself.
