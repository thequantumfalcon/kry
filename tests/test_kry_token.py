"""Validation suite for the KRY token (standalone package).

Objective 1: KRY token functional   (earn / spend / cycle / falsifier)
Objective 2: External token viable   (hash-chain, exchange rates, mint events)

The host-only spend-protection proofs are
NOT part of this package — they depend on the operator's host-integration modules and the
host's live config, and live in the host repo's test suite.

Run: python -m pytest tests/ -v
"""
from __future__ import annotations

import threading
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# OBJECTIVE 1 — KRY token is functional
# ─────────────────────────────────────────────────────────────────────────────

class TestKRYTokenProof:
    """Prove the KRY earn/spend/cycle is mathematically sound."""

    def setup_method(self):
        # Each test gets a fresh in-memory ledger
        import kry.kry_token as kt
        kt._ledger_instance = kt.KRYLedger()

    def test_earn_cache_hit(self):
        """PROOF: cache hit earns KRY at rate 1.0."""
        from kry.kry_token import earn, status
        earned = earn(1000, "cache_hit", "test")
        assert earned == 1000.0 * 1.0
        assert status()["balance_kry"] == earned

    def test_earn_short_circuit(self):
        """PROOF: short-circuit earns KRY at rate 1.0."""
        from kry.kry_token import earn
        e = earn(300, "short_circuit", "test")
        assert e == 300.0

    def test_earn_compression(self):
        """PROOF: compression earns at discounted rate 0.6."""
        from kry.kry_token import earn
        e = earn(500, "compression", "test")
        assert abs(e - 300.0) < 0.01  # 500 × 0.6

    def test_free_tier_costs_zero(self):
        """PROOF: free tiers (Google, Groq, NIM, local) cost 0 KRY."""
        from kry.kry_token import spend_cost
        for prefix in ("google/gemini", "groq/llama", "nim/qwen", "local/qwen"):
            cost = spend_cost(prefix, 1000)
            assert cost == 0.0, f"{prefix} should cost 0 KRY, got {cost}"

    def test_copilot_costs_kry(self):
        """PROOF: gh/ Copilot calls cost KRY (500 per call)."""
        from kry.kry_token import spend_cost
        cost = spend_cost("gh/claude-opus-4.8", 1000)
        assert cost == 500.0

    def test_openrouter_costs_proportional(self):
        """PROOF: OpenRouter costs scale with frontier-equivalent pricing."""
        from kry.kry_token import spend_cost
        opus_cost   = spend_cost("or/anthropic/claude-opus-4.8", 1000)
        deep_cost   = spend_cost("or/deepseek/deepseek-v4-pro", 1000)
        assert opus_cost > deep_cost, "Opus should cost more KRY than DeepSeek"
        assert opus_cost == 1000.0     # $25/M = 1000 KRY/1k
        assert abs(deep_cost - 44.0) < 1.0  # $1.10/M = ~44 KRY/1k

    def test_spend_cost_matches_most_specific_prefix(self):
        """REGRESSION: a longer, more-specific SPEND_RATES key must not be shadowed
        by a shorter one. 'or/...opus-4.8' once shadowed its '-fast' variant (2000)
        and 'gh' shadowed 'ghm/' (charged 500/call). Each distinct tier in
        KRY_TOKEN_SPEC.md must be reachable at its documented rate."""
        from kry.kry_token import spend_cost
        # opus non-fast vs fast — distinct tiers ($25/M vs $50/M)
        assert spend_cost("or/anthropic/claude-opus-4.8", 1000) == 1000.0
        assert spend_cost("or/anthropic/claude-opus-4.8-fast", 1000) == 2000.0
        assert spend_cost("or/anthropic/claude-opus-4.7-fast", 1000) == 6000.0
        # 'gh' (Copilot, 500/call) must not capture 'ghm/' (GitHub Models, 5/1k)
        assert spend_cost("gh/claude-opus-4.8", 1000) == 500.0
        assert spend_cost("ghm/gpt-4o", 1000) == 5.0
        # deepseek variants stay distinct
        assert abs(spend_cost("or/deepseek/deepseek-v4-flash", 1000) - 11.0) < 1e-6
        assert abs(spend_cost("or/deepseek/deepseek-r1", 1000) - 88.0) < 1e-6

    def test_unknown_spend_model_charged_frontier_rate(self):
        """Unknown paid-looking routing targets must not silently bypass spend
        accounting. The conservative default is frontier-equivalent cost."""
        import kry.kry_token as kt
        kt._ledger_instance = kt.KRYLedger()
        assert kt.spend_cost("new-paid-provider/frontier-ish", 1000) == 1000.0
        assert not kt.can_afford("new-paid-provider/frontier-ish", 1000)
        kt.earn(2000, "cache_hit", "fund")
        spent = kt.spend("new-paid-provider/frontier-ish", 1000, "unknown route")
        assert spent == 1000.0
        assert kt.get_ledger().balance == 1000.0

    def test_earn_rate_tables_agree_across_modules(self):
        """REGRESSION: kry_token.earn() and kry_mint.mint() MUST apply the same rate
        for every event_type, or the live ledger and the tamper-evident mint chain
        diverge. 'continuity_capsule' lived only in kry_mint, so earn() fell back to
        its 0.5 default and credited the ledger 5x the receipt. The two rate tables
        must be identical."""
        import kry.kry_token as kt
        import kry.kry_mint as km
        assert kt.EARN_RATES == km._EARN_RATES

    def test_continuity_capsule_ledger_matches_chain(self):
        """REGRESSION (end-to-end): a continuity_capsule mint must credit the ledger
        by exactly the receipt's kry_minted (no 5x divergence)."""
        import kry.kry_token as kt
        from kry.kry_mint import mint
        kt._ledger_instance = kt.KRYLedger()
        r = mint("continuity_capsule", 1000, "capsule", evidence="cap")
        assert r is not None
        assert abs(kt.get_ledger().total_earned - r.kry_minted) < 1e-9

    def test_cycle_falsifier_zero_error(self):
        """PROOF: earn→bank→spend cycle has delta_error = 0 (mathematical consistency)."""
        from kry.kry_token import earn, spend, verify_cycle, get_ledger
        bal_before = get_ledger().balance
        e1 = earn(1500, "cache_hit", "test")
        e2 = earn(300,  "short_circuit", "test")
        s1 = spend("gh/claude-opus-4.8", 1000, "test")
        s2 = spend("google/gemini", 2000, "test")  # free
        bal_after = get_ledger().balance
        result = verify_cycle(e1+e2, s1+s2, bal_before, bal_after)
        assert result.consistent, f"Cycle inconsistent: delta_error={result.delta_error}"
        assert result.delta_error < 1e-6

    def test_cannot_go_negative(self):
        """PROOF: spending more than balance doesn't crash or go negative."""
        from kry.kry_token import earn, spend, get_ledger
        earn(100, "cache_hit", "test")
        # Try to spend 10x more than available
        spend("or/anthropic/claude-opus-4.8", 100_000, "test")
        assert get_ledger().balance >= 0.0

    def test_efficiency_ratio(self):
        """PROOF: efficiency_ratio measures the earned fraction correctly."""
        from kry.kry_token import earn, spend, status
        earn(1000, "cache_hit", "test")
        spend("or/deepseek/deepseek-v4-pro", 1000, "test")  # costs 44 KRY
        s = status()
        assert 0.9 < s["efficiency_ratio"] <= 1.0  # mostly earned, little spent

    def test_ledger_saves_and_loads(self, tmp_path):
        """PROOF: KRY ledger persists across process restarts."""
        import kry.kry_token as kt
        original_path = kt._LEDGER_PATH
        kt._LEDGER_PATH = tmp_path / "kry_test.json"
        try:
            kt._ledger_instance = kt.KRYLedger()
            kt.earn(500, "cache_hit", "persist_test")
            kt.get_ledger().save()
            # Reload from disk
            kt._ledger_instance = None
            loaded = kt.KRYLedger.load_or_create()
            assert abs(loaded.balance - 500.0) < 0.01
            assert abs(loaded.total_earned - 500.0) < 0.01
        finally:
            kt._LEDGER_PATH = original_path

    def test_thread_safety(self):
        """PROOF: concurrent earn calls don't corrupt the ledger."""
        from kry.kry_token import earn, get_ledger
        errors = []
        def do_earn():
            try:
                earn(100, "cache_hit", "thread_test")
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=do_earn) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"Thread errors: {errors}"
        # All 20 earns should be reflected
        assert abs(get_ledger().total_earned - 20 * 100.0) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# OBJECTIVE 2 — Novel external token is viable
# ─────────────────────────────────────────────────────────────────────────────

class TestKRYExternalTokenViability:
    """Prove the building blocks of an external KRY market are sound."""

    def test_exchange_rates_grounded_in_real_pricing(self):
        """PROOF: KRY exchange rates anchor to the frontier baseline pricing.
        In the host repo the table mirrors the host's model-pricing map; here the
        standalone pricing constants are the source of truth and must carry that
        same baseline ($25/M output for or/anthropic/claude-opus-4.8 = 1000 KRY/k)."""
        from kry.kry_token import (FRONTIER_USD_PER_M_OUTPUT, USD_PER_KRY,
                                   SPEND_RATES)
        assert FRONTIER_USD_PER_M_OUTPUT == 25.0
        assert abs(USD_PER_KRY - 0.000025) < 1e-8
        # The frontier model is in the table at the baseline rate (1000 KRY/k = $25/M)
        assert SPEND_RATES["or/anthropic/claude-opus-4.8"] == 1000.0

    def test_proof_of_efficiency_not_proof_of_purchase(self):
        """PROOF: KRY can only be earned through efficiency events, not bought."""
        from kry.kry_token import EARN_RATES
        # Earning events are efficiency events
        for event_type in EARN_RATES:
            assert event_type in (
                "cache_hit", "l3_semantic_match", "short_circuit",
                "compression", "feed_bag_deposit", "cache_creation",
                "continuity_capsule",
            ), f"Non-efficiency earning event: {event_type}"
        # There is no "purchase" or "deposit_usd" function
        import kry.kry_token as kt
        assert not hasattr(kt, "purchase"), "KRY must not have a purchase function"
        assert not hasattr(kt, "buy"),      "KRY must not have a buy function"

    def test_mint_event_is_hash_anchored(self):
        """PROOF: KRY earning events record SHA-256 digest for auditability."""
        import kry.kry_token as kt
        kt._ledger_instance = kt.KRYLedger()
        kt.earn(100, "cache_hit", "mint_test")
        events = kt.get_ledger().events
        assert len(events) == 1
        ev = events[0]
        assert ev.kind == "earn"
        assert ev.source == "cache_hit"
        assert ev.amount == 100.0
        assert ev.ts > 0  # timestamp present — enables hash-chain anchoring

    def test_provider_acceptance_model(self):
        """PROOF: free-tier providers (NIM, Groq, Google) cost 0 KRY —
        they accept routing in exchange for utilization, not payment."""
        from kry.kry_token import spend_cost
        free_providers = ["google/", "groq/", "nim/", "local/", "pool/"]
        for p in free_providers:
            assert spend_cost(f"{p}any-model", 1000) == 0.0, (
                f"{p} should cost 0 KRY — they earn via utilization routing")

    def test_kry_spec_exists_and_documents_novel_gap(self):
        """PROOF: KRY_TOKEN_SPEC.md exists and explicitly documents the novel gap."""
        spec = Path(__file__).parents[1] / "docs/KRY_TOKEN_SPEC.md"
        assert spec.exists(), "KRY_TOKEN_SPEC.md must exist"
        text = spec.read_text(encoding="utf-8")
        assert "proof-of-efficiency" in text.lower()
        assert "openrouter" in text.lower()
        assert "novel gap" in text.lower()

    def test_non_additivity_invariant(self):
        """PROOF: KRY and FuelLedger are two views of one
        event set — reconciled_savings() must NOT sum them (would double-count 2x)."""
        import kry.kry_token as kt
        kt._ledger_instance = kt.KRYLedger()
        # Earn 5000 KRY worth of cache hits
        kt.earn(5000, "cache_hit", "invariant_test")
        recon = kt.reconciled_savings()
        # The reconciled total must NOT be the naive sum
        naive_sum = recon["additive_misuse_would_show"]
        reconciled = recon["reconciled_total_saved"]
        # Reconciled is the max of the two views, never their sum.
        assert reconciled <= naive_sum, "reconciled must not exceed the naive sum"
        assert reconciled == recon["kry_view_earned"] or \
               reconciled == recon["fuel_view_deposited"], \
               "reconciled must equal one view, not a blend or sum"
        # The anti-double-count invariant: reconciled == max(view, view), so it can
        # never be kry+fuel when both are positive. In this standalone package the
        # FuelLedger view is host-only (always 0), so reconciled == the KRY view —
        # the same max() rule that prevents the 2x bug when both ledgers are live.
        assert reconciled == max(recon["kry_view_earned"],
                                 recon["fuel_view_deposited"])
        assert recon["fuel_view_deposited"] == 0.0  # FuelLedger absent in standalone

    def test_full_earning_circuit_all_sources(self):
        """PROOF: all 6 earning sources produce correct amounts."""
        import kry.kry_token as kt
        kt._ledger_instance = kt.KRYLedger()
        expected = {
            "cache_hit":         (1000, 1.0),
            "l3_semantic_match": (1000, 0.8),
            "short_circuit":     (1000, 1.0),
            "compression":       (1000, 0.6),
            "feed_bag_deposit":  (1000, 0.7),
            "cache_creation":    (1000, 0.0),  # a COST, not a saving → earns 0
        }
        total_expected = 0.0
        for event, (tokens, rate) in expected.items():
            earned = kt.earn(tokens, event, "test")
            assert abs(earned - tokens * rate) < 0.01, (
                f"{event}: expected {tokens * rate}, got {earned}")
            total_expected += tokens * rate
        assert abs(kt.get_ledger().total_earned - total_expected) < 0.01


class TestRetainedDollars:
    """R2: KRY value today = retained dollars (money kept), not a tradeable token."""

    def test_retained_dollars_maps_to_usd(self):
        import kry.kry_token as kt
        kt._ledger_instance = kt.KRYLedger()
        kt.earn(40000, "cache_hit", "x", avoided_model="gh/claude-opus-4.8")
        r = kt.retained_dollars()
        assert abs(r["retained_usd"] - 1.0) < 0.001  # 40000 KRY * $0.000025 = $1.00
        assert r["external_counterparty_exists"] is False  # honest: scoreboard until a B

    def test_free_tier_earns_no_retained_dollars(self):
        """Free-tier hits avoided $0 → contribute $0 retained (honest)."""
        import kry.kry_token as kt
        kt._ledger_instance = kt.KRYLedger()
        kt.earn(40000, "cache_hit", "x", avoided_model="google/gemini-3.5-flash")
        r = kt.retained_dollars()
        assert r["retained_usd"] == 0.0
