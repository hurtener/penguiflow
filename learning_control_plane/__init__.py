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
    JobState,
    LearningControlPlane,
    LearningJob,
    PromotionPolicy,
    ReviewDecision,
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
from .mining import (
    CandidateDrafter,
    CandidateMiner,
    MinedCandidate,
    TraceCohorts,
    TraceLearningRecord,
    TracePattern,
    reserve_later_held_out_cohort,
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
    "JobState",
    "LearningControlPlane",
    "LearningJob",
    "LocalEvaluationBackend",
    "MLFLOW_LINEAGE_SCHEMA_VERSION",
    "MlflowEvidenceSink",
    "OpenTelemetryEvidenceSink",
    "OfflineEvaluationWorker",
    "Metric",
    "MinedCandidate",
    "PairedCaseResult",
    "PairedEvaluationResult",
    "PromotionPolicy",
    "PersistedControlPlaneState",
    "ReviewDecision",
    "RunOne",
    "SQLiteControlPlaneRepository",
    "TraceCohorts",
    "TraceLearningRecord",
    "TracePattern",
    "VariantCaseResult",
    "WorkerRun",
    "reserve_later_held_out_cohort",
]
