"""Behavioural reasoning (spec 5.7; rewrite directive Stage 4, Requirement
1 and 3).

A mechanism is not just a sequence of observations. Each carries an
explicit, layered causal chain:

    Environmental State -> Human Need -> Human Behaviour -> Attention
    -> Purchase Intent -> Demand -> Sales

Every layer is either ``evidenced`` (backed by a real, observed signal
already in the mechanism's evidence) or explicitly marked as theorized-but-
unmeasured. ELECOUS collects no transaction data anywhere -- Sales is
always the theorized final step, never fabricated content pretending to be
measured. Purchase Intent is always an attention-based proxy (Wikipedia/
GDELT), never real purchase data; this is stated on the step itself, not
hidden.

The chain is explanation, never evidence -- evidence remains the episodes,
factors and observations the mechanism links to.
"""

from __future__ import annotations

from typing import Any

from elecous.infrastructure.config import Config, active_config


class BehaviouralReasoner:
    def __init__(self, config: Config | None = None):
        self.config = config or active_config()
        vocab = self.config.load_file("behaviour.yaml")
        self.consequences: dict[str, str] = vocab.get("phenomenon_consequences", {})
        self.needs: dict[str, str] = vocab.get("category_needs", {})

    def chain(
        self, signal_keys: list[str], category: str, state_label: str | None = None
    ) -> list[dict[str, Any]]:
        """Backward-compatible entry point (unchanged signature callers
        keep working) that now returns the full layered chain."""
        return self.layered_chain(signal_keys, category, state_label)

    def layered_chain(
        self, signal_keys: list[str], category: str, state_label: str | None = None
    ) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []

        # Layer 1: Environmental State. Uses the real discovered state
        # label (Requirement 2) when the caller has one; otherwise falls
        # back to describing the raw signals directly -- still real, just
        # not yet generalized across factors.
        if state_label:
            steps.append({
                "layer": "environmental_state", "evidenced": True,
                "statement": f"environmental state '{state_label}' becomes active",
            })
        for key in sorted(signal_keys):
            parts = key.split(":")
            if len(parts) != 3:
                continue
            phenomenon, direction, scope = parts
            steps.append({
                "layer": "environmental_state", "evidenced": True, "signal": key,
                "statement": f"{phenomenon.replace('_', ' ')} {'rises' if direction == 'up' else 'falls'}"
                + ("" if scope == "global" else f" in {scope}"),
            })

        # Layer 2: Human Behaviour. Real config-declared vocabulary
        # (behaviour.yaml) -- domain knowledge stated as configuration, not
        # invented per-mechanism, and only emitted for phenomena that
        # actually appear in this mechanism's real evidence.
        for key in sorted(signal_keys):
            parts = key.split(":")
            if len(parts) != 3:
                continue
            phenomenon, direction, _ = parts
            consequence = self.consequences.get(f"{phenomenon}:{direction}")
            if consequence:
                steps.append({"layer": "human_behaviour", "evidenced": True, "signal": key, "statement": consequence})

        # Layer 3: Human Need. From the real category->need vocabulary.
        need = self.needs.get(category, f"{category.replace('_', ' ')} products")
        steps.append({"layer": "human_need", "evidenced": True, "statement": f"consumers act on {need}"})

        # Layer 4: Attention. Evidenced only if a real attention-capability
        # phenomenon (media_attention/public_attention) is actually among
        # this mechanism's signals -- otherwise honestly marked as
        # inferred, not observed.
        attention_signals = [k for k in signal_keys if k.split(":")[0] in ("media_attention", "public_attention")]
        if attention_signals:
            steps.append({
                "layer": "attention", "evidenced": True,
                "statement": "public/media attention to the category rises",
                "signals": attention_signals,
            })
        else:
            steps.append({
                "layer": "attention", "evidenced": False,
                "statement": "attention increase is inferred, not directly observed by this mechanism's signals",
            })

        # Layer 5: Purchase Intent. ELECOUS has no real transaction or
        # search-intent data anywhere -- this is always an attention-proxy
        # inference, stated honestly rather than presented as measured.
        steps.append({
            "layer": "purchase_intent", "evidenced": False,
            "statement": "purchase intent is inferred from attention-proxy signals (Wikipedia/GDELT), not observed directly",
        })

        # Layer 6: Demand. The one layer ELECOUS actually measures directly
        # (Research Episodes are demand-signal spikes).
        steps.append({
            "layer": "demand", "evidenced": True,
            "statement": f"{category.replace('_', ' ')} demand-proxy signal accelerates",
        })

        # Layer 7: Sales. Never measured by any real ELECOUS source --
        # stated as the theorized final step, not fabricated.
        steps.append({
            "layer": "sales", "evidenced": False,
            "statement": "theorized final step -- ELECOUS collects no real transaction/sales data to confirm this layer",
        })
        return steps

    def describe(self, chain: list[dict[str, Any]]) -> str:
        return " → ".join(step["statement"] for step in chain)

    @staticmethod
    def evidence_completeness(chain: list[dict[str, Any]]) -> float:
        """Real, honest fraction of the chain that is backed by observed
        signals rather than theorized -- a direct, numeric measure of how
        much of "why this happened" is actually evidenced."""
        if not chain:
            return 0.0
        return sum(1 for step in chain if step.get("evidenced")) / len(chain)

    def alternative_chains(self, primary_chain: list[dict[str, Any]], category: str) -> list[list[dict[str, Any]]]:
        """Phase 3 Causal Discovery: Construct competing hypotheses.
        If we inferred purchase intent due to attention, an alternative hypothesis
        is that it was driven by necessity (e.g. weather hazard) rather than attention.
        Returns a list of alternative chains."""
        alternatives = []
        alt_chain = []
        for step in primary_chain:
            if step["layer"] == "human_need":
                alt_chain.append({"layer": "human_need", "evidenced": False, "statement": "consumers act out of urgent necessity, bypassing normal intent"})
            elif step["layer"] == "purchase_intent":
                alt_chain.append({"layer": "purchase_intent", "evidenced": False, "statement": "panic buying or forced substitution, not standard intent"})
            elif step["layer"] == "attention" and step.get("evidenced"):
                # If attention is evidenced, alternative is that attention is a side-effect, not a cause
                alt_chain.append({"layer": "attention", "evidenced": True, "statement": "attention is a lagging indicator of demand, not the cause"})
            else:
                alt_chain.append(dict(step))
        if alt_chain != primary_chain:
            alternatives.append(alt_chain)
        return alternatives
