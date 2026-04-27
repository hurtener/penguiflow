from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jinja2 import Template

ROOT = Path(__file__).resolve().parents[1]


def _rendered_helper(path: Path) -> Any:
    rendered = Template(path.read_text()).render(with_a2a=True, project_name="Test Agent")
    module = ast.parse(rendered)
    helper = next(
        node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "_resolve_session_id"
    )
    namespace: dict[str, Any] = {}
    exec("from __future__ import annotations\n" + ast.unparse(helper), namespace)
    return namespace["_resolve_session_id"]


def test_a2a_templates_resolve_session_with_a2a_precedence() -> None:
    template_paths = sorted(ROOT.glob("penguiflow/templates/new/*/src/__package_name__/a2a.py.jinja"))
    assert template_paths

    for path in template_paths:
        resolve_session_id = _rendered_helper(path)
        message = SimpleNamespace(
            meta={
                "a2a_context_id": "ctx-a2a",
                "a2a": {"message": {"metadata": {"session_id": "metadata-session"}}},
            }
        )
        assert resolve_session_id(message, {"session_id": "payload-session"}) == "ctx-a2a"

        message = SimpleNamespace(meta={"a2a": {"message": {"metadata": {"session_id": "metadata-session"}}}})
        assert resolve_session_id(message, {"session_id": "payload-session"}) == "metadata-session"

        message = SimpleNamespace(meta={})
        assert resolve_session_id(message, {"session_id": "payload-session"}) == "payload-session"
        assert resolve_session_id(message, "hello") == "a2a-session"
