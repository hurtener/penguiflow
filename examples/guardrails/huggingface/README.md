# HuggingFace Prompt Injection Guardrail

This example demonstrates an async prompt-injection classifier using
HuggingFace transformers. Two model options are supported:
- `protectai/deberta-v3-base-prompt-injection-v2`
- `madhurjindal/Jailbreak-Detector-2-XL`

## Optional dependencies

```
uv pip install transformers torch
```

## Run

```
uv run python examples/guardrails/huggingface/flow.py
```

If transformers is not installed, the guardrail rule fails open and the flow
still runs.
