from penguiflow.planner import prompts


def test_render_tool_hints() -> None:
    rendered = prompts.render_tool_hints(
        [
            {"name": "charting.start_chart_analysis", "description": "Start chart analysis"},
            {"name": "charting.get_chart_config", "description": "Get chart config"},
        ],
        query="start_analysis charting",
    )
    assert rendered is not None
    assert "<tool_hints>" in rendered
    assert "charting.start_chart_analysis" in rendered


def test_render_tool_directory() -> None:
    rendered = prompts.render_tool_directory(
        [
            {
                "name": "charting",
                "trigger": "Charting MCP tools",
                "tool_count": 3,
                "tools": ["charting.start_chart_analysis", "charting.get_chart_config"],
            }
        ]
    )
    assert rendered is not None
    assert "<tool_directory>" in rendered
    assert "charting" in rendered
