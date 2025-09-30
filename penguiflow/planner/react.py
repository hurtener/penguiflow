"""JSON-only ReAct planner loop."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

from ..catalog import NodeSpec, build_catalog
from ..node import Node
from ..registry import ModelRegistry
from . import prompts


class JSONLLMClient(Protocol):
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        ...


class PlannerAction(BaseModel):
    thought: str
    next_node: str | None = None
    args: dict[str, Any] | None = None
    plan: list[dict[str, Any]] | None = None
    join: dict[str, Any] | None = None


class PlannerFinish(BaseModel):
    reason: str
    payload: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class TrajectoryStep:
    action: PlannerAction
    observation: Any | None = None
    error: str | None = None

    def dump(self) -> dict[str, Any]:
        return {
            "action": self.action.model_dump(mode="json"),
            "observation": self._serialise_observation(),
            "error": self.error,
        }

    def _serialise_observation(self) -> Any:
        if isinstance(self.observation, BaseModel):
            return self.observation.model_dump(mode="json")
        return self.observation


@dataclass(slots=True)
class Trajectory:
    query: str
    context_meta: Mapping[str, Any] | None = None
    steps: list[TrajectoryStep] = field(default_factory=list)

    def to_history(self) -> list[dict[str, Any]]:
        return [step.dump() for step in self.steps]


class _LiteLLMJSONClient:
    def __init__(
        self,
        llm: str | Mapping[str, Any],
        *,
        temperature: float,
        json_schema_mode: bool,
    ) -> None:
        self._llm = llm
        self._temperature = temperature
        self._json_schema_mode = json_schema_mode

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
    ) -> str:
        try:
            import litellm
        except ModuleNotFoundError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "LiteLLM is not installed. Install penguiflow[planner] or provide "
                "a custom llm_client."
            ) from exc

        params: dict[str, Any]
        if isinstance(self._llm, str):
            params = {"model": self._llm}
        else:
            params = dict(self._llm)
        params.setdefault("temperature", self._temperature)
        params["messages"] = list(messages)
        if self._json_schema_mode and response_format is not None:
            params["response_format"] = response_format

        response = await litellm.acompletion(**params)
        choice = response["choices"][0]
        content = choice["message"]["content"]
        if content is None:
            raise RuntimeError("LiteLLM returned empty content")
        return content


class _PlannerContext:
    __slots__ = ("meta",)

    def __init__(self, meta: Mapping[str, Any] | None = None) -> None:
        self.meta = dict(meta) if meta else {}


class ReactPlanner:
    """Minimal JSON-only ReAct loop."""

    def __init__(
        self,
        llm: str | Mapping[str, Any] | None = None,
        *,
        nodes: Sequence[Node] | None = None,
        catalog: Sequence[NodeSpec] | None = None,
        registry: ModelRegistry | None = None,
        llm_client: JSONLLMClient | None = None,
        max_iters: int = 8,
        temperature: float = 0.0,
        json_schema_mode: bool = True,
        system_prompt_extra: str | None = None,
        repair_attempts: int = 3,
    ) -> None:
        if catalog is None:
            if nodes is None or registry is None:
                raise ValueError(
                    "Either catalog or (nodes and registry) must be provided"
                )
            catalog = build_catalog(nodes, registry)

        self._specs = list(catalog)
        self._spec_by_name = {spec.name: spec for spec in self._specs}
        self._catalog_records = [spec.to_tool_record() for spec in self._specs]
        self._system_prompt = prompts.build_system_prompt(
            self._catalog_records,
            extra=system_prompt_extra,
        )
        self._max_iters = max_iters
        self._repair_attempts = repair_attempts
        self._json_schema_mode = json_schema_mode
        self._response_format = (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "planner_action",
                    "schema": PlannerAction.model_json_schema(),
                },
            }
            if json_schema_mode
            else None
        )
        if llm_client is not None:
            self._client = llm_client
        else:
            if llm is None:
                raise ValueError("llm or llm_client must be provided")
            self._client = _LiteLLMJSONClient(
                llm,
                temperature=temperature,
                json_schema_mode=json_schema_mode,
            )

    async def run(
        self,
        query: str,
        *,
        context_meta: Mapping[str, Any] | None = None,
    ) -> PlannerFinish:
        trajectory = Trajectory(query=query, context_meta=context_meta)
        last_observation: Any | None = None

        for _ in range(self._max_iters):
            action = await self.step(trajectory)

            if action.next_node is None:
                payload = action.args or last_observation
                return self._finish(
                    trajectory,
                    reason="answer_complete",
                    payload=payload,
                    thought=action.thought,
                )

            spec = self._spec_by_name.get(action.next_node)
            if spec is None:
                error = prompts.render_invalid_node(
                    action.next_node,
                    list(self._spec_by_name.keys()),
                )
                trajectory.steps.append(TrajectoryStep(action=action, error=error))
                continue

            try:
                parsed_args = spec.args_model.model_validate(action.args or {})
            except ValidationError as exc:
                error = prompts.render_validation_error(
                    spec.name,
                    json.dumps(exc.errors(), ensure_ascii=False),
                )
                trajectory.steps.append(TrajectoryStep(action=action, error=error))
                continue

            ctx = _PlannerContext(meta=context_meta)
            try:
                result = await spec.node.func(parsed_args, ctx)
            except Exception as exc:  # pragma: no cover - surfaced via metadata
                error = f"tool '{spec.name}' raised {exc.__class__.__name__}: {exc}"
                trajectory.steps.append(TrajectoryStep(action=action, error=error))
                return self._finish(
                    trajectory,
                    reason="error",
                    payload=None,
                    thought=action.thought,
                    error=error,
                )

            observation = spec.out_model.model_validate(result)
            trajectory.steps.append(
                TrajectoryStep(action=action, observation=observation)
            )
            last_observation = observation.model_dump(mode="json")

        return self._finish(
            trajectory,
            reason="no_path",
            payload=last_observation,
            thought="iteration limit reached",
        )

    async def step(self, trajectory: Trajectory) -> PlannerAction:
        base_messages = self._build_messages(trajectory)
        messages: list[dict[str, str]] = list(base_messages)
        last_error: str | None = None

        for _ in range(self._repair_attempts):
            if last_error is not None:
                messages = list(base_messages) + [
                    {
                        "role": "system",
                        "content": prompts.render_repair_message(last_error),
                    }
                ]

            raw = await self._client.complete(
                messages=messages,
                response_format=self._response_format,
            )

            try:
                return PlannerAction.model_validate_json(raw)
            except ValidationError as exc:
                last_error = json.dumps(exc.errors(), ensure_ascii=False)
                continue

        raise RuntimeError("Planner failed to produce valid JSON after repair attempts")

    def _build_messages(self, trajectory: Trajectory) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": prompts.build_user_prompt(
                    trajectory.query,
                    trajectory.context_meta,
                ),
            },
        ]

        for step in trajectory.steps:
            action_payload = json.dumps(
                step.action.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            )
            messages.append({"role": "assistant", "content": action_payload})
            messages.append(
                {
                    "role": "user",
                    "content": prompts.render_observation(
                        observation=step._serialise_observation(),
                        error=step.error,
                    ),
                }
            )
        return messages

    def _finish(
        self,
        trajectory: Trajectory,
        *,
        reason: str,
        payload: Any,
        thought: str,
        error: str | None = None,
    ) -> PlannerFinish:
        metadata = {
            "reason": reason,
            "thought": thought,
            "steps": trajectory.to_history(),
            "step_count": len(trajectory.steps),
        }
        if error is not None:
            metadata["error"] = error
        return PlannerFinish(reason=reason, payload=payload, metadata=metadata)


__all__ = [
    "PlannerAction",
    "PlannerFinish",
    "ReactPlanner",
    "Trajectory",
    "TrajectoryStep",
]
