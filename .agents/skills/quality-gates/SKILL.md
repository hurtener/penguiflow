---
name: quality-gates
description: Use whenever you change code, tests, examples or dependencies in this repo, add a workspace member, write a plan's verification section, or hit a ruff, mypy or pytest failure (including a `# noqa` or `# type: ignore` you are tempted to add). Every change passes ruff, ruff format, mypy and pytest, run with --frozen, and findings are fixed, never exempted.
---

# Quality gates

The rules live in the root `pyproject.toml`: ruff (79 columns, Google docstrings, complexity 10, a wide rule set) and strict mypy with the pydantic plugin. `ruff`, `mypy` and `pytest` are in the `dev` dependency group.

**Why:** the code stays typed, documented and consistent only if every change meets the same bar. One exemption invites the next.

## Before a change is finished

Run the gates from the repo root, each with `--frozen`:

```shell
uv sync --locked
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen pytest
```

- `uv sync --locked` installs the environment first. It is not a gate, and it keeps `--locked` because it is the step that fails when `uv.lock` no longer matches `pyproject.toml`; `--frozen` would skip that check.
- `mypy` takes no paths. It checks the `files` listed in `[tool.mypy]`: `penguiflow`, `penguiflow_a2a`, `examples` and `tests`.
- If the format check fails, run `uv run --frozen ruff format .` and check again.

List these commands in every plan's verification section.

## Fix, never exempt

1. When ruff or mypy flags code, change the code so it complies. Tests and examples included.
2. Never add `# noqa`, `# type: ignore`, `per-file-ignores`, `ignore` entries or any other exemption, and never offer one as an option.
3. Workarounds that only silence a checker are exemptions too: `del unused_arg`, `cast(Any, ...)`, or an `Any` annotation added just to quiet mypy.
4. Test fakes use every argument of the interface they implement, for example by recording the arguments they receive, instead of leaving them unused.

## What is not an exemption

- A method whose name or signature a real base class dictates, such as a gRPC servicer method, is marked `@override` (from `typing_extensions`). Ruff then skips N802, ARG002 and D102 for it, because the base class owns the name and the signature.
- Generated code is excluded from ruff and mypy, and typed through `.pyi` stubs: `penguiflow_a2a/grpc/a2a_pb2*.py` (see a2a-grpc-stubs).
- `runtime-evaluated-base-classes` tells ruff that pydantic reads annotations at runtime, so TC fixes keep those imports outside `TYPE_CHECKING`.
- The `numpy.*` mypy override only stops mypy from parsing numpy's stubs under the 3.11 target. It does not touch this repo's code.

## Typed tests

Tests are type-checked like the library, so fakes must match the types they stand in for. Reuse the shared doubles instead of writing new duck-typed ones:

- `tests/tool_context_doubles.py`: `FakeToolContext`, a complete `ToolContext` that records pauses and streamed chunks. Subclass it for other behaviour.
- `tests/state_store_doubles.py`: `RecordingStateStore`, the required `StateStore` methods. Subclass it and add the capability a test needs.
- `tests/planner_options.py`: `PlannerOptions`, for helpers that forward `**options` to `ReactPlanner` (`**kwargs: Unpack[PlannerOptions]`).
- `tests/fake_sdk_clients.py`: real OpenAI and google-genai clients with only their request method replaced, for a provider's `client=` argument.
- Planner fakes subclass the protocol the code depends on, such as `A2APlanner`, `TaskPlanner`, `JSONLLMClient` or `ToolVisibilityPolicy`, and mark its methods `@override`.
- Replace a method on an object with `monkeypatch.setattr(obj, "name", fake)`, not by assigning to the attribute.
- To test that invalid input is rejected at runtime, pass it untyped, the way it arrives in practice: `Model.model_validate({...})`, or keyword options read with `json.loads(...)`.
- Test internal state through public API. When a test needs internal state, add a public read-only property to the library; don't read a private name.

## The one allowed exemption

`[tool.ruff.lint.pep8-naming] ignore-names` lists six public exceptions that keep names without an `Error` suffix (N818), so the public API stays stable: `ModelRetry`, `ValidationRetry`, `MemoryBudgetExceeded`, `RemoteTaskInputRequired`, `RemoteTaskAuthRequired` and `SteeringCancelled`. The maintainer decided this. Never add names to it. A new exception ends in `Error`.

## New workspace members

A new workspace member, such as `scripts/` (see dev-scripts), is checked by the root config. Add its `src` folder to ruff `src`, and to mypy `files` and `mypy_path`.

**How to apply:** fix every finding in the code, run all the gates, and only then report the change as done.
