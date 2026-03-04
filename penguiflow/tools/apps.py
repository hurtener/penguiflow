"""MCP Apps support for ToolNode.

This module implements the MCP Apps extension (io.modelcontextprotocol/ui),
enabling tools to return interactive UIs rendered in sandboxed iframes.

Key concepts:
- App-enabled tools declare a ui:// resource URI in their metadata
- After executing the tool, the host fetches the UI resource HTML
- The HTML is rendered in a sandboxed iframe with a postMessage bridge
- The bridge supports bidirectional JSON-RPC 2.0 communication

See: https://modelcontextprotocol.io/docs/extensions/apps
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "AppCSP",
    "AppMetadata",
    "AppPermissions",
    "UI_EXTENSION_ID",
    "UI_MIME_TYPE",
    "extract_app_metadata",
]

logger = logging.getLogger(__name__)

# Extension constants
UI_EXTENSION_ID = "io.modelcontextprotocol/ui"
UI_MIME_TYPE = "text/html;profile=mcp-app"


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------


class AppCSP(BaseModel):
    """Content Security Policy for MCP App resources."""

    connect_domains: list[str] = Field(default_factory=list)
    """Origins allowed for fetch/XHR/WebSocket (connect-src)."""

    resource_domains: list[str] = Field(default_factory=list)
    """Origins allowed for scripts, images, styles, fonts."""

    frame_domains: list[str] = Field(default_factory=list)
    """Origins allowed for nested iframes (frame-src)."""

    base_uri_domains: list[str] = Field(default_factory=list)
    """Allowed base URIs for the document (base-uri)."""


class AppPermissions(BaseModel):
    """Iframe sandbox permissions for MCP App resources."""

    camera: bool = False
    microphone: bool = False
    geolocation: bool = False
    clipboard_write: bool = False


class AppMetadata(BaseModel):
    """Metadata for an MCP App-enabled tool."""

    resource_uri: str
    """URI of the UI resource (typically ui:// scheme)."""

    visibility: list[str] = Field(default_factory=lambda: ["app", "model"])
    """Where this tool is visible: 'app', 'model', or both."""

    csp: AppCSP = Field(default_factory=AppCSP)
    """Content Security Policy for the app iframe."""

    permissions: AppPermissions = Field(default_factory=AppPermissions)
    """Iframe sandbox permissions."""

    domain: str | None = None
    """Domain for the iframe."""

    prefers_border: bool = False
    """Whether the UI prefers a visible border."""


# -----------------------------------------------------------------------------
# Extraction helpers
# -----------------------------------------------------------------------------


def extract_app_metadata(tool: Any) -> AppMetadata | None:
    """Extract MCP App metadata from an MCP Tool object.

    The MCP Apps extension stores metadata in tool._meta["ui"].
    The "ui" key can be:
    - True (simple marker, no config)
    - dict with resourceUri, visibility, csp, permissions, etc.

    Args:
        tool: MCP Tool object (from list_tools())

    Returns:
        AppMetadata if the tool has app metadata, None otherwise.
    """
    # Get the _meta / meta dict
    meta = getattr(tool, "meta", None)
    if meta is None and isinstance(tool, dict):
        meta = tool.get("_meta") or tool.get("meta")
    if not isinstance(meta, Mapping):
        return None

    ui_data: dict[str, Any] = {}

    # Legacy nested key support: meta["ui"] = {...}
    raw_ui = meta.get("ui")
    if isinstance(raw_ui, Mapping):
        ui_data.update(dict(raw_ui))

    # Extension-id form support: meta["io.modelcontextprotocol/ui"] = {...}
    raw_ext = meta.get(UI_EXTENSION_ID)
    if isinstance(raw_ext, Mapping):
        ui_data.update(dict(raw_ext))

    # Flat key support used by MCP Apps:
    # meta["ui/resourceUri"] = "ui://..."
    ui_data.update(_extract_flat_ui_meta(meta))

    if not ui_data:
        return None

    resource_uri = ui_data.get("resourceUri") or ui_data.get("resource_uri")
    if not resource_uri:
        return None  # No resource URI — can't render

    # Parse CSP
    csp_data = ui_data.get("csp", {})
    if not isinstance(csp_data, Mapping):
        csp_data = {}
    csp = AppCSP(
        connect_domains=_list_or_empty(csp_data.get("connectDomains") or csp_data.get("connect_domains")),
        resource_domains=_list_or_empty(csp_data.get("resourceDomains") or csp_data.get("resource_domains")),
        frame_domains=_list_or_empty(csp_data.get("frameDomains") or csp_data.get("frame_domains")),
        base_uri_domains=_list_or_empty(csp_data.get("baseUriDomains") or csp_data.get("base_uri_domains")),
    )

    # Parse permissions — MCP Apps uses {} (empty dict) as "present/enabled"
    perms_data = ui_data.get("permissions", {})
    if not isinstance(perms_data, Mapping):
        perms_data = {}
    permissions = AppPermissions(
        camera=_permission_enabled(perms_data.get("camera")),
        microphone=_permission_enabled(perms_data.get("microphone")),
        geolocation=_permission_enabled(perms_data.get("geolocation")),
        clipboard_write=(
            _permission_enabled(perms_data.get("clipboardWrite"))
            or _permission_enabled(perms_data.get("clipboard_write"))
        ),
    )

    visibility = ui_data.get("visibility", ["app", "model"])
    if not isinstance(visibility, list):
        visibility = ["app", "model"]

    return AppMetadata(
        resource_uri=str(resource_uri),
        visibility=[str(v) for v in visibility],
        csp=csp,
        permissions=permissions,
        domain=ui_data.get("domain"),
        prefers_border=bool(ui_data.get("prefersBorder") or ui_data.get("prefers_border")),
    )


def _list_or_empty(val: Any) -> list[str]:
    """Coerce a value to list[str], defaulting to empty list."""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(v) for v in val]
    return []


def _extract_flat_ui_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    """Extract flat ui/* metadata into the nested ui dict shape."""
    data: dict[str, Any] = {}

    resource_uri = meta.get("ui/resourceUri") or meta.get("ui/resource_uri")
    if resource_uri is not None:
        data["resourceUri"] = resource_uri

    visibility = meta.get("ui/visibility")
    if visibility is not None:
        data["visibility"] = visibility

    csp = meta.get("ui/csp")
    if isinstance(csp, Mapping):
        data["csp"] = dict(csp)

    permissions = meta.get("ui/permissions")
    if isinstance(permissions, Mapping):
        data["permissions"] = dict(permissions)

    domain = meta.get("ui/domain")
    if domain is not None:
        data["domain"] = domain

    prefers_border = meta.get("ui/prefersBorder")
    if prefers_border is None:
        prefers_border = meta.get("ui/prefers_border")
    if prefers_border is not None:
        data["prefersBorder"] = bool(prefers_border)

    return data


def _permission_enabled(value: Any) -> bool:
    """MCP Apps permissions can be bools or marker objects."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return True
