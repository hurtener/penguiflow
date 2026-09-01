"""Framework-neutral, offline learning-control-plane package.

This package deliberately has no request-path dependency on PenguiFlow. Framework
providers and evaluation backends live behind explicit contracts so an unavailable
control plane cannot interrupt an agent serving a customer.
"""

from .control_plane import (
    AdvisorySkillCandidate,
    GateDecision,
    JobState,
    LearningControlPlane,
    LearningJob,
    PromotionPolicy,
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

__all__ = [
    "AdvisorySkillCandidate",
    "CompositeEvidenceSink",
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
    "Metric",
    "PairedCaseResult",
    "PairedEvaluationResult",
    "PromotionPolicy",
    "RunOne",
    "VariantCaseResult",
]
