"""Edge case tests for penguiflow patterns, policies, and registry."""

import pytest
from pydantic import BaseModel

from penguiflow.patterns import join_k
from penguiflow.policies import DictRoutingPolicy
from penguiflow.registry import ModelRegistry

# ─── join_k edge cases ───────────────────────────────────────────────────────


def test_join_k_invalid_k():
    """join_k with k <= 0 should raise ValueError."""
    with pytest.raises(ValueError, match="k must be positive"):
        join_k("test", 0)


def test_join_k_negative_k():
    """join_k with negative k should raise ValueError."""
    with pytest.raises(ValueError, match="k must be positive"):
        join_k("test", -1)


@pytest.mark.asyncio
async def test_join_k_no_trace_id():
    """join_k with message without trace_id should raise ValueError."""
    node = join_k("aggregator", 2)

    class NoTraceMsg:
        pass

    class FakeCtx:
        pass

    with pytest.raises(ValueError, match="join_k requires messages with trace_id"):
        await node.func(NoTraceMsg(), FakeCtx())


# ─── DictRoutingPolicy edge cases ────────────────────────────────────────────


def test_dict_routing_policy_from_env_missing(monkeypatch):
    """from_env with missing env var should raise KeyError."""
    monkeypatch.delenv("NONEXISTENT_VAR", raising=False)

    with pytest.raises(KeyError, match="Environment variable 'NONEXISTENT_VAR' not set"):
        DictRoutingPolicy.from_env("NONEXISTENT_VAR")


def test_dict_routing_policy_from_env_invalid_type(monkeypatch):
    """from_env with non-mapping JSON should raise TypeError."""
    monkeypatch.setenv("BAD_POLICY", '"not_a_mapping"')

    with pytest.raises(TypeError, match="Policy loader must return a mapping"):
        DictRoutingPolicy.from_env("BAD_POLICY")


def test_dict_routing_policy_from_env_array(monkeypatch):
    """from_env with array JSON should raise TypeError."""
    monkeypatch.setenv("ARRAY_POLICY", '["a", "b"]')

    with pytest.raises(TypeError, match="Policy loader must return a mapping"):
        DictRoutingPolicy.from_env("ARRAY_POLICY")


def test_dict_routing_policy_from_env_valid(monkeypatch):
    """from_env with valid JSON should work."""
    monkeypatch.setenv("VALID_POLICY", '{"key": "value"}')

    policy = DictRoutingPolicy.from_env("VALID_POLICY")
    assert policy._mapping == {"key": "value"}


def test_dict_routing_policy_from_env_with_loader(monkeypatch):
    """from_env with custom loader should use it."""
    monkeypatch.setenv("CUSTOM_POLICY", "key1:val1,key2:val2")

    def custom_loader(raw: str):
        return dict(item.split(":") for item in raw.split(","))

    policy = DictRoutingPolicy.from_env("CUSTOM_POLICY", loader=custom_loader)
    assert policy._mapping == {"key1": "val1", "key2": "val2"}


# ─── ModelRegistry edge cases ────────────────────────────────────────────────


def test_registry_non_basemodel_input():
    """register with non-BaseModel input should raise TypeError."""
    registry = ModelRegistry()

    class NotAModel:
        pass

    class OutModel(BaseModel):
        result: str

    with pytest.raises(TypeError, match="Models must inherit from pydantic.BaseModel"):
        registry.register("test", NotAModel, OutModel)  # type: ignore[arg-type]


def test_registry_non_basemodel_output():
    """register with non-BaseModel output should raise TypeError."""
    registry = ModelRegistry()

    class InModel(BaseModel):
        data: str

    class NotAModel:
        pass

    with pytest.raises(TypeError, match="Models must inherit from pydantic.BaseModel"):
        registry.register("test", InModel, NotAModel)  # type: ignore[arg-type]


def test_registry_duplicate_registration():
    """register with duplicate name should raise ValueError."""
    registry = ModelRegistry()

    class ModelA(BaseModel):
        data: str

    class ModelB(BaseModel):
        result: str

    registry.register("dup_node", ModelA, ModelB)

    with pytest.raises(ValueError, match="Node 'dup_node' already registered"):
        registry.register("dup_node", ModelA, ModelB)


def test_registry_has():
    """has() should check if node is registered."""
    registry = ModelRegistry()

    class InModel(BaseModel):
        data: str

    class OutModel(BaseModel):
        result: str

    assert registry.has("test_node") is False
    registry.register("test_node", InModel, OutModel)
    assert registry.has("test_node") is True
