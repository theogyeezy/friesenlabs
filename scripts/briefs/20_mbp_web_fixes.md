# LANE MBP — web fixes (pricing truth, leads capture, control wiring, funnel analytics)

You are a FLEETAGENT worker lane on the repo in the CURRENT DIRECTORY (~/dev/friesenlabs, multi-tenant agentic CRM "Uplift"). Read CLAUDE.md, CONTRIBUTING.md, AGENTS.md first.

## Git contract (non-negotiable)
- `git fetch origin` then `git switch -c feat/mbp-web-fixes origin/main`
- Touch ONLY `web/` (plus web/e2e tests). NEVER edit api/, infra/, db/, docs, CLAUDE.md, README.md, TODO.md, BUILD_STATUS.md.
- Small conventional commits. Rebase on origin/main before push. Push ONLY your branch.
- `gh pr create --fill` when done; poll `gh pr checks` and fix CI red on your branch. Do NOT merge. Do NOT push main.
- Definition of done: `cd web && npm run typecheck && npm run build` clean + Playwright e2e you touched passing locally.

## Tasks
1. **Pricing truth.** The ratified launch pricing is $99 (starter) / $299 (team) / $799 (scale) — Stripe Prices already exist. The signup/consent screens still show $49/$149/$… Find every price mention in web/ (signup flow, consent screen, landing pricing section) and wire them to a single source of truth (one constants module), showing 99/299/799.
2. **Leads stop hitting the floor.** The Book-a-call and Email-us modals confirm to the user then drop the data. Wire both to `POST /public/leads` with JSON `{kind: "book_call"|"email", name, email, message?, company?}` (this endpoint lands in another PR merging soon — code against that contract). On non-2xx or 404, fall back to a mailto: link AND keep the user-visible confirmation honest ("we'll get back to you" only on 2xx; otherwise show the mailto fallback). Add a tiny client-side retry (1 retry).
3. **Security controls honesty.** The kill-switch toggle and autonomy dial in the app UI are pure client-side state. Wire them to: `GET/PUT /control/killswitch` (`{engaged: bool, scope: "global"}`) and `GET/PUT /control/autonomy` (`{level: 0|1|2|3}`) — also landing from another PR. Feature-detect: if the endpoint 404s, show the control as disabled with a "not yet enabled" tooltip instead of a fake working toggle. Also add a read-only "Decision traces" list view that renders `GET /control/traces?limit=50` (id, ts, tool, decision, status) when available, same 404-degrade.
4. **PostHog funnel repair.** Client-side PostHog analytics is structurally dead (four gaps — find them: init/env var/proxy path/event names). Fix what is fixable purely in web/ (init guard, event wiring on signup steps + checkout click + chat usage). If an env var must be injected at build time, read it from import.meta.env with a safe default and note the required Amplify env var in the PR description (do NOT touch infra).

Work autonomously until the PR is green. Your final message: PR number + branch + summary + anything blocked.
