---
title: How the agent's autonomy levels work
category: faq
audience: sales
---

# Autonomy levels — FAQ

The CRM agent operates at an autonomy level your account configures. The level controls how much
the agent can do on its own versus what it must route to the Greenlight queue for a human. The
guardrails are independent of each other, so raising autonomy never removes the hard limits.

**The levels.**

- **L0 — observe only.** The agent reads and drafts, but proposes nothing for execution.
- **L1 — ask always.** Every side-effecting action (send an email, move a deal, issue a quote)
  is queued for human approval. This is the safe default for a new account.
- **L2 — auto within thresholds.** Low-risk actions execute automatically; anything that crosses
  a threshold still queues. The thresholds: actions under $1,000 in value may auto-execute, and
  any discount over 10% always queues regardless of value.
- **L3 — broad autonomy.** The agent executes most actions, with only the highest-risk items
  held; the hard limits below still apply.

**The hard limits never move.** Two guards are independent of level: the **value ceiling**
(large-dollar actions queue) and the **discount floor** (anything over 10% off queues for VP
sign-off). At L2, an $850 follow-up email auto-sends, but a $48,000 renewal and a 12% discount
both still stop — because value and discount are checked separately.

**Why straddle the line on purpose?** The clearest way to show how the dial works is with deals
that sit just on either side of a threshold: an $850 action that flips to auto at L2, and a
$1,200 one that doesn't. The boundary is real and enforced, not a suggestion.
