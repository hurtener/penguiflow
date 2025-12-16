"""Validation tests for the v2.6 agent spec."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from penguiflow.cli import spec as spec_module
from penguiflow.cli.spec import (
    LineIndex,
    PlannerSpec,
    SpecValidationError,
    TypeExpression,
    UnsupportedTypeAnnotation,
    load_spec,
    parse_spec,
    parse_type_annotation,
)


def test_parse_spec_success(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: demo-agent
          description: Demo agent
          template: react
          flags:
            memory: true
        tools:
          - name: search
            description: Search the web
            side_effects: read
            args:
              query: str
            result:
              hits: list[str]
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: |
            You are helpful.
          memory_prompt: |
            Use memory responsibly.
        """
    )

    path = tmp_path / "spec.yaml"
    path.write_text(content)

    spec = load_spec(path)
    assert spec.agent.name == "demo-agent"
    assert spec.tools[0].args["query"].render() == "str"
    assert spec.tools[0].result["hits"].render() == "list[str]"
    assert spec.planner.system_prompt_extra.startswith("You are helpful.")


def test_parse_spec_supports_short_term_memory(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: memory-agent
          description: Demo agent
          template: react
          flags:
            memory: false
        tools:
          - name: search
            description: Search the web
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: |
            You are helpful.
          short_term_memory:
            enabled: true
            strategy: rolling_summary
            budget:
              full_zone_turns: 3
              summary_max_tokens: 200
              total_max_tokens: 800
              overflow_policy: truncate_oldest
        """
    )

    path = tmp_path / "spec.yaml"
    path.write_text(content)

    spec = load_spec(path)
    assert spec.planner.short_term_memory is not None
    assert spec.planner.short_term_memory.strategy == "rolling_summary"
    assert spec.planner.short_term_memory.budget.total_max_tokens == 800


def test_short_term_memory_enabled_requires_strategy(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: bad-stm
          description: Demo agent
          template: react
          flags:
            memory: false
        tools:
          - name: search
            description: Search the web
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: "Hello"
          short_term_memory:
            enabled: true
            strategy: none
        """
    )

    path = tmp_path / "spec.yaml"
    path.write_text(content)

    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(path)

    assert "short_term_memory" in str(excinfo.value)
    assert "enabled=true is not compatible" in str(excinfo.value)

def test_invalid_tool_name_reports_line(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: bad-agent
          description: Demo agent
          template: react
          flags:
            memory: true
        tools:
          - name: SearchTool
            description: Bad casing
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: "Hello"
          memory_prompt: "Memory text"
        """
    )

    path = tmp_path / "spec.yaml"
    path.write_text(content)

    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(path)

    assert "snake_case" in str(excinfo.value)
    assert any(error.line == 8 for error in excinfo.value.errors)


def test_unsupported_type_annotation(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: typed-agent
          description: Demo agent
          template: react
        tools:
          - name: search
            description: Search
            args:
              query: list[string]
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: "Hello"
          memory_prompt: "Memory text"
        """
    )

    path = tmp_path / "spec.yaml"
    path.write_text(content)

    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(path)

    assert "Unsupported type annotation" in str(excinfo.value)
    assert any(error.line == 8 for error in excinfo.value.errors)


def test_missing_memory_prompt_when_enabled(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: missing-memory
          description: Demo agent
          template: react
        tools:
          - name: search
            description: Search
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: "Hello"
        """
    )

    path = tmp_path / "spec.yaml"
    path.write_text(content)

    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(path)

    assert "memory_prompt is required" in str(excinfo.value)
    assert any(error.line == 11 for error in excinfo.value.errors)


def test_flow_node_must_reference_tool(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: flow-agent
          description: Demo agent
          template: react
        tools:
          - name: search
            description: Search
        flows:
          - name: linear
            description: Pipeline
            nodes:
              - name: fetch
                description: Fetch data
            steps: [fetch]
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: "Hello"
          memory_prompt: "Memory text"
        """
    )

    path = tmp_path / "spec.yaml"
    path.write_text(content)

    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(path)

    assert "must reference a defined tool" in str(excinfo.value)
    assert any(error.line == 12 for error in excinfo.value.errors)


def test_duplicate_tool_names_are_rejected(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: dup-agent
          description: Demo agent
          template: react
        tools:
          - name: search
            description: First
          - name: search
            description: Second
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: "Hello"
          memory_prompt: "Memory text"
        """
    )

    path = tmp_path / "spec.yaml"
    path.write_text(content)

    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(path)

    assert "Duplicate tool name" in str(excinfo.value)
    assert any(error.line == 8 for error in excinfo.value.errors)


def test_yaml_syntax_error_has_line_number() -> None:
    content = "agent:\n  name: ok\n tools:\n  - name: bad"

    with pytest.raises(SpecValidationError) as excinfo:
        parse_spec(content, source="inline.yaml")

    assert "inline.yaml" in str(excinfo.value)
    assert any(error.line is not None for error in excinfo.value.errors)


def test_type_expression_render_and_errors() -> None:
    list_expr = parse_type_annotation("list[int]")
    optional_expr = parse_type_annotation("Optional[str]")
    dict_expr = parse_type_annotation("dict[str,bool]")

    assert list_expr.render() == "list[int]"
    assert optional_expr.render() == "Optional[str]"
    assert dict_expr.render() == "dict[str, bool]"

    with pytest.raises(UnsupportedTypeAnnotation):
        parse_type_annotation(123)  # type: ignore[arg-type]

    with pytest.raises(UnsupportedTypeAnnotation):
        parse_type_annotation("   ")

    with pytest.raises(UnsupportedTypeAnnotation):
        parse_type_annotation("dict[list,str]")


def test_line_index_missing_path() -> None:
    index = LineIndex({("root",): 1})
    assert index.line_for(("missing",)) is None


def test_empty_spec_and_non_mapping_root(tmp_path: Path) -> None:
    with pytest.raises(SpecValidationError) as empty_exc:
        parse_spec("", source="empty.yaml")
    assert "Spec file is empty" in str(empty_exc.value)

    list_spec = "- just-a-list"
    with pytest.raises(SpecValidationError) as root_exc:
        parse_spec(list_spec, source="list.yaml")
    assert "Spec root must be a mapping" in str(root_exc.value)


def test_yaml_constructor_error_reports_line() -> None:
    content = "unknown: !unknown_tag value"
    with pytest.raises(SpecValidationError) as excinfo:
        parse_spec(content, source="bad.yaml")

    assert "bad.yaml" in str(excinfo.value)
    assert any("unknown_tag" in detail.message for detail in excinfo.value.errors)
    assert any(detail.line is not None for detail in excinfo.value.errors)


def test_agent_tool_and_planner_field_validations() -> None:
    content = dedent(
        """\
        agent:
          name: ""
          description: ""
          template: react
        tools:
          - name: for
            description: ""
            args: []
        llm:
          primary:
            model: ""
        planner:
          system_prompt_extra: ""
          memory_prompt: ""
        """
    )

    with pytest.raises(SpecValidationError) as excinfo:
        parse_spec(content, source="invalid.yaml")

    message = str(excinfo.value)
    assert "agent.name is required" in message
    assert "agent.description is required" in message
    assert "tool description is required" in message
    assert "must be a mapping of field name to type annotation" in message
    assert "llm.primary.model is required" in message
    assert "planner.system_prompt_extra is required" in message
    assert "planner.memory_prompt cannot be empty" in message


def test_reserved_tool_name_rejected(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: reserved-tool
          description: Demo agent
          template: react
        tools:
          - name: for
            description: Reserved
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: "Hello"
          memory_prompt: "Memory text"
        """
    )

    path = tmp_path / "reserved.yaml"
    path.write_text(content)

    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(path)

    assert "Tool name 'for' is reserved" in str(excinfo.value)


def test_flow_validation_matrix(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: flow-checks
          description: Demo agent
          template: react
        tools:
          - name: search
            description: Search
          - name: run_task
            description: Run task
          - name: bad_node
            description: bad tool
        flows:
          - name: empty_flow
            description: Nothing here
          - name: duplicate_nodes
            description: Dupes
            nodes:
              - name: search
                description: first
              - name: search
                description: again
          - name: bad_node_name
            description: casing issues
            nodes:
              - name: BadNode
                description: not snake
          - name: invalid_steps
            description: step problems
            nodes:
              - name: run_task
                description: run
            steps: [run_task, not-snake, ghost]
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: "Hello"
          memory_prompt: "Memory text"
        """
    )

    path = tmp_path / "flows.yaml"
    path.write_text(content)

    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(path)

    message = str(excinfo.value)
    assert "Flow must define at least one node or step" in message
    assert "Flow node names must be unique within a flow" in message
    assert "Flow node names must be snake_case" in message
    assert "Flow step names must be snake_case" in message
    assert "Flow step 'ghost' is not defined" in message


def test_external_tool_preset_parses(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: ext-agent
          description: Demo agent
          template: react
        tools:
          - name: search
            description: Search the web
        external_tools:
          presets:
            - preset: github
              auth_override: bearer
              env:
                token: "${GITHUB_TOKEN}"
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: "Hello"
          memory_prompt: "Memory"
        """
    )
    path = tmp_path / "ext.yaml"
    path.write_text(content)
    spec = load_spec(path)
    assert spec.external_tools.presets[0].preset == "github"
    assert spec.external_tools.presets[0].env["token"] == "${GITHUB_TOKEN}"


def test_external_invalid_preset_name(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: bad-ext
          description: Demo agent
          template: react
        tools:
          - name: search
            description: Search
        external_tools:
          presets:
            - preset: unknown
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: "Hello"
          memory_prompt: "Memory"
        """
    )
    path = tmp_path / "ext-bad.yaml"
    path.write_text(content)
    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(path)
    assert "Unknown preset" in str(excinfo.value)


def test_external_custom_with_fields(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: ext-custom
          description: Demo agent
          template: react
        tools:
          - name: search
            description: Search
        external_tools:
          custom:
            - name: my_api
              transport: utcp
              connection: "https://api.example.com/.well-known/utcp"
              auth_type: bearer
              auth_config:
                token: "${API_TOKEN}"
              env:
                region: "us-east-1"
              description: "Custom API"
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: "Hello"
          memory_prompt: "Memory"
        """
    )
    path = tmp_path / "ext-custom.yaml"
    path.write_text(content)
    spec = load_spec(path)
    assert spec.external_tools.custom[0].name == "my_api"
    assert spec.external_tools.custom[0].transport == "utcp"
    assert spec.external_tools.custom[0].auth_config["token"] == "${API_TOKEN}"


def test_external_duplicate_custom_names_rejected(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: dup-ext
          description: Demo agent
          template: react
        tools:
          - name: search
            description: Search
        external_tools:
          custom:
            - name: data_api
              connection: "npx -y server-a"
            - name: data_api
              connection: "npx -y server-b"
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: "Hello"
          memory_prompt: "Memory"
        """
    )
    path = tmp_path / "ext-dup.yaml"
    path.write_text(content)
    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(path)
    assert "Duplicate external tool name" in str(excinfo.value)


def test_service_requires_base_url_when_enabled(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: service-check
          description: Demo agent
          template: react
        tools:
          - name: search
            description: Search
        services:
          memory_iceberg:
            enabled: true
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: "Hello"
          memory_prompt: "Memory text"
        """
    )

    path = tmp_path / "services.yaml"
    path.write_text(content)

    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(path)

    assert "base_url is required when enabled" in str(excinfo.value)


def test_llm_reflection_and_prompts_validation(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: llm-check
          description: Demo agent
          template: react
        tools:
          - name: search
            description: Search
        llm:
          primary:
            model: gpt-4o
          reflection:
            enabled: true
            quality_threshold: 1.5
        planner:
          system_prompt_extra: "   "
          memory_prompt: ""
        """
    )

    path = tmp_path / "llm.yaml"
    path.write_text(content)

    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(path)

    message = str(excinfo.value)
    assert "quality_threshold must be between 0.0 and 1.0" in message
    assert "planner.system_prompt_extra is required" in message
    assert "planner.memory_prompt cannot be empty" in message


def test_reflection_valid_threshold_and_memory_disabled(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: reflection-valid
          description: Demo agent
          template: react
          flags:
            memory: false
        tools:
          - name: search
            description: Search
        llm:
          primary:
            model: gpt-4o
          reflection:
            enabled: true
            quality_threshold: 0.5
        planner:
          system_prompt_extra: "Hello"
        """
    )

    path = tmp_path / "reflection.yaml"
    path.write_text(content)

    spec = load_spec(path)
    assert spec.llm.reflection is not None
    assert spec.planner.memory_prompt is None


def test_tool_and_flow_required_fields(tmp_path: Path) -> None:
    content = dedent(
        """\
        agent:
          name: required-fields
          description: Demo agent
          template: react
        tools:
          - name: ""
            description: ""
            args: null
        flows:
          - name: ""
            description: ""
            nodes:
              - name: ""
                description: ""
        llm:
          primary:
            model: gpt-4o
        planner:
          system_prompt_extra: "Hello"
          memory_prompt: "Memory text"
        """
    )

    path = tmp_path / "required.yaml"
    path.write_text(content)

    with pytest.raises(SpecValidationError) as excinfo:
        load_spec(path)

    message = str(excinfo.value)
    assert "tool name is required" in message
    assert "tool description is required" in message
    assert "flow name is required" in message
    assert "flow description is required" in message
    assert "flow node name is required" in message
    assert "flow node description is required" in message


def test_type_expression_raw_render_and_non_scalar_loc() -> None:
    expr = TypeExpression(raw="Custom", kind="custom")
    assert expr.render() == "Custom"

    normalized = spec_module._normalize_loc(["a", object()])
    assert normalized == ("a", str(normalized[1]))


def test_dict_keys_must_be_primitive() -> None:
    with pytest.raises(UnsupportedTypeAnnotation) as excinfo:
        parse_type_annotation("dict[list[str],int]")
    assert "dict keys must be primitive" in str(excinfo.value)


def test_line_index_handles_non_scalar_keys() -> None:
    class DummyMark:
        def __init__(self, line: int = 0) -> None:
            self.line = line

    key_node = SequenceNode(
        tag="tag:yaml.org,2002:seq",
        value=[ScalarNode(tag="tag:yaml.org,2002:str", value="a", start_mark=DummyMark(), end_mark=DummyMark())],
        start_mark=DummyMark(),
        end_mark=DummyMark(),
    )
    value_node = ScalarNode(
        tag="tag:yaml.org,2002:str",
        value="value",
        start_mark=DummyMark(2),
        end_mark=DummyMark(2),
    )
    mapping_node = MappingNode(
        tag="tag:yaml.org,2002:map",
        value=[(key_node, value_node)],
        start_mark=DummyMark(1),
        end_mark=DummyMark(3),
    )
    mapping: dict[tuple[str | int, ...], int] = {}
    spec_module._index_yaml_node(mapping_node, (), mapping)

    assert mapping[()] == 2


def test_memory_prompt_can_be_omitted_when_disabled() -> None:
    planner = PlannerSpec.model_validate({"system_prompt_extra": "hello"})
    assert planner.memory_prompt is None


def test_memory_prompt_validator_allows_none() -> None:
    assert PlannerSpec._non_empty_memory_prompt(None) is None
