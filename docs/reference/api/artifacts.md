# Artifacts

Artifact storage abstractions for persisting and referencing large or binary
tool outputs, with in-memory and no-op implementations and auto-discovery.

::: penguiflow.artifacts
    options:
      members:
        - ArtifactStore
        - ArtifactRef
        - ArtifactScope
        - ArtifactRetentionConfig
        - InMemoryArtifactStore
        - NoOpArtifactStore
        - discover_artifact_store
