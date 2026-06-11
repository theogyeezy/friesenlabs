#!/usr/bin/env bash
# Friesen Labs fleet dispatch v2 — git-native. BOSS = Mac Studio (this account).
#
# Every agent works in its OWN clone of theogyeezy/friesenlabs and pushes its OWN
# branches (feat/<agent>-<slug> cut fresh from origin/main). Nobody shares a branch;
# nobody pushes main. Integration = PR + green CI, boss squash-merges serially.
# Push auth on all machines = classic PAT "uplift-fleet-push" (diffusion23):
#   Studio: gh keyring (gh IS the git credential helper here — ~/.gitconfig)
#   MBP/mini: ~/.git-credentials (helper=store) + gh hosts.yml
# If pushes 403 again: re-check https://github.com/settings/tokens (diffusion23).
#
# Usage (from the Studio, repo root):  source scripts/fleet/dispatch.sh
#   fleet_ping                              # verify each worker responds
#   codex       scripts/briefs/NN_xxx.md    # Codex locally on the Studio (own worktree)
#   claude_mbp  scripts/briefs/NN_xxx.md    # Claude (nickfriesen23 acct) on the MBP
#   claude_mini scripts/briefs/NN_xxx.md    # Claude (matt.sam.yee acct) on the mini
#   fleet_status                            # tail all logs + open PRs
#
# Brief contract (put this in every brief):
#   - claim = the GitHub issue number in the brief; work ONLY that issue's files
#   - git fetch, then git switch -c feat/<agent>-<slug> origin/main
#   - small diffs; rebase on origin/main before push; push your own branch only
#   - gh pr create --fill linking the issue; fix CI red on your own branch
#   - NEVER push main; NEVER touch another lane's branch; secrets never in the repo

set -uo pipefail

# --- workers ---
MBP="macpro24gb@100.121.207.114"          # Claude acct: nickfriesen23 · author "MBP Claude"
MINI="andrew@100.84.121.104"              # Claude acct: matt.sam.yee  · author "Mini Claude"
CLAUDE_MBP="/Users/macpro24gb/.local/bin/claude"
CLAUDE_MINI="/Users/andrew/.nvm/versions/node/v22.22.1/bin/claude"
REPO_STUDIO=~/dev/friesenlabs
REPO_MBP="/Users/macpro24gb/dev/friesenlabs"
REPO_MINI="/Users/andrew/dev/friesenlabs"
LOGS="${FLEET_LOGS:-$HOME/dev/uplift/logs}"
# 3090 box nickf@100.126.107.65 = Ollama/GPU only (no cloud agent, never a git writer)

_reach(){ nc -z -G4 "$(echo "$1"|sed 's/.*@//')" 22 >/dev/null 2>&1; }

fleet_ping(){
  echo "[studio/codex gpt-5.5 ] $(command codex --version 2>&1 | head -1)"
  echo "[studio/claude boss  ] $(echo ok | claude --print --model haiku 2>&1 | head -1)"
  if _reach "$MBP";  then echo "[mbp/claude nickf    ] $(ssh -o BatchMode=yes $MBP  "echo ok | $CLAUDE_MBP  --print --model haiku" 2>&1 | head -1)"; else echo "[mbp] UNREACHABLE"; fi
  if _reach "$MINI"; then echo "[mini/claude matt    ] $(ssh -o BatchMode=yes $MINI "echo ok | $CLAUDE_MINI --print --model haiku" 2>&1 | head -1)"; else echo "[mini] OFFLINE — skipping"; fi
}

# Sync a worker's clone to latest main before dispatching (never dispatch onto a stale base)
_fresh(){ ssh -o BatchMode=yes "$1" "cd $2 && git fetch -q origin && git switch -q main && git pull -q --rebase"; }

# codex <brief.md> — Codex locally on the Studio, in the Studio clone, backgrounded
codex(){ local b="$1"; local t=$(basename "$b" .md); mkdir -p "$LOGS"
  ( cd "$REPO_STUDIO" && git fetch -q origin && nohup command codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox "$(cat "$b")" > "$LOGS/codex_${t}.log" 2>&1 & echo "started codex_${t} pid $!" ); }

# claude_mbp <brief.md> — Claude on the MBP via SSH, in its own clone, backgrounded
claude_mbp(){ local b="$1"; local t=$(basename "$b" .md); mkdir -p "$LOGS"
  if ! _reach "$MBP"; then echo "mbp unreachable — skipped $t"; return 1; fi
  _fresh "$MBP" "$REPO_MBP"
  ssh -o BatchMode=yes $MBP "export PATH=\"\$HOME/.local/bin:\$PATH\"; cd $REPO_MBP && BRIEF=\$(cat -) && nohup $CLAUDE_MBP --permission-mode acceptEdits -p \"\$BRIEF\" > /tmp/claude_mbp_${t}.log 2>&1 & echo started claude_mbp_${t}" < "$b"; }

# claude_mini <brief.md> — Claude on the mini via SSH, in its own clone, backgrounded
claude_mini(){ local b="$1"; local t=$(basename "$b" .md); mkdir -p "$LOGS"
  if ! _reach "$MINI"; then echo "mini offline — skipped $t"; return 1; fi
  _fresh "$MINI" "$REPO_MINI"
  ssh -o BatchMode=yes $MINI "export PATH=\"\$HOME/.local/bin:\$PATH\"; cd $REPO_MINI && BRIEF=\$(cat -) && nohup $CLAUDE_MINI --permission-mode acceptEdits -p \"\$BRIEF\" > /tmp/claude_mini_${t}.log 2>&1 & echo started claude_mini_${t}" < "$b"; }

fleet_status(){
  echo "== studio logs =="; for f in "$LOGS"/*.log; do [ -e "$f" ] && printf "  %-30s %s\n" "$(basename "$f")" "$(tail -1 "$f" 2>/dev/null|head -c90)"; done
  _reach "$MBP"  && { echo "== mbp logs ==";  ssh -o BatchMode=yes $MBP  'for f in /tmp/claude_mbp_*.log;  do [ -e "$f" ] && printf "  %-30s %s\n" "$(basename "$f")" "$(tail -1 "$f"|head -c90)"; done' 2>/dev/null; }
  _reach "$MINI" && { echo "== mini logs =="; ssh -o BatchMode=yes $MINI 'for f in /tmp/claude_mini_*.log; do [ -e "$f" ] && printf "  %-30s %s\n" "$(basename "$f")" "$(tail -1 "$f"|head -c90)"; done' 2>/dev/null; }
  echo "== open PRs =="; (cd "$REPO_STUDIO" && gh pr list --limit 15 2>/dev/null)
}
