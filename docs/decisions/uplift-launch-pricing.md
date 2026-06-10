<!-- Decision brief — produced by the QA+DECISIONS lane (2026-06-10).
     Research: parallel agents over repo code + current Anthropic docs; claims adversarially
     spot-checked by an independent critic agent. Status: DRAFT until ratified by Nick + Matt. -->

# Uplift Launch Pricing — Starter / Team / Scale + Stripe Price IDs

**Decision needed:** what to charge for the three self-serve plans already wired into the signup funnel, what each tier gates, and which Stripe Prices to create before flipping `signup_real_deps`.

## Context (what the code does today)

**The plan plumbing is built and expects exactly three plan ids.** `shared/config.py:193-197` maps `starter`/`team`/`scale` → `STRIPE_PRICE_ID_STARTER`/`_TEAM`/`_SCALE` env vars ("values land via task secrets, never here"). `signup/stripe_adapter.py:72-99` creates a `mode: "subscription"` Checkout Session from `price_ids.get(plan)`, stamping `metadata: {plan}`; an unwired plan raises `ValueError` → `PaymentError` → clean 400 (`signup/payment.py:48-52`). Provisioning fires only on the signed webhook (`payment.py:55-112`).

**Prices shown to the user are hard-coded separately in the SPA.** `web/src/signup/SignupFlow.tsx:38-42` ships placeholder `PLANS`: Starter **$49** ("One Managed agent, core CRM, Greenlight review"), Team **$149** ("Up to five Managed agents, Sidecar suite, shared inbox"), Scale **$399** ("Unlimited agents, Cortex intelligence, priority support"). Step 4 of the flow is an *explicit price consent* screen ("You'll be charged $X/mo") — but Stripe charges whatever the Price ID says. These two surfaces are not linked; drift means the consent screen lies. The marketing landing page (`web/src/screens/landing.tsx:117-134, 1364-1430`) additionally advertises a *different* model — per-module pricing ($25-49/module), bundles, and "$0.05/extra credit" — plus the promises "**No token costs — we eat the AI & compute bills**" and "Free to start … no card required." Whatever we pick must reconcile these three surfaces.

**Per-tenant marginal cost.** The roster (`agents/roster/__init__.py:31-39`, `agents/coordinator.py:10-19`) is 7 specialists + coordinator: **two Opus agents sit on every gated turn** (coordinator + critic), 3 Sonnet workhorses, 3 Haiku. Verified current pricing (claude-api skill + live platform.claude.com pricing page, fetched 2026-06-10): Opus 4.8 $5/$25 per MTok, Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5; cache reads 0.1x; MA **session runtime $0.08/session-hour metered only while `running`**; Batch -50% explicitly does **not** apply inside MA sessions — all matching `shared/cost.py:17-26`. Modeling a chat-heavy tenant (~1,500 coordinator turns/mo ≈ 4,500 model requests, ~8K input each at 80% cache hit, ~600 output each, ~50 running session-hours):

- Repo's optimistic 70/25/5 token mix (`cost.py:22`): ≈ **$45/mo**
- Roster-realistic mix (Opus ≈ 45% of tokens because coordinator + critic are both Opus): ≈ **$85-90/mo**

So a chat-heavy tenant costs **$45-90/mo in Claude spend**; a light tenant (5 turns/day) is $5-15. Note `shared/COST.md:7` claims tiering is "already encoded in the roster," but the Opus critic on *every* response is the single biggest cost driver — worth a Sonnet-critic experiment.

**Shared infra.** Run-rate is **~$185/mo** against the $200 budget ceiling (`BUILD_STATUS.md:362`, cube/worker deploys parked on this). Aurora Serverless v2 floor is 1 ACU / max 16 (`infra/modules/data/main.tf:28-30`); at us-east-1's ~$0.12/ACU-hr the ~$88/mo floor is already inside the $185. Per-tenant marginal Aurora share at launch volume is <$5/mo — tokens are the variable, exactly as `shared/COST.md:3` says.

**Enforcement caveat.** The margin-protection mechanism — per-workspace spend caps set at provisioning (`signup/provisioning.py:263`) — targets an **assumed, unverified** Admin API write endpoint and soft-fails to "set limits in Console" (`signup/anthropic_admin.py:23-26, 140-158`). Until verified, every new tenant's cap is a manual Console click.

**Competitive anchor.** GoHighLevel verified live (gohighlevel.com/pricing, 2026-06-10): **$97 Starter / $297 Unlimited / $497 Agency Pro**, with AI usage rebilled on top. Per the battlecard, Uplift wins narrow/deep: autonomy dial (L0-L3, `api/control/autonomy.py:56-69`, default L1 seeded per tenant at `signup/tenant_defaults.py:34`), Greenlight HITL, per-tenant Cortex models (`ml/train.py`, `ml/retrain.py`, registry → S3 per `shared/config.py:165`), Sidecar-no-migration.

## Options

**A. Ship the placeholders: $49 / $149 / $399.**
Cost: a single chat-heavy Starter tenant ($45-90 marginal) is breakeven-to-underwater; needs ~2-4 tenants just to cover the $185 baseline. Risk: HIGH — "we eat the AI bills" at $49 with an unautomated spend cap is an open tab; also prices the "enterprise-grade agents" story below GHL, contradicting the narrow/deep premium positioning. Effort: zero.

**B. Match GHL at entry/mid, premium at top: $99 / $299 / $799 (+ annual = 2 months free: $990 / $2,990 / $7,990).**
Cost: chat-heavy Team tenant → ~70% gross margin ($299 vs ~$90 Claude + <$5 infra share); Starter margin protected by a $30/mo token allowance (workspace cap); baseline covered by 2 Team or 1 Scale tenant. Risk: MODERATE — Starter still thin if caps stay manual; mitigated by invite-gated launch. Effort: update `SignupFlow.tsx` PLANS, create 6 Stripe Prices, 3-line `STRIPE_PRICE_ID_ENV` extension for annual.

**C. Clone GHL exactly: $97 / $297 / $497.**
Cost: Scale at $497 leaves ~$300-400/yr-tenant of Cortex/L3 value uncaptured and caps ACV. Risk: invites line-item comparison on breadth, where GHL wins (voice, calendars, funnels); we'd be pricing on their menu. Effort: same as B.

## Recommendation

**Option B: $99 / $299 / $799 monthly; annual at 2-months-free ($990 / $2,990 / $7,990).** Match GHL within $2 at the tiers where buyers comparison-shop — and the pitch writes itself: "GHL's price, but agents do the work and the AI bill is ours" — then price Scale at $799 because per-tenant Cortex models + L3 autonomy exist at *no* GHL price point. Gates map to seams already in the code:

| | Starter $99 ($990/yr) | Team $299 ($2,990/yr) | Scale $799 ($7,990/yr) |
|---|---|---|---|
| Agents | Coordinator + 3 Haiku specialists (pip, echo, scout) | Full 7-specialist roster + Opus critic | Custom roster up to the MA 20-agent cap (`agents/runtime.py:21`) |
| Autonomy | L0-L1 only (everything approved) | Up to L2 (auto under $1k / 10% thresholds, `autonomy.py:14-17`) | Up to L3 (flagged-only pauses) |
| Cortex | None | Stock-model scoring (`run_model`) | Per-tenant trained models + scheduled retraining (`ml/retrain.py`) |
| Seats | 2 | 10 | 25 (seat enforcement is app-side — not yet built, flag it) |
| Token allowance (workspace cap) | $30/mo | $100/mo | $250/mo, then talk-to-us |

**Stripe objects to create** (Stripe mints the `price_...` IDs; create with lookup keys, then drop the IDs into task secrets per `config.py:192`):

| Product | Price | Lookup key | → env |
|---|---|---|---|
| Uplift Starter | $99/mo | `uplift_starter_monthly` | `STRIPE_PRICE_ID_STARTER` |
| Uplift Starter | $990/yr | `uplift_starter_annual` | `STRIPE_PRICE_ID_STARTER_ANNUAL`* |
| Uplift Team | $299/mo | `uplift_team_monthly` | `STRIPE_PRICE_ID_TEAM` |
| Uplift Team | $2,990/yr | `uplift_team_annual` | `STRIPE_PRICE_ID_TEAM_ANNUAL`* |
| Uplift Scale | $799/mo | `uplift_scale_monthly` | `STRIPE_PRICE_ID_SCALE` |
| Uplift Scale | $7,990/yr | `uplift_scale_annual` | `STRIPE_PRICE_ID_SCALE_ANNUAL`* |

\*Annual needs `starter_annual`/`team_annual`/`scale_annual` keys added to `STRIPE_PRICE_ID_ENV` (`shared/config.py:193-197`) — ~3 lines; the adapter already 400s cleanly on unwired plans, so shipping monthly-first is safe. Same PR must update `SignupFlow.tsx` PLANS (consent-screen truth), and the landing-page module-builder should be relabeled "custom — talk to us" until it's reconciled with the 3-plan reality. Pre-launch blocker: verify or replace `set_limits` so the per-tier token allowance is enforced automatically, not via Console clicks.

## What would flip this

- **Cache hit rate materially below ~50%** in real traffic → chat-heavy marginal cost roughly doubles → Starter to $149, Team to $349.
- **`set_limits` can't be automated** (Admin API write stays Console-only) → either keep launch invite-only behind manual caps, or add an app-side turn limiter before opening self-serve.
- **GTM pivots to the $2-8K/mo done-for-you retainers** (`shared/COST.md:26`) as the primary motion → self-serve tiers become lead-gen; ship B's numbers but stop optimizing them.
- **GHL ships chat-to-build bundled at $97** (battlecard says the moat window is ~6-12mo) → don't price-chase; push differentiation harder into Scale/Cortex and consider dropping Starter entirely.
- **Opus critic proves unnecessary** (Sonnet critic A/B holds quality) → marginal cost falls ~35-40%, creating room for a $49 hook tier later.

## Sources

- Repo (read 2026-06-10): `shared/config.py:193-203`; `signup/stripe_adapter.py:72-113`; `signup/payment.py:35-112`; `web/src/signup/SignupFlow.tsx:38-42`; `web/src/screens/landing.tsx:117-134, 1364-1430`; `agents/roster/__init__.py:12-39`; `agents/coordinator.py:10-33`; `agents/runtime.py:21`; `shared/cost.py:17-68`; `shared/COST.md`; `api/control/autonomy.py:14-69`; `api/control/types.py:15-19`; `signup/tenant_defaults.py:34`; `signup/provisioning.py:263`; `signup/anthropic_admin.py:23-26, 140-158`; `infra/modules/data/main.tf:28-30`; `BUILD_STATUS.md:362`.
- Claude pricing: **claude-api skill** (cached 2026-05-26) + **live fetch of platform.claude.com/docs/en/about-claude/pricing** (2026-06-10): Opus 4.8 $5/$25, Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5 per MTok; cache read 0.1x / 5-min write 1.25x; Batch -50% (not in MA sessions); MA session runtime $0.08/session-hour (`running` only).
- GoHighLevel: live fetch of gohighlevel.com/pricing (2026-06-10): $97 / $297 / $497.
- Aurora Serverless v2 ACU rate (~$0.12/ACU-hr us-east-1): AWS list price from general knowledge — verify against the AWS pricing page before quoting externally; it only affects the (already-sunk) baseline, not the tier math.


## Critic-noted gaps (non-blocking)
- Claude pricing claims verified against the live pricing page: Opus 4.8 $5/$25, Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5, cache reads 0.1x, MA session runtime $0.08/session-hour metered only while running, and the Batch discount explicitly listed as NOT applying to MA sessions. Repo wiring verified at the exact cited lines (config.py:193-197, stripe_adapter.py:75-79, payment.py:48-52, SignupFlow.tsx:38-42 hard-codes $49/$149/$399, BUILD_STATUS ~$185/mo, set_limits ASSUMED + soft-fail).
- The chat-heavy tenant cost model (claim 5) omits the $0.08/session-hour MA runtime charge — at 1,500 turns/mo with a few minutes running each it adds only a few dollars, but it should appear in the unit-economics line.
- GHL's $97/$297/$497 was not re-fetched live here (matches its long-standing public pricing); re-verify before publishing competitive copy.
- A higher tier above Opus now exists (Fable 5 at $10/$50 per MTok) — irrelevant unless the roster's top tier changes, but worth knowing when modeling Scale-tier margins.
