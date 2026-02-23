# `penguiflow tools`

## What it is / when to use it

`penguiflow tools` provides lightweight helpers for working with ToolNode presets:

- list built-in MCP presets
- print the connection/auth details for a preset
- optionally connect and discover the tool names exposed by that server

Use it to validate connectivity and discover tool names before wiring a preset into your agent.

## Non-goals / boundaries

- This is not a production configuration manager. For production, use explicit ToolNode configuration in code and/or configuration files (see **[Tools configuration](../tools/configuration.md)**).
- Tool discovery can be slow or fail depending on auth/network. Treat it as a dev-only diagnostic.

## Contract surface

### Commands

List presets:

```bash
penguiflow tools list
```

Connect to a preset (dry-run info):

```bash
penguiflow tools connect github
```

Discover tools for a preset:

```bash
penguiflow tools connect github --discover --show-tools
```

Useful options for `connect`:

- `--discover`: actually connect and fetch tool list
- `--show-tools/--no-show-tools`: print tool names (default: show)
- `--max-tools`: cap printed tool names
- `--env KEY=VALUE`: override preset environment variables (repeatable)

### Dependencies / extras

Tool discovery requires the planner/tooling extras:

```bash
pip install "penguiflow[planner]"
```

## Operational defaults (recommended)

- Start with `penguiflow tools list` to see available presets.
- Use `connect` without `--discover` first to confirm auth/connection details.
- Use `--env` overrides for one-off checks; prefer real config in code for production.

## Failure modes & recovery

- **“penguiflow[planner] is required”**: install `penguiflow[planner]`.
- **Invalid `--env` format**: use `KEY=VALUE` (the CLI rejects missing `=`).
- **Discovery fails**: verify auth env vars are present and valid for the preset; for OAuth presets you typically need HITL flows to acquire user tokens.

## Observability

- The CLI prints discovered tool counts and (optionally) tool names.
- For full tool execution observability, use:
  - planner trajectory logging, and
  - runtime `FlowEvent` capture.

## Security / multi-tenancy notes

- Don’t paste tokens into shell history; prefer env files and secret managers.
- Tool discovery surfaces tool names; do not assume every tool is safe to expose to end users.

## Troubleshooting checklist

- If discovery returns 0 tools, confirm the server is reachable and the auth mode matches the preset.
- If OAuth is involved, confirm you’ve enabled HITL in your agent and have a token persistence strategy.
