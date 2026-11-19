# Interactive HITL Tools: `ui_form`, `ui_confirm`, `ui_select_option`

These rich-output tools double as **pause tools** — they emit a UI component **and** pause the planner waiting for user input.

This reference covers the rich-output side of the contract. The general pause/resume mechanics are covered in [[penguiflow-hitl-pause-resume]].

## How they're different from `render_*`

| | `render_*` | `ui_form` / `ui_confirm` / `ui_select_option` |
|---|---|---|
| Emits `artifact_chunk` | Yes | Yes |
| Pauses planner | No | Yes (`reason="await_input"`) |
| Returns `artifact_ref` | Yes | N/A (run pauses before return) |
| Reads user response | N/A | Yes (via `tool_context` on resume) |

## `ui_form` (`UIFormArgs`)

Render a form built from typed `FormField` entries, pause for the user to submit.

```python
from penguiflow.rich_output.tools import FormField, UIFormArgs

ui_form(UIFormArgs(
    title="Send email",
    description="Compose and confirm",
    fields=[
        FormField(name="recipient", type="email", label="To", required=True),
        FormField(name="subject", type="text", label="Subject", required=True),
        FormField(name="body", type="textarea", label="Body"),
    ],
    submit_label="Send",                # aliases as `submitLabel` over the wire
    cancel_label="Cancel",              # aliases as `cancelLabel`
    layout="vertical",                  # vertical | horizontal | inline
))
```

`FormField.type` is a `Literal[...]` from the actual library: `text`, `number`, `email`, `password`, `url`, `tel`, `textarea`, `select`, `multiselect`, `checkbox`, `radio`, `switch`, `date`, `datetime`, `time`, `file`, `range`, `color`. There is no `schema`/`initial_values`/`input_key` field — the form is defined via fields, defaults via `FormField.default`, and the user's response arrives in `tool_context` keyed by the artifact id or your application's convention.

Pause payload mirrors the args plus the artifact_ref the planner emitted. On resume, pass the submitted form values back through `tool_context` (your app picks the key — there is no library-mandated `input_key`). See [[penguiflow-hitl-pause-resume]] for the resume contract.

## `ui_confirm` (`UIConfirmArgs`)

Yes/no confirmation gate.

```python
from penguiflow.rich_output.tools import UIConfirmArgs

ui_confirm(UIConfirmArgs(
    title="Delete this record?",
    message="This action cannot be undone.",
    confirm_label="Delete",              # aliases as `confirmLabel`
    cancel_label="Keep",                 # aliases as `cancelLabel`
    variant="danger",                    # info | warning | danger | success
    details="Affects 3 child rows.",
))
```

`variant` (not `danger: bool`) is the visual emphasis knob — pick `"danger"` for destructive intent. On resume, communicate the decision via `tool_context` (boolean/string of your choice; not a library-mandated `input_key`).

## `ui_select_option` (`UISelectOptionArgs`)

Pick one (or many) from a list of `SelectOptionItem`s.

```python
ui_select_option(
    title="Select target environment",
    options=[
        SelectOptionItem(value="prod", label="Production"),
        SelectOptionItem(value="staging", label="Staging"),
        SelectOptionItem(value="dev", label="Development"),
    ],
    multiple=False,                       # not `multi_select`
    min_selections=1,                     # aliases as `minSelections`; default 1
    max_selections=None,                  # aliases as `maxSelections`
    layout="list",                        # list | grid | cards
    searchable=False,
))
```

`SelectOptionItem` supports `value`, `label`, optional `description`, `icon`, `disabled`, and `metadata`. The args expose `multiple` (boolean) and selection counts — not `multi_select`/`default`/`input_key`. The user's selection comes back through `tool_context` (your app's choice of key) along with `UIInteractionResult.ok`.

## Wiring requirements

1. `pause_enabled=True` on the planner (default).
2. `RichOutputConfig(enabled=True, allowlist=[..., "form", "confirm", "select_option", ...])` — the interactive components are part of the allowlist set, not a separate switch.
3. The frontend renders the components AND knows how to call `planner.resume(...)` (typically via your host API).
4. For durable resume across workers: `StateStore` implementing `SupportsPlannerState` — see [[penguiflow-hitl-pause-resume]].

## Why not just `ctx.pause(...)` directly?

You could pause directly in any tool. The interactive HITL tools have advantages:
- **Structured payload schema.** The frontend can render any `ui_form` / `ui_confirm` / `ui_select_option` consistently.
- **Validation.** Form schemas are JSON-validated server-side.
- **Tool-call lifecycle events.** Tool-call telemetry tracks the pause as a tool invocation, not a freeform raise.

Use `ctx.pause(...)` directly for ad-hoc / non-standard pauses. Use the interactive tools for any UI gating.

## Anti-patterns

- **Embedding side effects before the pause.** The tool body returns when `ctx.pause` raises, but anything **before** the pause already happened. Don't write to a DB and then ask for confirmation — split into a plan tool and a commit tool.
- **Picking a shared response key by accident.** When you decide where the user's submission lands in `tool_context`, pick a unique key per artifact (e.g., the artifact_ref) — colliding keys overwrite earlier responses.
- **Long-running prompts.** Pauses can sit waiting for hours. Set TTL on the StateStore record.
- **Using a `danger` boolean.** The library uses `variant: Literal["info","warning","danger","success"]` instead — `variant="danger"` is the destructive emphasis.

## Operational defaults

- `confirm_label`/`cancel_label` should describe the actual action ("Delete" not "OK").
- Use `variant="danger"` only for destructive confirmations.
- Keep `FormField` lists short and use typed `FormFieldType`s — the planner validates the schema before pause.
- Multi-select with many options: turn on `searchable=True` and keep `layout="list"` (or switch to `"cards"`/`"grid"` for visual selection).

## Cross-channel rules

For A2A specialists serving downstream agents (not humans), **hide these tools** via `ToolVisibilityPolicy` and set `tool_context["delivery_channel"]="a2a"`. Downstream agents can't render forms; emitting them is lossy. See [[penguiflow-reactplanner-config]] step 9.
