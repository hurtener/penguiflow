"""Pydantic models of the A2A protocol wire format."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class A2ABaseModel(BaseModel):
    """Base of the A2A models: camelCase aliases, populated by name too."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
        use_enum_values=True,
    )


class TaskState(str, Enum):
    """Lifecycle state of a task."""

    UNSPECIFIED = "unspecified"
    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INPUT_REQUIRED = "input-required"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth-required"


class Role(str, Enum):
    """Author of a message."""

    USER = "user"
    AGENT = "agent"


class FilePart(A2ABaseModel):
    """A file, inline as bytes or referenced by URI."""

    file_with_uri: str | None = None
    file_with_bytes: str | None = None
    media_type: str | None = None
    name: str | None = None

    @model_validator(mode="after")
    def _oneof_file(self) -> FilePart:
        provided = [self.file_with_uri, self.file_with_bytes]
        if sum(value is not None for value in provided) != 1:
            msg = (
                "FilePart must set exactly one of file_with_uri or "
                "file_with_bytes"
            )
            raise ValueError(msg)
        return self


class DataPart(A2ABaseModel):
    """Structured JSON data."""

    data: dict[str, Any]


class Part(A2ABaseModel):
    """One part of a message or artifact: text, file or data."""

    text: str | None = None
    file: FilePart | None = None
    data: DataPart | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _oneof_part(self) -> Part:
        provided = [self.text, self.file, self.data]
        if sum(value is not None for value in provided) != 1:
            msg = "Part must set exactly one of text, file, or data"
            raise ValueError(msg)
        return self


class Message(A2ABaseModel):
    """A message between the client and the agent."""

    message_id: str
    context_id: str | None = None
    task_id: str | None = None
    role: Role
    parts: list[Part] = Field(min_length=1)
    metadata: dict[str, Any] | None = None
    extensions: list[str] | None = None
    reference_task_ids: list[str] | None = None


class TaskStatus(A2ABaseModel):
    """The state of a task at a point in time."""

    state: TaskState
    message: Message | None = None
    timestamp: str | None = None


class Artifact(A2ABaseModel):
    """An output a task produced."""

    artifact_id: str
    name: str | None = None
    description: str | None = None
    parts: list[Part] = Field(min_length=1)
    metadata: dict[str, Any] | None = None
    extensions: list[str] | None = None


class Task(A2ABaseModel):
    """A unit of work the agent performs for a context."""

    id: str
    context_id: str
    status: TaskStatus
    artifacts: list[Artifact] | None = None
    history: list[Message] | None = None
    metadata: dict[str, Any] | None = None


class TaskStatusUpdateEvent(A2ABaseModel):
    """A change of a task's status, streamed to subscribers."""

    task_id: str
    context_id: str
    status: TaskStatus
    final: bool
    metadata: dict[str, Any] | None = None


class TaskArtifactUpdateEvent(A2ABaseModel):
    """A new or updated artifact, streamed to subscribers."""

    task_id: str
    context_id: str
    artifact: Artifact
    append: bool | None = None
    last_chunk: bool | None = None
    metadata: dict[str, Any] | None = None


class StreamResponse(A2ABaseModel):
    """One streamed event: a task, message, status or artifact update."""

    task: Task | None = None
    message: Message | None = None
    status_update: TaskStatusUpdateEvent | None = None
    artifact_update: TaskArtifactUpdateEvent | None = None

    @model_validator(mode="after")
    def _oneof_stream(self) -> StreamResponse:
        provided = [
            self.task,
            self.message,
            self.status_update,
            self.artifact_update,
        ]
        if sum(value is not None for value in provided) != 1:
            msg = (
                "StreamResponse must set exactly one of task, message, "
                "status_update, or artifact_update"
            )
            raise ValueError(msg)
        return self


class AuthenticationInfo(A2ABaseModel):
    """Authentication a push notification receiver requires."""

    schemes: list[str] = Field(min_length=1)
    credentials: str | None = None


class PushNotificationConfig(A2ABaseModel):
    """Where and how to deliver a task's push notifications."""

    id: str | None = None
    url: str
    token: str | None = None
    authentication: AuthenticationInfo | None = None


class SendMessageConfiguration(A2ABaseModel):
    """Options of a ``SendMessage`` request."""

    accepted_output_modes: list[str] | None = None
    push_notification_config: PushNotificationConfig | None = None
    history_length: int | None = None
    blocking: bool | None = None


class SendMessageRequest(A2ABaseModel):
    """The ``SendMessage`` request."""

    message: Message
    configuration: SendMessageConfiguration | None = None
    metadata: dict[str, Any] | None = None


class SendMessageResponse(A2ABaseModel):
    """The ``SendMessage`` response: the task or the agent's reply."""

    task: Task | None = None
    message: Message | None = None

    @model_validator(mode="after")
    def _oneof_send_response(self) -> SendMessageResponse:
        provided = [self.task, self.message]
        if sum(value is not None for value in provided) != 1:
            msg = "SendMessageResponse must set exactly one of task or message"
            raise ValueError(msg)
        return self


class ListTasksResponse(A2ABaseModel):
    """A page of tasks."""

    tasks: list[Task]
    next_page_token: str
    page_size: int
    total_size: int


class TaskPushNotificationConfig(A2ABaseModel):
    """A named push notification config of a task."""

    name: str | None = None
    push_notification_config: PushNotificationConfig


class ListTaskPushNotificationConfigResponse(A2ABaseModel):
    """A page of push notification configs."""

    configs: list[TaskPushNotificationConfig]
    next_page_token: str


class AgentExtension(A2ABaseModel):
    """A protocol extension the agent supports."""

    uri: str
    description: str | None = None
    required: bool | None = None
    params: dict[str, Any] | None = None


class AgentCapabilities(A2ABaseModel):
    """Optional protocol features the agent supports."""

    streaming: bool | None = None
    push_notifications: bool | None = None
    extensions: list[AgentExtension] | None = None
    state_transition_history: bool | None = None
    extended_agent_card: bool | None = None


class AgentInterface(A2ABaseModel):
    """A transport endpoint serving the agent."""

    url: str
    protocol_binding: str
    tenant: str | None = None


class AgentProvider(A2ABaseModel):
    """The organization providing the agent."""

    url: str
    organization: str


class SecurityScheme(A2ABaseModel):
    """One security scheme the agent accepts."""

    api_key_security_scheme: dict[str, Any] | None = None
    http_auth_security_scheme: dict[str, Any] | None = None
    oauth2_security_scheme: dict[str, Any] | None = None
    open_id_connect_security_scheme: dict[str, Any] | None = None
    mtls_security_scheme: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _oneof_scheme(self) -> SecurityScheme:
        provided = [
            self.api_key_security_scheme,
            self.http_auth_security_scheme,
            self.oauth2_security_scheme,
            self.open_id_connect_security_scheme,
            self.mtls_security_scheme,
        ]
        if sum(value is not None for value in provided) != 1:
            msg = "SecurityScheme must set exactly one scheme variant"
            raise ValueError(msg)
        return self


class Security(A2ABaseModel):
    """The security schemes a request must satisfy."""

    schemes: dict[str, list[str]]


class AgentSkill(A2ABaseModel):
    """A skill the agent offers."""

    id: str
    name: str
    description: str
    tags: list[str] = Field(min_length=1)
    examples: list[str] | None = None
    input_modes: list[str] | None = None
    output_modes: list[str] | None = None
    security: list[Security] | None = None


class AgentCardSignature(A2ABaseModel):
    """A JWS signature of the agent card."""

    protected: str
    signature: str
    header: dict[str, Any] | None = None


class AgentCard(A2ABaseModel):
    """The self-description an A2A agent publishes."""

    protocol_versions: list[str] = Field(min_length=1)
    name: str
    description: str
    supported_interfaces: list[AgentInterface] = Field(min_length=1)
    provider: AgentProvider | None = None
    version: str
    documentation_url: str | None = None
    capabilities: AgentCapabilities
    security_schemes: dict[str, SecurityScheme] | None = None
    security: list[Security] | None = None
    default_input_modes: list[str] = Field(min_length=1)
    default_output_modes: list[str] = Field(min_length=1)
    skills: list[AgentSkill] = Field(min_length=1)
    signatures: list[AgentCardSignature] | None = None
    icon_url: str | None = None
