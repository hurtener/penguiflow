"""Tests for guardrails policy pack."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

from penguiflow.planner.guardrails import (
    AsyncRuleEvaluator,
    ContextSnapshotBuilder,
    DefaultRiskRouter,
    GatewayConfig,
    GuardrailGateway,
    RuleRegistry,
    SecretRedactionRule,
    ToolAllowlistRule,
    apply_policy_config,
    load_policy_pack,
)
from penguiflow.steering import InMemoryGuardInbox

if TYPE_CHECKING:
    from pathlib import Path


def test_load_policy_pack_env_override(tmp_path: Path) -> None:
    """Load policy pack env override."""
    content = textwrap.dedent(
        """
        policy_pack: "default"
        version: "1.0.0"
        gateway:
          mode: "enforce"
        environments:
          dev:
            gateway:
              mode: "shadow"
        """
    )
    path = tmp_path / "policy.yaml"
    path.write_text(content, encoding="utf-8")

    pack = load_policy_pack(path, env="dev")
    assert pack.policy_id == "default"
    assert pack.data["gateway"]["mode"] == "shadow"


def test_apply_policy_config_updates_rules_and_gateway(tmp_path: Path) -> None:
    """Apply policy config updates rules and gateway."""
    content = textwrap.dedent(
        """
        policy_pack: "default"
        version: "1.0.0"
        gateway:
          mode: "shadow"
          sync:
            timeout_ms: 10
            parallel: false
        tool_risks:
          "tool.a": "high"
        sync_rules:
          - id: "tool-allowlist"
            enabled: true
            config:
              denied_tools: ["tool.a"]
          - id: "secret-redaction"
            enabled: false
        """
    )
    path = tmp_path / "policy.yaml"
    path.write_text(content, encoding="utf-8")

    registry = RuleRegistry()
    registry.register(ToolAllowlistRule())
    registry.register(SecretRedactionRule())

    gateway = GuardrailGateway(
        registry=registry,
        guard_inbox=InMemoryGuardInbox(AsyncRuleEvaluator(registry)),
        config=GatewayConfig(),
    )
    builder = ContextSnapshotBuilder()
    router = DefaultRiskRouter()

    pack = load_policy_pack(path)
    apply_policy_config(registry, gateway, builder, router, pack)

    assert gateway.config.mode == "shadow"
    assert gateway.config.sync_timeout_ms == 10
    assert gateway.config.sync_parallel is False

    allowlist = next(
        rule
        for rule in registry.all_rules()
        if rule.rule_id == "tool-allowlist"
    )
    assert isinstance(allowlist, ToolAllowlistRule)
    assert allowlist.denied_tools == frozenset({"tool.a"})
    secret = next(
        rule
        for rule in registry.all_rules()
        if rule.rule_id == "secret-redaction"
    )
    assert secret.enabled is False
