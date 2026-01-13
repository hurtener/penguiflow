"""Output mode strategies for the LLM layer.

Provides three output modes for structured responses:
- NATIVE: Provider-native schema-guided output
- TOOLS: Tool/function calling
- PROMPTED: Schema in prompt + parse/retry
"""

from __future__ import annotations

from .native import NativeOutputStrategy
from .prompted import PromptedOutputStrategy
from .tool import ToolsOutputStrategy

__all__ = [
    "NativeOutputStrategy",
    "PromptedOutputStrategy",
    "ToolsOutputStrategy",
]
