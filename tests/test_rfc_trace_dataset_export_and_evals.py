from __future__ import annotations

from pathlib import Path


def test_rfc_trace_dataset_export_mentions_dspy_gepa_contracts() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    rfc_path = repo_root / "docs" / "proposals" / "RFC_TRACE_DERIVED_DATASETS_AND_EVALS.md"
    text = rfc_path.read_text(encoding="utf-8")
    lower = text.lower()

    # DSPy/GEPA compatibility is primarily about the metric callable contract and
    # prompting "patch points" that can be swept manually or optimized.
    assert "gepa" in lower
    assert "pred_name" in text
    assert "pred_trace" in text
    assert "gold" in lower and "pred" in lower and "trace" in lower
    assert "patch point" in lower

    # Dataset curation should be trace-scoped via tags (not "entire history").
    assert "--tag" in text or "export by tag" in lower or "export-by-tag" in lower

    # DSPy datasets are signature/view-driven (flat fields keyed by signature names).
    assert "dataset view" in lower or "projection" in lower


def test_rfc_trace_dataset_export_has_phase_feature_matrix() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    rfc_path = repo_root / "docs" / "proposals" / "RFC_TRACE_DERIVED_DATASETS_AND_EVALS.md"
    text = rfc_path.read_text(encoding="utf-8")
    lower = text.lower()

    # Ensure the RFC explicitly calls out phased rollout and capability gaps.
    assert "missing capabilities" in lower
    assert "phase 1" in lower
    assert "phase 2" in lower


def test_rfc_mentions_patch_bundle_single_entrypoint() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    rfc_path = repo_root / "docs" / "proposals" / "RFC_TRACE_DERIVED_DATASETS_AND_EVALS.md"
    text = rfc_path.read_text(encoding="utf-8")
    lower = text.lower()

    assert "patch bundle" in lower
    assert "patchbundlev1" in lower
    assert "production" in lower


def test_rfc_mentions_optional_mlflow_integration() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    rfc_path = repo_root / "docs" / "proposals" / "RFC_TRACE_DERIVED_DATASETS_AND_EVALS.md"
    text = rfc_path.read_text(encoding="utf-8")
    lower = text.lower()

    # The RFC should explicitly call MLflow out as an optional sink/integration
    # rather than a core dependency.
    assert "optional integrations" in lower
    assert "mlflow" in lower
    assert "sink" in lower
