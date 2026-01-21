# Scope Classifier (Analytics Assistant)

This example shows a binary in-scope vs out-of-scope classifier for an
Analytics Assistant. The classifier is trained locally and loaded into memory
at agent startup as an async guardrail rule.

## Why async?

Even a small classifier can run asynchronously without blocking LLM/tool
execution. The guardrail uses RETRY guidance to ask for clarification rather
than hard stopping.

## Optional dependencies

```
uv pip install scikit-learn joblib sentence-transformers
```

## Train (optional)

```
uv run python examples/guardrails/scope_classifier/train_classifier.py \
  --seeds examples/guardrails/scope_classifier/seeds.json \
  --output examples/guardrails/scope_classifier/scope_model.joblib \
  --min-recall 0.9
```

If optional dependencies are missing, the training script will exit with a
helpful message.

## Train with LLM augmentation (optional)

Set a model and API key before running the augmentation pipeline. The script
uses `penguiflow.llm.LLMClient` to generate additional examples.

```
export SCOPE_CLASSIFIER_LLM_MODEL=openai/gpt-4o
export SCOPE_CLASSIFIER_LLM_API_KEY=...
```

```
uv run python examples/guardrails/scope_classifier/train_classifier.py \
  --seeds examples/guardrails/scope_classifier/seeds.json \
  --output examples/guardrails/scope_classifier/scope_model.joblib \
  --total 200 \
  --augment-with-llm
```

## Run

```
uv run python examples/guardrails/scope_classifier/flow.py
```

If the model is not present, the rule fails open (no blocking).

## Calibration

The training script auto-calibrates a recommended threshold on a small validation
split (default target recall 0.9) and stores it in the model payload. The guardrail
uses that threshold automatically.

## Context for short replies

To handle short replies like "yes, go on", augment the classifier input with
recent context. The example rule reads optional fields from the guardrail event
payload:
- `last_assistant`: last assistant response or summary
- `task_scope`: short description of the agent scope

These can be populated by a custom guardrail event hook or by extending the
planner with a lightweight wrapper that adds context before evaluation.
