---
title: Onboarding — the Greenlight approval queue
category: onboarding
audience: sales
---

# Onboarding: the Greenlight approval queue

Meridian's CRM has an AI agent that drafts customer emails, proposes deal updates, and prepares
quotes. It never executes a customer-facing action on its own — anything that would send an
email, move a deal, or issue a quote is routed to the **Greenlight queue** for a human, unless
your account's autonomy level explicitly allows that action to auto-run.

**What lands in the queue.** Each item shows the proposed action, the agent's reasoning, and the
value at stake. Three action types are side-effecting and queue by default: `send_email`,
`update_deal`, and `issue_quote`. Read-only work (looking things up, drafting internal notes)
never needs approval.

**What you can do with an item.**

- **Approve** — the action executes as proposed.
- **Edit** — change the draft (fix a number, soften a line, correct the recipient) and approve
  the edited version. Use this constantly; the agent's draft is a starting point.
- **Deny with a message** — reject it and tell the agent why ("max 10% without VP sign-off",
  "wrong audience — property manager only"). The reason flows back so the agent learns the
  boundary.

**Why it works this way.** A customer email or a quote is hard to unsend. The queue means the
agent can move fast on the busywork while a human owns every irreversible, customer-facing step.
Treat it as your safety net, not a chore — and keep the queue current so nothing the customer is
waiting on gets stuck behind it.
