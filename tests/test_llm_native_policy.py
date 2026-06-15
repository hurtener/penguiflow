from penguiflow.llm.native_policy import resolve_policy


def _resolve(model: str, requested_mode: str):
    return resolve_policy(
        provider_name="databricks",
        model=model,
        requested_mode=requested_mode,  # type: ignore[arg-type]
        mode_override=None,
        structured_reasoning_fallback_off=False,
        use_native_reasoning=True,
    )


def test_databricks_claude_structured_disables_reasoning_injection() -> None:
    policy = _resolve("databricks-claude-sonnet-4-5", "json_schema")
    assert policy.inject_reasoning_effort is False
    assert policy.emit_reasoning_callbacks is False


def test_databricks_claude_json_object_disables_reasoning_injection() -> None:
    policy = _resolve("databricks-claude-haiku-4-5", "json_object")
    assert policy.inject_reasoning_effort is False
    assert policy.emit_reasoning_callbacks is False


def test_databricks_gemini_25_structured_disables_reasoning_injection() -> None:
    policy = _resolve("databricks-gemini-2-5-pro", "json_schema")
    assert policy.inject_reasoning_effort is False
    assert policy.emit_reasoning_callbacks is False


def test_databricks_claude_text_keeps_reasoning_injection() -> None:
    policy = _resolve("databricks-claude-sonnet-4-5", "text")
    assert policy.inject_reasoning_effort is True
    assert policy.emit_reasoning_callbacks is True


def test_databricks_gpt5_structured_keeps_reasoning_injection() -> None:
    policy = _resolve("databricks-gpt-5-mini", "json_schema")
    assert policy.inject_reasoning_effort is True
    assert policy.emit_reasoning_callbacks is True
