# penguiflow bugs

Checked against penguiflow 3.11.2 ([hurtener/penguiflow](https://github.com/hurtener/penguiflow) at commit `e8435d0`); line numbers refer to that commit.

Each repro script runs with penguiflow 3.11.2 installed, for example with `uv run python <script>.py` inside a penguiflow checkout. Bug 7 also needs `httpx` for FastAPI's `TestClient` (`penguiflow[a2a-client]`).

| # | Bug | Area |
| --- | --- | --- |
| 1 | `rolling_summary` loses its summary when a state store is attached | Short-term memory |
| 2 | `rolling_summary` drops turns that arrive during a summarizer call | Short-term memory |
| 3 | `overflow_policy="truncate_summary"` hangs the event loop | Short-term memory |
| 4 | Skill search orders results by name length, not relevance | Skill packs |
| 5 | Three `BackgroundTasksConfig` limit fields are never read | Background tasks |
| 6 | A guardrail PAUSE raises a private exception out of `run()` | Guardrails |
| 7 | A2A `/v1/...` routes are read as tenant `v1` | A2A server |

### 1. `rolling_summary` loses its summary when a state store is attached

- **Where:**
  - `penguiflow/planner/memory.py`: `DefaultShortTermMemory.from_dict` (442) overwrites `_summary` and `_pending` with the stored state (486–490).
  - `penguiflow/planner/memory_integration.py`: `_apply_memory_context` hydrates at the start of every run (168), and `_maybe_record_memory_turn` persists right after `add_turn` (293, 303).
- **Symptom:** with any state store, `conversation_memory` never contains `summary`. `pending_turns` grows by one per turn until `total_max_tokens` drops turns. The summarizer is still called, and billed, on every turn.
- **Cause:** the state is saved right after `add_turn`, before the background summarizer returns. The next run's hydrate replaces the in-process summary with that stale state. The bug depends on timing: if the next message arrives while the summarizer call is still running, the summary survives. In a chat, the next message usually arrives later.
- **Workaround:** `strategy="truncation"` whenever a state store is attached.

<details><summary>Repro (prints <code>summary: None</code> on every turn while <code>pending</code> grows)</summary>

```python
import asyncio
from penguiflow.planner.memory import ConversationTurn, DefaultShortTermMemory, MemoryBudget, ShortTermMemoryConfig
from penguiflow.state import InMemoryStateStore

async def summarize(request):
    await asyncio.sleep(0.05)
    return {"summary": f"summary of {len(request['turns'])} turns"}

async def main():
    cfg = ShortTermMemoryConfig(strategy="rolling_summary", budget=MemoryBudget(full_zone_turns=1))
    memory, store = DefaultShortTermMemory(config=cfg, summarizer=summarize), InMemoryStateStore()
    for i in range(5):
        await memory.hydrate(store, "t:u:s")   # ReactPlanner: start of every run
        ctx = (await memory.get_llm_context())["conversation_memory"]
        print(i, "summary:", ctx.get("summary"), "pending:", len(ctx.get("pending_turns", [])))
        await memory.add_turn(ConversationTurn(user_message=f"u{i}", assistant_response=f"a{i}"))
        await memory.persist(store, "t:u:s")   # ReactPlanner: right after the turn
        await asyncio.sleep(0.2)               # next message arrives after the summarizer returned

asyncio.run(main())
```

```
0 summary: None pending: 0
1 summary: None pending: 0
2 summary: None pending: 1
3 summary: None pending: 2
4 summary: None pending: 3
```

</details>

### 2. `rolling_summary` drops turns that arrive during a summarizer call

- **Where:** `penguiflow/planner/memory.py`, `DefaultShortTermMemory._run_summarization_impl` (550). It sends a snapshot of `_pending` to the summarizer (573) and clears all of `_pending` when the call succeeds (595).
- **Symptom:** turns that move to `pending` while a summarizer call is running end up in neither the summary nor `recent_turns`.
- **Cause:** the success path clears every pending turn, not only the ones in the snapshot it summarized.

<details><summary>Repro (the summarizer sees only u0; u1–u3 are gone)</summary>

```python
import asyncio
from penguiflow.planner.memory import ConversationTurn, DefaultShortTermMemory, MemoryBudget, ShortTermMemoryConfig

async def summarize(request):
    print("summarizer saw:", [t["user"] for t in request["turns"]])
    await asyncio.sleep(0.2)
    return {"summary": "S"}

async def main():
    cfg = ShortTermMemoryConfig(strategy="rolling_summary", budget=MemoryBudget(full_zone_turns=1))
    memory = DefaultShortTermMemory(config=cfg, summarizer=summarize)
    for i in range(5):
        await memory.add_turn(ConversationTurn(user_message=f"u{i}", assistant_response=f"a{i}"))
        await asyncio.sleep(0.01)  # turns keep arriving while the summarizer call runs
    await asyncio.sleep(0.5)
    print(await memory.get_llm_context())  # u1-u3 are in neither summary nor recent_turns

asyncio.run(main())
```

```
summarizer saw: ['u0']
{'conversation_memory': {'recent_turns': [{'user': 'u4', 'assistant': 'a4'}], 'summary': 'S'}}
```

</details>

### 3. `overflow_policy="truncate_summary"` hangs the event loop

- **Where:** `penguiflow/planner/memory.py`, `DefaultShortTermMemory._enforce_budget_locked` (653), lines 669–671. `_truncate_summary_to_budget_locked` returns without changes when the summary already fits `summary_max_tokens` (644).
- **Symptom:** `add_turn` never returns. The loop has no `await`, so the whole event loop blocks; inside `ReactPlanner` the run never ends and the process stops responding.
- **Cause:** the loop runs whenever the summary fits `summary_max_tokens` but the payload is over `total_max_tokens`. Each pass truncates nothing and `continue`s.
- **Workaround:** keep the default `overflow_policy="truncate_oldest"`.

<details><summary>Repro (prints the first line, then hangs; run it under a timeout)</summary>

```python
import asyncio
from penguiflow.planner.memory import ConversationTurn, DefaultShortTermMemory, MemoryBudget, ShortTermMemoryConfig

async def summarize(request):
    return {"summary": "short"}

async def main():
    budget = MemoryBudget(full_zone_turns=1, summary_max_tokens=50, total_max_tokens=100,
                          overflow_policy="truncate_summary")
    memory = DefaultShortTermMemory(config=ShortTermMemoryConfig(strategy="rolling_summary", budget=budget),
                                    summarizer=summarize)
    await memory.add_turn(ConversationTurn(user_message="u0", assistant_response="a0"))
    await memory.add_turn(ConversationTurn(user_message="u1", assistant_response="a1"))
    await asyncio.sleep(0.1)  # summary is now "short", within summary_max_tokens
    print("adding a turn larger than total_max_tokens ...", flush=True)
    await memory.add_turn(ConversationTurn(user_message="u2", assistant_response="x" * 2000))
    print("never printed")

asyncio.run(main())
```

</details>

### 4. Skill search orders results by name length, not relevance

- **Where:** `penguiflow/skills/local_store.py`. The FTS scoring clamps the raw rank with `raw_value = max(raw_value, 0.0)` (759) and falls back to `score = 0.5` (777). The sort key is `(-score, len(name), name)` (300).
- **Symptom:** `skill_search` and the skills the planner adds to the system prompt come back shortest-name first. Every FTS result has `score` 0.5.
- **Cause:** SQLite FTS5's `bm25()` returns negative values, where more negative means a better match. Clamping to 0 gives every row the same raw score, normalisation then sets every score to 0.5, and the sort falls through to `len(name)`.

<details><summary>Repro (the billing skill ranks first although the replica skill matches more query words)</summary>

```python
import asyncio, pathlib, tempfile
from penguiflow.skills import LocalSkillProvider, SkillPackConfig, SkillsConfig, SkillSearchQuery

root = pathlib.Path(tempfile.mkdtemp())
(root / "ops.skill.yaml").write_text("""
- name: ops.restart-failed-database-replica
  trigger: restart failed database replica after replication failure
  steps: [Restart the replica.]
- name: ops.billing
  trigger: billing question about a failed payment
  steps: [Check the invoice.]
""")
config = SkillsConfig(enabled=True, cache_dir=str(root / "cache"),
                      skill_packs=[SkillPackConfig(name="ops", path=str(root / "ops.skill.yaml"))])
provider = LocalSkillProvider(config)
provider.load_packs()

async def main():
    result = await provider.search(SkillSearchQuery(query="restart the failed database replica, not a payment"),
                                   tool_context={}, capability_context=None)
    print([(s.name, s.score) for s in result.skills])  # every score 0.5; shortest name first

asyncio.run(main())
```

```
[('ops.billing', 0.5), ('ops.restart-failed-database-replica', 0.5)]
```

</details>

### 5. Three `BackgroundTasksConfig` limit fields are never read

- **Where:** `penguiflow/planner/models.py`, `BackgroundTasksConfig`: `max_concurrent_tasks = 5` (689), `max_tasks_per_session = 50` (692), `task_timeout_s = 3600` (695).
  - The limits that apply are `SessionLimits` in `penguiflow/sessions/session.py` (76), with defaults of 8 tasks, 4 background, 3 concurrent and 900 s (93–96).
  - They are checked at 828–833.
- **Symptom:** setting these fields on `ReactPlanner(background_tasks=...)` changes nothing; the `SessionLimits` values apply.
- **Find it:** `grep -rn 'max_concurrent_tasks\|max_tasks_per_session\|task_timeout_s' penguiflow/ | grep '\.py:' | grep -v sessions/session.py`. The only hits are the field definitions and the CLI spec and generator (`cli/spec.py`, `cli/generate.py`), which copy the values into generated projects. No runtime code reads them.
- **Workaround:** set the limits on `SessionLimits` and pass them to `SessionManager(limits=...)`.

### 6. A guardrail PAUSE raises a private exception out of `run()`

- **Where:** `penguiflow/planner/tool_calls.py`, 146–153 (`tool_call_start`) and 481–488 (`tool_call_result`).
  - Both call `planner._pause_from_context`, which always raises `_PlannerPauseSignal` (`penguiflow/planner/pause_management.py`, function at 31, raise at 45).
  - The only handlers are `pause_management.py:26`, in the public `pause()` wrapper, and `tool_calls.py:335`, around the tool function call. Neither covers these two calls, so the `return ToolCallOutcome(pause=...)` after each one never runs.
- **Symptom:** `run()` raises `penguiflow.planner.pause._PlannerPauseSignal` instead of returning `PlannerPause(reason="approval_required")`.
  - With `pause_enabled=False`, it raises `RuntimeError: Pause/resume is disabled for this planner`.
  - A DEEP rule timeout where the risk router returns `on_timeout="pause"` takes the same path.
- **Workaround:** use STOP instead of PAUSE.

<details><summary>Repro (prints <code>raised penguiflow.planner.pause._PlannerPauseSignal</code>)</summary>

```python
import asyncio, json
from pydantic import BaseModel
from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog
from penguiflow.planner import ReactPlanner
from penguiflow.planner.guardrails import (AsyncRuleEvaluator, GatewayConfig, GuardrailAction,
    GuardrailDecision, GuardrailGateway, RuleCost, RuleRegistry)
from penguiflow.steering import InMemoryGuardInbox

class Args(BaseModel):
    text: str

async def echo(args: Args, ctx) -> Args:
    return args

class PauseRule:  # FAST rule that asks for approval before every tool call
    rule_id, version, cost, enabled = "pause-all", "1", RuleCost.FAST, True
    supports_event_types = frozenset({"tool_call_start"})
    async def evaluate(self, event, snapshot):
        return GuardrailDecision(action=GuardrailAction.PAUSE, rule_id=self.rule_id, reason="approval")

class OneToolCall:  # LLM stub: call echo once, then answer
    def __init__(self): self.steps = [{"next_node": "echo", "args": {"text": "hi"}}]
    async def complete(self, *, messages, response_format=None, stream=False, on_stream_chunk=None):
        step = self.steps.pop(0) if self.steps else {"next_node": "final_response", "args": {"answer": "ok"}}
        return json.dumps(step)

registry = ModelRegistry()
registry.register("echo", Args, Args)
rules = RuleRegistry()
rules.register(PauseRule())
gateway = GuardrailGateway(registry=rules, guard_inbox=InMemoryGuardInbox(AsyncRuleEvaluator(rules)),
                           config=GatewayConfig(mode="enforce", async_enabled=False))
planner = ReactPlanner(llm_client=OneToolCall(), catalog=build_catalog([Node(echo, name="echo")], registry),
                       guardrail_gateway=gateway)

try:
    print(asyncio.run(planner.run("hi")))
except BaseException as exc:  # expected: PlannerPause returned; actual: private exception raised
    print("raised", type(exc).__module__ + "." + type(exc).__name__)
```

</details>

### 7. A2A `/v1/...` routes are read as tenant `v1`

- **Where:** `penguiflow_a2a/bindings/http.py`, `_include_operation_routes` (298). The `/{tenant}` copy of the routes (308) is mounted before the `/v1` copy (318), so `/{tenant}` matches `/v1/...` first.
- **Symptom:** with the default `A2AConfig` (`allow_v1_aliases=True`, `allow_tenant_prefix=True`), `POST /v1/message:send` reaches the flow with `message.headers.tenant == "v1"`, and tasks are stored under tenant `v1`.
- **Workaround:**
  - call the root routes or `/{tenant}/v1/...`;
  - or set `allow_tenant_prefix=False`.

<details><summary>Repro (the <code>/v1</code> request reports tenant <code>v1</code>)</summary>

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
from penguiflow import Node, NodePolicy, create
from penguiflow_a2a import A2AConfig, A2AService, install_a2a_http
from penguiflow_a2a.models import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

async def entry(message, _ctx):
    return {"tenant": message.headers.tenant}

card = AgentCard(protocol_versions=["0.3"], name="n", description="d", version="1",
                 supported_interfaces=[AgentInterface(url="https://x", protocol_binding="HTTP+JSON")],
                 capabilities=AgentCapabilities(), default_input_modes=["application/json"],
                 default_output_modes=["application/json"],
                 skills=[AgentSkill(id="s", name="s", description="d", tags=["t"])])
app = FastAPI()
install_a2a_http(app, A2AService(create(Node(entry, name="entry", policy=NodePolicy(validate="none")).to()),
                                 agent_card=card, config=A2AConfig()))
body = {"message": {"messageId": "m1", "role": "user", "parts": [{"text": "hi"}]},
        "configuration": {"blocking": True}}
with TestClient(app) as client:
    for path in ("/message:send", "/v1/message:send", "/acme/v1/message:send"):
        task = client.post(path, json=body, headers={"Content-Type": "application/json"}).json()["task"]
        print(path, "->", task["artifacts"][0]["parts"][0]["data"])  # /v1 gives tenant "v1"
```

```
/message:send -> {'data': {'tenant': 'default'}}
/v1/message:send -> {'data': {'tenant': 'v1'}}
/acme/v1/message:send -> {'data': {'tenant': 'acme'}}
```

</details>
