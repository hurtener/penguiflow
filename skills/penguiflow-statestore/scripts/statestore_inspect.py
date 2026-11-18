#!/usr/bin/env python3
"""Inspect PenguiFlow StateStore / ArtifactStore surface area.

This helper is designed for downstream teams implementing custom persistence
backends and for frontend teams needing to understand which capabilities are
required for specific UI features.

Run with a Python >=3.11 environment where `penguiflow` is importable.

In the PenguiFlow repo, prefer:

    uv run python ~/.codex/skills/penguiflow-statestore/scripts/statestore_inspect.py
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
from dataclasses import fields as dc_fields
from dataclasses import is_dataclass
from enum import Enum
from importlib import metadata
from types import ModuleType
from typing import Any


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


def _import_attr(spec: str) -> Any:
    """Import an attribute from 'module:attr.path'."""
    if ":" not in spec:
        raise ValueError("Expected 'module:attr' (e.g. 'my_pkg.persistence:store').")
    module_name, attr_path = spec.split(":", 1)
    module = importlib.import_module(module_name)
    obj: Any = module
    for part in [p for p in attr_path.split(".") if p]:
        obj = getattr(obj, part)
    return obj


def _safe_name(obj: object) -> str:
    return getattr(obj, "__name__", obj.__class__.__name__)


def _proto_surface(proto: type[Any]) -> dict[str, Any]:
    methods: list[str] = []
    props: list[str] = []
    for name, value in proto.__dict__.items():
        if name.startswith("_"):
            continue
        if isinstance(value, property):
            props.append(name)
            continue
        if inspect.isfunction(value):
            methods.append(name)
    return {"name": _safe_name(proto), "methods": sorted(methods), "properties": sorted(props)}


def _describe_model(obj: Any) -> dict[str, Any] | None:
    # Dataclasses
    if is_dataclass(obj):
        return {
            "kind": "dataclass",
            "name": _safe_name(obj),
            "fields": [
                {
                    "name": field.name,
                    "type": str(field.type),
                }
                for field in dc_fields(obj)
            ],
        }

    # Enums
    if inspect.isclass(obj) and issubclass(obj, Enum):
        return {
            "kind": "enum",
            "name": _safe_name(obj),
            "values": [member.value for member in obj],  # type: ignore[union-attr]
        }

    # Pydantic v2 BaseModel
    model_fields = getattr(obj, "model_fields", None)
    if isinstance(model_fields, dict):
        fields_out = []
        for name, info in model_fields.items():
            annotation = getattr(info, "annotation", None)
            fields_out.append({"name": name, "type": str(annotation)})
        return {
            "kind": "pydantic",
            "name": _safe_name(obj),
            "fields": fields_out,
        }

    return None


def _iter_public(module: ModuleType) -> list[tuple[str, Any]]:
    items = []
    for name in dir(module):
        if name.startswith("_"):
            continue
        try:
            items.append((name, getattr(module, name)))
        except Exception:
            continue
    return items


def _print_section(title: str) -> None:
    print(f"\n== {title} ==\n")


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True))


def _capability_report(store: object, *, state_module: Any, artifacts_module: Any) -> dict[str, Any]:
    required = ["save_event", "load_history", "save_remote_binding"]

    optional_groups: dict[str, list[str]] = {
        "planner_state": ["save_planner_state", "load_planner_state"],
        "memory_state": ["save_memory_state", "load_memory_state"],
        "tasks": ["save_task", "list_tasks", "save_update", "list_updates"],
        "steering": ["save_steering", "list_steering"],
        "trajectories": ["save_trajectory", "get_trajectory", "list_traces"],
        "planner_events": ["save_planner_event", "list_planner_events"],
        "artifacts": ["artifact_store"],
    }

    def _missing(names: list[str]) -> list[str]:
        return [name for name in names if not hasattr(store, name)]

    discovered_artifact_store = None
    discover = getattr(artifacts_module, "discover_artifact_store", None)
    if callable(discover):
        try:
            discovered_artifact_store = discover(store)
        except Exception:
            discovered_artifact_store = None

    report = {
        "type": f"{store.__class__.__module__}.{store.__class__.__name__}",
        "required": {"missing": _missing(required), "present": [n for n in required if hasattr(store, n)]},
        "optional": {
            group: {"missing": _missing(names), "present": [n for n in names if hasattr(store, n)]}
            for group, names in optional_groups.items()
        },
        "artifact_store_discovered": bool(discovered_artifact_store is not None),
        "artifact_store_type": (
            f"{discovered_artifact_store.__class__.__module__}.{discovered_artifact_store.__class__.__name__}"
            if discovered_artifact_store is not None
            else None
        ),
    }

    require_capabilities = getattr(state_module, "require_capabilities", None)
    if callable(require_capabilities):
        try:
            require_capabilities(store, feature="core", methods=tuple(required))
            report["required"]["ok"] = True
        except Exception as exc:  # pragma: no cover - best effort
            report["required"]["ok"] = False
            report["required"]["error"] = repr(exc)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect PenguiFlow StateStore/ArtifactStore surface.")
    parser.add_argument(
        "--check",
        metavar="MODULE:ATTR",
        help="Import and capability-check a concrete backend object/factory (e.g. 'myapp.persistence:store').",
    )
    parser.add_argument(
        "--call",
        action="store_true",
        help="If --check resolves to a callable factory, call it with no args.",
    )
    parser.add_argument(
        "--instantiate",
        action="store_true",
        help="If --check resolves to a class, instantiate it with no args.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON only (suppresses pretty sections).",
    )
    args = parser.parse_args()

    try:
        import penguiflow.artifacts as artifacts_module  # type: ignore[import-not-found]
        import penguiflow.state as state_module  # type: ignore[import-not-found]
        import penguiflow.state.models as state_models  # type: ignore[import-not-found]
        import penguiflow.state.protocol as state_protocol  # type: ignore[import-not-found]
    except Exception as exc:
        _eprint(f"ERROR: Failed to import penguiflow modules: {exc!r}")
        _eprint("Hint: run inside a project that depends on PenguiFlow (or from the PenguiFlow repo using `uv run`).")
        return 2

    payload: dict[str, Any] = {
        "python": sys.version.split()[0],
        "penguiflow_version": None,
        "state_protocols": [],
        "models": [],
        "capability_check": None,
    }
    try:
        payload["penguiflow_version"] = metadata.version("penguiflow-lite")
    except metadata.PackageNotFoundError:
        payload["penguiflow_version"] = None

    # Protocol surfaces
    for proto_name in (
        "StateStore",
        "SupportsPlannerState",
        "SupportsMemoryState",
        "SupportsTasks",
        "SupportsSteering",
        "SupportsTrajectories",
        "SupportsPlannerEvents",
        "SupportsArtifacts",
    ):
        proto = getattr(state_protocol, proto_name, None)
        if inspect.isclass(proto):
            payload["state_protocols"].append(_proto_surface(proto))

    # Common models (both from state.models and artifacts)
    for module in (state_models, artifacts_module):
        for _name, obj in _iter_public(module):
            if not inspect.isclass(obj):
                continue
            if getattr(obj, "__module__", None) != module.__name__:
                continue
            described = _describe_model(obj)
            if described is not None:
                payload["models"].append(described)

    # Optional: capability-check a concrete object
    if args.check:
        try:
            target = _import_attr(args.check)
        except Exception as exc:
            payload["capability_check"] = {"error": f"import_failed:{exc!r}", "spec": args.check}
        else:
            if args.call:
                if not callable(target):
                    payload["capability_check"] = {"error": "target_not_callable", "spec": args.check}
                else:
                    try:
                        target = target()
                    except Exception as exc:
                        payload["capability_check"] = {"error": f"call_failed:{exc!r}", "spec": args.check}
            if args.instantiate:
                if not inspect.isclass(target):
                    payload["capability_check"] = {"error": "target_not_class", "spec": args.check}
                else:
                    try:
                        target = target()
                    except Exception as exc:
                        payload["capability_check"] = {"error": f"instantiate_failed:{exc!r}", "spec": args.check}

            if payload["capability_check"] is None and not isinstance(target, object):  # pragma: no cover
                payload["capability_check"] = {"error": "unexpected_target", "spec": args.check}
            elif payload["capability_check"] is None:
                payload["capability_check"] = _capability_report(
                    target,
                    state_module=state_module,
                    artifacts_module=artifacts_module,
                )

    if args.json:
        _print_json(payload)
        return 0

    # Pretty sections
    _print_section("StateStore Protocol Surface")
    for entry in payload["state_protocols"]:
        print(f"- {entry['name']}")
        if entry["methods"]:
            print(f"  methods: {', '.join(entry['methods'])}")
        if entry["properties"]:
            print(f"  properties: {', '.join(entry['properties'])}")

    _print_section("Models (dataclasses/enums/pydantic)")
    for model in payload["models"]:
        kind = model["kind"]
        name = model["name"]
        print(f"- [{kind}] {name}")
        if kind in {"dataclass", "pydantic"}:
            fields = model.get("fields", [])
            for field in fields:
                print(f"  - {field['name']}: {field['type']}")
        elif kind == "enum":
            values = model.get("values", [])
            print(f"  - values: {values}")

    if payload["capability_check"] is not None:
        _print_section("Capability Check")
        _print_json(payload["capability_check"])

    print("\nTip: for the full method contracts, open references/statestore-implementation-spec.md\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
