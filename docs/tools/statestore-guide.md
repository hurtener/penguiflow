# StateStore Implementation Guide for Production

This guide explains how to implement a production-ready `StateStore` for ToolNode v2, enabling distributed pause/resume flows and OAuth token persistence.

## Overview

When using `ToolNode` with user-level OAuth (`AuthType.OAUTH2_USER`), the planner may pause execution to wait for user authentication. In production deployments with multiple workers, you need a persistent `StateStore` to:

1. **Save planner state** when pausing for OAuth
2. **Load planner state** when resuming after OAuth callback
3. **Store OAuth tokens** for future requests

## StateStore Protocol

Penguiflow's `StateStore` protocol (`penguiflow/state/protocol.py`, import path `penguiflow.state`) defines three required methods:

```python
class StateStore(Protocol):
    async def save_event(self, event: StoredEvent) -> None:
        """Persist a flow event for replay/audit."""
        ...

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        """Load all events for a trace."""
        ...

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        """Save A2A remote binding."""
        ...
```

For pause/resume support (required for OAuth flows), add these **duck-typed** methods:

```python
async def save_planner_state(self, token: str, payload: dict) -> None:
    """Save pause record for distributed resume."""
    ...

async def load_planner_state(self, token: str) -> dict:
    """Load pause record for resume."""
    ...
```

The `ReactPlanner` checks for these methods via `hasattr()` at runtime.

---

## PostgreSQL Implementation

### Database Schema

```sql
-- Events table (required)
CREATE TABLE flow_events (
    id SERIAL PRIMARY KEY,
    trace_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(32) NOT NULL,
    node_name VARCHAR(128),
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    INDEX idx_trace_id (trace_id)
);

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

-- Remote bindings (for A2A)
CREATE TABLE remote_bindings (
    node_name VARCHAR(128) PRIMARY KEY,
    remote_url TEXT NOT NULL,
    transport VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Cleanup expired pauses
CREATE INDEX idx_pauses_expires ON planner_pauses(expires_at);
```

### Python Implementation

```python
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
        """Persist a flow event."""
        await self._pool.execute(
            """
            INSERT INTO flow_events (trace_id, event_type, node_name, payload)
            VALUES ($1, $2, $3, $4)
            """,
            event.trace_id,
            event.event_type,
            event.node_name,
            json.dumps(event.payload) if event.payload else None,
        )

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        """Load all events for a trace."""
        rows = await self._pool.fetch(
            """
            SELECT trace_id, event_type, node_name, payload, created_at
            FROM flow_events
            WHERE trace_id = $1
            ORDER BY created_at ASC
            """,
            trace_id,
        )
        return [
            StoredEvent(
                trace_id=row["trace_id"],
                event_type=row["event_type"],
                node_name=row["node_name"],
                payload=json.loads(row["payload"]) if row["payload"] else None,
            )
            for row in rows
        ]

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        """Save A2A remote binding."""
        await self._pool.execute(
            """
            INSERT INTO remote_bindings (node_name, remote_url, transport)
            VALUES ($1, $2, $3)
            ON CONFLICT (node_name) DO UPDATE
            SET remote_url = EXCLUDED.remote_url,
                transport = EXCLUDED.transport
            """,
            binding.node_name,
            binding.remote_url,
            binding.transport,
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

    async def load_planner_state(self, token: str) -> dict:
        """Load pause record for resume.

        Called when resuming planner after OAuth callback.
        """
        row = await self._pool.fetchrow(
            """
            SELECT payload FROM planner_pauses
            WHERE token = $1 AND expires_at > NOW()
            """,
            token,
        )
        if not row:
            return {}
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
    state_store=state_store,  # Enables distributed pause/resume
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

    async def load_planner_state(self, token: str) -> dict:
        data = await self._redis.get(f"planner:pause:{token}")
        if not data:
            return {}
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
