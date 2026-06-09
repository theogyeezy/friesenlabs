"""Unit-economics cost model (Build Guide Phase 11, Step 57).

Inference is the variable that scales; storage/embeddings/DB I/O are nearly free. This estimates the
inference bill given a workload so the $2-8K/mo plans can be priced. The levers:
  - model tiering 70/25/5 (Haiku/Sonnet/Opus) — encoded in the roster.
  - prompt caching — stable system/skill context caches at ~-90% on cached reads (biggest saver).
  - Batch -50% for offline work (embeddings, bulk scoring) — does NOT apply inside MA sessions.
  - active-session-hour meter ($0.08/hr) — meters only while running; parallel specialist threads stack.

Illustrative June-2026 per-MTok prices; verify against live pricing before relying on them.
"""
from __future__ import annotations

from dataclasses import dataclass

# Illustrative $/1M tokens (input, output) — VERIFY against live pricing.
TIER_PRICES = {
    "haiku":  (0.80, 4.00),
    "sonnet": (3.00, 15.00),
    "opus":   (15.00, 75.00),
}
DEFAULT_MIX = {"haiku": 0.70, "sonnet": 0.25, "opus": 0.05}  # 70/25/5

CACHE_READ_DISCOUNT = 0.90   # cached reads cost ~10% of a normal input read
BATCH_DISCOUNT = 0.50        # offline Batch is -50% (NOT inside MA sessions)
ACTIVE_SESSION_HOUR = 0.08   # $/active-session-hour


@dataclass
class CostBreakdown:
    inference: float
    sessions: float

    @property
    def total(self) -> float:
        return round(self.inference + self.sessions, 4)


def _tier_cost(tier: str, in_tokens: float, out_tokens: float, cached_fraction: float, batch: bool) -> float:
    pin, pout = TIER_PRICES[tier]
    cached = in_tokens * cached_fraction
    fresh_in = in_tokens - cached
    in_cost = (fresh_in + cached * (1 - CACHE_READ_DISCOUNT)) / 1_000_000 * pin
    out_cost = out_tokens / 1_000_000 * pout
    cost = in_cost + out_cost
    if batch:
        cost *= (1 - BATCH_DISCOUNT)  # offline only
    return cost


def estimate(*, input_tokens: float, output_tokens: float, mix: dict | None = None,
             cached_fraction: float = 0.0, session_hours: float = 0.0,
             parallel_threads: int = 1, batch: bool = False) -> CostBreakdown:
    """Estimate inference + session cost for a workload.

    `mix` defaults to 70/25/5. `cached_fraction` of input tokens are cached reads (-90%). `batch`
    applies the offline -50% (use only for non-session bulk work). Session cost = hours x threads x rate.
    """
    mix = mix or DEFAULT_MIX
    if abs(sum(mix.values()) - 1.0) > 1e-9:
        raise ValueError("tier mix must sum to 1.0")

    inference = 0.0
    for tier, share in mix.items():
        inference += _tier_cost(tier, input_tokens * share, output_tokens * share, cached_fraction, batch)

    sessions = session_hours * max(1, parallel_threads) * ACTIVE_SESSION_HOUR
    return CostBreakdown(inference=round(inference, 4), sessions=round(sessions, 4))
