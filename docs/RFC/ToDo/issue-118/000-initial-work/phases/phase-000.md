# Phase 000: Planner flag `ui_component_delivery` (plumbing + init raise)

## Objective
Introduce the tri-state planner flag `ui_component_delivery` (`"inline"` | `"both"` | `"artifact"`, default
`"inline"`) and thread it through every constructor site so `ReactPlanner(ui_component_delivery=...)` works and
survives `fork()`. Add validation and the init-time `ValueError` raise for store-backed modes on a NoOp/None store.
This is the foundation: nothing store-backed can be wired until this flag exists and is readable as
`planner._ui_component_delivery`. The default path stays byte-for-byte today's behavior.

## Tasks
1. Add `ui_component_delivery: str = "inline"` to `ReactPlanner.__init__` signature, the `_init_kwargs` dict, and
   the `_init_react_planner(...)` call in `react.py`.
2. Add the param to `_init_react_planner`'s signature in `react_init.py`; validate against
   `{"inline","both","artifact"}`; store as `planner._ui_component_delivery`.
3. After the existing 3-tier store resolution, raise a clear `ValueError` when delivery is store-backed and the
   resolved store is `None` or a `NoOpArtifactStore`.
4. Leave the tier-3 `NoOpArtifactStore()` default unchanged (no default flip).

## Detailed Steps

### Step 1: `ReactPlanner.__init__` signature (`react.py:417-471`)
- Add `ui_component_delivery: str = "inline"` as a keyword-only parameter in the `__init__` signature, placed
  alongside the other planner-flag kwargs (e.g. near `multi_action_*` / `auto_seq_*`).

### Step 2: `_init_kwargs` dict (`react.py:484-536`)
- Add `"ui_component_delivery": ui_component_delivery,` to the `self._init_kwargs` dict. This is REQUIRED so
  `fork()` (`react.py:608`, used for background tasks) reconstructs forked planners with the same delivery mode
  instead of silently reverting to `"inline"`.

### Step 3: `_init_react_planner(...)` call (`react.py:537-589`)
- Pass `ui_component_delivery=ui_component_delivery` into the `_init_react_planner(self, ...)` call.

### Step 4: `_init_react_planner` signature + validation + raise (`react_init.py`)
- Add `ui_component_delivery: str = "inline"` to the `_init_react_planner` signature.
- Validate: if `ui_component_delivery not in {"inline","both","artifact"}` raise `ValueError` with the allowed set.
- Store `planner._ui_component_delivery = ui_component_delivery` (set it near the other `planner._*` assignments).
- AFTER the existing store-resolution block (`react_init.py:445-455`, which keeps `NoOpArtifactStore()` as tier-3),
  add the store-backed-mode raise:
  - If `ui_component_delivery in {"both","artifact"}` and (`planner._artifact_store is None` or
    `isinstance(planner._artifact_store, NoOpArtifactStore)`), raise a clear `ValueError` instructing the caller to
    pass an `artifact_store=` (e.g. `InMemoryArtifactStore()`).
- Per decision 2 (simple form): ALWAYS raise when store-backed + NoOp/None. Do NOT gate on catalog contents / the
  presence of rich-output tools.

## Required Code

```python
# Target file: penguiflow/planner/react.py  (inside ReactPlanner.__init__ signature, near multi_action_* kwargs)
        multi_action_max_tools: int = 2,
        ui_component_delivery: str = "inline",
        auto_seq_enabled: bool = False,
```

```python
# Target file: penguiflow/planner/react.py  (inside the self._init_kwargs dict, near multi_action_max_tools)
            "multi_action_max_tools": multi_action_max_tools,
            "ui_component_delivery": ui_component_delivery,
            "auto_seq_enabled": auto_seq_enabled,
```

```python
# Target file: penguiflow/planner/react.py  (inside the _init_react_planner(self, ...) call, near multi_action_max_tools)
            multi_action_max_tools=multi_action_max_tools,
            ui_component_delivery=ui_component_delivery,
            auto_seq_enabled=auto_seq_enabled,
```

```python
# Target file: penguiflow/planner/react_init.py  (add param to _init_react_planner signature, default "inline")
    ui_component_delivery: str = "inline",
```

```python
# Target file: penguiflow/planner/react_init.py  (validation + store assignment; place near planner._state_store)
    if ui_component_delivery not in {"inline", "both", "artifact"}:
        raise ValueError(
            "ui_component_delivery must be one of 'inline', 'both', 'artifact'; "
            f"got {ui_component_delivery!r}"
        )
    planner._ui_component_delivery = ui_component_delivery
```

```python
# Target file: penguiflow/planner/react_init.py
# AFTER the existing 3-tier store resolution block (react_init.py:445-455) and
# AFTER planner._artifact_registry = ArtifactRegistry() (so the store is resolved):
    if ui_component_delivery in {"both", "artifact"} and (
        planner._artifact_store is None
        or isinstance(planner._artifact_store, NoOpArtifactStore)
    ):
        raise ValueError(
            f"ui_component_delivery={ui_component_delivery!r} requires a real ArtifactStore. "
            "Pass artifact_store=InMemoryArtifactStore() (or another ArtifactStore) to ReactPlanner; "
            "the default NoOpArtifactStore cannot persist UI components."
        )
```

## Exit Criteria (Success)
- [ ] `ReactPlanner(ui_component_delivery="both", artifact_store=InMemoryArtifactStore())` constructs without error
      and `planner._ui_component_delivery == "both"`.
- [ ] `ReactPlanner(ui_component_delivery="artifact")` with no/NoOp store raises a clear `ValueError`.
- [ ] `ReactPlanner(ui_component_delivery="inline")` with a NoOp store does NOT raise; `_ui_component_delivery`
      defaults to `"inline"` when the kwarg is omitted.
- [ ] `ui_component_delivery="bogus"` raises `ValueError` listing the allowed set.
- [ ] A forked planner (`fork()`) carries the same `_ui_component_delivery` as its parent (the kwarg is in
      `_init_kwargs`).
- [ ] The tier-3 default store remains `NoOpArtifactStore()` (no default flip) — verified by reading
      `react_init.py:444-455`.
- [ ] Backwards-compat: the existing test suite passes unchanged on the default (`inline`) path.

## Implementation Notes
- Follows the existing planner-flag convention (`pause_enabled`, `auto_seq_enabled`, `multi_action_*` — string/bool
  kwargs stored as `planner._*`).
- The closed `__init__` signature in `react.py` lists every kwarg explicitly and forwards each to
  `_init_react_planner`; adding the kwarg in only one place causes `TypeError: unexpected keyword argument`. All four
  sites are mandatory (signature, `_init_kwargs`, the call, and `_init_react_planner`'s own signature).
- `NoOpArtifactStore` is already imported/used in `react_init.py` (tier-3 fallback at `:453`/`:455`).
- The raise is intentionally at construction (fail fast), never mid-stream. Because of it,
  `_register_component_payload` (Phase 002) can assume a real store in store-backed mode without an `isinstance`
  check.
- Do NOT touch `pyproject.toml` or `CHANGELOG.md` (decision 5 — versioning/changelog out of scope).

## Verification Commands
```bash
# Validation + init raise behavior
uv run pytest tests/ -k "rich_output or artifact" -q

# Targeted: confirm the flag plumbs through the public constructor and fork()
uv run python -c "
from penguiflow.artifacts import InMemoryArtifactStore
from penguiflow.planner.react import ReactPlanner
p = ReactPlanner(ui_component_delivery='both', artifact_store=InMemoryArtifactStore())
assert p._ui_component_delivery == 'both'
assert p._init_kwargs['ui_component_delivery'] == 'both'
try:
    ReactPlanner(ui_component_delivery='artifact')
    raise SystemExit('expected ValueError on NoOp store')
except ValueError:
    pass
try:
    ReactPlanner(ui_component_delivery='bogus', artifact_store=InMemoryArtifactStore())
    raise SystemExit('expected ValueError on bad mode')
except ValueError:
    pass
print('OK')
"

uv run ruff check . && uv run mypy
```
