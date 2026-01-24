from fastapi.testclient import TestClient

from penguiflow import Message as FlowMessage
from penguiflow import Node, NodePolicy, create
from penguiflow_a2a import A2AConfig, A2AService, create_a2a_http_app
from penguiflow_a2a.models import AgentCapabilities, AgentCard, AgentInterface, AgentSkill


def _agent_card(*, extended: bool) -> AgentCard:
    return AgentCard(
        protocol_versions=["0.3"],
        name="Test Agent",
        description="Test",
        supported_interfaces=[AgentInterface(url="https://example.com/a2a", protocol_binding="HTTP+JSON")],
        version="1.0.0",
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extended_agent_card=extended,
            state_transition_history=False,
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[AgentSkill(id="echo", name="Echo", description="Echo", tags=["test"])],
    )


def _build_client(
    *,
    extended: bool,
    extended_card: AgentCard | None = None,
    auth_cb=None,
) -> TestClient:
    async def echo(message: FlowMessage, _ctx) -> str:
        return f"echo:{message.payload}"

    node = Node(echo, name="echo", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(
        flow,
        agent_card=_agent_card(extended=extended),
        config=A2AConfig(),
        extended_agent_card=extended_card,
        extended_agent_card_auth=auth_cb,
    )
    app = create_a2a_http_app(service, include_docs=False)
    return TestClient(app, raise_server_exceptions=False)


def _extended_card() -> AgentCard:
    return AgentCard(
        protocol_versions=["0.3"],
        name="Extended Agent",
        description="Extended",
        supported_interfaces=[AgentInterface(url="https://example.com/a2a", protocol_binding="HTTP+JSON")],
        version="1.0.1",
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extended_agent_card=True,
            state_transition_history=False,
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[AgentSkill(id="echo", name="Echo", description="Echo", tags=["test"])],
    )


def test_extended_agent_card_not_supported() -> None:
    with _build_client(extended=False) as client:
        response = client.get("/extendedAgentCard", headers={"A2A-Version": "0.3"})
        assert response.status_code == 400
        assert response.json()["type"].endswith("unsupported-operation")


def test_extended_agent_card_not_configured() -> None:
    with _build_client(extended=True) as client:
        response = client.get("/extendedAgentCard", headers={"A2A-Version": "0.3"})
        assert response.status_code == 400
        assert response.json()["type"].endswith("extended-agent-card-not-configured")


def test_extended_agent_card_requires_auth() -> None:
    def auth(headers) -> bool:
        return headers.get("authorization") == "Bearer secret"

    with _build_client(extended=True, extended_card=_extended_card(), auth_cb=auth) as client:
        response = client.get("/extendedAgentCard", headers={"A2A-Version": "0.3"})
        assert response.status_code == 401

        response = client.get(
            "/extendedAgentCard",
            headers={"A2A-Version": "0.3", "Authorization": "Bearer secret"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["name"] == "Extended Agent"
