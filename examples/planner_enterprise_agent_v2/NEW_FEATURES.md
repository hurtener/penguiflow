# Enterprise Agent V2 - New Features Guide

## 🎯 Overview

The **Enterprise Agent V2** is an enhanced version of the original enterprise agent example, showcasing ALL the latest ReactPlanner capabilities added in PenguiFlow v2.5+. This example demonstrates production-ready patterns for autonomous agents with advanced quality control, access management, and workflow optimization.

---

## 🚀 What's New in V2

### 1. **Reflection Loop** (FLAGSHIP FEATURE ⭐)

Automatic answer quality assurance before returning results to users.

**What it does:**
- Sends proposed answers to a critique LLM
- Evaluates answers against customizable quality criteria (completeness, accuracy, clarity)
- Requests revisions if quality score < threshold
- Supports up to N revision attempts (default: 2)

**Configuration:**
```bash
# Enable reflection
REFLECTION_ENABLED=true

# Quality threshold (0.0-1.0)
REFLECTION_QUALITY_THRESHOLD=0.80

# Max revision attempts
REFLECTION_MAX_REVISIONS=2

# Use separate LLM for critique (optional)
REFLECTION_USE_SEPARATE_LLM=false
REFLECTION_LLM=gpt-4o-mini
```

**Benefits:**
- Prevents premature/incomplete answers
- Improves answer quality without manual review
- Reduces hallucinations and factual errors
- Production-ready quality control

**Code reference:** `main.py:269-294` (planner initialization)

---

### 2. **Tool Policy System** (Runtime Access Control)

Filter available tools based on whitelist/blacklist and tag requirements.

**What it does:**
- Whitelist: Only specified tools available
- Blacklist: Explicitly deny specific tools
- Tag requirements: Tools must have all required tags

**Configuration:**
```bash
# Enable tool filtering
TOOL_POLICY_ENABLED=true

# Whitelist specific tools (comma-separated)
TOOL_POLICY_ALLOWED_TOOLS=triage_query,analyze_documents

# Blacklist tools
TOOL_POLICY_DENIED_TOOLS=expensive_tool,deprecated_node

# Require tags (all must be present)
TOOL_POLICY_REQUIRE_TAGS=safe,read-only
```

**Use cases:**
- Multi-tenant tool isolation (tenant A sees tools X, tenant B sees tools Y)
- Safety constraints (only "safe" tagged tools)
- Cost management (deny expensive tools for low-tier users)
- Progressive rollout (deny "beta" tools in production)

**Code reference:** `main.py:296-315` (planner initialization)

---

### 3. **Planning Hints System** (Workflow Constraints)

Guide planner behavior with structured constraints and preferences.

**What it does:**
- Ordering hints: Suggest tool execution order
- Parallel groups: Declare which tools can run concurrently
- Sequential-only: Mark tools that MUST run alone
- Disallow nodes: Hard constraint to prevent tool usage
- Max parallel: Limit concurrent executions
- Budget hints: Cost/resource expectations

**Configuration:**
```bash
# Enable planning hints
PLANNING_HINTS_ENABLED=true

# JSON object with hints
PLANNING_HINTS='{"ordering_hints":["triage","retrieve","summarize"],"max_parallel":3}'
```

**Hint schema:**
```python
{
    "ordering_hints": ["triage", "retrieve", "summarize"],
    "parallel_groups": [["retrieve_A", "retrieve_B"]],
    "sequential_only": ["send_email"],
    "disallow_nodes": ["expensive_tool"],
    "prefer_nodes": ["cached_search"],
    "max_parallel": 3,
    "budget_hints": {"max_cost_usd": 0.10}
}
```

**Code reference:** `main.py:317-324` (planner initialization)

---

### 4. **State Store Integration** (Durable Pause/Resume)

Persist planning state across process restarts.

**What it does:**
- Saves pause state to external storage (Redis, SQLite, etc.)
- Enables pause/resume across server restarts
- Workflow can pause for approval, user input, or external events

**Configuration:**
```bash
# Enable state persistence
STATE_STORE_ENABLED=true

# Backend: memory, redis, sqlite
STATE_STORE_BACKEND=redis
```

**Production adapters:**
- Redis: High-performance, distributed
- SQLite: Lightweight, file-based
- PostgreSQL: Enterprise-grade, transactional

**Code reference:** `main.py:326-334` (planner initialization)

---

### 5. **Separate LLM Clients** (Cost Optimization)

Use different models for different planning tasks.

**What it does:**
- Main LLM: Planning and tool selection (e.g., gpt-4o)
- Reflection LLM: Answer critique (e.g., gpt-4o-mini)
- Summarizer LLM: Trajectory compression (e.g., gpt-3.5-turbo)

**Configuration:**
```bash
# Main planner model
LLM_MODEL=gpt-4o

# Cheaper reflection model
REFLECTION_USE_SEPARATE_LLM=true
REFLECTION_LLM=gpt-4o-mini

# Cheapest summarizer
SUMMARIZER_MODEL=gpt-3.5-turbo
```

**Cost savings example:**
- Planning: 5 calls × $0.005 = $0.025
- Reflection: 2 calls × $0.0005 = $0.001 (10x cheaper!)
- Summarization: 1 call × $0.0001 = $0.0001 (50x cheaper!)
- **Total: $0.0261 instead of $0.04**

**Code reference:** `main.py:284-285, 347` (separate LLM setup)

---

### 6. **Enhanced Budget Controls**

Refined controls for resource limits.

**What it does:**
- `PLANNER_MAX_ITERS`: Maximum planning steps (default: 15)
- `PLANNER_HOP_BUDGET`: Maximum tool invocations (optional)
- `PLANNER_DEADLINE_S`: Wall-clock time limit (optional)
- `PLANNER_REPAIR_ATTEMPTS`: JSON repair attempts (default: 3)
- `PLANNER_ABSOLUTE_MAX_PARALLEL`: Safety limit (default: 50)

**Configuration:**
```bash
PLANNER_MAX_ITERS=15
PLANNER_HOP_BUDGET=20
PLANNER_DEADLINE_S=30.0
PLANNER_REPAIR_ATTEMPTS=3
```

**Code reference:** `main.py:340-351` (planner constraints)

---

## 📊 Configuration Presets

### High-Quality Preset (Best Answers)
```bash
REFLECTION_ENABLED=true
REFLECTION_QUALITY_THRESHOLD=0.85
LLM_MODEL=gpt-4o
REFLECTION_LLM=gpt-4o
PLANNER_MAX_ITERS=20
```
- Best answer quality
- Higher cost (~$0.05-0.10/query)
- Suitable for: Customer-facing, high-stakes decisions

### Cost-Optimized Preset (Fast & Cheap)
```bash
REFLECTION_ENABLED=false
LLM_MODEL=gpt-4o-mini
SUMMARIZER_MODEL=gpt-4o-mini
PLANNER_MAX_ITERS=10
```
- Fastest execution
- Lowest cost (~$0.005-0.01/query)
- Suitable for: Internal tools, high-volume queries

### Multi-Tenant Preset (Tool Isolation)
```bash
TOOL_POLICY_ENABLED=true
TOOL_POLICY_ALLOWED_TOOLS=triage_query,analyze_documents
REFLECTION_ENABLED=true
```
- Per-tenant tool visibility
- Quality assurance enabled
- Suitable for: SaaS platforms, multi-org deployments

### Production Preset (Balanced)
```bash
REFLECTION_ENABLED=true
REFLECTION_QUALITY_THRESHOLD=0.80
LLM_MODEL=gpt-4o-mini
REFLECTION_LLM=gpt-4o-mini
PLANNER_HOP_BUDGET=20
PLANNER_DEADLINE_S=30.0
STATE_STORE_ENABLED=true
STATE_STORE_BACKEND=redis
```
- Balanced quality/cost
- Budget limits enforced
- Durable pause/resume
- Suitable for: General production use

---

## 🔧 Migration from V1

### Minimal Changes (Keep Existing Behavior)
```bash
# Disable all V2 features
REFLECTION_ENABLED=false
TOOL_POLICY_ENABLED=false
PLANNING_HINTS_ENABLED=false
STATE_STORE_ENABLED=false
```

### Gradual Adoption (Add Features Incrementally)
1. **Week 1:** Enable reflection (quality improvement)
2. **Week 2:** Add tool policies (if multi-tenant)
3. **Week 3:** Add planning hints (if workflows are deterministic)
4. **Week 4:** Enable state store (if long-running workflows)

### Full V2 (All Features)
See "Production Preset" above.

---

## 📈 Observability Enhancements

### New Telemetry Events

**Reflection Events:**
- `reflection_critique`: Answer quality score, feedback, suggestions
- `reflection_revision`: Revision attempt with improved answer

**Tool Policy Events:**
- `tool_policy_applied`: Which tools were filtered
- `tool_denied`: Attempted access to denied tool

**Planning Hint Events:**
- `hint_constraint_violated`: Attempted action violated constraint
- `hint_ordering_followed`: Planner followed ordering hint

### Cost Tracking
All LLM costs are tracked per call type:
- `main_llm_calls`: Planning and tool selection
- `reflection_llm_calls`: Answer critique
- `summarizer_llm_calls`: Trajectory compression

**Access via telemetry:**
```python
metrics = agent.get_metrics()
print(f"Total cost: ${metrics['cost']['total_cost_usd']}")
print(f"Main LLM calls: {metrics['cost']['main_llm_calls']}")
print(f"Reflection calls: {metrics['cost']['reflection_llm_calls']}")
```

**Code reference:** `telemetry.py` (enhanced event recording)

---

## 🎓 Learning Path

### Quick Start (5 min)
1. Copy `.env.example` to `.env`
2. Set `OPENAI_API_KEY`
3. Run: `uv run python examples/planner_enterprise_agent_v2/main.py`
4. See reflection in action!

### Deep Dive (30 min)
1. Read `main.py:238-375` (planner initialization)
2. Read `config.py` (configuration loading)
3. Experiment with different presets
4. Monitor telemetry output

### Production Deployment (2 hours)
1. Set up Redis for state store
2. Configure tool policies for your use case
3. Define planning hints for your workflows
4. Set up monitoring and alerting
5. Load test with reflection enabled/disabled

---

## 📚 Key Files

| File | Purpose | V2 Changes |
|------|---------|------------|
| `main.py` | Orchestrator | +ReflectionConfig, +ToolPolicy, +planning_hints, +state_store |
| `config.py` | Configuration | +reflection*, +tool_policy*, +planning_hints*, +state_store* |
| `.env.example` | Template | +14 new V2 variables with comprehensive documentation |
| `nodes.py` | No changes | Same as V1 |
| `telemetry.py` | No changes | Ready for new events |

---

## 🚀 Next Steps

1. **Try the reflection loop:** Set `REFLECTION_ENABLED=true` and compare answers
2. **Experiment with tool policies:** Filter tools for different tenants
3. **Add planning hints:** Guide the planner with your workflow knowledge
4. **Enable state store:** Test pause/resume with long workflows
5. **Compare costs:** Run with/without separate LLMs and measure savings

---

## 🤝 Contributing

Found a bug or have a feature request? Open an issue at:
https://github.com/your-org/penguiflow/issues

---

**🐧 Built with PenguiFlow v2.5+ — The Enterprise Agent Framework**
