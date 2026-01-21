"""Train a binary in-scope vs out-of-scope classifier."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from penguiflow.llm import LLMClient, LLMClientConfig, LLMMessage, TextPart


@dataclass(frozen=True)
class AugmentConfig:
    model: str
    api_key: str | None
    base_url: str | None
    total: int
    batch_size: int = 25


class AugmentedExamples(BaseModel):
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)


def _load_seeds(path: Path) -> dict[str, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Seeds file must be a JSON object")
    in_scope = data.get("in_scope")
    out_scope = data.get("out_of_scope")
    if not isinstance(in_scope, list) or not isinstance(out_scope, list):
        raise ValueError("Seeds file must include in_scope and out_of_scope lists")
    return {"in_scope": [str(x) for x in in_scope], "out_of_scope": [str(x) for x in out_scope]}


def _dedupe(existing: list[str], incoming: list[str]) -> list[str]:
    seen = {item.strip().lower() for item in existing if item.strip()}
    output: list[str] = []
    for item in incoming:
        candidate = str(item).strip()
        if not candidate:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return output


def _resolve_env(name: str) -> str | None:
    value = os.getenv(name)
    return value if value else None


async def _augment_with_llm(
    seeds: dict[str, list[str]],
    config: AugmentConfig,
) -> dict[str, list[str]]:
    target_total = max(config.total, len(seeds["in_scope"]) + len(seeds["out_of_scope"]))
    target_in_scope = target_total // 2
    target_out_scope = target_total - target_in_scope

    in_scope = list(seeds["in_scope"])
    out_scope = list(seeds["out_of_scope"])

    remaining_in = max(0, target_in_scope - len(in_scope))
    remaining_out = max(0, target_out_scope - len(out_scope))
    if remaining_in == 0 and remaining_out == 0:
        return {"in_scope": in_scope, "out_of_scope": out_scope}

    client = LLMClient(
        config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        config=LLMClientConfig(timeout_s=120.0, max_retries=2, temperature=0.7),
    )

    attempts = 0
    while (remaining_in > 0 or remaining_out > 0) and attempts < 6:
        attempts += 1
        batch_in = min(remaining_in, config.batch_size)
        batch_out = min(remaining_out, config.batch_size)
        if batch_in == 0 and batch_out == 0:
            break

        prompt = (
            "You are generating training data for an analytics assistant scope classifier.\n"
            "Create realistic requests in a mix of short fragments and full sentences.\n"
            "Avoid duplicates and avoid repeating the seeds.\n"
            f"Return {batch_in} in-scope examples and {batch_out} out-of-scope examples.\n\n"
            "In-scope seeds:\n"
            + "\n".join(f"- {item}" for item in in_scope[:10])
            + "\n\nOut-of-scope seeds:\n"
            + "\n".join(f"- {item}" for item in out_scope[:10])
        )

        messages = [
            LLMMessage(role="system", parts=[TextPart(text="Return JSON with in_scope and out_of_scope lists.")]),
            LLMMessage(role="user", parts=[TextPart(text=prompt)]),
        ]

        result = await client.generate(messages=messages, response_model=AugmentedExamples)
        batch = result.data

        new_in_scope = _dedupe(in_scope, batch.in_scope)
        new_out_scope = _dedupe(out_scope, batch.out_of_scope)

        if new_in_scope:
            in_scope.extend(new_in_scope)
            remaining_in = max(0, target_in_scope - len(in_scope))
        if new_out_scope:
            out_scope.extend(new_out_scope)
            remaining_out = max(0, target_out_scope - len(out_scope))

    return {"in_scope": in_scope, "out_of_scope": out_scope}


def _augment_with_llm_sync(
    seeds: dict[str, list[str]],
    config: AugmentConfig,
) -> dict[str, list[str]]:
    return asyncio.run(_augment_with_llm(seeds, config))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--total", type=int, default=200)
    parser.add_argument("--min-recall", type=float, default=0.9)
    parser.add_argument("--augment-with-llm", action="store_true")
    parser.add_argument("--llm-model", type=str, default=None)
    parser.add_argument("--llm-api-key", type=str, default=None)
    parser.add_argument("--llm-base-url", type=str, default=None)
    args = parser.parse_args()

    try:
        import joblib
        from sentence_transformers import SentenceTransformer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import precision_recall_curve
        from sklearn.model_selection import train_test_split
    except Exception as exc:
        raise SystemExit(
            "Optional deps missing. Install: scikit-learn joblib sentence-transformers"
        ) from exc

    seeds = _load_seeds(args.seeds)

    dataset = seeds
    if args.augment_with_llm:
        llm_model = args.llm_model or _resolve_env("SCOPE_CLASSIFIER_LLM_MODEL")
        llm_api_key = args.llm_api_key or _resolve_env("SCOPE_CLASSIFIER_LLM_API_KEY")
        llm_base_url = args.llm_base_url or _resolve_env("SCOPE_CLASSIFIER_LLM_BASE_URL")
        if not llm_model:
            raise SystemExit("LLM augmentation requested but no model provided.")

        dataset = _augment_with_llm_sync(
            seeds,
            AugmentConfig(
                model=llm_model,
                api_key=llm_api_key,
                base_url=llm_base_url,
                total=args.total,
            ),
        )

    texts = dataset["in_scope"] + dataset["out_of_scope"]
    labels = [1] * len(dataset["in_scope"]) + [0] * len(dataset["out_of_scope"])

    if len(texts) < 4:
        raise SystemExit("Need at least 2 in-scope and 2 out-of-scope examples")

    def _choose_threshold(scores: list[float], target: list[int]) -> tuple[float, dict[str, float]]:
        if not scores:
            return 0.5, {}
        precision, recall, thresholds = precision_recall_curve(target, scores)
        best_threshold: float | None = None
        for idx, thresh in enumerate(thresholds):
            if recall[idx] >= args.min_recall:
                best_threshold = float(thresh)
        if best_threshold is None:
            best_threshold = 0.5
        tp = sum(1 for s, y in zip(scores, target, strict=False) if s >= best_threshold and y == 1)
        fp = sum(1 for s, y in zip(scores, target, strict=False) if s >= best_threshold and y == 0)
        fn = sum(1 for s, y in zip(scores, target, strict=False) if s < best_threshold and y == 1)
        precision_val = tp / (tp + fp) if (tp + fp) else 0.0
        recall_val = tp / (tp + fn) if (tp + fn) else 0.0
        return best_threshold, {"precision": precision_val, "recall": recall_val}

    if len(texts) >= 10 and len(set(labels)) > 1:
        train_texts, test_texts, train_labels, test_labels = train_test_split(
            texts,
            labels,
            test_size=0.2,
            stratify=labels,
            random_state=42,
        )
    else:
        train_texts, test_texts, train_labels, test_labels = texts, texts, labels, labels

    encoder = SentenceTransformer("BAAI/bge-small-en-v1.5")
    train_embeddings = encoder.encode(train_texts, normalize_embeddings=True)
    test_embeddings = encoder.encode(test_texts, normalize_embeddings=True)

    clf = LogisticRegression(max_iter=1000, class_weight="balanced", solver="liblinear")
    clf.fit(train_embeddings, train_labels)

    test_scores = clf.predict_proba(test_embeddings)[:, 1].tolist()
    threshold, metrics = _choose_threshold(test_scores, list(test_labels))

    payload: dict[str, Any] = {
        "encoder": "BAAI/bge-small-en-v1.5",
        "classifier": clf,
        "threshold": threshold,
        "min_recall": args.min_recall,
        "metrics": metrics,
    }
    joblib.dump(payload, args.output)
    print(f"Saved model to {args.output}")


if __name__ == "__main__":
    main()
