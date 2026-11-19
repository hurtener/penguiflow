# StateStore Implementation Guide for Production

This guide explains how to implement a production-ready `StateStore` for PenguiFlow, enabling durable audit/replay, A2A router continuity, and distributed pause/resume flows (OAuth/HITL).

!!! note
    Canonical pages:
    - **[State store](statestore.md)**
    - **[OAuth & HITL](oauth-hitl.md)**

## Overview

In production deployments with multiple workers, you need a persistent `StateStore` to:

1. **Persist runtime events** for audit/replay
2. **Persist A2A remote bindings** for remote task tracking
3. **Reuse A2A specialist context** across router turns when `A2AAgentToolset` is used
4. **Save planner state** when pausing for OAuth
5. **Load planner state** when resuming after OAuth callback
6. **Store OAuth tokens** for future requests

## StateStore Protocol

Penguiflow's `StateStore` protocol (`penguiflow/state/protocol.py`, import path `penguiflow.state`) defines three required methods:

```python
class StateStore(Protocol):
    async def save_event(self, event: StoredEvent) -> None:
        """Persist a runtime event for replay/audit."""
        ...

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        """Load all events for a trace."""
        ...

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        """Save A2A remote binding."""
        ...
```

For pause/resume support (required for OAuth/HITL flows), add these **duck-typed** methods:

```python
async def save_planner_state(self, token: str, payload: dict) -> None:
    """Save pause record for distributed resume."""
    ...

async def load_planner_state(self, token: str) -> dict | None:
    """Load pause record for resume (return None if missing/expired)."""
    ...
```

For A2A router-to-specialist continuity, add these **duck-typed** methods. They are used by `A2AAgentToolset` when the store is available through `tool_context["state_store"]` or planner `_state_store`:

```python
async def find_binding(
    self,
    *,
    router_session_id: str,
    agent_url: str,
    remote_skill: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> RemoteBinding | None:
    """Return the newest non-terminal specialist binding for this router scope."""
    ...

async def list_bindings(self, *, router_session_id: str) -> Sequence[RemoteBinding]:
    """List bindings for debugging/inspection."""
    ...

async def mark_binding_terminal(self, *, trace_id: str, context_id: str | None, task_id: str) -> None:
    """Prevent an exact remote binding from being reused."""
    ...
```

`ReactPlanner` and A2A tooling check optional methods via `hasattr()` / `getattr()` at runtime. If these methods are missing, A2A calls still work but remote `contextId` reuse is disabled.

Normal continuity reuses the stored remote `context_id` and omits `task_id`. `task_id` is only reused when binding metadata marks an unfinished input/auth-required remote task with `awaiting_remote_input` or `awaiting_remote_auth`.

---

## PostgreSQL Implementation

### Database Schema

```sql
-- Events table (required)
--
-- NOTE: Core runtime may emit trace_id=None. Normalise that to '__global__'
-- so trace_id can be NOT NULL and participate in uniqueness constraints.
CREATE TABLE flow_events (
    id BIGSERIAL PRIMARY KEY,
    trace_id VARCHAR(128) NOT NULL,
    ts DOUBLE PRECISION NOT NULL,
    kind VARCHAR(64) NOT NULL,
    node_name VARCHAR(128),
    node_id VARCHAR(128),
    event_fp VARCHAR(64) NOT NULL,
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (trace_id, event_fp)
);

CREATE INDEX idx_events_trace_ts ON flow_events(trace_id, ts, id);

-- Planner pauses (for OAuth flows)
CREATE TABLE planner_pauses (
    token VARCHAR(128) PRIMARY KEY,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '1 hour')
);

-- OAuth tokens (for ToolNode auth)
CREATE TABLE oauth_tokens (
    user_id VARCHAR(64) NOT NULL,
    provider VARCHAR(64) NOT NULL,
    access_token TEXT NOT NULL,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, provider)
);

-- Remote bindings (for A2A server tasks and A2AAgentToolset continuity)
CREATE TABLE remote_bindings (
    id BIGSERIAL PRIMARY KEY,
    trace_id VARCHAR(128) NOT NULL,
    context_id VARCHAR(128),
    task_id VARCHAR(128) NOT NULL,
    agent_url TEXT NOT NULL,
    router_session_id VARCHAR(128),
    remote_skill VARCHAR(128),
    tenant_id VARCHAR(128),
    user_id VARCHAR(128),
    last_remote_task_id VARCHAR(128),
    is_terminal BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_remote_bindings_identity
ON remote_bindings (trace_id, context_id, task_id) NULLS NOT DISTINCT;

-- If your database does not support NULLS NOT DISTINCT, use a generated
-- context_id_key = COALESCE(context_id, '') column for the unique index instead.

CREATE INDEX idx_remote_bindings_active_conversation
ON remote_bindings (router_session_id, agent_url, remote_skill, tenant_id, user_id, updated_at DESC)
WHERE router_session_id IS NOT NULL
  AND remote_skill IS NOT NULL
  AND is_terminal = FALSE;

-- Cleanup expired pauses
CREATE INDEX idx_pauses_expires ON planner_pauses(expires_at);
```

### Python Implementation

```python
import hashlib
import json
from typing import Any, Sequence
from datetime import datetime, timezone

import asyncpg

from penguiflow.state import StoredEvent, RemoteBinding


class PostgresStateStore:
    """Production StateStore backed by PostgreSQL."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    # ─── Required Methods ────────────────────────────────────────────────────────

    async def save_event(self, event: StoredEvent) -> None:
        """Persist a runtime event."""
        trace_id = event.trace_id or "__global__"
        event_fp = hashlib.sha256(
            json.dumps(
                {
                    "trace_id": trace_id,
                    "ts": event.ts,
                    "kind": event.kind,
                    "node_name": event.node_name,
                    "node_id": event.node_id,
                    "payload": dict(event.payload),
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        await self._pool.execute(
            """
            INSERT INTO flow_events (trace_id, ts, kind, node_name, node_id, event_fp, payload)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (trace_id, event_fp) DO NOTHING
            """,
            trace_id,
            event.ts,
            event.kind,
            event.node_name,
            event.node_id,
            event_fp,
            json.dumps(dict(event.payload)) if event.payload else None,
        )

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        """Load all events for a trace."""
        trace_key = trace_id
        rows = await self._pool.fetch(
            """
            SELECT trace_id, ts, kind, node_name, node_id, payload
            FROM flow_events
            WHERE trace_id = $1
            ORDER BY ts ASC, id ASC
            """,
            trace_key,
        )
        return [
            StoredEvent(
                trace_id=row["trace_id"],
                ts=float(row["ts"]),
                kind=row["kind"],
                node_name=row["node_name"],
                node_id=row["node_id"],
                payload=json.loads(row["payload"]) if row["payload"] else {},
            )
            for row in rows
        ]

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        """Save A2A remote binding."""
        await self._pool.execute(
            """
            INSERT INTO remote_bindings (
                trace_id, context_id, task_id, agent_url,
                router_session_id, remote_skill, tenant_id, user_id,
                last_remote_task_id, is_terminal, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
            ON CONFLICT (trace_id, context_id, task_id) DO UPDATE
            SET agent_url = EXCLUDED.agent_url,
                router_session_id = EXCLUDED.router_session_id,
                remote_skill = EXCLUDED.remote_skill,
                tenant_id = EXCLUDED.tenant_id,
                user_id = EXCLUDED.user_id,
                last_remote_task_id = EXCLUDED.last_remote_task_id,
                is_terminal = EXCLUDED.is_terminal,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            """,
            binding.trace_id,
            binding.context_id,
            binding.task_id,
            binding.agent_url,
            binding.router_session_id,
            binding.remote_skill,
            binding.tenant_id,
            binding.user_id,
            binding.last_remote_task_id,
            binding.is_terminal,
            json.dumps(dict(binding.metadata or {})),
        )

    async def find_binding(
        self,
        *,
        router_session_id: str,
        agent_url: str,
        remote_skill: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> RemoteBinding | None:
        """Return the newest active A2A specialist binding for this router scope."""
        rows = await self._pool.fetch(
            """
            SELECT * FROM remote_bindings
            WHERE router_session_id = $1
              AND agent_url = $2
              AND remote_skill = $3
              AND is_terminal = FALSE
            ORDER BY updated_at DESC, id DESC
            """,
            router_session_id,
            agent_url,
            remote_skill,
        )
        for row in rows:
            if (tenant_id is not None or row["tenant_id"] is not None) and row["tenant_id"] != tenant_id:
                continue
            if (user_id is not None or row["user_id"] is not None) and row["user_id"] != user_id:
                continue
            return self._parse_remote_binding(row)
        return None

    async def list_bindings(self, *, router_session_id: str) -> list[RemoteBinding]:
        rows = await self._pool.fetch(
            "SELECT * FROM remote_bindings WHERE router_session_id = $1 ORDER BY updated_at DESC, id DESC",
            router_session_id,
        )
        return [self._parse_remote_binding(row) for row in rows]

    async def mark_binding_terminal(self, *, trace_id: str, context_id: str | None, task_id: str) -> None:
        await self._pool.execute(
            """
            UPDATE remote_bindings
            SET is_terminal = TRUE, updated_at = NOW()
            WHERE trace_id = $1
              AND context_id IS NOT DISTINCT FROM $2
              AND task_id = $3
            """,
            trace_id,
            context_id,
            task_id,
        )

    def _parse_remote_binding(self, row) -> RemoteBinding:
        return RemoteBinding(
            trace_id=row["trace_id"],
            context_id=row["context_id"],
            task_id=row["task_id"],
            agent_url=row["agent_url"],
            router_session_id=row["router_session_id"],
            remote_skill=row["remote_skill"],
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            last_remote_task_id=row["last_remote_task_id"],
            is_terminal=bool(row["is_terminal"]),
            metadata=json.loads(row["metadata"]) if isinstance(row["metadata"], str) else dict(row["metadata"] or {}),
        )

    # ─── Pause/Resume Support (duck-typed) ───────────────────────────────────────

    async def save_planner_state(self, token: str, payload: dict) -> None:
        """Save pause record for distributed resume.

        Called when planner pauses for OAuth or HITL approval.
        """
        await self._pool.execute(
            """
            INSERT INTO planner_pauses (token, payload)
            VALUES ($1, $2)
            ON CONFLICT (token) DO UPDATE SET payload = EXCLUDED.payload
            """,
            token,
            json.dumps(payload),
        )

    async def load_planner_state(self, token: str) -> dict | None:
        """Load pause record for resume.

        Called when resuming planner after OAuth callback.
        """
        row = await self._pool.fetchrow(
            """
            DELETE FROM planner_pauses
            WHERE token = $1 AND expires_at > NOW()
            RETURNING payload
            """,
            token,
        )
        if not row:
            return None
        return json.loads(row["payload"])

    # ─── Cleanup ─────────────────────────────────────────────────────────────────

    async def cleanup_expired_pauses(self) -> int:
        """Remove expired pause records. Run periodically."""
        result = await self._pool.execute(
            "DELETE FROM planner_pauses WHERE expires_at < NOW()"
        )
        return int(result.split()[-1])  # Returns "DELETE N"
```

### Usage with ToolNode

```python
import asyncpg
from penguiflow.tools import ToolNode, ExternalToolConfig, TransportType, AuthType
from penguiflow.tools import OAuthManager, OAuthProviderConfig
from penguiflow.planner import ReactPlanner
from penguiflow.registry import ModelRegistry

# Create database pool
pool = await asyncpg.create_pool("postgresql://user:pass@localhost/mydb")

# Create StateStore
state_store = PostgresStateStore(pool)

# Create TokenStore that uses the same database
class PostgresTokenStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def store(self, user_id: str, provider: str, token: str, expires_at: float | None) -> None:
        expires_dt = datetime.fromtimestamp(expires_at, tz=timezone.utc) if expires_at else None
        await self._pool.execute(
            """
            INSERT INTO oauth_tokens (user_id, provider, access_token, expires_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, provider) DO UPDATE
            SET access_token = EXCLUDED.access_token,
                expires_at = EXCLUDED.expires_at
            """,
            user_id, provider, token, expires_dt,
        )

    async def get(self, user_id: str, provider: str) -> str | None:
        row = await self._pool.fetchrow(
            """
            SELECT access_token FROM oauth_tokens
            WHERE user_id = $1 AND provider = $2
              AND (expires_at IS NULL OR expires_at > NOW())
            """,
            user_id, provider,
        )
        return row["access_token"] if row else None

    async def delete(self, user_id: str, provider: str) -> None:
        await self._pool.execute(
            "DELETE FROM oauth_tokens WHERE user_id = $1 AND provider = $2",
            user_id, provider,
        )

# Create OAuth manager with PostgreSQL token store
token_store = PostgresTokenStore(pool)
oauth_manager = OAuthManager(
    providers={
        "github": OAuthProviderConfig(
            name="github",
            display_name="GitHub",
            auth_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            client_id=os.environ["GITHUB_CLIENT_ID"],
            client_secret=os.environ["GITHUB_CLIENT_SECRET"],
            redirect_uri="https://myapp.com/oauth/callback",
            scopes=["repo", "user"],
        ),
    },
    token_store=token_store,
)

# Create ToolNode with OAuth
registry = ModelRegistry()
github = ToolNode(
    config=ExternalToolConfig(
        name="github",
        transport=TransportType.MCP,
        connection="npx -y @modelcontextprotocol/server-github",
        auth_type=AuthType.OAUTH2_USER,
    ),
    registry=registry,
    auth_manager=oauth_manager,
)
await github.connect()

# Create planner with StateStore
planner = ReactPlanner(
    catalog=github.get_tools(),
    llm="gpt-4",
    state_store=state_store,  # Enables distributed pause/resume and A2AAgentToolset continuity
)
```

---

## Redis Implementation

For high-throughput scenarios, use Redis for pause/resume state:

```python
import json
import redis.asyncio as redis


class RedisStateStore:
    """StateStore with Redis for pause/resume, PostgreSQL for events."""

    def __init__(self, redis_client: redis.Redis, pg_pool: asyncpg.Pool):
        self._redis = redis_client
        self._pool = pg_pool
        self._pause_ttl = 3600  # 1 hour

    # Events still go to PostgreSQL (for audit/replay)
    async def save_event(self, event: StoredEvent) -> None:
        # ... same as PostgresStateStore ...

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        # ... same as PostgresStateStore ...

    # Pause/resume uses Redis for speed
    async def save_planner_state(self, token: str, payload: dict) -> None:
        await self._redis.setex(
            f"planner:pause:{token}",
            self._pause_ttl,
            json.dumps(payload),
        )

    async def load_planner_state(self, token: str) -> dict | None:
        # One-time use: consume/delete the pause record when loading.
        data = await self._redis.getdel(f"planner:pause:{token}")
        if not data:
            return None
        return json.loads(data)


class RedisTokenStore:
    """TokenStore backed by Redis."""

    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client

    async def store(self, user_id: str, provider: str, token: str, expires_at: float | None) -> None:
        key = f"oauth:token:{user_id}:{provider}"
        if expires_at:
            ttl = int(expires_at - time.time())
            if ttl > 0:
                await self._redis.setex(key, ttl, token)
        else:
            await self._redis.set(key, token)

    async def get(self, user_id: str, provider: str) -> str | None:
        return await self._redis.get(f"oauth:token:{user_id}:{provider}")

    async def delete(self, user_id: str, provider: str) -> None:
        await self._redis.delete(f"oauth:token:{user_id}:{provider}")
```

---

## OAuth Callback Handler

When the user completes OAuth, your callback endpoint should:

1. Exchange the code for a token
2. Store the token
3. Resume the planner

```python
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

@app.post("/oauth/callback")
async def oauth_callback(request: Request):
    """Handle OAuth callback from provider."""
    body = await request.json()
    code = body.get("code")
    state = body.get("state")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code/state")

    try:
        # Exchange code for token and store it
        user_id, trace_id = await oauth_manager.handle_callback(code, state)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Resume the planner (if using distributed workers, publish to queue)
    # For simple deployments:
    # result = await planner.resume(resume_token, tool_context={"user_id": user_id})

    return {"ok": True, "user_id": user_id, "trace_id": trace_id}
```

---

## Distributed Worker Architecture

For production deployments with multiple workers:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Worker 1  │     │   Worker 2  │     │   Worker 3  │
│  (planner)  │     │  (planner)  │     │  (planner)  │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
                    ┌──────▼──────┐
                    │   Redis     │ ← Pause state (fast)
                    │   Cluster   │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  PostgreSQL │ ← Events, tokens (durable)
                    └─────────────┘
```

**Flow:**
1. Worker 1 receives request, starts planner
2. Planner pauses for OAuth, saves state to Redis
3. User completes OAuth in browser
4. Callback stores token in PostgreSQL, publishes resume event
5. Worker 2 picks up resume, loads state from Redis
6. Planner continues execution

---

## Best Practices

### Token Security

1. **Encrypt tokens at rest** - Use database-level encryption or application-level encryption for `access_token` column
2. **Use short TTLs** - Set `expires_at` based on provider's token lifetime
3. **Rotate refresh tokens** - If provider supports refresh tokens, implement rotation logic

### State Cleanup

1. **Expire old pauses** - Run `cleanup_expired_pauses()` periodically (cron job or scheduled task)
2. **Monitor pause table size** - Alert if pauses accumulate (indicates callback failures)
3. **Log pause/resume events** - Track OAuth flow completion rates

### High Availability

1. **PostgreSQL replication** - Use read replicas for `load_history()` calls
2. **Redis Cluster** - Use Redis Cluster for pause state distribution
3. **Connection pooling** - Always use connection pools (`asyncpg.Pool`, `redis.ConnectionPool`)

---

## See Also

- [Concurrency Configuration Guide](./concurrency-guide.md)
- [ToolNode Configuration Guide](./configuration-guide.md)
- [TOOLNODE_V2_PLAN.md](../proposals/TOOLNODE_V2_PLAN.md) - Full design specification
