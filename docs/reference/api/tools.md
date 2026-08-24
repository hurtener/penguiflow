# Tools

The `ToolNode` for wrapping external MCP/UTCP tools, its configuration models,
OAuth support, connection and artifact-extraction presets, and the typed tool
error hierarchy.

::: penguiflow.tools
    options:
      members:
        - ToolNode
        - ExternalToolConfig
        - ArtifactExtractionConfig
        - ArtifactFieldConfig
        - AuthType
        - BinaryDetectionConfig
        - ResourceHandlingConfig
        - RetryPolicy
        - TransportType
        - UtcpMode
        - OAuthManager
        - OAuthProviderConfig
        - TokenStore
        - InMemoryTokenStore
        - POPULAR_MCP_SERVERS
        - get_preset
        - ARTIFACT_PRESETS
        - FILESYSTEM_ARTIFACT_PRESET
        - GITHUB_ARTIFACT_PRESET
        - GOOGLE_DRIVE_ARTIFACT_PRESET
        - TABLEAU_ARTIFACT_PRESET
        - get_artifact_preset
        - get_artifact_preset_info
        - get_artifact_preset_with_overrides
        - list_artifact_presets
        - merge_artifact_preset
        - adapt_exception
        - adapt_mcp_error
        - adapt_utcp_error
        - ErrorCategory
        - ToolNodeError
        - ToolAuthError
        - ToolClientError
        - ToolConnectionError
        - ToolRateLimitError
        - ToolServerError
        - ToolTimeoutError
