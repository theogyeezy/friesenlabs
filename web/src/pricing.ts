// Single source of truth for the ratified launch plan pricing.
//
// These three tiers (starter / team / scale) are the paid plans the signup
// funnel offers, and each `id` maps 1:1 to a live Stripe Price. The amounts
// here are the RATIFIED launch prices — change them in ONE place and the
// signup flow, the consent screen, and anything else that imports from here
// move together. Never hard-code a plan price anywhere else.
//
// (The marketing landing's à-la-carte suite builder prices modules
// individually — a deliberately different pricing model — and is not driven by
// these tiers.)

export type PlanId = "starter" | "team" | "scale";

export interface PlanTier {
  /** Stable plan id — must match the Stripe Price lookup on the server. */
  id: PlanId;
  /** Display name. */
  name: string;
  /** Monthly price in whole US dollars. */
  pricePerMonth: number;
  /** Short marketing blurb shown on the plan card. */
  blurb: string;
}

/** The canonical, ordered plan tiers shown in the signup funnel. */
export const PLAN_TIERS: readonly PlanTier[] = [
  {
    id: "starter",
    name: "Starter",
    pricePerMonth: 99,
    blurb: "One Managed agent, core CRM, Greenlight review.",
  },
  {
    id: "team",
    name: "Team",
    pricePerMonth: 299,
    blurb: "Up to five Managed agents, Sidecar suite, shared inbox.",
  },
  {
    id: "scale",
    name: "Scale",
    pricePerMonth: 799,
    blurb: "Unlimited agents, Cortex intelligence, priority support.",
  },
] as const;

/** Format a tier price as the canonical "$99/mo" string used across surfaces. */
export function formatMonthlyPrice(pricePerMonth: number): string {
  return `$${pricePerMonth}/mo`;
}
