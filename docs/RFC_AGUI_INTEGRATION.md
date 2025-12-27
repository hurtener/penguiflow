# RFC: AG-UI Protocol Integration (v2)

| Field | Value |
|-------|-------|
| **RFC ID** | RFC-2025-001 |
| **Title** | AG-UI Protocol Integration for Playground_UI and Backend Services |
| **Author** | Platform Team |
| **Status** | Draft |
| **Created** | 2025-12-23 |
| **Revised** | 2025-12-23 |

---

## 1. Executive Summary

This RFC proposes adopting the AG-UI protocol as the standard communication layer between all backend agent services and the Playground_UI Svelte frontend.

**Key decision: We use the official AG-UI SDKs, not reimplement from scratch.**

| Layer | Official SDK | What We Build |
|-------|--------------|---------------|
| Backend types & encoder | `ag-ui-protocol` (PyPI) | — |
| Backend adapter | — | Thin wrapper for our services |
| Frontend types & client | `@ag-ui/client`, `@ag-ui/core` (npm) | — |
| Svelte integration | — | Stores + components |

---

## 2. Dependencies

### 2.1 Backend (Python)

```bash
pip install ag-ui-protocol
```

This provides:
- All 17 event types as Pydantic models (`ag_ui.core`)
- SSE encoder (`ag_ui.encoder.EventEncoder`)
- Request types (`RunAgentInput`, `Message`, `Tool`, etc.)

Source: https://github.com/ag-ui-protocol/ag-ui/tree/main/python/ag_ui

### 2.2 Frontend (TypeScript/Svelte)

```bash
npm install @ag-ui/client @ag-ui/core
```

This provides:
- `HttpAgent` — SSE client with RxJS observables
- All TypeScript types for events and requests
- Middleware system for custom processing

Source: https://github.com/ag-ui-protocol/ag-ui/tree/main/typescript/client

---

## 3. What We Build: Backend Adapter

The adapter pattern wraps our service execution (Penguiflow, Iceberg, ACE-MCP) and emits AG-UI events.

### 3.1 Base Adapter Pattern

```python
"""
agui_adapter/base.py

Base adapter pattern for AG-UI integration.
Uses official ag-ui-protocol package for all types and encoding.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

# ═══════════════════════════════════════════════════════════════════════════
# Import everything from official SDK — DO NOT REIMPLEMENT
# ═══════════════════════════════════════════════════════════════════════════
from ag_ui.core import (
    # Request types
    RunAgentInput,
    Message,
    Tool,
    
    # Event types
    EventType,
    RunStartedEvent,
    RunFinishedEvent,
    RunErrorEvent,
    StepStartedEvent,
    StepFinishedEvent,
    TextMessageStartEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    ToolCallStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    StateSnapshotEvent,
    StateDeltaEvent,
    CustomEvent,
    MessagesSnapshotEvent,
)
from ag_ui.encoder import EventEncoder


# Type alias for any AG-UI event
AGUIEvent = (
    RunStartedEvent | RunFinishedEvent | RunErrorEvent |
    StepStartedEvent | StepFinishedEvent |
    TextMessageStartEvent | TextMessageContentEvent | TextMessageEndEvent |
    ToolCallStartEvent | ToolCallArgsEvent | ToolCallEndEvent | ToolCallResultEvent |
    StateSnapshotEvent | StateDeltaEvent |
    CustomEvent | MessagesSnapshotEvent
)


def generate_id(prefix: str = "") -> str:
    """Generate a unique ID with optional prefix."""
    uid = uuid4().hex[:12]
    return f"{prefix}_{uid}" if prefix else uid


class AGUIAdapter(ABC):
    """
    Base adapter for wrapping service execution with AG-UI events.
    
    Subclass this for each backend service (Penguiflow, Iceberg, etc.)
    
    Example:
        class PenguiflowAdapter(AGUIAdapter):
            def __init__(self, pipeline: Pipeline):
                self.pipeline = pipeline
            
            async def run(self, input: RunAgentInput) -> AsyncIterator[AGUIEvent]:
                async def body():
                    yield self.text_start()
                    async for chunk in self.pipeline.execute(...):
                        yield self.text_content(chunk)
                    yield self.text_end()

                async for event in self.with_run_lifecycle(input, body()):
                    yield event
    """
    
    def __init__(self):
        self._thread_id: str | None = None
        self._run_id: str | None = None
        self._current_message_id: str | None = None
        self._message_started: bool = False
    
    @abstractmethod
    async def run(self, input: RunAgentInput) -> AsyncIterator[AGUIEvent]:
        """
        Execute the service and yield AG-UI events.
        
        Implementations MUST:
        1. Yield RunStartedEvent first
        2. Yield RunFinishedEvent or RunErrorEvent last
        """
        raise NotImplementedError
    
    # ═══════════════════════════════════════════════════════════════════════
    # Lifecycle Helpers
    # ═══════════════════════════════════════════════════════════════════════
    
    async def with_run_lifecycle(
        self,
        input: RunAgentInput,
        events: AsyncIterator[AGUIEvent],
        *,
        initial_state: dict[str, Any] | None = None,
    ) -> AsyncIterator[AGUIEvent]:
        """Wrap an event stream with RUN_STARTED/FINISHED/ERROR.

        Note: this is an async generator (not an async contextmanager). A contextmanager
        cannot yield multiple times, but AG-UI streaming needs to yield many events.

        Usage:
            async def run(self, input):
                async def body():
                    yield self.text_start()
                    yield self.text_content("Hello!")
                    yield self.text_end()

                async for event in self.with_run_lifecycle(input, body()):
                    yield event
        """
        self._thread_id = input.thread_id
        self._run_id = input.run_id

        try:
            # Emit RUN_STARTED
            yield RunStartedEvent(
                type=EventType.RUN_STARTED,
                thread_id=input.thread_id,
                run_id=input.run_id,
            )
            
            # Emit initial state if provided
            if initial_state is not None:
                yield StateSnapshotEvent(
                    type=EventType.STATE_SNAPSHOT,
                    snapshot=initial_state,
                )

            # Yield inner events
            async for event in events:
                yield event

            # Auto-close message if still open
            if self._message_started:
                yield self.text_end()
            
            # Emit RUN_FINISHED
            yield RunFinishedEvent(
                type=EventType.RUN_FINISHED,
                thread_id=input.thread_id,
                run_id=input.run_id,
            )
            
        except Exception as e:
            # Emit RUN_ERROR
            yield RunErrorEvent(
                type=EventType.RUN_ERROR,
                message=str(e),
                code=type(e).__name__,
            )
            raise
        
        finally:
            self._thread_id = None
            self._run_id = None
            self._current_message_id = None
            self._message_started = False
    
    # ═══════════════════════════════════════════════════════════════════════
    # Message Helpers
    # ═══════════════════════════════════════════════════════════════════════
    
    def text_start(
        self,
        message_id: str | None = None,
        role: str = "assistant",
    ) -> TextMessageStartEvent:
        """Start a new text message stream."""
        self._current_message_id = message_id or generate_id("msg")
        self._message_started = True
        return TextMessageStartEvent(
            type=EventType.TEXT_MESSAGE_START,
            message_id=self._current_message_id,
            role=role,
        )
    
    def text_content(self, delta: str) -> TextMessageContentEvent:
        """Emit a text content chunk."""
        if not self._message_started:
            raise RuntimeError("Call text_start() before text_content()")
        return TextMessageContentEvent(
            type=EventType.TEXT_MESSAGE_CONTENT,
            message_id=self._current_message_id,
            delta=delta,
        )
    
    def text_end(self) -> TextMessageEndEvent:
        """End the current text message stream."""
        if not self._message_started:
            raise RuntimeError("No message to end")
        self._message_started = False
        return TextMessageEndEvent(
            type=EventType.TEXT_MESSAGE_END,
            message_id=self._current_message_id,
        )
    
    # ═══════════════════════════════════════════════════════════════════════
    # Tool Call Helpers
    # ═══════════════════════════════════════════════════════════════════════
    
    def tool_start(
        self,
        name: str,
        tool_call_id: str | None = None,
    ) -> ToolCallStartEvent:
        """Start a tool call."""
        return ToolCallStartEvent(
            type=EventType.TOOL_CALL_START,
            tool_call_id=tool_call_id or generate_id("call"),
            tool_call_name=name,
            parent_message_id=self._current_message_id,
        )
    
    def tool_args(self, tool_call_id: str, delta: str) -> ToolCallArgsEvent:
        """Stream tool arguments."""
        return ToolCallArgsEvent(
            type=EventType.TOOL_CALL_ARGS,
            tool_call_id=tool_call_id,
            delta=delta,
        )
    
    def tool_end(self, tool_call_id: str) -> ToolCallEndEvent:
        """End tool argument streaming."""
        return ToolCallEndEvent(
            type=EventType.TOOL_CALL_END,
            tool_call_id=tool_call_id,
        )
    
    def tool_result(
        self,
        tool_call_id: str,
        content: str | dict,
        message_id: str | None = None,
    ) -> ToolCallResultEvent:
        """Emit tool execution result."""
        if isinstance(content, dict):
            content = json.dumps(content)
        return ToolCallResultEvent(
            type=EventType.TOOL_CALL_RESULT,
            tool_call_id=tool_call_id,
            message_id=message_id or generate_id("msg"),
            role="tool",
            content=content,
        )
    
    # ═══════════════════════════════════════════════════════════════════════
    # State Helpers
    # ═══════════════════════════════════════════════════════════════════════
    
    def state_snapshot(self, state: dict[str, Any]) -> StateSnapshotEvent:
        """Emit full state snapshot."""
        return StateSnapshotEvent(
            type=EventType.STATE_SNAPSHOT,
            snapshot=state,
        )
    
    def state_delta(self, operations: list[dict]) -> StateDeltaEvent:
        """
        Emit state delta (JSON Patch).
        
        Args:
            operations: List of JSON Patch operations
                [{"op": "replace", "path": "/status", "value": "done"}]
        """
        return StateDeltaEvent(
            type=EventType.STATE_DELTA,
            delta=operations,
        )
    
    def state_set(self, path: str, value: Any) -> StateDeltaEvent:
        """Convenience: replace value at path."""
        return self.state_delta([{"op": "replace", "path": path, "value": value}])
    
    def state_add(self, path: str, value: Any) -> StateDeltaEvent:
        """Convenience: add value at path."""
        return self.state_delta([{"op": "add", "path": path, "value": value}])
    
    # ═══════════════════════════════════════════════════════════════════════
    # Step Helpers
    # ═══════════════════════════════════════════════════════════════════════
    
    def step_start(self, name: str, **metadata) -> StepStartedEvent:
        """Mark entry into a named step."""
        return StepStartedEvent(
            type=EventType.STEP_STARTED,
            step_name=name,
            metadata=metadata if metadata else None,
        )
    
    def step_end(self, name: str, **metadata) -> StepFinishedEvent:
        """Mark exit from a named step."""
        return StepFinishedEvent(
            type=EventType.STEP_FINISHED,
            step_name=name,
            metadata=metadata if metadata else None,
        )
    
    # ═══════════════════════════════════════════════════════════════════════
    # Custom Events
    # ═══════════════════════════════════════════════════════════════════════
    
    def custom(self, name: str, value: Any) -> CustomEvent:
        """Emit application-specific event."""
        return CustomEvent(
            type=EventType.CUSTOM,
            name=name,
            value=value,
        )


class RunContext:
    """Placeholder for run context (can be extended for state tracking)."""
    def __init__(self, adapter: AGUIAdapter):
        self.adapter = adapter
```

### 3.2 FastAPI Endpoint Helper

```python
"""
agui_adapter/fastapi.py

FastAPI integration for AG-UI adapters.
"""
from typing import Callable, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder


def create_agui_endpoint(
    adapter_run: Callable[[RunAgentInput], AsyncIterator],
) -> Callable:
    """
    Create a FastAPI endpoint handler for an AG-UI adapter.
    
    Usage:
        adapter = MyAdapter()
        
        @app.post("/agent")
        async def agent(input: RunAgentInput, request: Request):
            return await create_agui_endpoint(adapter.run)(input, request)
    """
    async def endpoint(input: RunAgentInput, request: Request) -> StreamingResponse:
        accept = request.headers.get("accept", "text/event-stream")
        encoder = EventEncoder(accept=accept)
        
        async def stream():
            async for event in adapter_run(input):
                yield encoder.encode(event)
        
        return StreamingResponse(
            stream(),
            media_type=encoder.get_content_type(),
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    
    return endpoint


def add_agui_route(
    app: FastAPI,
    path: str,
    adapter_run: Callable[[RunAgentInput], AsyncIterator],
    **route_kwargs,
) -> None:
    """
    Add an AG-UI route to a FastAPI app.
    
    Usage:
        app = FastAPI()
        adapter = MyAdapter()
        add_agui_route(app, "/agent", adapter.run)
    """
    endpoint = create_agui_endpoint(adapter_run)
    
    @app.post(path, **route_kwargs)
    async def agui_route(input: RunAgentInput, request: Request):
        return await endpoint(input, request)
```

### 3.3 Example: Wrapping an LLM Service

```python
"""
Example adapter wrapping OpenAI streaming.
"""
from typing import AsyncIterator
from openai import AsyncOpenAI

from ag_ui.core import RunAgentInput
from agui_adapter import AGUIAdapter, AGUIEvent


class OpenAIAdapter(AGUIAdapter):
    def __init__(self, model: str = "gpt-4o"):
        super().__init__()
        self.client = AsyncOpenAI()
        self.model = model
    
    async def run(self, input: RunAgentInput) -> AsyncIterator[AGUIEvent]:
        # Lifecycle start (manual approach without context manager)
        yield self.run_started(input)
        
        try:
            # Start message
            yield self.text_start()
            
            # Stream from OpenAI
            stream = await self.client.chat.completions.create(
                model=self.model,
                stream=True,
                messages=[
                    {"role": m.role, "content": m.content or ""}
                    for m in input.messages
                ],
            )
            
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield self.text_content(delta.content)
            
            # End message
            yield self.text_end()
            
            # Lifecycle end
            yield self.run_finished(input)
            
        except Exception as e:
            yield self.run_error(str(e))
            raise
    
    # Additional helpers for manual lifecycle
    def run_started(self, input: RunAgentInput):
        from ag_ui.core import RunStartedEvent, EventType
        return RunStartedEvent(
            type=EventType.RUN_STARTED,
            thread_id=input.thread_id,
            run_id=input.run_id,
        )
    
    def run_finished(self, input: RunAgentInput):
        from ag_ui.core import RunFinishedEvent, EventType
        return RunFinishedEvent(
            type=EventType.RUN_FINISHED,
            thread_id=input.thread_id,
            run_id=input.run_id,
        )
    
    def run_error(self, message: str):
        from ag_ui.core import RunErrorEvent, EventType
        return RunErrorEvent(
            type=EventType.RUN_ERROR,
            message=message,
        )


# FastAPI app
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from agui_adapter.fastapi import create_agui_endpoint

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

adapter = OpenAIAdapter()

@app.post("/agent")
async def agent_endpoint(input: RunAgentInput, request: Request):
    return await create_agui_endpoint(adapter.run)(input, request)
```

### 3.4 PenguiFlow Alignment (Contexts, Tool Calls, Artifacts)

PenguiFlow already has strong primitives that AG-UI should sit *on top of*, not replace:

- **Two contexts**: `llm_context` vs `tool_context` (core design; preserve it).
- **ArtifactStore + ArtifactRef**: out-of-band binary/large text storage with compact refs.
- **ToolNode**: MCP/UTCP external tools with artifact extraction + MCP resources support.
- **PlannerEvent stream**: structured execution telemetry from `ReactPlanner` (used by the Playground today).

This RFC should explicitly account for these so AG-UI becomes a protocol adapter layer.

#### 3.4.1 Preserve `llm_context` vs `tool_context`

AG-UI inputs should carry **both**:
- `llm_context`: JSON-only, safe to be LLM-visible.
- `tool_context`: JSON-only for UI-originating runs; runtime-only objects remain supported for direct Python usage (non-HTTP) but cannot cross the wire.

Recommendation:
- Keep PenguiFlow’s split in the backend API shape.
- Encode them into AG-UI `forwarded_props` (namespaced) so the adapter can pass them through cleanly:

```json
{
  "forwardedProps": {
    "penguiflow": {
      "llm_context": { "...": "..." },
      "tool_context": { "tenant_id": "...", "user_id": "...", "session_id": "..." }
    }
  }
}
```

#### 3.4.2 Tool Call Telemetry (Needed for AG-UI ToolCall events)

AG-UI has explicit tool-call events (`TOOL_CALL_START/ARGS/END/RESULT`). Today, PenguiFlow exposes:
- Tool name + args at `TrajectoryStep.action`
- Tool output at `TrajectoryStep.observation` (already binary-safe due to ArtifactRef extraction and planner guardrails)
- Step boundaries via `PlannerEvent(step_start/step_complete)` and gating via `action_seq`

To support first-class AG-UI tool-call UI (without guessing), we should add **new internal events** emitted by `ReactPlanner` during tool execution:
- `tool_call_start`: includes `tool_call_id`, `tool_name`, `args_json`, `trajectory_step`, `action_seq`
- `tool_call_end`: includes `tool_call_id`
- `tool_call_result`: includes `tool_call_id`, `result_json` (post-transformation, artifact-safe)

This is mostly a mapping of data PenguiFlow already has; the key is emitting it at the right time so the AG-UI adapter can stream accurately.

#### 3.4.3 Binary Artifacts + MCP Resources in an AG-UI World

AG-UI does not define an official “artifact download” event. PenguiFlow should:
- Keep **ArtifactRef embedded** in tool observations/results (LLM-safe).
- Use **CustomEvent** for “downloadable artifact available” UX:

```python
CustomEvent(
  type=EventType.CUSTOM,
  name="artifact_stored",
  value={
    "artifact": artifact_ref.model_dump(),
    "download_url": f"/artifacts/{artifact_ref.id}",
  },
)
```

This complements existing HTTP endpoints (already implemented in PenguiFlow Playground backends, e.g. `/artifacts/{artifact_id}`).

For MCP resources:
- Preserve `resource_link` as a lazy pointer in tool output by default.
- Expose `resources_list/resources_read` tools (already generated by ToolNode) and/or emit a CustomEvent for cache invalidation:
  - `name="resource_updated", value={"namespace": "...", "uri": "..."}`

#### 3.4.4 Existing Partial Plumbing (Gap)

PenguiFlow’s Playground SSE mapper already has branches for:
- `artifact_stored`
- `resource_updated`

…but these `PlannerEvent` types are not emitted today. When implementing AG-UI support, we should close this gap so both the legacy SSE stream and AG-UI streams can surface artifacts/resources consistently.

---

## 4. What We Build: Svelte Integration

We use `@ag-ui/client` for the SSE connection and build Svelte stores + components on top.

### 4.1 Package Structure

```
src/lib/agui/
├── index.ts              # Public exports
├── stores.ts             # Svelte stores
├── patch.ts              # JSON Patch helper (thin wrapper)
└── components/
    ├── index.ts
    ├── AGUIProvider.svelte
    ├── MessageList.svelte
    ├── Message.svelte
    ├── ToolCall.svelte
    └── StateDebugger.svelte
```

### 4.2 Dependencies

```json
{
  "dependencies": {
    "@ag-ui/client": "^0.x",
    "@ag-ui/core": "^0.x",
    "fast-json-patch": "^3.1.1"
  }
}
```

### 4.3 Svelte Stores (`stores.ts`)

```typescript
/**
 * AG-UI Svelte Stores
 * 
 * Wraps @ag-ui/client HttpAgent with Svelte reactive stores.
 */
import { writable, derived, get, type Readable } from 'svelte/store';
import { getContext, setContext } from 'svelte';
import { HttpAgent } from '@ag-ui/client';
import type {
  BaseEvent,
  RunAgentInput,
  Message,
  Tool,
} from '@ag-ui/core';
import { applyPatch } from 'fast-json-patch';

// ═══════════════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════════════

export type RunStatus = 'idle' | 'running' | 'finished' | 'error';

export interface StreamingMessage {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  isStreaming: boolean;
  toolCalls: StreamingToolCall[];
}

export interface StreamingToolCall {
  id: string;
  name: string;
  arguments: string;
  isStreaming: boolean;
  result?: string;
}

export interface ActiveStep {
  name: string;
  startedAt: Date;
  metadata?: Record<string, unknown>;
}

export interface AGUIStoreState {
  status: RunStatus;
  threadId: string | null;
  runId: string | null;
  messages: StreamingMessage[];
  agentState: Record<string, unknown>;
  activeSteps: ActiveStep[];
  error: { message: string; code?: string } | null;
}

export interface AGUIStoreOptions {
  url: string;
  tools?: Tool[];
  initialState?: Record<string, unknown>;
  onComplete?: () => void;
  onError?: (error: { message: string; code?: string }) => void;
  onCustomEvent?: (name: string, value: unknown) => void;
}

export interface AGUIStore {
  // Stores
  state: Readable<AGUIStoreState>;
  status: Readable<RunStatus>;
  messages: Readable<StreamingMessage[]>;
  agentState: Readable<Record<string, unknown>>;
  isRunning: Readable<boolean>;
  error: Readable<{ message: string; code?: string } | null>;
  activeSteps: Readable<ActiveStep[]>;
  
  // Actions
  sendMessage: (content: string) => Promise<void>;
  cancel: () => void;
  reset: () => void;
}

// ═══════════════════════════════════════════════════════════════════════════
// Store Factory
// ═══════════════════════════════════════════════════════════════════════════

function createInitialState(): AGUIStoreState {
  return {
    status: 'idle',
    threadId: null,
    runId: null,
    messages: [],
    agentState: {},
    activeSteps: [],
    error: null,
  };
}

export function createAGUIStore(options: AGUIStoreOptions): AGUIStore {
  const agent = new HttpAgent({ url: options.url });
  const state = writable<AGUIStoreState>({
    ...createInitialState(),
    agentState: options.initialState ?? {},
  });
  
  let messageHistory: Message[] = [];
  let abortController: AbortController | null = null;
  
  // ─────────────────────────────────────────────────────────────────────────
  // Event Processing
  // ─────────────────────────────────────────────────────────────────────────
  
  function processEvent(event: BaseEvent): void {
    state.update(s => {
      switch (event.type) {
        // Lifecycle
        case 'RUN_STARTED':
          return {
            ...s,
            status: 'running',
            threadId: (event as any).threadId,
            runId: (event as any).runId,
            error: null,
          };
        
        case 'RUN_FINISHED':
          options.onComplete?.();
          return { ...s, status: 'finished' };
        
        case 'RUN_ERROR': {
          const err = { message: (event as any).message, code: (event as any).code };
          options.onError?.(err);
          return { ...s, status: 'error', error: err };
        }
        
        case 'STEP_STARTED':
          return {
            ...s,
            activeSteps: [...s.activeSteps, {
              name: (event as any).stepName,
              startedAt: new Date(),
              metadata: (event as any).metadata,
            }],
          };
        
        case 'STEP_FINISHED':
          return {
            ...s,
            activeSteps: s.activeSteps.filter(step => step.name !== (event as any).stepName),
          };
        
        // Text Messages
        case 'TEXT_MESSAGE_START': {
          const e = event as any;
          return {
            ...s,
            messages: [...s.messages, {
              id: e.messageId,
              role: e.role,
              content: '',
              isStreaming: true,
              toolCalls: [],
            }],
          };
        }
        
        case 'TEXT_MESSAGE_CONTENT': {
          const e = event as any;
          return {
            ...s,
            messages: s.messages.map(msg =>
              msg.id === e.messageId
                ? { ...msg, content: msg.content + e.delta }
                : msg
            ),
          };
        }
        
        case 'TEXT_MESSAGE_END': {
          const e = event as any;
          const updated = s.messages.map(msg =>
            msg.id === e.messageId
              ? { ...msg, isStreaming: false }
              : msg
          );
          
          // Update history
          const msg = updated.find(m => m.id === e.messageId);
          if (msg && msg.role !== 'tool') {
            messageHistory.push({
              id: msg.id,
              role: msg.role as any,
              content: msg.content,
            });
          }
          
          return { ...s, messages: updated };
        }
        
        // Tool Calls
        case 'TOOL_CALL_START': {
          const e = event as any;
          return {
            ...s,
            messages: s.messages.map(msg =>
              msg.id === e.parentMessageId
                ? {
                    ...msg,
                    toolCalls: [...msg.toolCalls, {
                      id: e.toolCallId,
                      name: e.toolCallName,
                      arguments: '',
                      isStreaming: true,
                    }],
                  }
                : msg
            ),
          };
        }
        
        case 'TOOL_CALL_ARGS': {
          const e = event as any;
          return {
            ...s,
            messages: s.messages.map(msg => ({
              ...msg,
              toolCalls: msg.toolCalls.map(tc =>
                tc.id === e.toolCallId
                  ? { ...tc, arguments: tc.arguments + e.delta }
                  : tc
              ),
            })),
          };
        }
        
        case 'TOOL_CALL_END': {
          const e = event as any;
          return {
            ...s,
            messages: s.messages.map(msg => ({
              ...msg,
              toolCalls: msg.toolCalls.map(tc =>
                tc.id === e.toolCallId
                  ? { ...tc, isStreaming: false }
                  : tc
              ),
            })),
          };
        }
        
        case 'TOOL_CALL_RESULT': {
          const e = event as any;
          
          // Add to history
          messageHistory.push({
            id: e.messageId,
            role: 'tool',
            content: e.content,
          });
          
          return {
            ...s,
            messages: s.messages.map(msg => ({
              ...msg,
              toolCalls: msg.toolCalls.map(tc =>
                tc.id === e.toolCallId
                  ? { ...tc, result: e.content }
                  : tc
              ),
            })),
          };
        }
        
        // State
        case 'STATE_SNAPSHOT':
          return { ...s, agentState: (event as any).snapshot };
        
        case 'STATE_DELTA': {
          const result = applyPatch(s.agentState, (event as any).delta, true, false);
          return { ...s, agentState: result.newDocument };
        }
        
        // Custom
        case 'CUSTOM':
          options.onCustomEvent?.((event as any).name, (event as any).value);
          return s;
        
        // Messages snapshot
        case 'MESSAGES_SNAPSHOT': {
          const msgs = (event as any).messages;
          messageHistory = [...msgs];
          return {
            ...s,
            messages: msgs.map((m: Message) => ({
              id: m.id,
              role: m.role,
              content: m.content ?? '',
              isStreaming: false,
              toolCalls: [],
            })),
          };
        }
        
        default:
          return s;
      }
    });
  }
  
  // ─────────────────────────────────────────────────────────────────────────
  // Actions
  // ─────────────────────────────────────────────────────────────────────────
  
  async function sendMessage(content: string): Promise<void> {
    const currentState = get(state);
    if (currentState.status === 'running') return;
    
    // Add user message
    const userMsg: Message = {
      id: `msg_${Date.now()}`,
      role: 'user',
      content,
    };
    messageHistory.push(userMsg);
    
    state.update(s => ({
      ...s,
      messages: [...s.messages, {
        id: userMsg.id,
        role: 'user',
        content,
        isStreaming: false,
        toolCalls: [],
      }],
    }));
    
    // Prepare request
    const input: RunAgentInput = {
      threadId: currentState.threadId ?? `thread_${Date.now()}`,
      runId: `run_${Date.now()}`,
      messages: messageHistory,
      tools: options.tools ?? [],
      context: [],
      state: currentState.agentState,
      forwardedProps: {},
    };
    
    // Stream events using HttpAgent
    abortController = new AbortController();
    
    try {
      // HttpAgent.runAgent returns an observable, convert to async iteration
      const observable = agent.runAgent(input);
      
      await new Promise<void>((resolve, reject) => {
        const subscription = observable.subscribe({
          next: (event) => processEvent(event),
          error: (err) => {
            processEvent({
              type: 'RUN_ERROR',
              message: err.message,
              code: 'CLIENT_ERROR',
            } as any);
            reject(err);
          },
          complete: () => resolve(),
        });
        
        // Handle cancellation
        abortController!.signal.addEventListener('abort', () => {
          subscription.unsubscribe();
          state.update(s => ({ ...s, status: 'idle' }));
          resolve();
        });
      });
      
    } catch (err) {
      // Error already handled via observable
    } finally {
      abortController = null;
    }
  }
  
  function cancel(): void {
    abortController?.abort();
  }
  
  function reset(): void {
    cancel();
    messageHistory = [];
    state.set({
      ...createInitialState(),
      agentState: options.initialState ?? {},
    });
  }
  
  // ─────────────────────────────────────────────────────────────────────────
  // Derived Stores
  // ─────────────────────────────────────────────────────────────────────────
  
  return {
    state,
    status: derived(state, $s => $s.status),
    messages: derived(state, $s => $s.messages),
    agentState: derived(state, $s => $s.agentState),
    isRunning: derived(state, $s => $s.status === 'running'),
    error: derived(state, $s => $s.error),
    activeSteps: derived(state, $s => $s.activeSteps),
    sendMessage,
    cancel,
    reset,
  };
}

// ═══════════════════════════════════════════════════════════════════════════
// Context
// ═══════════════════════════════════════════════════════════════════════════

const AGUI_KEY = Symbol('agui');

export function setAGUIContext(store: AGUIStore): void {
  setContext(AGUI_KEY, store);
}

export function getAGUIContext(): AGUIStore {
  const store = getContext<AGUIStore>(AGUI_KEY);
  if (!store) throw new Error('AGUIProvider not found');
  return store;
}
```

### 4.4 Components

#### `AGUIProvider.svelte`

```svelte
<script lang="ts">
  import { onDestroy } from 'svelte';
  import { createAGUIStore, setAGUIContext, type AGUIStoreOptions } from '../stores';
  import type { Tool } from '@ag-ui/core';
  
  export let url: string;
  export let tools: Tool[] = [];
  export let initialState: Record<string, unknown> = {};
  export let onComplete: (() => void) | undefined = undefined;
  export let onError: ((e: { message: string }) => void) | undefined = undefined;
  export let onCustomEvent: ((name: string, value: unknown) => void) | undefined = undefined;
  
  const store = createAGUIStore({
    url,
    tools,
    initialState,
    onComplete,
    onError,
    onCustomEvent,
  });
  
  setAGUIContext(store);
  
  onDestroy(() => store.cancel());
</script>

<slot {store} />
```

#### `MessageList.svelte`

```svelte
<script lang="ts">
  import { getAGUIContext } from '../stores';
  import Message from './Message.svelte';
  
  const { messages } = getAGUIContext();
  
  let container: HTMLElement;
  
  $: if ($messages.length && container) {
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
  }
</script>

<div class="agui-message-list" bind:this={container}>
  {#each $messages as message (message.id)}
    <Message {message} />
  {/each}
  
  {#if $messages.length === 0}
    <slot name="empty">
      <p class="agui-empty">No messages yet</p>
    </slot>
  {/if}
</div>

<style>
  .agui-message-list {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    overflow-y: auto;
    padding: 1rem;
  }
  .agui-empty {
    text-align: center;
    opacity: 0.5;
  }
</style>
```

#### `Message.svelte`

```svelte
<script lang="ts">
  import type { StreamingMessage } from '../stores';
  import ToolCall from './ToolCall.svelte';
  
  export let message: StreamingMessage;
</script>

<div class="agui-message" class:user={message.role === 'user'} class:assistant={message.role === 'assistant'}>
  <div class="agui-message-role">{message.role}</div>
  <div class="agui-message-content">
    {message.content}{#if message.isStreaming}<span class="cursor">▋</span>{/if}
  </div>
  
  {#if message.toolCalls.length > 0}
    <div class="agui-tool-calls">
      {#each message.toolCalls as tc (tc.id)}
        <ToolCall toolCall={tc} />
      {/each}
    </div>
  {/if}
</div>

<style>
  .agui-message {
    padding: 0.75rem 1rem;
    border-radius: 0.5rem;
    max-width: 80%;
  }
  .agui-message.user {
    align-self: flex-end;
    background: #3b82f6;
    color: white;
  }
  .agui-message.assistant {
    align-self: flex-start;
    background: #f3f4f6;
  }
  .agui-message-role {
    font-size: 0.7rem;
    text-transform: uppercase;
    opacity: 0.6;
    margin-bottom: 0.25rem;
  }
  .agui-message-content {
    white-space: pre-wrap;
  }
  .cursor {
    animation: blink 0.7s infinite;
  }
  @keyframes blink {
    50% { opacity: 0; }
  }
  .agui-tool-calls {
    margin-top: 0.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
</style>
```

#### `ToolCall.svelte`

```svelte
<script lang="ts">
  import type { StreamingToolCall } from '../stores';
  
  export let toolCall: StreamingToolCall;
  
  $: parsedArgs = (() => {
    try { return JSON.parse(toolCall.arguments); }
    catch { return null; }
  })();
</script>

<div class="agui-tool-call" class:streaming={toolCall.isStreaming}>
  <div class="header">
    🔧 <strong>{toolCall.name}</strong>
    {#if toolCall.isStreaming}<span class="dots">...</span>{/if}
  </div>
  
  <pre class="args">{parsedArgs ? JSON.stringify(parsedArgs, null, 2) : toolCall.arguments}</pre>
  
  {#if toolCall.result}
    <div class="result">
      <div class="label">Result:</div>
      <pre>{toolCall.result}</pre>
    </div>
  {/if}
</div>

<style>
  .agui-tool-call {
    background: #f0fdf4;
    border: 1px solid #86efac;
    border-radius: 0.375rem;
    padding: 0.5rem;
    font-size: 0.8rem;
  }
  .agui-tool-call.streaming {
    border-style: dashed;
  }
  .header {
    margin-bottom: 0.25rem;
  }
  .dots {
    animation: pulse 1s infinite;
  }
  @keyframes pulse {
    50% { opacity: 0.5; }
  }
  pre {
    margin: 0;
    font-size: 0.75rem;
    background: rgba(0,0,0,0.05);
    padding: 0.25rem;
    border-radius: 0.25rem;
    overflow-x: auto;
  }
  .result {
    margin-top: 0.5rem;
    padding-top: 0.5rem;
    border-top: 1px dashed #86efac;
  }
  .label {
    font-size: 0.7rem;
    opacity: 0.7;
  }
</style>
```

#### `StateDebugger.svelte`

```svelte
<script lang="ts">
  import { getAGUIContext } from '../stores';
  
  const { state, status, agentState, activeSteps } = getAGUIContext();
  
  export let expanded = false;
</script>

<details class="agui-debugger" bind:open={expanded}>
  <summary>
    AG-UI Debug
    <span class="status" class:running={$status === 'running'} class:error={$status === 'error'}>
      {$status}
    </span>
  </summary>
  
  <div class="content">
    <section>
      <h4>Thread / Run</h4>
      <code>{$state.threadId ?? '—'} / {$state.runId ?? '—'}</code>
    </section>
    
    {#if $activeSteps.length > 0}
      <section>
        <h4>Active Steps</h4>
        <ul>{#each $activeSteps as s}<li>{s.name}</li>{/each}</ul>
      </section>
    {/if}
    
    <section>
      <h4>Agent State</h4>
      <pre>{JSON.stringify($agentState, null, 2)}</pre>
    </section>
    
    {#if $state.error}
      <section class="error">
        <h4>Error</h4>
        <pre>{JSON.stringify($state.error, null, 2)}</pre>
      </section>
    {/if}
  </div>
</details>

<style>
  .agui-debugger {
    font-family: monospace;
    font-size: 0.75rem;
    background: #1f2937;
    color: #e5e7eb;
    padding: 0.5rem;
    border-radius: 0.5rem;
  }
  summary {
    cursor: pointer;
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }
  .status {
    padding: 0.1rem 0.3rem;
    border-radius: 0.25rem;
    font-size: 0.65rem;
    background: #374151;
  }
  .status.running { background: #3b82f6; }
  .status.error { background: #ef4444; }
  .content {
    margin-top: 0.5rem;
    padding-top: 0.5rem;
    border-top: 1px solid #374151;
  }
  section { margin-bottom: 0.5rem; }
  h4 {
    margin: 0 0 0.25rem;
    font-size: 0.65rem;
    text-transform: uppercase;
    opacity: 0.6;
  }
  pre, code {
    margin: 0;
    font-size: 0.7rem;
  }
  ul { margin: 0; padding-left: 1rem; }
  .error { color: #fca5a5; }
</style>
```

### 4.5 Public Exports (`index.ts`)

```typescript
// Types (re-export from @ag-ui/core)
export type {
  BaseEvent,
  RunAgentInput,
  Message,
  Tool,
} from '@ag-ui/core';

// Client (re-export from @ag-ui/client)
export { HttpAgent } from '@ag-ui/client';

// Our stores
export {
  createAGUIStore,
  setAGUIContext,
  getAGUIContext,
  type AGUIStore,
  type AGUIStoreOptions,
  type StreamingMessage,
  type StreamingToolCall,
  type RunStatus,
} from './stores';

// Our components
export { default as AGUIProvider } from './components/AGUIProvider.svelte';
export { default as MessageList } from './components/MessageList.svelte';
export { default as Message } from './components/Message.svelte';
export { default as ToolCall } from './components/ToolCall.svelte';
export { default as StateDebugger } from './components/StateDebugger.svelte';
```

---

## 5. Full Integration Example

### 5.1 Playground_UI Page

```svelte
<!-- routes/+page.svelte -->
<script lang="ts">
  import {
    AGUIProvider,
    MessageList,
    StateDebugger,
  } from '$lib/agui';
  
  const tools = [
    {
      name: 'search',
      description: 'Search documents',
      parameters: {
        type: 'object',
        properties: {
          query: { type: 'string' },
        },
        required: ['query'],
      },
    },
  ];
  
  let input = '';
  let store: any;
  
  function send() {
    if (!input.trim()) return;
    store.sendMessage(input);
    input = '';
  }
</script>

<AGUIProvider url="http://localhost:8000/agent" {tools} let:store>
  <div class="playground">
    <MessageList />
    
    <form on:submit|preventDefault={send}>
      <input bind:value={input} placeholder="Message..." disabled={$store.isRunning} />
      <button disabled={$store.isRunning}>Send</button>
      {#if $store.isRunning}
        <button type="button" on:click={() => store.cancel()}>Cancel</button>
      {/if}
    </form>
    
    <StateDebugger expanded />
  </div>
</AGUIProvider>
```

---

## 6. Migration Plan

This migration is structured in phases (not weeks) so we can ship value incrementally and track progress directly in this RFC.

### Phase 0 — Backend Foundation

**Goal:** Introduce a minimal, correct AG-UI backend streaming surface using the official SDKs (types + encoder).

**Non-goals:** PenguiFlow-specific planner mapping; replacing existing Playground SSE; frontend changes.

**Tasks:**
- [ ] Add `ag-ui-protocol` (PyPI) to backend optional dependencies.
- [ ] Implement `agui_adapter/` (or `penguiflow/agui_adapter/`) with `AGUIAdapter` + `create_agui_endpoint`.
- [ ] Add a dedicated FastAPI route (e.g. `POST /agui/agent`) that streams AG-UI events.

**Done / Notes:**
- [ ] _TBD_

### Phase 1 — PenguiFlow Adapter (Planner → AG-UI)

**Goal:** Wrap `ReactPlanner` execution and stream an AG-UI-compatible run: lifecycle + steps + assistant text.

**Non-goals:** Tool-call events; artifact/resource UX; UI migration.

**Tasks:**
- [ ] Define ID mapping: `thread_id ↔ session_id`, `run_id ↔ trace_id` (one AG-UI run per `planner.run()` call).
- [ ] Preserve PenguiFlow `llm_context` vs `tool_context` split via `RunAgentInput.forwarded_props`.
- [ ] Map PenguiFlow step boundaries (`PlannerEvent.step_start/step_complete`) to `STEP_STARTED/STEP_FINISHED`.
- [ ] Map PenguiFlow final-answer streaming (`PlannerEvent.llm_stream_chunk` with `channel="answer"`) to `TEXT_MESSAGE_*` events.

**Done / Notes:**
- [ ] _TBD_

### Phase 2 — Tool Calls + Binary/Resource Events

**Goal:** Achieve first-class tool-call UI parity and fully support the binary artifact + MCP resource model.

**Non-goals:** Reimplementing AG-UI; streaming raw bytes over SSE; changing ArtifactStore contracts.

**Tasks:**
- [ ] Add internal planner events: `tool_call_start`, `tool_call_end`, `tool_call_result` (keyed by `trajectory_step` / `action_seq`).
- [ ] Emit artifact/resource signals:
  - [ ] `artifact_stored` (for ArtifactRef announcements)
  - [ ] `resource_updated` (for MCP resource invalidation)
- [ ] Map them to AG-UI `CustomEvent` (`name="artifact_stored"`, `name="resource_updated"`) and include download/read URLs.

**Done / Notes:**
- [ ] _TBD_

### Phase 3 — Playground UI Migration (AG-UI Client)

**Goal:** Make the Playground UI consume AG-UI streams (optionally behind a toggle) while keeping the existing UI working during rollout.

**Non-goals:** Large UI redesign; breaking existing `/chat/stream` consumers.

**Tasks:**
- [ ] Add `@ag-ui/client` + `@ag-ui/core` to the UI build.
- [ ] Implement AG-UI stores/components (or adapt existing Playground stores) for:
  - [ ] messages (assistant + user)
  - [ ] steps
  - [ ] tool calls
  - [ ] custom events for artifacts/resources
- [ ] Render downloadable artifacts using existing `/artifacts/{artifact_id}` endpoints and CustomEvent payloads.

**Done / Notes:**
- [ ] _TBD_

### Phase 4 — Stabilization + Deprecation

**Goal:** Harden the integration, document it, and move toward AG-UI as the default protocol.

**Non-goals:** Removing legacy SSE immediately; forcing downstream users to migrate without overlap.

**Tasks:**
- [ ] Add tests for adapter event ordering and edge cases (errors, cancellation, pause/resume). (Library COV gate 85%)
- [ ] Document the contract: PenguiFlow contexts, artifacts, resources, tool-call events.
- [ ] Run legacy SSE (`/chat/stream`) and AG-UI (`/agui/agent`) in parallel until stable; then deprecate legacy endpoints.

**Done / Notes:**
- [ ] _TBD_

### Compatibility and Rollout

**Default posture: opt-in and additive.**

- Existing PenguiFlow core APIs (`ReactPlanner.run`, ToolNode, ArtifactStore) remain supported.
- The current Playground streaming contract (`/chat/stream`) remains available while AG-UI is introduced via a separate endpoint (e.g. `/agui/agent`).
- New internal telemetry (tool-call + artifact/resource events) is additive; consumers that ignore unknown events continue to work.
- Any future switch of defaults (e.g. Playground UI consuming AG-UI by default) should be done behind a flag first, then with a deprecation window.

---

## 7. Summary

**What we import (don't reimplement):**

| Package | Provides |
|---------|----------|
| `ag-ui-protocol` (PyPI) | All 17 event types, `EventEncoder`, `RunAgentInput` |
| `@ag-ui/client` (npm) | `HttpAgent` SSE client |
| `@ag-ui/core` (npm) | TypeScript types |
| `fast-json-patch` (npm) | JSON Patch application |

**What we build:**

| Module | Purpose | ~LOC |
|--------|---------|------|
| `agui_adapter/base.py` | Adapter pattern + helpers | ~200 |
| `agui_adapter/fastapi.py` | FastAPI integration | ~40 |
| `src/lib/agui/stores.ts` | Svelte stores wrapping HttpAgent | ~250 |
| `src/lib/agui/components/` | 5 Svelte components | ~200 |

**Total new code: ~700 lines** (vs ~2000+ if reimplementing from scratch)

---

## References

- [ag-ui-protocol PyPI](https://pypi.org/project/ag-ui-protocol/)
- [AG-UI Python SDK Source](https://github.com/ag-ui-protocol/ag-ui/tree/main/python/ag_ui)
- [@ag-ui/client npm](https://www.npmjs.com/package/@ag-ui/client)
- [AG-UI TypeScript SDK Source](https://github.com/ag-ui-protocol/ag-ui/tree/main/typescript)
- [AG-UI Documentation](https://docs.ag-ui.com)
- PenguiFlow artifact + MCP output handling: `docs/RFC_MCP_BINARY_CONTENT_HANDLING.md`
- PenguiFlow ArtifactStore guide: `docs/tools/artifacts-guide.md`
- PenguiFlow Playground SSE contract (legacy/parallel-run): `docs/PLAYGROUND_BACKEND_CONTRACTS.md`
