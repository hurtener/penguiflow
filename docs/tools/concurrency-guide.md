# Concurrency Configuration Guide

This guide explains Penguiflow's three-level concurrency model and how to tune it for production workloads.

## Overview

Penguiflow provides concurrency control at three levels:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Level 1: ReactPlanner (absolute_max_parallel)                              │
│  Controls: Total parallel tool calls across ALL tools                       │
│  Default: 50                                                                │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  Level 2: Planning Hints (planning_hints.max_parallel)                │  │
│  │  Controls: Per-query parallel limit                                   │  │
│  │  Default: None (uses absolute_max_parallel)                           │  │
│  │                                                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │  Level 3: ToolNode (config.max_concurrency)                     │  │  │
│  │  │  Controls: Concurrent calls to a specific external source       │  │  │
│  │  │  Default: 10                                                    │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Level 1: Planner-Level Concurrency

The `ReactPlanner` enforces an absolute maximum on parallel tool execution.

```python
from penguiflow.planner import ReactPlanner

planner = ReactPlanner(
    catalog=catalog,
    llm="gpt-4",
    absolute_max_parallel=50,  # Default: 50
)
```

**When to adjust:**
- **Increase** (up to 100) if your infrastructure can handle more concurrent connections and you have many independent tools
- **Decrease** (to 10-20) if running on resource-constrained environments or if LLM costs are a concern

**Impact:**
- Limits how many `call_tool()` operations can execute concurrently
- Affects overall planner throughput
- Does NOT affect LLM calls (only tool execution)

## Level 2: Per-Query Concurrency (Planning Hints)

Use `planning_hints` to limit parallelism for specific queries:

```python
result = await planner.run(
    "Process all customer data",
    planning_hints={
        "max_parallel": 5,  # Limit this query to 5 parallel tools
    },
)
```

**Use cases:**
- Sensitive operations that shouldn't overwhelm backends
- Rate-limited APIs where you want tighter control
- Debugging/troubleshooting (set to 1 for sequential execution)

**Additional planning hints:**

```python
planning_hints = {
    "max_parallel": 5,                    # Max concurrent tool calls
    "preferred_order": ["auth", "fetch"], # Hint execution order
    "parallel_groups": [["a", "b"]],      # Tools that can run together
    "disallow_nodes": ["dangerous_tool"], # Block specific tools
    "preferred_nodes": ["safe_tool"],     # Prefer certain tools
    "budget": {"max_hops": 10},           # Limit planning iterations
}
```

## Level 3: ToolNode-Level Concurrency

Each `ToolNode` has its own concurrency limit protecting the external service:

```python
from penguiflow.tools import ToolNode, ExternalToolConfig, TransportType

github = ToolNode(
    config=ExternalToolConfig(
        name="github",
        transport=TransportType.MCP,
        connection="npx -y @modelcontextprotocol/server-github",
        max_concurrency=10,  # Default: 10
    ),
    registry=registry,
)
```

**When to adjust:**
- **Decrease** (to 3-5) for rate-limited APIs (GitHub, Twitter, etc.)
- **Increase** (to 20-50) for high-throughput internal services
- **Set to 1** for strictly sequential APIs (some payment processors)

**Impact:**
- Semaphore-based limiting within the ToolNode
- Protects external APIs from overwhelming
- Independent of planner concurrency

## Interaction Between Levels

```
ReactPlanner (absolute_max_parallel=50)
    │
    ├─ github.create_issue ──────┐
    ├─ github.list_repos ────────┼── ToolNode(max_concurrency=10)
    ├─ github.get_user ──────────┤       └── FastMCP Client
    ├─ github.search_code ───────┘
    │
    ├─ stripe.create_charge ─────┐
    ├─ stripe.list_customers ────┼── ToolNode(max_concurrency=5)
    ├─ stripe.refund ────────────┘       └── UTCP Client
    │
    └─ local.summarize ──────────── Native tool (no limit)
```

**Example scenario:**
1. Planner issues 50 parallel tool calls
2. 30 go to GitHub, 15 to Stripe, 5 to native tools
3. GitHub ToolNode's semaphore allows 10 concurrent, queues 20
4. Stripe ToolNode's semaphore allows 5 concurrent, queues 10
5. Native tools execute immediately (no ToolNode limiting)

## Configuration Patterns

### High-Throughput Processing

For batch processing with reliable backends:

```python
planner = ReactPlanner(
    catalog=catalog,
    llm="gpt-4",
    absolute_max_parallel=100,
)

data_api = ToolNode(
    config=ExternalToolConfig(
        name="data_api",
        transport=TransportType.UTCP,
        connection="https://internal-api.company.com/.well-known/utcp.json",
        max_concurrency=50,  # High concurrency for internal service
        timeout_s=60,        # Longer timeout for batch operations
    ),
    registry=registry,
)
```

### Rate-Limited External APIs

For APIs with strict rate limits:

```python
twitter = ToolNode(
    config=ExternalToolConfig(
        name="twitter",
        transport=TransportType.UTCP,
        connection="https://api.twitter.com/.well-known/utcp.json",
        max_concurrency=3,   # Very limited (Twitter rate limits)
        retry_policy=RetryPolicy(
            max_attempts=5,
            wait_exponential_min_s=1.0,
            wait_exponential_max_s=60.0,
            retry_on_status=[429, 500, 502, 503, 504],
        ),
    ),
    registry=registry,
)
```

### Sequential-Only Processing

For services that don't handle concurrent requests well:

```python
legacy_api = ToolNode(
    config=ExternalToolConfig(
        name="legacy",
        transport=TransportType.HTTP,
        connection="https://old-system.internal/api",
        max_concurrency=1,   # Strictly sequential
        timeout_s=120,       # Legacy systems may be slow
    ),
    registry=registry,
)
```

### Mixed Workload

For multi-tool agents with varying requirements:

```python
# Fast, reliable internal API
internal = ToolNode(
    config=ExternalToolConfig(
        name="internal",
        transport=TransportType.UTCP,
        connection="https://api.internal/.well-known/utcp.json",
        max_concurrency=30,
    ),
    registry=registry,
)

# Rate-limited external API
github = ToolNode(
    config=ExternalToolConfig(
        name="github",
        transport=TransportType.MCP,
        connection="npx -y @modelcontextprotocol/server-github",
        max_concurrency=10,
    ),
    registry=registry,
)

# Expensive external API (costs per call)
openai_tools = ToolNode(
    config=ExternalToolConfig(
        name="openai",
        transport=TransportType.HTTP,
        connection="https://api.openai.com/v1",
        max_concurrency=5,  # Limit spend rate
    ),
    registry=registry,
)

# Planner respects all limits
planner = ReactPlanner(
    catalog=[*internal.get_tools(), *github.get_tools(), *openai_tools.get_tools()],
    llm="gpt-4",
    absolute_max_parallel=50,
)
```

## Retry Configuration

Concurrency interacts with retry behavior. Configure retries to respect rate limits:

```python
from penguiflow.tools import RetryPolicy

# Aggressive retry for transient failures
fast_retry = RetryPolicy(
    max_attempts=3,
    wait_exponential_min_s=0.1,
    wait_exponential_max_s=2.0,
    retry_on_status=[500, 502, 503, 504],  # Server errors only
)

# Conservative retry for rate limits
rate_limit_retry = RetryPolicy(
    max_attempts=5,
    wait_exponential_min_s=1.0,
    wait_exponential_max_s=60.0,
    retry_on_status=[429, 500, 502, 503, 504],  # Include rate limit
)

# No retry for critical operations
no_retry = RetryPolicy(
    max_attempts=1,
    retry_on_status=[],
)
```

## Monitoring Concurrency

### Metrics to Watch

1. **Queue depth** - How many calls are waiting for semaphore
2. **Wait time** - How long calls wait before execution
3. **Timeout rate** - Calls that exceed `timeout_s`
4. **Retry rate** - Calls that need retry (indicates upstream issues)

### Example Logging

```python
import logging
from penguiflow import log_flow_events

logger = logging.getLogger("penguiflow.concurrency")

def track_latency(event_type: str, latency_ms: float, event: Any) -> None:
    if latency_ms > 1000:  # > 1 second
        logger.warning(
            f"Slow tool execution: {event.node_name} took {latency_ms:.0f}ms"
        )

middleware = log_flow_events(logger, latency_callback=track_latency)
```

## Backpressure Handling

When downstream services are overwhelmed:

1. **ToolNode semaphore** - Queues excess calls locally
2. **Retry policy** - Automatically retries 429/5xx errors with backoff
3. **Timeout** - Fails calls that exceed `timeout_s`
4. **Planner budget** - Stops planning after `max_hops` iterations

**No additional backpressure needed** - These mechanisms are sufficient for production use.

## Best Practices

1. **Start conservative** - Begin with lower limits and increase based on monitoring
2. **Match rate limits** - Set `max_concurrency` to match API rate limits
3. **Use planning hints** - Limit specific queries rather than global settings
4. **Monitor and adjust** - Watch latency and timeout metrics
5. **Test under load** - Verify behavior with realistic concurrency

## Troubleshooting

### Symptoms and Solutions

| Symptom | Likely Cause | Solution |
|---------|--------------|----------|
| Slow response times | max_concurrency too low | Increase ToolNode limit |
| 429 errors | max_concurrency too high | Decrease ToolNode limit |
| Timeouts | Backend overloaded | Decrease max_concurrency |
| Memory growth | Too many queued calls | Decrease absolute_max_parallel |

### Debug Mode

Run with sequential execution to isolate issues:

```python
result = await planner.run(
    "Debug this query",
    planning_hints={"max_parallel": 1},  # Sequential execution
)
```

---

## See Also

- [StateStore Implementation Guide](./statestore-guide.md)
- [ToolNode Configuration Guide](./configuration-guide.md)
- [TOOLNODE_V2_PLAN.md](../proposals/TOOLNODE_V2_PLAN.md) - Full design specification
