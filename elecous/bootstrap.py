"""Wires every engine together in the one legal dependency order (spec 10.2).

Internet -> Observation -> Demand -> Environment -> Mechanisms -> Validation
-> Knowledge -> Prediction -> Dashboard.

This module is the single place that constructs the full stack so the
dashboard, scheduler and CLI never duplicate wiring logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from elecous.demand.engine import DemandEngine
from elecous.environment.engine import EnvironmentalEngine
from elecous.expansion.curiosity import CuriosityEngine
from elecous.expansion.engine import ExpansionEngine
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.event_bus import EventBus, global_bus
from elecous.infrastructure.resources import ResourceManager
from elecous.infrastructure.supervisor import AutonomousSupervisor
from elecous.knowledge.engine import KnowledgeEngine
from elecous.mechanisms.engine import MechanismEngine
from elecous.observation.gap import ObservationGapEngine
from elecous.observation.network import ObservationNetwork
from elecous.observation.sandbox.sandbox import ObservationSandbox
from elecous.prediction.engine import PredictionEngine
from elecous.scheduler.scheduler import Scheduler
from elecous.storage.database import DatabaseManager, global_databases
from elecous.storage.tiers import TierManager
from elecous.validation.engine import ValidationEngine


@dataclass
class ElecousStack:
    config: Config
    databases: DatabaseManager
    bus: EventBus
    resources: ResourceManager
    tiers: TierManager
    observation: ObservationNetwork
    gap: ObservationGapEngine
    demand: DemandEngine
    environment: EnvironmentalEngine
    mechanisms: MechanismEngine
    validation: ValidationEngine
    knowledge: KnowledgeEngine
    prediction: PredictionEngine
    scheduler: Scheduler
    expansion: ExpansionEngine
    curiosity: CuriosityEngine
    supervisor: AutonomousSupervisor


def build_stack(config: Config | None = None) -> ElecousStack:
    config = config or active_config()
    databases = global_databases()
    bus = global_bus()
    resources = ResourceManager(config)
    tiers = TierManager(config)

    observation = ObservationNetwork(config=config, bus=bus)
    observation.bootstrap()
    gap = ObservationGapEngine(observation.store, observation.registry, bus=bus)

    demand = DemandEngine(observation.store, config=config, databases=databases, bus=bus)
    environment = EnvironmentalEngine(observation.store, demand.episodes, config=config, databases=databases, bus=bus)
    mechanisms = MechanismEngine(
        environment.factors, demand.episodes, environment.discovery, observation.store,
        config=config, databases=databases,
    )
    validation = ValidationEngine(
        environment.library, environment.factors, mechanisms.store, demand.episodes,
        environment.discovery, environment.extractor, environment.world,
        config=config, databases=databases, bus=bus,
    )
    knowledge = KnowledgeEngine(
        environment.library, environment.factors, mechanisms.store, demand.episodes,
        environment.world, observation.store, config=config, databases=databases, bus=bus,
    )
    prediction = PredictionEngine(
        mechanisms.store, environment.world, validation.prospective,
        config=config, databases=databases, bus=bus,
    )

    scheduler = Scheduler(config=config, pause_check=resources.should_pause)
    sandbox = ObservationSandbox(observation.registry, config=config)
    expansion = ExpansionEngine(observation.registry, sandbox, gap, config=config, bus=bus)
    curiosity = CuriosityEngine(knowledge, mechanisms.store, environment.factors, gap)

    stack = ElecousStack(
        config=config, databases=databases, bus=bus, resources=resources, tiers=tiers,
        observation=observation, gap=gap, demand=demand, environment=environment,
        mechanisms=mechanisms, validation=validation, knowledge=knowledge,
        prediction=prediction, scheduler=scheduler, expansion=expansion,
        curiosity=curiosity, supervisor=None,  # type: ignore[arg-type]
    )
    stack.supervisor = AutonomousSupervisor(stack)
    return stack
