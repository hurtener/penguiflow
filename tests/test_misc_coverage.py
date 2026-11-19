"""Coverage tests for message envelopes and node policies."""

import pytest

from penguiflow import NodePolicy

# ─── Core coverage - Message envelope ────────────────────────────────────────


def test_message_headers_propagation():
    """Message headers should be preserved through copy."""
    from penguiflow.types import Headers, Message

    headers = Headers(tenant="test_tenant", topic="test_topic", priority=5)
    msg = Message(payload={"data": "test"}, headers=headers)

    copy = msg.model_copy(update={"payload": {"new": "data"}})
    assert copy.headers.tenant == "test_tenant"
    assert copy.headers.priority == 5


# ─── Node policy tests ───────────────────────────────────────────────────────


def test_node_policy_defaults():
    """NodePolicy should have correct defaults."""
    policy = NodePolicy()
    assert policy.validate == "both"
    assert policy.timeout_s is None
    assert policy.max_retries == 0


def test_node_policy_custom():
    """NodePolicy should accept custom values."""
    policy = NodePolicy(validate="in", timeout_s=30.0, max_retries=3)
    assert policy.validate == "in"
    assert policy.timeout_s == 30.0
    assert policy.max_retries == 3


def test_node_policy_invalid_validate():
    """NodePolicy should reject invalid validate values."""
    with pytest.raises(ValueError, match="validate must be one of"):
        NodePolicy(validate="invalid")
