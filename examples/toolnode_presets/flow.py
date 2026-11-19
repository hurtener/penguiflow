"""ToolNode example: build tool sources from presets."""

import sys

from penguiflow.tools import POPULAR_MCP_SERVERS, get_preset


def main() -> None:
    """Run the example."""
    sys.stdout.write("Available MCP presets:\n")
    for name in POPULAR_MCP_SERVERS:
        cfg = POPULAR_MCP_SERVERS[name]
        sys.stdout.write(
            f"- {name}: transport={cfg.transport.value}, "
            f"auth={cfg.auth_type.value}, connection={cfg.connection}\n"
        )

    sys.stdout.write("\nFetching single preset:\n")
    github = get_preset("github")
    sys.stdout.write(
        f"github preset -> transport={github.transport.value}, "
        f"auth={github.auth_type.value}, connection={github.connection}\n"
    )


if __name__ == "__main__":
    main()
