"""Harness eval runner for baseline/candidate scoring."""

from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

MetricFn = Callable[[object, object, object | None, str | None, object | None], float | dict[str, object]]
RunOneFn = Callable[[dict[str, Any], dict[str, Any] | None], Any]
DEFAULT_CONTEXT_IGNORE_KEYS: tuple[str, ...] = ("trace_id", "session_id", "__pf_patch_bundle")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _as_score_payload(raw: float | dict[str, object]) -> tuple[float, str | None]:
    if isinstance(raw, dict):
        score_raw = raw.get("score", 0.0)
        score = 0.0
        if isinstance(score_raw, bool):
            score = 1.0 if score_raw else 0.0
        elif isinstance(score_raw, (int, float)):
            score = float(score_raw)
        elif isinstance(score_raw, str):
            try:
                score = float(score_raw)
            except ValueError:
                score = 0.0
        feedback = raw.get("feedback")
        return score, str(feedback) if feedback is not None else None
    if isinstance(raw, bool):
        return (1.0 if raw else 0.0), None
    if isinstance(raw, (int, float)):
        return float(raw), None
    return 0.0, None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _normalise_context(value: object, ignore_keys: tuple[str, ...]) -> object:
    if isinstance(value, dict):
        return {
            key: _normalise_context(item, ignore_keys)
            for key, item in value.items()
            if key not in ignore_keys and not key.startswith("__pf_")
        }
    if isinstance(value, list):
        return [_normalise_context(item, ignore_keys) for item in value]
    return value


def _stable_hash(value: object, ignore_keys: tuple[str, ...]) -> str | None:
    if not isinstance(value, dict):
        return None
    try:
        payload = json.dumps(_normalise_context(value, ignore_keys), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _gold_context_hashes(gold: dict[str, Any], ignore_keys: tuple[str, ...]) -> tuple[str | None, str | None]:
    trace = gold.get("gold_trace")
    if not isinstance(trace, dict):
        return None, None
    inputs = trace.get("inputs")
    if not isinstance(inputs, dict):
        return None, None
    return _stable_hash(inputs.get("llm_context"), ignore_keys), _stable_hash(inputs.get("tool_context"), ignore_keys)


def _pred_context_hashes(pred_trace: object | None, ignore_keys: tuple[str, ...]) -> tuple[str | None, str | None]:
    if not isinstance(pred_trace, dict):
        return None, None
    return _stable_hash(pred_trace.get("llm_context"), ignore_keys), _stable_hash(
        pred_trace.get("tool_context"), ignore_keys
    )


def _context_match(gold: dict[str, Any], pred_trace: object | None, ignore_keys: tuple[str, ...]) -> bool | None:
    gold_llm_hash, gold_tool_hash = _gold_context_hashes(gold, ignore_keys)
    pred_llm_hash, pred_tool_hash = _pred_context_hashes(pred_trace, ignore_keys)

    # We can only assert stability when both sides provide context payloads.
    if None in {gold_llm_hash, gold_tool_hash, pred_llm_hash, pred_tool_hash}:
        return None
    return bool(gold_llm_hash == pred_llm_hash and gold_tool_hash == pred_tool_hash)


async def run_harness_eval(
    *,
    dataset_path: str | Path,
    output_dir: str | Path,
    run_one: RunOneFn,
    metric: MetricFn,
    mode: str,
    pred_name: str,
    patch_bundle: dict[str, Any] | None = None,
    ignore_keys: tuple[str, ...] = DEFAULT_CONTEXT_IGNORE_KEYS,
) -> dict[str, Any]:
    """Run harness eval over dataset rows and persist predictions/results."""

    rows = _read_jsonl(Path(dataset_path))
    run_id = uuid.uuid4().hex

    predictions: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    comparable_context = 0
    matched_context = 0
    for index, gold in enumerate(rows):
        example_id = str(gold.get("example_id", f"example-{index}"))
        split = str(gold.get("split", "unknown"))

        run_output = await _maybe_await(run_one(gold, patch_bundle))
        pred_trace = None
        pred = run_output
        if isinstance(run_output, tuple) and len(run_output) == 2:
            pred, pred_trace = run_output

        metric_raw = metric(gold, pred, gold, pred_name, pred_trace)
        score, feedback = _as_score_payload(metric_raw)
        context_match = _context_match(gold, pred_trace, ignore_keys)
        if context_match is not None:
            comparable_context += 1
            if context_match:
                matched_context += 1

        predictions.append(
            {
                "run_id": run_id,
                "mode": mode,
                "example_id": example_id,
                "split": split,
                "pred": pred,
                "pred_trace": pred_trace,
                "context_match": context_match,
            }
        )
        results.append(
            {
                "run_id": run_id,
                "mode": mode,
                "example_id": example_id,
                "split": split,
                "score": score,
                "feedback": feedback,
                "context_match": context_match,
            }
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    predictions_path = out_dir / f"predictions.{mode}.jsonl"
    results_path = out_dir / f"results.{mode}.jsonl"
    _write_jsonl(predictions_path, predictions)
    _write_jsonl(results_path, results)

    report_path = out_dir / "report.harness.json"
    report: dict[str, Any]
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        report = {"primary_metric": "score", "modes": {}}

    mean_score = (sum(item["score"] for item in results) / len(results)) if results else 0.0
    context_match_rate = (matched_context / comparable_context) if comparable_context else None
    report.setdefault("modes", {})[mode] = {
        "run_id": run_id,
        "count": len(results),
        "mean_score": mean_score,
        "context_match_rate": context_match_rate,
        "context_stability_pass": bool(comparable_context > 0 and matched_context == comparable_context),
        "context_comparable_count": comparable_context,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")

    return {
        "run_id": run_id,
        "example_count": len(rows),
        "predictions_path": str(predictions_path),
        "results_path": str(results_path),
        "report_path": str(report_path),
    }


__all__ = ["run_harness_eval"]
