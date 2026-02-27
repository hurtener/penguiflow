"""Unit tests for playground session-message helper functions."""

from __future__ import annotations

from penguiflow.cli.playground import _build_memory_state_key, _messages_from_memory_state


def test_build_memory_state_key_normalizes_blanks() -> None:
    key = _build_memory_state_key(
        tenant_id="  ",
        user_id="",
        session_id=" session-1 ",
    )
    assert key == "playground-tenant:playground-user:session-1"


def test_messages_from_memory_state_orders_and_limits() -> None:
    payload = {
        "turns": [
            {"user_message": "u2", "assistant_response": "a2", "ts": 2},
        ],
        "pending": [
            {"user_message": "u1", "assistant_response": "a1", "ts": 1},
        ],
    }
    messages = _messages_from_memory_state(payload, limit=3)
    assert [msg.content for msg in messages] == ["a1", "u2", "a2"]
    assert [msg.role for msg in messages] == ["assistant", "user", "assistant"]


def test_messages_from_memory_state_ignores_invalid_shapes() -> None:
    payload = {
        "turns": [
            {"user_message": "", "assistant_response": "", "ts": 1},
            {"user_message": "ok", "assistant_response": "fine"},
            {"user_message": 1, "assistant_response": "bad"},  # type: ignore[list-item]
        ],
        "pending": "not-a-list",
    }
    messages = _messages_from_memory_state(payload, limit=10)
    assert len(messages) == 2
    assert [msg.content for msg in messages] == ["ok", "fine"]
