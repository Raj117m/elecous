"""Subsystem schedule registration (spec 10.13).

Wires every engine's recurring work onto the central Scheduler with its own
cadence, so nothing runs continuously without reason. This is the one place
production, tests, and the dashboard's optional live mode all go to start
autonomous operation.
"""

from __future__ import annotations

from datetime import timedelta

from elecous.bootstrap import ElecousStack


def register_all_schedules(stack: ElecousStack) -> None:
    scheduler = stack.scheduler

    # Observation: each source on its own dynamic cadence (spec 2.7-2.8).
    stack.observation.register_schedules(scheduler)

    # Demand: scan for candidate spikes.
    scheduler.register(
        "demand:scan", "demand", lambda: stack.demand.scan(), timedelta(hours=6), priority=3
    )
    scheduler.register(
        "demand:improve", "demand", stack.demand.improve_from_episodes, timedelta(days=1), priority=6
    )
    scheduler.register(
        "demand:signal_registry", "demand", stack.demand.signal_registry_refresh, timedelta(days=1), priority=6
    )

    # Environment: research cycle over research-grade episodes.
    scheduler.register(
        "environment:research_cycle", "environment", stack.environment.research_cycle, timedelta(hours=12), priority=3
    )
    scheduler.register(
        "environment:world_state", "environment", lambda: stack.environment.world.build(), timedelta(minutes=15), priority=2
    )

    # Mechanisms: generate candidates from surviving factors.
    scheduler.register(
        "mechanisms:generate", "mechanisms", stack.mechanisms.generate_from_factors, timedelta(hours=12), priority=4
    )
    scheduler.register(
        "mechanisms:competitions", "mechanisms", stack.mechanisms.detect_competitions, timedelta(days=1), priority=6
    )

    # Validation: revalidate due mechanisms, resolve prospective outcomes.
    scheduler.register(
        "validation:revalidate", "validation", stack.validation.revalidate_due, timedelta(hours=6), priority=3
    )
    scheduler.register(
        "validation:prospective", "validation", stack.validation.resolve_prospective, timedelta(hours=6), priority=3
    )
    scheduler.register(
        "validation:drift", "validation", stack.validation.drift.scan_all, timedelta(days=1), priority=5
    )
    # Final Patch, Task 6: rolling benchmark against a real, simple
    # baseline. Bounded sample (measured at ~0.3s/mechanism for both
    # replays) keeps this well under the daily cadence's real cost budget.
    scheduler.register(
        "validation:rolling_benchmark", "validation",
        lambda: stack.validation.run_rolling_benchmark(sample_size=30), timedelta(days=1), priority=6,
    )
    # v1.0 directive: candidates with >=3 recorded all-failed validation
    # attempts stop consuming revalidation compute forever.
    scheduler.register(
        "validation:retire", "validation", stack.validation.retire_persistent_failures, timedelta(days=1), priority=6,
    )

    # Knowledge: sync graph, snapshot world state, age confidence.
    scheduler.register("knowledge:sync", "knowledge", stack.knowledge.sync, timedelta(hours=1), priority=4)
    scheduler.register(
        "knowledge:snapshot", "knowledge", stack.knowledge.snapshot_current_world, timedelta(hours=1), priority=5
    )
    scheduler.register(
        "knowledge:half_life", "knowledge", stack.knowledge.apply_half_life, timedelta(days=1), priority=7
    )

    # Prediction: rebuild opportunities against the live World State.
    scheduler.register("prediction:predict", "prediction", stack.prediction.predict, timedelta(minutes=15), priority=2)
    # v1.0 directive: every stored prediction is automatically revisited
    # after its own lead-time horizon and judged against real episodes.
    scheduler.register(
        "prediction:resolve", "prediction", stack.prediction.resolve_due, timedelta(hours=6), priority=3
    )
    scheduler.register(
        "prediction:advance_stages", "prediction", stack.prediction.advance_stages, timedelta(hours=1), priority=4
    )

    # Research Intelligence (rewrite directive, Stage 2): plan and collect
    # only the evidence open questions actually need, within a real budget.
    # Priority 4, hourly -- same tier as knowledge:sync, since planning
    # depends on validation outcomes that land at that cadence.
    scheduler.register("research:cycle", "research", stack.research.tick, timedelta(hours=1), priority=4)
    # Autonomous Scientific Experimentation (rewrite directive, Stage 7):
    # design falsifiable experiments for real active hypotheses and
    # resolve any past their real deadline -- runs after research:cycle at
    # the same real cadence tier since it depends on the same evidence.
    scheduler.register(
        "research:experiments", "research", stack.experiments.run_scientific_cycle, timedelta(hours=6), priority=4
    )

    # Autonomy: self-healing, expansion, curiosity (spec ch.12).
    scheduler.register(
        "supervisor:self_heal", "supervisor", stack.supervisor.self_heal, timedelta(minutes=30), priority=1
    )
    scheduler.register(
        "expansion:cycle", "supervisor", stack.expansion.run_expansion_cycle, timedelta(days=1), priority=6
    )
    scheduler.register(
        "curiosity:hourly_question", "supervisor", stack.curiosity.one_hourly_question, timedelta(hours=1), priority=6
    )

    # Maintenance: storage tiering.
    scheduler.register(
        "maintenance:tier_hot_to_warm",
        "infrastructure",
        lambda: stack.tiers.migrate_hot_to_warm(stack.databases.get("observations")),
        timedelta(days=1),
        priority=7,
    )
    scheduler.register(
        "maintenance:tier_warm_to_cold",
        "infrastructure",
        lambda: stack.tiers.migrate_warm_to_cold(stack.databases.get("observations")),
        timedelta(days=1),
        priority=7,
    )
