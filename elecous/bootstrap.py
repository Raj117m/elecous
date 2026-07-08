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
from elecous.expansion.connector_evolution import ConnectorEvolutionEngine
from elecous.expansion.curiosity import CuriosityEngine
from elecous.expansion.engine import ExpansionEngine
from elecous.expansion.source_discovery import SourceDiscoveryEngine
from elecous.infrastructure.config import Config, active_config
from elecous.infrastructure.event_bus import EventBus, EventType, global_bus
from elecous.infrastructure.resources import ResourceManager
from elecous.infrastructure.supervisor import AutonomousSupervisor
from elecous.knowledge.engine import KnowledgeEngine
from elecous.mechanisms.engine import MechanismEngine
from elecous.observation.gap import ObservationGapEngine
from elecous.observation.network import ObservationNetwork
from elecous.observation.sandbox.sandbox import ObservationSandbox
from elecous.prediction.engine import PredictionEngine
from elecous.research.engine import ResearchEngine
from elecous.research.experiments import ExperimentEngine
from elecous.research.questions import ResearchQuestionStore
from elecous.research.source_value import SourceValueLedger
from elecous.scheduler.backlog import BacklogManager
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
    source_discovery: SourceDiscoveryEngine
    connector_evolution: ConnectorEvolutionEngine
    curiosity: CuriosityEngine
    research: ResearchEngine
    experiments: ExperimentEngine
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
        config=config, databases=databases, states=environment.states, concepts=environment.concepts,
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
    backlog = BacklogManager(scheduler, environment.world)
    sandbox = ObservationSandbox(observation.registry, config=config)
    connector_evolution = ConnectorEvolutionEngine(observation.registry, sandbox=sandbox, knowledge=knowledge.graph)
    source_discovery = SourceDiscoveryEngine(
        observation.registry, gap, knowledge=knowledge.graph, connector_evolution=connector_evolution, config=config,
    )
    expansion = ExpansionEngine(
        observation.registry, sandbox, gap, config=config, bus=bus, source_discovery=source_discovery,
    )
    curiosity = CuriosityEngine(knowledge, mechanisms.store, environment.factors, gap)

    # Research Intelligence (rewrite directive, Stage 2): Level-2 memory of
    # how to do research, layered on top of the pipeline above rather than
    # replacing any of it -- see docs/decisions/0004.
    research_questions = ResearchQuestionStore(databases=databases, config=config)
    source_value = SourceValueLedger(
        databases=databases, config=config,
        factors=environment.factors, mechanisms=mechanisms.store,
        discovery=environment.discovery, registry=observation.registry,
    )
    research = ResearchEngine(
        research_questions, source_value, research_questions.experience,
        observation, observation.health, observation.registry,
        resources=resources, config=config, databases=databases,
    )
    # Autonomous Scientific Experimentation (rewrite directive, Stage 7):
    # composes the real objects already built above -- no new store, no
    # new engine, only the missing falsifiable-prediction behavior on top
    # of ResearchQuestion.
    experiments = ExperimentEngine(
        research_questions, environment.states, environment.concepts, demand.episodes,
        environment.factors, mechanisms,
    )

    stack = ElecousStack(
        config=config, databases=databases, bus=bus, resources=resources, tiers=tiers,
        observation=observation, gap=gap, demand=demand, environment=environment,
        mechanisms=mechanisms, validation=validation, knowledge=knowledge,
        prediction=prediction, scheduler=scheduler, expansion=expansion,
        source_discovery=source_discovery, connector_evolution=connector_evolution,
        curiosity=curiosity, research=research, experiments=experiments,
        supervisor=None,  # type: ignore[arg-type]
    )
    stack.supervisor = AutonomousSupervisor(stack)

    # Additive listeners (Stage 2): VALIDATION_COMPLETED has fired since
    # Phase 6 with zero subscribers -- these are its first two real
    # consumers, both read-only with respect to validation's own authority.
    bus.subscribe(EventType.VALIDATION_COMPLETED, source_value.on_validation_completed)
    bus.subscribe(EventType.VALIDATION_COMPLETED, research.on_validation_completed)
    # Active question generation: every surfaced unknown becomes a real,
    # budgeted question instead of sitting inert in the Unknown Repository.
    bus.subscribe(EventType.UNKNOWN_RECORDED, research.on_unknown_recorded)
    return stack
