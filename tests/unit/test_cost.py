"""Unit: the cost model — tiering, caching discount, batch (offline only), session hours."""
import pytest

from shared import cost


@pytest.mark.unit
def test_tier_prices_match_spec_table():
    # Build Guide pricing (Appendix B): haiku 1/5, sonnet 3/15, opus 5/25 ($/MTok in,out).
    assert cost.TIER_PRICES["haiku"] == (1.00, 5.00)
    assert cost.TIER_PRICES["sonnet"] == (3.00, 15.00)
    assert cost.TIER_PRICES["opus"] == (5.00, 25.00)


@pytest.mark.unit
def test_mix_must_sum_to_one():
    with pytest.raises(ValueError):
        cost.estimate(input_tokens=1000, output_tokens=1000, mix={"haiku": 0.5})


@pytest.mark.unit
def test_caching_reduces_input_cost():
    no_cache = cost.estimate(input_tokens=1_000_000, output_tokens=0, cached_fraction=0.0)
    cached = cost.estimate(input_tokens=1_000_000, output_tokens=0, cached_fraction=1.0)
    # Fully-cached reads cost ~10% of fresh reads.
    assert cached.inference == pytest.approx(no_cache.inference * (1 - cost.CACHE_READ_DISCOUNT), rel=1e-6)


@pytest.mark.unit
def test_batch_halves_offline_cost():
    online = cost.estimate(input_tokens=2_000_000, output_tokens=500_000, batch=False)
    offline = cost.estimate(input_tokens=2_000_000, output_tokens=500_000, batch=True)
    assert offline.inference == pytest.approx(online.inference * (1 - cost.BATCH_DISCOUNT), rel=1e-6)


@pytest.mark.unit
def test_session_hours_scale_with_parallel_threads():
    one = cost.estimate(input_tokens=0, output_tokens=0, session_hours=10, parallel_threads=1)
    five = cost.estimate(input_tokens=0, output_tokens=0, session_hours=10, parallel_threads=5)
    assert one.sessions == pytest.approx(10 * cost.ACTIVE_SESSION_HOUR)
    assert five.sessions == pytest.approx(5 * one.sessions)


@pytest.mark.unit
def test_tiering_cheaper_than_all_opus():
    workload = dict(input_tokens=5_000_000, output_tokens=1_000_000)
    tiered = cost.estimate(**workload)  # 70/25/5
    all_opus = cost.estimate(**workload, mix={"haiku": 0.0, "sonnet": 0.0, "opus": 1.0})
    assert tiered.inference < all_opus.inference


@pytest.mark.unit
def test_worked_example_in_sane_range():
    # A medium tenant-month: 50M input (60% cached), 8M output, 200 session-hours avg 2 threads.
    bd = cost.estimate(input_tokens=50_000_000, output_tokens=8_000_000,
                       cached_fraction=0.6, session_hours=200, parallel_threads=2)
    assert 0 < bd.total < 2000  # comfortably under a $2-8K/mo plan price
    assert bd.sessions == pytest.approx(200 * 2 * cost.ACTIVE_SESSION_HOUR)
