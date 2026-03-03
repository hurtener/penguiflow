"""PenguiFlow command-line interface."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from penguiflow.cli.dev import CLIError as DevCLIError
from penguiflow.cli.dev import run_dev
from penguiflow.cli.tools import ToolsCLIError, parse_env_overrides, run_tools_connect, run_tools_list


@click.group()
@click.version_option()
def app() -> None:
    """PenguiFlow CLI - bootstrap and manage agent projects."""


@app.command()
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing files if they already exist.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show which files would be created without writing them.",
)
@click.option(
    "--no-launch",
    is_flag=True,
    help="Skip generating .vscode/launch.json.",
)
@click.option(
    "--no-tasks",
    is_flag=True,
    help="Skip generating .vscode/tasks.json.",
)
@click.option(
    "--no-settings",
    is_flag=True,
    help="Skip generating .vscode/settings.json.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress output messages (useful for scripting).",
)
def init(
    force: bool,
    dry_run: bool,
    no_launch: bool,
    no_tasks: bool,
    no_settings: bool,
    quiet: bool,
) -> None:
    """Initialize PenguiFlow development tooling in the current directory."""
    from .init import CLIError, run_init

    try:
        result = run_init(
            force=force,
            dry_run=dry_run,
            include_launch=not no_launch,
            include_tasks=not no_tasks,
            include_settings=not no_settings,
            quiet=quiet,
        )
        if not result.success:
            sys.exit(1)
    except CLIError as e:
        click.echo(f"✗ {e.message}", err=True)
        if e.hint:
            click.echo(f"  Hint: {e.hint}", err=True)
        sys.exit(1)


@app.command()
@click.option(
    "--project-root",
    "-p",
    type=click.Path(path_type=str),
    default=".",
    help="Project directory containing the agent. Defaults to current directory.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host to bind the playground server.",
)
@click.option(
    "--port",
    default=8001,
    show_default=True,
    help="Port to bind the playground server.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Do not open the browser automatically.",
)
def dev(project_root: str, host: str, port: int, no_browser: bool) -> None:
    """Launch the playground backend + UI for a project.

    IMPORTANT: The playground runs in penguiflow's Python environment, not the
    agent project's venv. To use LLM features, ensure the agent project has
    penguiflow[planner] installed, or install dependencies in penguiflow's venv:

    \b
    # Option 1: Install agent in editable mode (recommended)
    cd <project_root> && uv sync
    cd <penguiflow_dir> && uv pip install -e <project_root>

    \b
    # Option 2: Install litellm directly
    uv pip install litellm
    """
    from pathlib import Path

    try:
        run_dev(
            project_root=Path(project_root),
            host=host,
            port=port,
            open_browser=not no_browser,
        )
    except DevCLIError as e:
        click.echo(f"✗ {e.message}", err=True)
        if e.hint:
            click.echo(f"  Hint: {e.hint}", err=True)
        sys.exit(1)


@app.command()
@click.argument("name")
@click.option(
    "--template",
    "-t",
    default="react",
    type=click.Choice(
        ["minimal", "react", "parallel", "flow", "controller", "rag_server", "wayfinder", "analyst", "enterprise"],
        case_sensitive=False,
    ),
    show_default=True,
    help="Template to scaffold.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing files if they already exist.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show which files would be created without writing them.",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=str),
    default=None,
    help="Directory where the project should be created (defaults to cwd).",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress output messages.",
)
@click.option(
    "--with-streaming",
    is_flag=True,
    help="Include streaming support (StreamChunk + status publisher).",
)
@click.option(
    "--with-hitl",
    is_flag=True,
    help="Include HITL pause/resume handling.",
)
@click.option(
    "--with-a2a",
    is_flag=True,
    help="Include A2A HTTP+JSON binding.",
)
@click.option(
    "--with-rich-output",
    is_flag=True,
    help="Include rich output component tooling (UI artifacts).",
)
@click.option(
    "--no-memory",
    is_flag=True,
    help="Skip memory integration stubs.",
)
@click.option(
    "--with-background-tasks",
    is_flag=True,
    help="Include background task orchestration (subagent spawning, task management).",
)
def new(
    name: str,
    template: str,
    force: bool,
    dry_run: bool,
    output_dir: str | None,
    quiet: bool,
    with_streaming: bool,
    with_hitl: bool,
    with_a2a: bool,
    with_rich_output: bool,
    no_memory: bool,
    with_background_tasks: bool,
) -> None:
    """Create a new PenguiFlow agent project."""
    from pathlib import Path

    from .init import CLIError
    from .new import run_new

    try:
        result = run_new(
            name=name,
            template=template.lower(),
            force=force,
            dry_run=dry_run,
            output_dir=Path(output_dir) if output_dir else None,
            quiet=quiet,
            with_streaming=with_streaming,
            with_hitl=with_hitl,
            with_a2a=with_a2a,
            with_rich_output=with_rich_output,
            no_memory=no_memory,
            with_background_tasks=with_background_tasks,
        )
        if not result.success:
            sys.exit(1)
    except CLIError as e:
        click.echo(f"✗ {e.message}", err=True)
        if e.hint:
            click.echo(f"  Hint: {e.hint}", err=True)
        sys.exit(1)


@app.group()
def tools() -> None:
    """Manage ToolNode presets and discovery."""


@tools.command("list")
def tools_list() -> None:
    """List built-in ToolNode presets."""
    try:
        run_tools_list()
    except ToolsCLIError as e:
        click.echo(f"✗ {e.message}", err=True)
        if e.hint:
            click.echo(f"  Hint: {e.hint}", err=True)
        sys.exit(1)


@tools.command("connect")
@click.argument("preset")
@click.option(
    "--discover",
    is_flag=True,
    help="Connect to the preset and fetch available tools.",
)
@click.option(
    "--show-tools/--no-show-tools",
    default=True,
    show_default=True,
    help="Display tool names when discovering.",
)
@click.option(
    "--max-tools",
    default=20,
    show_default=True,
    help="Maximum tools to display when showing tools.",
)
@click.option(
    "--env",
    "env_overrides",
    multiple=True,
    help="Override environment variables for the preset (KEY=VALUE).",
)
def tools_connect(
    preset: str,
    discover: bool,
    show_tools: bool,
    max_tools: int,
    env_overrides: tuple[str, ...],
) -> None:
    """Fetch and display tools for a preset."""
    try:
        env = parse_env_overrides(env_overrides)
        result = run_tools_connect(
            preset,
            discover=discover,
            show_tools=show_tools,
            max_tools=max_tools,
            env_overrides=env,
        )
        if not result.success:
            sys.exit(1)
    except ToolsCLIError as e:
        click.echo(f"✗ {e.message}", err=True)
        if e.hint:
            click.echo(f"  Hint: {e.hint}", err=True)
        sys.exit(1)


@app.group()
def eval() -> None:
    """Run trace-derived dataset export and evaluation workflows."""


def _load_env_file_values(file_path: Path) -> dict[str, str]:
    if not file_path.exists():
        return {}
    values: dict[str, str] = {}
    for line in file_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key] = value
    return values


def _resolve_cli_env_files(raw_paths: tuple[str, ...], *, base_dir: Path) -> list[Path]:
    resolved: list[Path] = []
    for raw in raw_paths:
        candidate = Path(raw)
        resolved.append(candidate.resolve() if candidate.is_absolute() else (base_dir / candidate).resolve())
    return resolved


def _apply_env_files(
    *,
    spec_env_files: tuple[Path, ...],
    cli_env_files: tuple[str, ...],
    base_dir: Path,
) -> None:
    ordered_env_files: list[Path] = list(spec_env_files)
    ordered_env_files.extend(_resolve_cli_env_files(cli_env_files, base_dir=base_dir))
    for env_path in ordered_env_files:
        if not env_path.exists():
            raise ValueError(f"env file does not exist: {env_path} (resolution base: {base_dir})")

    for env_path in ordered_env_files:
        for key, value in _load_env_file_values(env_path).items():
            if key not in os.environ:
                os.environ[key] = value


@eval.command("run")
@click.option(
    "--spec",
    "spec_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Path to eval spec JSON file. Relative spec fields resolve from project_root.",
)
@click.option(
    "--env-file",
    "env_files",
    multiple=True,
    type=click.Path(dir_okay=False, path_type=str),
    help="Optional environment file(s) loaded before evaluation (relative to project_root).",
)
def eval_run(spec_path: str, env_files: tuple[str, ...]) -> None:
    """Run collect->export->evaluate workflow from spec."""
    import asyncio
    import json
    from pathlib import Path

    from penguiflow.evals.api import load_eval_run_spec, run_eval

    path = Path(spec_path).resolve()

    try:
        run_spec = load_eval_run_spec(path)
    except Exception as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    try:
        _apply_env_files(
            spec_env_files=run_spec.env_files,
            cli_env_files=env_files,
            base_dir=run_spec.project_root,
        )
    except Exception as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    try:
        result = asyncio.run(
            run_eval(
                project_root=run_spec.project_root,
                query_suite_path=run_spec.query_suite_path,
                candidates_path=run_spec.candidates_path,
                metric_spec=run_spec.metric_spec,
                output_dir=run_spec.output_dir,
                session_id=run_spec.session_id,
                dataset_tag=run_spec.dataset_tag,
                agent_package=run_spec.agent_package,
                state_store_spec=run_spec.state_store_spec,
                run_one_spec=run_spec.run_one_spec,
            )
        )
    except Exception as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    click.echo(
        json.dumps(
            {
                **result,
                "resolved_paths": {
                    "resolution_base": str(run_spec.project_root),
                    "project_root": str(run_spec.project_root),
                    "query_suite_path": str(run_spec.query_suite_path),
                    "candidates_path": str(run_spec.candidates_path),
                    "output_dir": str(run_spec.output_dir),
                    "env_files": [str(item) for item in run_spec.env_files],
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


@eval.command("collect")
@click.option(
    "--spec",
    "spec_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Path to eval collect spec JSON file. Relative spec fields resolve from project_root.",
)
@click.option(
    "--env-file",
    "env_files",
    multiple=True,
    type=click.Path(dir_okay=False, path_type=str),
    help="Optional environment file(s) loaded before collection (relative to project_root).",
)
def eval_collect(spec_path: str, env_files: tuple[str, ...]) -> None:
    """Run collect->export workflow from spec (no evaluation)."""
    import asyncio
    import json
    from pathlib import Path

    from penguiflow.evals.api import collect_and_export_traces, load_eval_collect_spec

    path = Path(spec_path).resolve()

    try:
        collect_spec = load_eval_collect_spec(path)
    except Exception as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    try:
        _apply_env_files(
            spec_env_files=collect_spec.env_files,
            cli_env_files=env_files,
            base_dir=collect_spec.project_root,
        )
    except Exception as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    try:
        result = asyncio.run(
            collect_and_export_traces(
                project_root=collect_spec.project_root,
                query_suite_path=collect_spec.query_suite_path,
                output_dir=collect_spec.output_dir,
                session_id=collect_spec.session_id,
                dataset_tag=collect_spec.dataset_tag,
                agent_package=collect_spec.agent_package,
                state_store_spec=collect_spec.state_store_spec,
            )
        )
    except Exception as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    click.echo(
        json.dumps(
            {
                **result,
                "resolved_paths": {
                    "resolution_base": str(collect_spec.project_root),
                    "project_root": str(collect_spec.project_root),
                    "query_suite_path": str(collect_spec.query_suite_path),
                    "output_dir": str(collect_spec.output_dir),
                    "env_files": [str(item) for item in collect_spec.env_files],
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


@eval.command("evaluate")
@click.option(
    "--spec",
    "spec_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Path to eval dataset spec JSON file. Relative spec fields resolve from project_root if provided.",
)
@click.option(
    "--env-file",
    "env_files",
    multiple=True,
    type=click.Path(dir_okay=False, path_type=str),
    help="Optional environment file(s) loaded before evaluation (relative to project_root if set).",
)
def eval_evaluate(spec_path: str, env_files: tuple[str, ...]) -> None:
    """Run evaluation against an existing dataset bundle from spec."""
    import asyncio
    import json
    from pathlib import Path

    from penguiflow.evals.api import evaluate_dataset_from_spec_file, load_eval_dataset_spec

    path = Path(spec_path).resolve()

    try:
        spec = load_eval_dataset_spec(path)
    except Exception as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    eval_base = spec.project_root if spec.project_root is not None else path.parent
    try:
        _apply_env_files(
            spec_env_files=spec.env_files,
            cli_env_files=env_files,
            base_dir=eval_base,
        )
    except Exception as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    try:
        result = asyncio.run(evaluate_dataset_from_spec_file(path))
    except Exception as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(1)

    click.echo(
        json.dumps(
            {
                **result,
                "resolved_paths": {
                    "resolution_base": str(eval_base),
                    "project_root": str(spec.project_root) if spec.project_root is not None else None,
                    "dataset_path": str(spec.dataset_path),
                    "candidates_path": str(spec.candidates_path),
                    "output_dir": str(spec.output_dir),
                    "env_files": [str(item) for item in spec.env_files],
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command()
@click.option(
    "--init",
    "init_name",
    type=str,
    default=None,
    help="Initialize a spec workspace with sample spec and docs (e.g., --init my-agent).",
)
@click.option(
    "--spec",
    "-s",
    "spec_path",
    type=click.Path(exists=True, path_type=str),
    default=None,
    help="Path to the agent spec YAML file.",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=str),
    default=None,
    help="Directory where the project should be created (defaults to cwd).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing files if they already exist.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show which files would be created without writing them.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress output messages.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed generation progress.",
)
def generate(
    init_name: str | None,
    spec_path: str | None,
    output_dir: str | None,
    force: bool,
    dry_run: bool,
    quiet: bool,
    verbose: bool,
) -> None:
    """Generate tools and planner code from an agent spec.

    Use --init to create a new spec workspace:

        penguiflow generate --init my-agent

    Use --spec to generate from an existing spec:

        penguiflow generate --spec my-agent.yaml
    """
    from pathlib import Path

    from .generate import run_generate, run_init_spec
    from .init import CLIError
    from .spec_errors import SpecValidationError

    # Handle --init mode
    if init_name is not None:
        if spec_path is not None:
            click.echo("✗ Cannot use --init and --spec together", err=True)
            sys.exit(1)
        if dry_run:
            click.echo("✗ --dry-run is not supported with --init", err=True)
            sys.exit(1)

        try:
            init_result = run_init_spec(
                agent_name=init_name,
                output_dir=Path(output_dir) if output_dir else None,
                force=force,
                quiet=quiet,
            )
            if not init_result.success:
                sys.exit(1)
        except CLIError as e:
            click.echo(f"✗ {e.message}", err=True)
            if e.hint:
                click.echo(f"  Hint: {e.hint}", err=True)
            sys.exit(1)
        return

    # Handle --spec mode (original behavior)
    if spec_path is None:
        click.echo("✗ Either --init or --spec is required", err=True)
        click.echo("  Use --init to create a new spec workspace", err=True)
        click.echo("  Use --spec to generate from an existing spec", err=True)
        sys.exit(1)

    try:
        result = run_generate(
            spec_path=Path(spec_path),
            output_dir=Path(output_dir) if output_dir else None,
            dry_run=dry_run,
            force=force,
            quiet=quiet,
            verbose=verbose,
        )
        if not result.success:
            sys.exit(1)
    except SpecValidationError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    except CLIError as e:
        click.echo(f"✗ {e.message}", err=True)
        if e.hint:
            click.echo(f"  Hint: {e.hint}", err=True)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    app()
