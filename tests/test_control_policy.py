from __future__ import annotations

from penguiflow.sessions.policy import ControlPolicy
from penguiflow.steering import SteeringEvent, SteeringEventType


def test_control_policy_requires_confirmation_for_agent_destructive() -> None:
    policy = ControlPolicy()
    event = SteeringEvent(
        session_id="session",
        task_id="task",
        event_type=SteeringEventType.CANCEL,
        payload={},
        source="agent",
    )
    assert policy.requires_confirmation(event) is True


def test_control_policy_allows_user_destructive() -> None:
    policy = ControlPolicy()
    event = SteeringEvent(
        session_id="session",
        task_id="task",
        event_type=SteeringEventType.REDIRECT,
        payload={"instruction": "new goal"},
        source="user",
    )
    assert policy.requires_confirmation(event) is False


def test_control_policy_allows_confirmed_agent_action() -> None:
    policy = ControlPolicy()
    event = SteeringEvent(
        session_id="session",
        task_id="task",
        event_type=SteeringEventType.REDIRECT,
        payload={"instruction": "new goal", "confirmed": True},
        source="agent",
    )
    assert policy.requires_confirmation(event) is False


def test_control_policy_non_destructive() -> None:
    policy = ControlPolicy()
    event = SteeringEvent(
        session_id="session",
        task_id="task",
        event_type=SteeringEventType.PAUSE,
        payload={},
        source="agent",
    )
    assert policy.requires_confirmation(event) is False
