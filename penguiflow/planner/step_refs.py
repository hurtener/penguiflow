"""Letting an action point at a previous step's data instead of re-typing it.

A planner that has just received 200 rows from a query and wants to render them as a table has to
write all 200 rows out again as arguments. Measured on one real `render_table` call: 8,492 characters
of arguments, of which 51 of the 52 long numbers already appeared verbatim in the tool result the
planner was holding. At the measured 58 output tokens per second that is about 40 seconds spent
re-typing, and across one agent's recorded traffic it is a quarter of everything the model writes.

So an action may write `{"rows_from_step": 3}` in place of `{"rows": [...]}`, and the value is
substituted from step 3's observation before anything else sees the arguments.

**Substitution happens before the arguments are validated, and that is the whole design.** Three
things fall out of it, and each one would be a bug if the expansion happened later:

- The tool's own model still sees a complete, literal argument, so `extra="forbid"` and required
  fields keep working and no handler changes.
- Render tools are pre-validated against their component schema before they execute
  (`validation_repair.py` builds the payload and validates it), which a reference resolved inside a
  handler would fail on a missing required field.
- The recorded step keeps the real data in its arguments, so anything downstream that reads a run's
  tool arguments to check the answer was grounded in fetched data still sees that data. Replacing the
  rows with a reference in the record would make a grounded answer look invented.

The reference is deliberately dull: one integer, naming a step by its position. It carries no query,
no path expression and no filtering, because every one of those is a way for the model to be subtly
wrong about data it already has.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: An argument named `<field>_from_step` supplies `<field>` from a previous step's observation.
STEP_REF_SUFFIX = "_from_step"


class StepRefError(ValueError):
    """A reference that cannot be resolved.

    Raised rather than skipped. A dangling reference means the model asked for data that is not
    there, and rendering an empty table instead would turn a loud mistake into a quiet one.
    """


def step_ref_fields(args: Mapping[str, Any]) -> tuple[str, ...]:
    """The field names these arguments supply by reference, in the order they appear."""

    return tuple(
        key[: -len(STEP_REF_SUFFIX)]
        for key in args
        if key.endswith(STEP_REF_SUFFIX) and len(key) > len(STEP_REF_SUFFIX)
    )


def expand_step_refs(
    args: Mapping[str, Any],
    steps: Sequence[Any],
    *,
    tool: str = "",
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Arguments with every `<field>_from_step` replaced by that step's data.

    Returns the expanded arguments and the field names that were filled, so a caller can log what
    the model referenced rather than typed. Arguments with no reference come back unchanged, which
    keeps this a no-op for every tool and every action that does not use it.

    `steps` is the trajectory's steps so far; a reference is the 1-based position of one of them,
    counted the way the planner's own prompt numbers them.
    """

    refs = step_ref_fields(args)
    if not refs:
        return dict(args), ()

    expanded = dict(args)
    filled: list[str] = []
    for field in refs:
        key = f"{field}{STEP_REF_SUFFIX}"
        position = expanded.pop(key)
        if field in expanded:
            raise StepRefError(
                f"{tool or 'the action'} gave both {field!r} and {key!r}; supply one or the other"
            )
        expanded[field] = _value_at(position, field, steps, key=key, tool=tool)
        filled.append(field)
    return expanded, tuple(filled)


def _value_at(
    position: Any,
    field: str,
    steps: Sequence[Any],
    *,
    key: str,
    tool: str,
) -> Any:
    """One field of one step's observation, or a `StepRefError` naming what was wrong.

    Every failure here names the reference and what the step actually held, because the model has to
    be able to correct itself from the message alone — it cannot see this code.
    """

    where = f"{tool or 'the action'} {key}={position!r}"
    if isinstance(position, bool) or not isinstance(position, int):
        raise StepRefError(f"{where}: a step reference must be a whole number, counting from 1")
    if position < 1:
        # Nought is the off-by-one, and saying how many steps exist would not correct it.
        raise StepRefError(f"{where}: steps are numbered counting from 1, so 1 is the first")
    if position > len(steps):
        raise StepRefError(
            f"{where}: there {'is' if len(steps) == 1 else 'are'} {len(steps)} earlier step(s)"
        )

    step = steps[position - 1]
    observation = getattr(step, "observation", None)
    if not isinstance(observation, Mapping):
        tool_name = getattr(getattr(step, "action", None), "next_node", None)
        raise StepRefError(
            f"{where}: step {position} ({tool_name or 'unknown tool'}) recorded no structured result"
        )
    if field not in observation:
        raise StepRefError(
            f"{where}: step {position} returned {sorted(observation)} and nothing called {field!r}"
        )
    return observation[field]


__all__ = ["STEP_REF_SUFFIX", "StepRefError", "expand_step_refs", "step_ref_fields"]
