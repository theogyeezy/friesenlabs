# Brief: Phase 9 — App, Auth & API

## Goal
The control-plane API behind every surface: Cognito multi-tenant auth, a FastAPI app (on Fargate
behind an ALB) that owns JWT verification + Greenlight/approvals endpoints + view CRUD + agent-session
orchestration + the action-gate pipeline, and the React front end wired to it. Author + validate IaC
(no apply); build + test the FastAPI app offline with a mock JWT verifier.

## THE TRUST RULE (security-critical — I own this)
The API validates the JWT signature against the pool's JWKS and reads `custom:tenant_id`. **That claim
— never a header or request body — is the only source of tenant identity.** It is what gets pushed
into Postgres `app.current_tenant`, Cube's `securityContext`, and the agent session metadata. The
attribute is immutable + not client-writable. Any endpoint that takes tenant_id from the body/query
is a bug.

## Files (orchestrator builds api/; security-critical)
- `api/auth.py` — `TenantClaims` + `verify_jwt(token, verifier)` (verifier injected; real one checks
  signature against Cognito JWKS — author + flag verify; tests inject a fake). A FastAPI dependency
  `current_tenant(request)` that extracts tenant_id ONLY from the verified claim; missing/invalid →
  401. Reject any request that tries to pass tenant_id another way.
- `api/app.py` — the FastAPI app. Routes:
  - `GET /healthz` (no auth).
  - `GET /approvals` + `POST /approvals/{id}/decide` → `api/control/greenlight` (tenant-scoped).
  - `GET /views` + `GET /views/{id}` + `POST /views` + `POST /views/{id}/refine` → `api/views`.
  - `POST /chat` → opens/uses a `conv.session.Conversation` (FakeRuntime) and returns the structured
    turn (answer + citations + any pending approvals).
  - Every authed route resolves tenant via `current_tenant` and threads it into the gate/greenlight/
    views/session — never trusts the body.
  - Wire the action-gate so `POST /actions` runs through `api/control/gate.ActionGate` (autonomy +
    compliance + Greenlight + kill switch).
- `api/README.md` update (the HTTP surface section).
- IaC (validate only): `infra/modules/auth` (Cognito user pool + client; `tenant_id` immutable custom
  attr; auto-verify email), `infra/modules/alb` (public ALB :443, target group :8000, listener),
  `infra/modules/api_service` (api Fargate service: 2 tasks, private subnets, SG_API, behind the ALB
  target group, secrets from SM). Wire into `infra/main.tf` + outputs.

## Frontend wiring (delegate to a background agent in web/ — separate dir)
A second brief `scripts/briefs/09b_frontend_wiring.md`: point the existing chat dock, dashboard
renderer, and the Greenlight UI (the demo centerpiece: editable drafts + reasoning + value-at-stake) at
the API via a typed client with an injectable base URL + a mock-mode for offline Playwright. Keep
build + typecheck + e2e green.

## Tests (offline, no AWS/Anthropic — use FastAPI TestClient + httpx)
- `tests/integration/test_api_auth.py` — no/invalid token → 401; a valid (faked) token with
  `custom:tenant_id=A` scopes everything to A; a request that puts `tenant_id=B` in the body is ignored
  (still A) — the trust rule. Two tenants never see each other's approvals/views.
- `tests/integration/test_api_endpoints.py` — approvals list/decide; view save/get/refine; chat returns
  a cited answer; an action routes through the gate (auto vs pending vs blocked).
- Keep the full suite green.

## Constraints
- No live AWS/Cognito/Anthropic. JWKS verification + Cognito calls authored + flagged verify; tests use
  an injected fake verifier. No secrets. tenant_id ONLY from the verified claim. terraform validate clean.
- Reuse api/control/*, api/views, conv/*, agents/* by import — do not duplicate.

## Done when
The FastAPI app enforces the trust rule (tenant only from the verified JWT), exposes approvals/views/
chat/actions all tenant-scoped through the existing modules, with the auth + endpoint tests green and
two-tenant isolation proven at the HTTP layer; Cognito/ALB/api-service IaC validates; the frontend is
wired (or its wiring brief queued + dispatched). BUILD_STATUS Phase 9 updated; live Cognito/ALB/Fargate
apply BLOCKED: needs Nick.
