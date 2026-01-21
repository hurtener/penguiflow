# Custom Guardrails

This folder is reserved for custom, local guardrail integrations. If you want
fully tailored guardrails (e.g., proprietary classifiers or policies), build
your rule here and register it in a GuardrailGateway.

Example usage:

```python
from penguiflow.planner.guardrails import GuardrailGateway, RuleRegistry
from penguiflow.steering import InMemoryGuardInbox
from penguiflow.planner.guardrails import AsyncRuleEvaluator

registry = RuleRegistry()
# registry.register(MyCustomRule())

gateway = GuardrailGateway(
    registry=registry,
    guard_inbox=InMemoryGuardInbox(AsyncRuleEvaluator(registry)),
)
```
