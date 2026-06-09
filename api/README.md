# api/ — the control plane (the moat)

Every agent action flows through one uniform pipeline, so control is uniform, auditable, and
human-governed. This is the thing GoHighLevel structurally can't do.

```
propose -> validate (kill-switch + compliance) -> autonomy check -> [Greenlight if needed] -> execute -> trace
```
Read-only actions skip Greenlight but are still validated and traced. Exactly one trace per run.

## control/
- `gate.py` — `ActionGate.run(action, ctx)`: the single path. Side-effecting actions never execute
  unless autonomy says AUTO (within compliance + thresholds) or a human approves.
- `autonomy.py` — levels **L0–L3** (suggest-only / ask-first / act-within-limits / fully-autonomous).
  L2 uses thresholds (value-at-stake, discount). `resolve(agent, tenant)` + `decide(level, action)`.
- `greenlight.py` — the HITL queue over the `approvals` table. Conforms to the `Greenlight` protocol
  in `agents/tools/base.py`, so Phase 4 tools route their proposals straight in (proven by
  `tests/integration/test_control_tool_seam.py`). Maps to the MA `user.tool_confirmation` reply
  (authored + flagged verify; never called live).
- `compliance.py` — runs **before** Greenlight; a hard fail never reaches the queue. TCPA (consent +
  quiet hours for SMS), CAN-SPAM (unsubscribe for email), plus an injected LLM-critic hook for
  regulated verticals.
- `traces.py` — per-step decision traces (agent, tool, minimized inputs, summarized outputs, reasoning,
  tokens) → the `traces` table; powers the "why I did this" UI.
- `killswitch.py` — per-tenant + global pause the gate checks before any execute (live: also
  `user.interrupt`, flagged verify).

## Autonomy levels
| Level | Behavior |
|---|---|
| L0 | Suggest only — everything → Greenlight |
| L1 | Ask first — side-effecting → Greenlight |
| L2 | Act within limits — auto under thresholds; above → Greenlight |
| L3 | Fully autonomous — acts + reports; only flagged cases pause |

## Test
```bash
pytest tests/unit/test_autonomy.py tests/unit/test_gate.py tests/unit/test_greenlight_queue.py \
       tests/unit/test_killswitch.py tests/unit/test_compliance.py \
       tests/integration/test_control_tool_seam.py -q
```

## HTTP surface (Phase 9)
`app.py` + `auth.py` are the FastAPI control plane (`create_app(deps)` for testability).
**THE TRUST RULE:** every authed route derives the tenant ONLY from the verified Cognito JWT
`custom:tenant_id` claim (`auth.make_current_tenant`) — never from a header/body. Routes:
`GET /healthz`, `GET /approvals` + `POST /approvals/{id}/decide`, `GET/POST /views` +
`GET /views/{id}` + `POST /views/{id}/refine`, `POST /chat` (conv.session), `POST /actions`
(runs through `control/gate`). Two-tenant isolation is proven at the HTTP layer in
`tests/integration/test_api_auth.py`. Live Cognito JWKS verification + the org API key for sessions
are authored + flagged verify — BLOCKED: needs Nick.
