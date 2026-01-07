"""Tests for the penguiflow generate command."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from click.testing import CliRunner

from penguiflow.cli import app
from penguiflow.cli.generate import run_generate


def _write_spec(path: Path) -> None:
    path.write_text(
        dedent(
            """\
            agent:
              name: demo-gen
              description: Demo agent
              template: react
              flags:
                streaming: true
                memory: true
            tools:
              - name: fetch_data
                description: Fetch data from source
                side_effects: read
                tags: ["io"]
                args:
                  query: str
                  limit: Optional[int]
                result:
                  items: list[str]
              - name: write_data
                description: Persist data
                side_effects: write
                tags: ["io", "db"]
                args:
                  payload: dict[str,int]
                result:
                  status: str
            flows:
              - name: pipeline
                description: Linear pipeline
                nodes:
                  - name: fetch_data
                    description: Fetch data
                  - name: write_data
                    description: Write data
                steps: [fetch_data, write_data]
            llm:
              primary:
                model: gpt-4o
              summarizer:
                enabled: true
              reflection:
                enabled: true
                quality_threshold: 0.7
                max_revisions: 1
            planner:
              max_iters: 9
              hop_budget: 5
              absolute_max_parallel: 3
              system_prompt_extra: |
                You are helpful.
              memory_prompt: |
                Use memory responsibly.
              short_term_memory:
                enabled: true
                strategy: rolling_summary
                budget:
                  full_zone_turns: 3
                  summary_max_tokens: 200
                  total_max_tokens: 800
                  overflow_policy: truncate_oldest
              hints:
                ordering: ["fetch_data", "write_data"]
                disallow: []
            """
        )
    )


def _write_spec_with_background_tasks(path: Path) -> None:
    path.write_text(
        dedent(
            """\
            agent:
              name: demo-gen-bg
              description: Demo agent with background tasks
              template: react
              flags:
                streaming: true
                hitl: true
                memory: false
                background_tasks: true
            tools:
              - name: fetch_data
                description: Fetch data from source
                side_effects: read
                tags: ["io"]
                args:
                  query: str
                result:
                  items: list[str]
            llm:
              primary:
                model: gpt-4o
            planner:
              max_iters: 3
              hop_budget: 2
              absolute_max_parallel: 3
              system_prompt_extra: |
                You are helpful.
              background_tasks:
                enabled: true
                allow_tool_background: true
                default_mode: subagent
                default_merge_strategy: HUMAN_GATED
                context_depth: full
                propagate_on_cancel: cascade
                spawn_requires_confirmation: false
                include_prompt_guidance: true
                max_concurrent_tasks: 3
                max_tasks_per_session: 10
                task_timeout_s: 600
                max_pending_steering: 2
            """
        )
    )


def test_run_generate_creates_planner_and_tools(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.yaml"
    _write_spec(spec_path)

    result = run_generate(
        spec_path=spec_path,
        output_dir=tmp_path,
        force=True,
        quiet=True,
    )

    assert result.success
    project_dir = tmp_path / "demo-gen"
    package_dir = project_dir / "src" / "demo_gen"

    fetch_tool = package_dir / "tools" / "fetch_data.py"
    write_tool = package_dir / "tools" / "write_data.py"
    tools_init = package_dir / "tools" / "__init__.py"
    planner = package_dir / "planner.py"
    flow_file = package_dir / "flows" / "pipeline.py"
    flows_init = package_dir / "flows" / "__init__.py"
    flow_test = project_dir / "tests" / "test_flows" / "test_pipeline.py"
    tool_test = project_dir / "tests" / "test_tools" / "test_fetch_data.py"
    config_file = package_dir / "config.py"
    env_example = project_dir / ".env.example"

    assert fetch_tool.exists()
    assert write_tool.exists()
    assert tools_init.exists()
    assert planner.exists()
    assert flow_file.exists()
    assert flows_init.exists()
    assert flow_test.exists()
    assert tool_test.exists()
    assert config_file.exists()
    assert env_example.exists()

    fetch_content = fetch_tool.read_text()
    assert "class FetchDataArgs" in fetch_content
    assert "limit: int | None" in fetch_content
    assert "@tool" in fetch_content

    write_content = write_tool.read_text()
    assert "dict[str, int]" in write_content
    assert "NotImplementedError" in write_content

    init_content = tools_init.read_text()
    assert "build_catalog_bundle" in init_content
    assert "registry.register(\"fetch_data\"" in init_content

    planner_content = planner.read_text()
    assert "ReactPlanner" in planner_content
    assert "SYSTEM_PROMPT_EXTRA" in planner_content
    assert "memory_enabled" in planner_content
    assert "_build_short_term_memory" in planner_content
    assert "ReflectionConfig" in planner_content
    assert "\"ordering\": ['fetch_data', 'write_data']" in planner_content or "ordering" in planner_content
    assert "absolute_max_parallel" in planner_content

    flow_content = flow_file.read_text()
    assert "Flow bundle for pipeline" in flow_content
    assert "NodePolicy" in flow_content
    assert "registry.register" in flow_content

    flow_test_content = flow_test.read_text()
    assert "Flow executes end-to-end" in flow_test_content

    tool_test_content = tool_test.read_text()
    assert "NotImplementedError" in tool_test_content

    config_content = config_file.read_text()
    assert "gpt-4o" in config_content
    assert "memory_enabled" in config_content
    assert "short_term_memory_enabled" in config_content

    env_content = env_example.read_text()
    assert "LLM_MODEL" in env_content
    assert "PLANNER_MAX_ITERS" in env_content
    assert "SHORT_TERM_MEMORY_ENABLED" in env_content

    # Test agent.yaml is persisted for playground discovery
    agent_yaml = project_dir / "agent.yaml"
    assert agent_yaml.exists(), "agent.yaml should be created for playground discovery"
    agent_yaml_content = agent_yaml.read_text()
    assert "demo-gen" in agent_yaml_content
    assert "fetch_data" in agent_yaml_content

    # Test ENV_SETUP.md is generated with provider docs
    env_setup_md = project_dir / "ENV_SETUP.md"
    assert env_setup_md.exists(), "ENV_SETUP.md should be created"
    env_setup_content = env_setup_md.read_text()
    assert "OPENAI_API_KEY" in env_setup_content
    assert "ANTHROPIC_API_KEY" in env_setup_content
    assert "demo-gen" in env_setup_content or "demo_gen" in env_setup_content


def test_run_generate_includes_background_tasks_wiring(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.yaml"
    _write_spec_with_background_tasks(spec_path)

    result = run_generate(
        spec_path=spec_path,
        output_dir=tmp_path,
        force=True,
        quiet=True,
    )

    assert result.success
    project_dir = tmp_path / "demo-gen-bg"
    package_dir = project_dir / "src" / "demo_gen_bg"

    planner = package_dir / "planner.py"
    config_file = package_dir / "config.py"
    env_example = project_dir / ".env.example"
    orchestrator = package_dir / "orchestrator.py"

    assert planner.exists()
    assert config_file.exists()
    assert env_example.exists()
    assert orchestrator.exists()

    planner_content = planner.read_text()
    assert "build_task_tool_specs" in planner_content
    assert "BackgroundTasksConfig" in planner_content
    assert "background_tasks=" in planner_content

    config_content = config_file.read_text()
    assert "background_tasks_enabled" in config_content
    assert "BACKGROUND_TASKS_ENABLED" in config_content

    env_content = env_example.read_text()
    assert "BACKGROUND_TASKS_ENABLED" in env_content
    assert "BACKGROUND_TASKS_MAX_CONCURRENT_TASKS" in env_content

    orchestrator_content = orchestrator.read_text()
    assert "InProcessTaskService" in orchestrator_content
    assert "in-process fallback" in orchestrator_content


def test_generate_cli_dry_run(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.yaml"
    _write_spec(spec_path)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "generate",
            "--spec",
            str(spec_path),
            "--output-dir",
            str(tmp_path),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert not (tmp_path / "demo-gen").exists()
