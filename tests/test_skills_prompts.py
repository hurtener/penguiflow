from __future__ import annotations

from penguiflow.planner import prompts


def test_render_skill_directory_uses_title_then_trigger() -> None:
    entries = [
        {"name": "pack.core.auth.login_basic", "title": "Login", "trigger": "Log in"},
        {"name": "pack.core.api.pagination", "trigger": "Paginate results"},
    ]
    rendered = prompts.render_skill_directory(entries, include_fields=["name", "title", "trigger"])
    assert rendered is not None
    assert "pack.core.auth.login_basic" in rendered
    assert "Login" in rendered
    assert "Paginate results" in rendered
