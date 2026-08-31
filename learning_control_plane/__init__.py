"""Framework-neutral, offline learning-control-plane package.

This package deliberately has no request-path dependency on PenguiFlow. Framework
providers and evaluation backends live behind explicit contracts so an unavailable
control plane cannot interrupt an agent serving a customer.
"""

from .contracts import EvidenceContext, EvidenceEvent
from .evidence import CompositeEvidenceSink, EvidenceSink, MlflowEvidenceSink, OpenTelemetryEvidenceSink

__all__ = [
    "CompositeEvidenceSink",
    "EvidenceContext",
    "EvidenceEvent",
    "EvidenceSink",
    "MlflowEvidenceSink",
    "OpenTelemetryEvidenceSink",
]
