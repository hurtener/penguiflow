from penguiflow.tools import POPULAR_MCP_SERVERS, get_preset


def main() -> None:
    print("Available MCP presets:")
    for name in POPULAR_MCP_SERVERS:
        cfg = POPULAR_MCP_SERVERS[name]
        print(f"- {name}: transport={cfg.transport.value}, auth={cfg.auth_type.value}, connection={cfg.connection}")

    print("\nFetching single preset:")
    github = get_preset("github")
    print(
        f"github preset -> transport={github.transport.value}, "
        f"auth={github.auth_type.value}, connection={github.connection}"
    )


if __name__ == "__main__":
    main()
