# Fleet — multi-agent dispatch for this repo

How the Claude/Codex agent fleet builds Uplift in parallel without overwriting work.
Full-page system diagram: [`fleet-diagram.pdf`](fleet-diagram.pdf) (preview: `preview.png`).

## Topology

| Machine | Role | Agent / account | Git identity |
|---|---|---|---|
| Mac Studio | **Boss** — plans, files issues, dispatches, squash-merges serially | Claude (`matthew.samuel.yee`) + Codex gpt-5.5 worker | diffusion23 (classic PAT `uplift-fleet-push`) |
| MacBook Pro | Worker — web lane | Claude (`nickfriesen23`) via SSH | nickfriesen23 (own gho token) |
| Mac mini | Worker — tests/docs/fixtures lane | Claude (`matt.sam.yee`) via SSH | diffusion23 |
| 3090 box | Ollama / GPU only | — | **never a git writer** |

One GitHub account per machine, never `gh auth switch` (keychain/token collisions make
pushes silently go out as the wrong account — cli/cli#8875).

## Git contract

- **Branch-per-task, agent-namespaced**: `feat/<agent>-<slug>`, cut fresh from `origin/main`.
  One branch = one writer, ever. Small diffs, hours not days; rebase on `origin/main` before push.
- **Issues = claims**: the boss files file-disjoint tasks as GitHub issues; assignment is the
  claim; the branch/PR references the issue. Never two concurrent tasks touching the same file
  (see the territory table in `CONTRIBUTING.md`).
- **Boss merges serially**: PR + green CI → `gh pr merge --squash --auto`, one at a time.
  Nobody pushes `main` (protected: PR required, checks `python,terraform,web,smoke`,
  squash-only, auto-delete head branches).
- Secrets never in the repo.

## Usage (from the Studio, repo root)

```bash
source scripts/fleet/dispatch.sh
fleet_ping                               # verify each worker responds
codex       scripts/briefs/NN_xxx.md     # Codex locally on the Studio
claude_mbp  scripts/briefs/NN_xxx.md     # Claude on the MBP
claude_mini scripts/briefs/NN_xxx.md     # Claude on the mini (skips itself if offline)
fleet_status                             # tail worker logs + open PRs
```

Briefs live in `scripts/briefs/` and must be self-contained (the worker has zero context):
goal, claimed issue, territory files, method, the tests that define done, constraints —
plus the git contract above. See `20_mbp_web_fixes.md` / `21_mini_mvp_fixtures.md` for the shape.

## Worker auth (one-time per machine)

Claude over SSH can't read the macOS Keychain, so each SSH worker needs a portable token:
run `claude setup-token` **locally on that machine** (Terminal, not over SSH) under the right
account, then write the token to `~/.claude/.credentials.json` (mode 600). Verify from the
Studio: `ssh <worker> "echo ok | claude --print --model haiku"`. Push auth is per-machine
(`~/.git-credentials`, helper=store) — see the header of `dispatch.sh` for the account map.
