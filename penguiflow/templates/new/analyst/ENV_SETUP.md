# Environment Setup

This document explains how to configure environment variables for your agent.

## Quick Start

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Set your LLM provider API key (see Provider Configuration below)

3. Run your agent:
   ```bash
   uv sync && uv run <package_name>
   # or use the playground:
   penguiflow dev .
   ```

## LLM Provider Configuration

PenguiFlow uses [LiteLLM](https://docs.litellm.ai/) for LLM integration. Set the appropriate API key based on your provider:

### OpenAI
```bash
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o
```

### Anthropic (Claude)
```bash
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=anthropic/claude-sonnet-4-20250514
```

### OpenRouter
```bash
OPENROUTER_API_KEY=sk-or-...
LLM_MODEL=openrouter/anthropic/claude-sonnet-4-20250514
```

### Azure OpenAI
```bash
AZURE_API_KEY=...
AZURE_API_BASE=https://your-resource.openai.azure.com/
AZURE_API_VERSION=2024-02-15-preview
LLM_MODEL=azure/your-deployment-name
```

### Google (Gemini)
```bash
GEMINI_API_KEY=...
LLM_MODEL=gemini/gemini-1.5-pro
```

### AWS Bedrock
```bash
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION_NAME=us-east-1
LLM_MODEL=bedrock/anthropic.claude-3-sonnet-20240229-v1:0
```

## Environment Variables Reference

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_MODEL` | Primary LLM model identifier | `stub-llm` |
| `MEMORY_BASE_URL` | Memory service endpoint | `http://localhost:8000` |

## Switching from ScriptedLLM to Real LLM

By default, this template uses `ScriptedLLM` for testing. To use a real LLM:

1. Open `src/<package>/planner.py`
2. Replace `ScriptedLLM` usage with:
   ```python
   planner = ReactPlanner(
       llm=config.llm_model,  # e.g., "gpt-4o"
       nodes=nodes,
       registry=registry,
       event_callback=event_callback,
   )
   ```
3. Set your API key in `.env`

## Security Notes

- **Never commit `.env` to version control** - add it to `.gitignore`
- Use `.env.example` as a template (safe to commit)
- For production, use secret management (AWS Secrets Manager, Vault, etc.)
- Rotate API keys regularly
