---
name: databricks-apps-cookie-auth
description: Guide for authenticating with Databricks Apps using cookie-based auth when OAuth/PAT tokens don't work. Use when connecting to Databricks Apps with User Authorization enabled.
---

# Databricks Apps Cookie Authentication

## Problem Solved

Databricks Apps that require browser-based OAuth cannot be accessed with service principals or PATs when the app is configured for "User Authorization" only. The app always redirects to OAuth login regardless of Bearer tokens sent.

## Solution: Cookie-Based Authentication

After a user completes browser OAuth login, Databricks Apps set a session cookie `__Host-databricksapps` that can be captured and reused for API access.

### How It Works

1. User logs into the Databricks App via browser
2. Browser receives `__Host-databricksapps` cookie after successful OAuth
3. Cookie is captured from browser DevTools (Application > Cookies)
4. Cookie is passed as header in MCP client requests

### Cookie Format

```
__Host-databricksapps=<encrypted_session_data>|<timestamp>|<signature>
```

The timestamp indicates expiry - cookies typically expire after a session timeout.

### Code Example

```python
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from datetime import timedelta

async def connect_with_cookie(url: str, cookie_value: str):
    http_context = streamablehttp_client(
        url,
        headers={'Cookie': f'__Host-databricksapps={cookie_value}'}
    )
    read, write, _ = await http_context.__aenter__()

    session = ClientSession(read, write, read_timeout_seconds=timedelta(seconds=60))
    await session.__aenter__()
    await session.initialize()

    tools = await session.list_tools()
    return tools
```

### When to Use This

- Databricks Apps with "User Authorization" enabled but no service principal access
- Apps that redirect to OAuth regardless of Bearer tokens
- Testing/development scenarios where HITL OAuth isn't implemented yet

### Limitations

1. **Session Expiry**: Cookies expire, requiring periodic browser re-login
2. **User-Specific**: Cookie is tied to the user who logged in
3. **Security**: Cookie contains sensitive session data - handle securely

### Relevant Files

- `penguiflow/tools/node.py` - ToolNode implementation
- `penguiflow/tools/config.py` - AuthType enum
- `test_generation/reporting-agent/src/reporting_agent/external_tools.py` - Example usage

### Related Auth Types

| Auth Type | Use Case | Connection Phase | Tool Execution Phase |
|-----------|----------|------------------|---------------------|
| BEARER | Static tokens, PATs | Headers | Headers |
| API_KEY | API keys | Headers | Headers |
| OAUTH2_USER | HITL OAuth | Deferred | HITL flow |
| COOKIE (new) | Databricks Apps | Cookie header | Cookie header |
