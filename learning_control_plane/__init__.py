"""Framework-neutral, offline learning-control-plane package.

This package deliberately has no request-path dependency on PenguiFlow. Framework
providers and evaluation backends live behind explicit contracts so an unavailable
control plane cannot interrupt an agent serving a customer.
"""

from .control_plane import (
    ActivationReceipt,
    AdvisorySkillCandidate,
    DeliveryAuthorization,
    GateDecision,
    JobAuditRecord,
    JobState,
    LearningControlPlane,
    LearningJob,
    PromotionPolicy,
    ReviewDecision,
    ReviewQueueItem,
)
from .evaluation import (
    EvaluationBackend,
    EvaluationCase,
    EvaluationDataset,
    EvaluationRequest,
    EvaluationVariant,
    LocalEvaluationBackend,
    Metric,
    PairedCaseResult,
    PairedEvaluationResult,
    RunOne,
    VariantCaseResult,
)
from .evidence import (
    MLFLOW_LINEAGE_SCHEMA_VERSION,
    CompositeEvidenceSink,
    EvidenceContext,
    EvidenceEvent,
    EvidenceSink,
    MlflowEvidenceSink,
    OpenTelemetryEvidenceSink,
)
from .investigation import (
    INVESTIGATION_TRAJECTORY_SCHEMA_VERSION,
    InvestigationStatus,
    InvestigationTrajectoryV1,
    SourceTraceRef,
)
from .investigation_publisher import InvestigationPublisher, MlflowAttachmentPublisher
from .mining import (
    CandidateDrafter,
    CandidateMiner,
    MinedCandidate,
    TraceCohorts,
    TraceLearningRecord,
    TracePattern,
    reserve_later_held_out_cohort,
)
from .penguiflow import (
    PenguiFlowInvestigationContext,
    PenguiFlowInvestigationProjector,
    PenguiFlowInvestigationPublicationHook,
)
from .persistence import PersistedControlPlaneState, SQLiteControlPlaneRepository
from .worker import OfflineEvaluationWorker, WorkerRun

__all__ = [
    "ActivationReceipt",
    "AdvisorySkillCandidate",
    "CandidateDrafter",
    "CandidateMiner",
    "CompositeEvidenceSink",
    "DeliveryAuthorization",
    "EvidenceContext",
    "EvidenceEvent",
    "EvidenceSink",
    "EvaluationBackend",
    "EvaluationCase",
    "EvaluationDataset",
    "EvaluationRequest",
    "EvaluationVariant",
    "GateDecision",
    "INVESTIGATION_TRAJECTORY_SCHEMA_VERSION",
    "InvestigationStatus",
    "InvestigationTrajectoryV1",
    "InvestigationPublisher",
    "JobState",
    "JobAuditRecord",
    "LearningControlPlane",
    "LearningJob",
    "LocalEvaluationBackend",
    "MLFLOW_LINEAGE_SCHEMA_VERSION",
    "MlflowEvidenceSink",
    "MlflowAttachmentPublisher",
    "OpenTelemetryEvidenceSink",
    "OfflineEvaluationWorker",
    "Metric",
    "MinedCandidate",
    "PairedCaseResult",
    "PairedEvaluationResult",
    "PromotionPolicy",
    "PersistedControlPlaneState",
    "PenguiFlowInvestigationContext",
    "PenguiFlowInvestigationProjector",
    "PenguiFlowInvestigationPublicationHook",
    "ReviewDecision",
    "ReviewQueueItem",
    "RunOne",
    "SQLiteControlPlaneRepository",
    "SourceTraceRef",
    "TraceCohorts",
    "TraceLearningRecord",
    "TracePattern",
    "VariantCaseResult",
    "WorkerRun",
    "reserve_later_held_out_cohort",
]
