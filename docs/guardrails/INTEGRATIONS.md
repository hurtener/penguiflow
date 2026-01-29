# Guardrails Integrations

This document describes opt-in guardrail integrations and local examples. These
examples do not add dependencies to the core library. Install optional packages
only when you run the example scripts.

## Prompt Injection (HuggingFace)

Recommended models:
- protectai/deberta-v3-base-prompt-injection-v2
- madhurjindal/Jailbreak-Detector-2-XL

Use the example in `examples/guardrails/huggingface` to wire the async rule.
The rule fails open when transformers is not installed.

## Scope / Off-Topic Detection (Analytics Assistant)

This example provides a binary in-scope vs out-of-scope classifier for an
analytics-focused agent. It is designed to be trained locally and loaded into
memory at agent startup.

Highlights:
- Few-shot seed prompts expanded with LLM augmentation to 100-200 samples.
- Lightweight model (embeddings + LogisticRegression) for fast CPU inference.
- Async guardrail uses RETRY guidance rather than hard STOP.
- Classification input is augmented with context to handle short utterances
  like "yes, go on" or "continue".

See `examples/guardrails/scope_classifier` for a runnable flow and training
script. The runtime rule falls back to no-op if optional ML dependencies are
missing.
