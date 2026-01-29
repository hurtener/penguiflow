"""Control policy for steering events."""

from __future__ import annotations

from dataclasses import dataclass, field

from penguiflow.steering import SteeringEvent, SteeringEventType


@dataclass(slots=True)
class ControlPolicy:
    """Default control policy: agent proposes; user confirms destructive actions."""

    require_confirmation: bool = True
    destructive_events: set[SteeringEventType] = field(
        default_factory=lambda: {SteeringEventType.CANCEL, SteeringEventType.REDIRECT}
    )

    def requires_confirmation(self, event: SteeringEvent) -> bool:
        if not self.require_confirmation:
            return False
        if event.event_type not in self.destructive_events:
            return False
        return event.source != "user" and not bool(event.payload.get("confirmed"))


__all__ = ["ControlPolicy"]
