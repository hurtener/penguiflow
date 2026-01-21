"""Policy pack loading and application for guardrails."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml  # type: ignore[import-untyped,unused-ignore]

from .context import ContextSnapshotBuilder, ToolRiskTier
from .gateway import GatewayConfig, GuardrailGateway
from .models import RuleCost
from .registry import RuleRegistry
from .routing import DefaultRiskRouter
from .rules import InjectionPatternRule, JailbreakIntent, SecretRedactionRule, ToolAllowlistRule


@dataclass(frozen=True)
class GuardrailPolicyPack:
    """Normalized policy pack payload."""

    policy_id: str
    version: str
    data: dict[str, Any]


def load_policy_pack(path: str | Path, *, env: str | None = None) -> GuardrailPolicyPack:
    """Load and validate a guardrail policy pack from YAML."""

    source = Path(path)
    raw = source.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, Mapping):
        raise ValueError("Policy pack must be a mapping")

    payload = dict(data)
    if env is not None:
        envs = payload.get("environments")
        if isinstance(envs, Mapping) and env in envs:
            payload = _deep_merge(payload, envs[env])

    _validate_policy_pack(payload)

    policy_id = str(payload.get("policy_pack"))
    version = str(payload.get("version"))
    return GuardrailPolicyPack(policy_id=policy_id, version=version, data=dict(payload))


def apply_policy_config(
    registry: RuleRegistry,
    gateway: GuardrailGateway,
    builder: ContextSnapshotBuilder,
    router: DefaultRiskRouter,
    policy: GuardrailPolicyPack,
) -> None:
    """Apply policy pack settings to guardrail components."""

    data = policy.data

    gateway_cfg = data.get("gateway")
    if isinstance(gateway_cfg, Mapping):
        _apply_gateway_config(gateway.config, gateway_cfg)

    tool_risks = data.get("tool_risks")
    if isinstance(tool_risks, Mapping):
        builder.set_tool_risks(_parse_tool_risks(tool_risks))

    sync_rules = data.get("sync_rules")
    if isinstance(sync_rules, list):
        _apply_rule_configs(registry, sync_rules, is_async=False)

    async_rules = data.get("async_rules")
    if isinstance(async_rules, list):
        _apply_rule_configs(registry, async_rules, is_async=True)

    risk_cfg = data.get("risk_router")
    if isinstance(risk_cfg, Mapping):
        _apply_risk_router(router, risk_cfg)


def _apply_gateway_config(cfg: GatewayConfig, payload: Mapping[str, Any]) -> None:
    mode = payload.get("mode")
    if isinstance(mode, str):
        if mode not in {"shadow", "enforce"}:
            raise ValueError(f"Unsupported gateway mode: {mode}")
        cfg.mode = cast(Literal["shadow", "enforce"], mode)

    sync_cfg = payload.get("sync")
    if isinstance(sync_cfg, Mapping):
        timeout_ms = sync_cfg.get("timeout_ms")
        if isinstance(timeout_ms, (int, float)):
            cfg.sync_timeout_ms = float(timeout_ms)
        parallel = sync_cfg.get("parallel")
        if isinstance(parallel, bool):
            cfg.sync_parallel = parallel
        fail_open = sync_cfg.get("fail_open")
        if isinstance(fail_open, bool):
            cfg.sync_fail_open = fail_open

    async_cfg = payload.get("async")
    if isinstance(async_cfg, Mapping):
        enabled = async_cfg.get("enabled")
        if isinstance(enabled, bool):
            cfg.async_enabled = enabled
        fail_open = async_cfg.get("fail_open")
        if isinstance(fail_open, bool):
            cfg.async_fail_open = fail_open


def _apply_rule_configs(registry: RuleRegistry, rules: list[Any], *, is_async: bool) -> None:
    rules_by_id = {rule.rule_id: rule for rule in registry.all_rules()}

    for entry in rules:
        if not isinstance(entry, Mapping):
            raise ValueError("Rule entry must be a mapping")
        rule_id = entry.get("id")
        if not isinstance(rule_id, str):
            raise ValueError("Rule entry requires string 'id'")
        rule = rules_by_id.get(rule_id)
        if rule is None:
            raise ValueError(f"Unknown rule id: {rule_id}")
        if isinstance(rule.cost, RuleCost):
            if is_async and rule.cost != RuleCost.DEEP:
                raise ValueError(f"Rule '{rule_id}' is not an async rule")
            if not is_async and rule.cost != RuleCost.FAST:
                raise ValueError(f"Rule '{rule_id}' is not a sync rule")
        enabled = entry.get("enabled")
        if isinstance(enabled, bool):
            rule.enabled = enabled
        config = entry.get("config")
        if isinstance(config, Mapping):
            _apply_rule_config(rule, config)


def _apply_rule_config(rule: Any, config: Mapping[str, Any]) -> None:
    if isinstance(rule, ToolAllowlistRule):
        denied = config.get("denied_tools")
        if isinstance(denied, list):
            rule.denied_tools = frozenset(str(item) for item in denied)
        allowed = config.get("allowed_tools")
        if isinstance(allowed, list):
            rule.allowed_tools = frozenset(str(item) for item in allowed)
        return

    if isinstance(rule, SecretRedactionRule):
        patterns = config.get("patterns")
        if isinstance(patterns, Mapping):
            compiled: dict[str, re.Pattern[str]] = {}
            for key, value in patterns.items():
                if isinstance(value, str):
                    compiled[str(key)] = re.compile(value)
            if compiled:
                rule.patterns.update(compiled)
        return

    if isinstance(rule, InjectionPatternRule):
        patterns = config.get("patterns")
        if isinstance(patterns, list):
            converted: list[tuple[str, JailbreakIntent]] = []
            for item in patterns:
                if isinstance(item, Mapping):
                    pattern = item.get("pattern")
                    intent = item.get("intent")
                    if isinstance(pattern, str) and isinstance(intent, str):
                        try:
                            converted.append((pattern, JailbreakIntent(intent)))
                        except ValueError:
                            continue
                elif isinstance(item, (list, tuple)) and len(item) == 2:
                    pattern, intent = item
                    if isinstance(pattern, str) and isinstance(intent, str):
                        try:
                            converted.append((pattern, JailbreakIntent(intent)))
                        except ValueError:
                            continue
            if converted:
                rule.patterns = tuple(converted)
        return

    for key, value in config.items():
        if hasattr(rule, key):
            setattr(rule, key, value)


def _apply_risk_router(router: DefaultRiskRouter, payload: Mapping[str, Any]) -> None:
    high_wait = payload.get("high_risk_wait_ms")
    if isinstance(high_wait, (int, float)):
        router._high_wait = float(high_wait)
    medium_wait = payload.get("medium_risk_wait_ms")
    if isinstance(medium_wait, (int, float)):
        router._medium_wait = float(medium_wait)
    critical_closed = payload.get("critical_fail_closed")
    if isinstance(critical_closed, bool):
        router._critical_fail_closed = critical_closed


def _parse_tool_risks(payload: Mapping[str, Any]) -> dict[str, ToolRiskTier]:
    parsed: dict[str, ToolRiskTier] = {}
    for key, value in payload.items():
        if not isinstance(value, str):
            continue
        try:
            parsed[str(key)] = ToolRiskTier(value)
        except ValueError:
            continue
    return parsed


def _validate_policy_pack(payload: Mapping[str, Any]) -> None:
    if "policy_pack" not in payload or not isinstance(payload.get("policy_pack"), str):
        raise ValueError("policy_pack must be a string")
    if "version" not in payload or not isinstance(payload.get("version"), str):
        raise ValueError("version must be a string")

    for key in ("gateway", "tool_risks", "risk_router"):
        if key in payload and not isinstance(payload[key], Mapping):
            raise ValueError(f"{key} must be a mapping")

    for key in ("sync_rules", "async_rules"):
        if key in payload and not isinstance(payload[key], list):
            raise ValueError(f"{key} must be a list")


def _deep_merge(base: Mapping[str, Any], overlay: Any) -> dict[str, Any]:
    merged = dict(base)
    if not isinstance(overlay, Mapping):
        return merged
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


__all__ = ["GuardrailPolicyPack", "apply_policy_config", "load_policy_pack"]
