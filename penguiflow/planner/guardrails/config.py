"""Policy pack loading and application for guardrails."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import yaml

from .context import ContextSnapshotBuilder, ToolRiskTier
from .models import RuleCost
from .rules import (
    InjectionPatternRule,
    JailbreakIntent,
    SecretRedactionRule,
    ToolAllowlistRule,
)

if TYPE_CHECKING:
    from .gateway import GatewayConfig, GuardrailGateway
    from .registry import RuleRegistry
    from .routing import DefaultRiskRouter


class PolicyPackError(ValueError):
    """Raised when a guardrail policy pack is malformed."""


@dataclass(frozen=True)
class GuardrailPolicyPack:
    """Normalized policy pack payload."""

    policy_id: str
    version: str
    data: dict[str, Any]


def load_policy_pack(
    path: str | Path, *, env: str | None = None
) -> GuardrailPolicyPack:
    """Load and validate a guardrail policy pack from YAML."""
    source = Path(path)
    raw = source.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, Mapping):
        msg = "Policy pack must be a mapping"
        raise PolicyPackError(msg)

    payload = dict(data)
    if env is not None:
        envs = payload.get("environments")
        if isinstance(envs, Mapping) and env in envs:
            payload = _deep_merge(payload, envs[env])

    _validate_policy_pack(payload)

    policy_id = str(payload.get("policy_pack"))
    version = str(payload.get("version"))
    return GuardrailPolicyPack(
        policy_id=policy_id, version=version, data=dict(payload)
    )


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


def _apply_gateway_config(
    cfg: GatewayConfig, payload: Mapping[str, Any]
) -> None:
    mode = payload.get("mode")
    if isinstance(mode, str):
        if mode not in {"shadow", "enforce"}:
            msg = f"Unsupported gateway mode: {mode}"
            raise PolicyPackError(msg)
        cfg.mode = cast("Literal['shadow', 'enforce']", mode)

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


def _apply_rule_configs(
    registry: RuleRegistry, rules: list[Any], *, is_async: bool
) -> None:
    rules_by_id = {rule.rule_id: rule for rule in registry.all_rules()}

    for entry in rules:
        if not isinstance(entry, Mapping):
            msg = "Rule entry must be a mapping"
            raise PolicyPackError(msg)
        rule_id = entry.get("id")
        if not isinstance(rule_id, str):
            msg = "Rule entry requires string 'id'"
            raise PolicyPackError(msg)
        rule = rules_by_id.get(rule_id)
        if rule is None:
            msg = f"Unknown rule id: {rule_id}"
            raise PolicyPackError(msg)
        if isinstance(rule.cost, RuleCost):
            if is_async and rule.cost != RuleCost.DEEP:
                msg = f"Rule '{rule_id}' is not an async rule"
                raise PolicyPackError(msg)
            if not is_async and rule.cost != RuleCost.FAST:
                msg = f"Rule '{rule_id}' is not a sync rule"
                raise PolicyPackError(msg)
        enabled = entry.get("enabled")
        if isinstance(enabled, bool):
            rule.enabled = enabled
        config = entry.get("config")
        if isinstance(config, Mapping):
            _apply_rule_config(rule, config)


def _apply_allowlist_config(
    rule: ToolAllowlistRule, config: Mapping[str, Any]
) -> None:
    """Replace the allow/deny lists given in ``config``."""
    denied = config.get("denied_tools")
    if isinstance(denied, list):
        rule.denied_tools = frozenset(str(item) for item in denied)
    allowed = config.get("allowed_tools")
    if isinstance(allowed, list):
        rule.allowed_tools = frozenset(str(item) for item in allowed)


def _apply_redaction_config(
    rule: SecretRedactionRule, config: Mapping[str, Any]
) -> None:
    """Add the secret patterns given in ``config``."""
    patterns = config.get("patterns")
    if not isinstance(patterns, Mapping):
        return
    compiled = {
        str(key): re.compile(value)
        for key, value in patterns.items()
        if isinstance(value, str)
    }
    if compiled:
        rule.patterns.update(compiled)


def _injection_pattern(item: object) -> tuple[str, JailbreakIntent] | None:
    """Parse a ``{pattern, intent}`` mapping or ``[pattern, intent]`` pair."""
    if isinstance(item, Mapping):
        pattern, intent = item.get("pattern"), item.get("intent")
    elif isinstance(item, (list, tuple)) and len(item) == 2:
        pattern, intent = item
    else:
        return None
    if not isinstance(pattern, str) or not isinstance(intent, str):
        return None
    try:
        return pattern, JailbreakIntent(intent)
    except ValueError:
        return None


def _apply_injection_config(
    rule: InjectionPatternRule, config: Mapping[str, Any]
) -> None:
    """Replace the injection patterns with the valid ones in ``config``."""
    patterns = config.get("patterns")
    if not isinstance(patterns, list):
        return
    converted = [
        parsed
        for item in patterns
        if (parsed := _injection_pattern(item)) is not None
    ]
    if converted:
        rule.patterns = tuple(converted)


def _apply_rule_config(rule: object, config: Mapping[str, Any]) -> None:
    if isinstance(rule, ToolAllowlistRule):
        _apply_allowlist_config(rule, config)
    elif isinstance(rule, SecretRedactionRule):
        _apply_redaction_config(rule, config)
    elif isinstance(rule, InjectionPatternRule):
        _apply_injection_config(rule, config)
    else:
        for key, value in config.items():
            if hasattr(rule, key):
                setattr(rule, key, value)


def _apply_risk_router(
    router: DefaultRiskRouter, payload: Mapping[str, Any]
) -> None:
    high_wait = payload.get("high_risk_wait_ms")
    medium_wait = payload.get("medium_risk_wait_ms")
    critical_closed = payload.get("critical_fail_closed")
    router.configure(
        high_risk_wait_ms=float(high_wait)
        if isinstance(high_wait, (int, float))
        else None,
        medium_risk_wait_ms=float(medium_wait)
        if isinstance(medium_wait, (int, float))
        else None,
        critical_fail_closed=critical_closed
        if isinstance(critical_closed, bool)
        else None,
    )


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
    if "policy_pack" not in payload or not isinstance(
        payload.get("policy_pack"), str
    ):
        msg = "policy_pack must be a string"
        raise PolicyPackError(msg)
    if "version" not in payload or not isinstance(payload.get("version"), str):
        msg = "version must be a string"
        raise PolicyPackError(msg)

    for key in ("gateway", "tool_risks", "risk_router"):
        if key in payload and not isinstance(payload[key], Mapping):
            msg = f"{key} must be a mapping"
            raise PolicyPackError(msg)

    for key in ("sync_rules", "async_rules"):
        if key in payload and not isinstance(payload[key], list):
            msg = f"{key} must be a list"
            raise PolicyPackError(msg)


def _deep_merge(base: Mapping[str, Any], overlay: object) -> dict[str, Any]:
    merged = dict(base)
    if not isinstance(overlay, Mapping):
        return merged
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


__all__ = [
    "GuardrailPolicyPack",
    "PolicyPackError",
    "apply_policy_config",
    "load_policy_pack",
]
