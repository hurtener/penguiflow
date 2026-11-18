# Coverage and CI

## pytest-asyncio setup

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

`asyncio_mode = "auto"` treats every `async def test_*` as a coroutine test. Otherwise, decorate each with `@pytest.mark.asyncio`.

For tests that mix sync and async fixtures, use the `pytest-asyncio` `@pytest_asyncio.fixture` decorator on async fixtures.

## Running tests

```bash
uv run pytest                                # all tests
uv run pytest -x                             # stop on first failure
uv run pytest tests/test_core.py -k "name"   # single test
uv run pytest -v                             # verbose
uv run pytest --cov=penguiflow --cov-report=term-missing   # with coverage
```

## Coverage targets

The project sets (`fail_under` in `pyproject.toml`):
- **Minimum: 84.5% line coverage** (hard CI gate).
- Every new feature includes at least one negative/error-path test.

### Where coverage commonly drops
- `middlewares.py` — direct hook tests are easy to skip.
- `viz.py` — DOT/Mermaid outputs need explicit assertion tests.
- `types.py` — beyond `StreamChunk`, type validation edges are under-tested.
- `errors.py` — `FlowError` construction paths.

### Patterns that earn coverage

**Test the error path explicitly:**
```python
def test_node_raises_specific_error():
    node = Node(simulate_error("n", "X", fail_times=1),
                name="n", policy=NodePolicy(max_retries=0, validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    msg = Message(payload={}, headers=Headers(tenant="t"))
    result = await run_one(flow, msg)
    assert isinstance(result, FlowError)
```

**Test the middleware directly:**
```python
async def test_log_flow_events_filters_node_start(caplog):
    from penguiflow import FlowEvent, log_flow_events
    import logging

    mw = log_flow_events(logging.getLogger("test"))
    caplog.set_level(logging.INFO)
    await mw(FlowEvent(event_type="node_success", node_name="n", latency_ms=10.0))
    assert "node_success" in caplog.text
```

**Test viz output structure:**
```python
def test_to_mermaid_includes_nodes():
    from penguiflow import flow_to_mermaid
    flow = create(Node(lambda m, _: m, name="a").to())
    out = flow_to_mermaid(flow)
    assert "flowchart" in out
    assert "a" in out
```

## CI matrix

Matrix:
- Python: 3.11, 3.12, 3.13
- OS: Ubuntu

Checks before merge:
- Ruff (lint): `uv run ruff check penguiflow`
- Mypy: `uv run mypy penguiflow`
- Pytest with coverage ≥84.5%

Artifacts:
- `.coverage.xml` uploaded.
- Badges in README for CI status + coverage trend.

Optional:
- Performance benchmarks (`pytest-benchmark`).
- Codecov / Coveralls integration.

## Test organization

```
tests/
  test_core.py            # core runtime
  test_types.py           # types and envelopes
  test_registry.py        # ModelRegistry
  test_patterns.py        # routers, joins, map_concurrent
  test_controller.py      # controller loops
  test_streaming.py       # StreamChunk, emit_chunk
  test_cancel.py          # per-trace cancel
  test_budgets.py         # deadlines, hop budgets
  test_metadata.py        # meta propagation
  test_metrics.py         # FlowEvent, middlewares
  test_viz.py             # mermaid/dot
  test_routing_policy.py  # DictRoutingPolicy
  test_errors.py          # FlowError, FlowErrorCode
  test_testkit.py         # the testkit itself
```

One module per subsystem keeps imports tight and lets `pytest -k` filter quickly.

## Useful pytest features

### `caplog` for log assertions
```python
def test_logs_emit(caplog):
    caplog.set_level("INFO", logger="penguiflow.flow")
    # ... run flow
    assert any("node_success" in r.message for r in caplog.records)
```

### `monkeypatch` for env overrides
```python
def test_with_env(monkeypatch):
    monkeypatch.setenv("FEATURE_X", "1")
    # ... test
```

### Parametrize for matrix tests
```python
@pytest.mark.parametrize("max_retries,fail_times,should_succeed", [
    (3, 2, True),
    (1, 2, False),
    (5, 5, False),
])
async def test_retry_matrix(max_retries, fail_times, should_succeed):
    ...
```

## Speed

Keep tests under 1 second each. Tactics:
- `backoff_base=0.01` in node policies.
- `timeout_s=1.0` in `run_one` (default).
- In-memory stores for any persistence.
- No real network.
- `asyncio.sleep(0)` for cooperative yields, not `sleep(0.1)`.

Slow tests get skipped or moved out of the unit suite. Long integration tests belong in a separate `tests/integration/` directory with a marker, run on a slower CI lane.

## Debugging slow / flaky tests

- `uv run pytest --durations=10` — top 10 slowest tests.
- `uv run pytest -v -s` — verbose with stdout (catches print debugging).
- `pytest-timeout` plugin — fail tests over a wall-clock cap.
- `pytest-xdist` — parallel test execution (`pytest -n auto`); also surfaces hidden shared-state bugs.

## Negative-path coverage checklist

For each new feature, ensure tests cover:
- [ ] Happy path with expected return.
- [ ] Validation failure (invalid input).
- [ ] Retry-then-success.
- [ ] Retries exhausted (terminal failure).
- [ ] Timeout (where applicable).
- [ ] Cancel mid-execution (where applicable).
- [ ] Cross-tenant isolation (if multi-tenant).
- [ ] Empty / boundary inputs.
