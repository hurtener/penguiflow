"""Implementation of `penguiflow new`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import NamedTuple

import click

from .init import CLIError


class TemplateNotFoundError(CLIError):
    """Raised when a requested template is missing."""


class TemplateRenderError(CLIError):
    """Raised when a template cannot be rendered."""


class ProjectExistsError(CLIError):
    """Raised when target directory exists without --force."""


class NewResult(NamedTuple):
    """Result of running `penguiflow new`."""

    success: bool
    created: list[str]
    skipped: list[str]
    errors: list[str]


@dataclass(frozen=True)
class TemplateContext:
    """Values exposed to template rendering."""

    project_name: str
    package_name: str
    class_name: str
    template: str
    with_streaming: bool = False
    with_hitl: bool = False
    with_a2a: bool = False
    no_memory: bool = False


def _normalise_package_name(name: str) -> str:
    """Convert project name into a safe python package name."""
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    if not slug:
        raise CLIError("Project name must contain at least one alphanumeric character.")
    if slug[0].isdigit():
        slug = f"app_{slug}"
    return slug


def _class_name(package_name: str) -> str:
    """Convert package name into a PascalCase class name."""
    parts = [part for part in package_name.split("_") if part]
    return "".join(part.capitalize() for part in parts) or "App"


def _iter_template_files(base: Traversable, prefix: Path | None = None):
    """Yield (relative_path, resource) tuples for all files under base."""
    current_prefix = prefix or Path()
    for entry in base.iterdir():
        if entry.name.startswith("__pycache__"):
            continue
        relative_path = current_prefix / entry.name
        if entry.is_dir():
            yield from _iter_template_files(entry, relative_path)
        elif entry.is_file():
            yield relative_path, entry


def _render_path(path: Path, ctx: TemplateContext) -> Path:
    """Render a destination path by replacing placeholders and suffixes."""
    rendered = Path(
        str(path).replace("__package_name__", ctx.package_name).replace("__project_name__", ctx.project_name)
    )
    if rendered.suffix == ".jinja":
        rendered = rendered.with_suffix("")
    return rendered


def _render_content(raw: str, ctx: TemplateContext) -> str:
    """Render text content with Jinja2."""
    try:
        from jinja2 import Template
    except ImportError as exc:  # pragma: no cover - guard for optional extra
        raise CLIError(
            "Jinja2 is required for `penguiflow new`.",
            hint="Install with `pip install penguiflow[cli]`.",
        ) from exc

    try:
        template = Template(raw)
        return template.render(
            project_name=ctx.project_name,
            package_name=ctx.package_name,
            class_name=ctx.class_name,
            template=ctx.template,
            with_streaming=ctx.with_streaming,
            with_hitl=ctx.with_hitl,
            with_a2a=ctx.with_a2a,
            no_memory=ctx.no_memory,
            memory_enabled=not ctx.no_memory,
        )
    except Exception as exc:  # pragma: no cover - defensive, covered indirectly via tests
        raise TemplateRenderError(f"Failed to render template: {exc}") from exc


def _load_template_root(template: str) -> Traversable:
    """Load the template directory resource."""
    base = resources.files("penguiflow.templates.new")
    target = base.joinpath(template)
    if not target.is_dir():
        available = sorted(entry.name for entry in base.iterdir() if entry.is_dir() and not entry.name.startswith("__"))
        hint = f"Available templates: {', '.join(available)}" if available else None
        raise TemplateNotFoundError(f"Unknown template: {template}", hint=hint)
    return target


def run_new(
    name: str,
    *,
    template: str = "react",
    force: bool = False,
    dry_run: bool = False,
    output_dir: Path | None = None,
    quiet: bool = False,
    with_streaming: bool = False,
    with_hitl: bool = False,
    with_a2a: bool = False,
    no_memory: bool = False,
) -> NewResult:
    """Create a new PenguiFlow agent project from templates.

    Args:
        name: Project directory name (also used for display).
        template: Template name (minimal, react, parallel).
        force: Overwrite existing files if True.
        dry_run: Only show what would be created.
        output_dir: Directory where the project should be created (defaults to cwd).
        quiet: Suppress stdout messages.

    Returns:
        NewResult with success flag and created/skipped/error paths.
    """
    # Use only the final component for display name (handles paths like "foo/bar")
    display_name = Path(name).name
    package_name = _normalise_package_name(display_name)
    ctx = TemplateContext(
        project_name=display_name,
        package_name=package_name,
        class_name=_class_name(package_name),
        template=template,
        with_streaming=with_streaming,
        with_hitl=with_hitl,
        with_a2a=with_a2a,
        no_memory=no_memory,
    )
    base_dir = output_dir or Path.cwd()
    project_dir = base_dir / name

    template_root = _load_template_root(template)
    files = list(_iter_template_files(template_root))

    created: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    if dry_run:
        if not quiet:
            click.echo(f"PenguiFlow new (dry run) - template '{template}'")
            for rel_path, _ in files:
                target = project_dir / _render_path(rel_path, ctx)
                click.echo(f"  would create: {target.as_posix()}")
        return NewResult(success=True, created=[], skipped=[], errors=[])

    if project_dir.exists() and project_dir.is_file():
        raise ProjectExistsError(
            f"Target path '{project_dir}' is a file.",
            hint="Choose a different project name or delete the conflicting file.",
        )
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise CLIError(
            f"Cannot create project directory: {project_dir}",
            hint="Check write permissions or choose a different path.",
        ) from exc

    for rel_path, resource in files:
        destination = project_dir / _render_path(rel_path, ctx)
        if destination.exists() and not force:
            skipped.append(destination.as_posix())
            continue

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            content = resource.read_text()
            rendered = _render_content(content, ctx)
            destination.write_text(rendered)
            created.append(destination.as_posix())
        except CLIError:
            raise
        except PermissionError as exc:
            raise CLIError(
                f"Cannot write file: {destination}",
                hint="Check write permissions for the target directory.",
            ) from exc
        except Exception as exc:
            errors.append(f"{destination.as_posix()}: {exc}")

    if not quiet:
        for path in created:
            click.echo(f"✓ Created {path}")
        for path in skipped:
            click.echo(f"⚠ Skipped {path} (exists, use --force to overwrite)")
        for path in errors:
            click.echo(f"✗ Error: {path}", err=True)

    return NewResult(success=len(errors) == 0, created=created, skipped=skipped, errors=errors)
