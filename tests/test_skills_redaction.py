from penguiflow.skills.redaction import redact_pii, redact_tool_references


def test_redact_pii_masks_common_patterns() -> None:
    text = (
        "Email me at foo@example.com or call (555) 123-4567. "
        "Use token sk-testtoken123 and visit https://example.com?secret=1"
    )
    redacted = redact_pii(text)
    assert "[REDACTED_EMAIL]" in redacted
    assert "[REDACTED_PHONE]" in redacted
    assert "[REDACTED_TOKEN]" in redacted
    assert "https://example.com" in redacted
    assert "?secret=1" not in redacted
    assert redact_pii("") == ""


def test_redact_tool_references_replaces_disallowed_tools() -> None:
    text = "Use browser.open and browser.click to proceed"
    redacted = redact_tool_references(
        text,
        ["browser.open", "browser.click"],
        tool_search_available=True,
    )
    assert "browser.open" not in redacted
    assert "browser.click" not in redacted
    assert "a suitable tool (use tool_search)" in redacted
    assert redact_tool_references(text, [], tool_search_available=False) == text
