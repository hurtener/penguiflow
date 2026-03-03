from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import tempfile
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, HttpUrl

from penguiflow.artifacts import ArtifactRef, ArtifactScope

from .untrusted import wrap_untrusted_text


class WebFetchArgs(BaseModel):
    url: HttpUrl
    max_bytes: int = Field(default=5_000_000, ge=1, le=50_000_000)
    timeout_s: float = Field(default=20.0, ge=1.0, le=300.0)
    follow_redirects: bool = True
    max_redirects: int = Field(default=5, ge=0, le=20)
    max_preview_chars: int = Field(default=8_000, ge=200, le=50_000)
    allow_private_networks: bool = False
    allow_video: bool = False


class WebFetchResult(BaseModel):
    url: str
    final_url: str
    status_code: int
    content_type: str | None = None
    fetched_bytes: int
    markdown_preview: str | None = None
    truncated: bool = False
    artifact: ArtifactRef | None = None
    notes: list[str] = Field(default_factory=list)


_VIDEO_TYPE_RE = re.compile(r"^video/", re.IGNORECASE)


def _artifact_scope(tool_context: Mapping[str, Any] | None) -> ArtifactScope | None:
    if not tool_context:
        return None
    return ArtifactScope(
        tenant_id=tool_context.get("tenant_id") if isinstance(tool_context.get("tenant_id"), str) else None,
        user_id=tool_context.get("user_id") if isinstance(tool_context.get("user_id"), str) else None,
        session_id=tool_context.get("session_id") if isinstance(tool_context.get("session_id"), str) else None,
        trace_id=tool_context.get("trace_id") if isinstance(tool_context.get("trace_id"), str) else None,
    )


def _is_ip_disallowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or getattr(ip, "is_reserved", False)
    )


async def _resolve_and_validate_host(host: str, *, allow_private_networks: bool) -> None:
    host = host.strip()
    if not host:
        raise ValueError("Invalid URL host")

    if host.lower() in {"localhost"} and not allow_private_networks:
        raise ValueError("Refusing to fetch localhost (SSRF protection). Set allow_private_networks=true to override.")

    # Literal IP fast-path.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not allow_private_networks and _is_ip_disallowed(ip):
            raise ValueError(f"Refusing to fetch private/internal IP {host} (SSRF protection).")
        return

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed for host '{host}': {exc}") from exc

    if allow_private_networks:
        return

    addrs: set[str] = set()
    for _family, _type, _proto, _canon, sockaddr in infos:
        if not sockaddr:
            continue
        addr = sockaddr[0]
        addrs.add(addr)

    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_ip_disallowed(ip):
            raise ValueError(
                f"Refusing to fetch host '{host}' resolving to disallowed IP {addr} (SSRF protection)."
            )


def _validate_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are allowed.")
    if parts.username or parts.password:
        raise ValueError("Refusing to fetch URL with embedded credentials.")


def _normalize_url(url: str) -> str:
    parts = urlsplit(url)
    # Normalize: strip fragments (do not affect fetch); keep query.
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


async def web_fetch(args: WebFetchArgs, ctx: Any) -> WebFetchResult:
    """Fetch a URL and return markdown (or store binary/large content as an artifact)."""
    try:
        import aiohttp
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ModuleNotFoundError("aiohttp is required for web_fetch. Install penguiflow[web].") from exc

    url = _normalize_url(str(args.url))
    _validate_url(url)

    parts = urlsplit(url)
    await _resolve_and_validate_host(parts.hostname or "", allow_private_networks=bool(args.allow_private_networks))

    timeout = aiohttp.ClientTimeout(total=float(args.timeout_s))
    max_bytes = int(args.max_bytes)

    redirects_remaining = int(args.max_redirects)
    current_url = url
    status_code = 0
    content_type: str | None = None
    body: bytes = b""
    notes: list[str] = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            async with session.get(current_url, allow_redirects=False) as resp:
                status_code = int(resp.status)
                content_type = resp.headers.get("Content-Type")

                if 300 <= status_code < 400 and args.follow_redirects:
                    if redirects_remaining <= 0:
                        raise ValueError("Too many redirects.")
                    location = resp.headers.get("Location")
                    if not location:
                        raise ValueError("Redirect response missing Location header.")
                    redirects_remaining -= 1
                    next_url = str(aiohttp.helpers.URL(current_url).join(aiohttp.helpers.URL(location)))
                    next_url = _normalize_url(next_url)
                    _validate_url(next_url)
                    next_parts = urlsplit(next_url)
                    await _resolve_and_validate_host(
                        next_parts.hostname or "",
                        allow_private_networks=bool(args.allow_private_networks),
                    )
                    current_url = next_url
                    continue

                if status_code >= 400:
                    text = await resp.text(errors="replace")
                    raise RuntimeError(f"Fetch failed with HTTP {status_code}: {text[:500]}")

                length_hdr = resp.headers.get("Content-Length")
                if length_hdr is not None:
                    try:
                        declared = int(length_hdr)
                    except ValueError:
                        declared = None
                    if declared is not None and declared > max_bytes:
                        raise ValueError(f"Response too large (Content-Length {declared} > max_bytes {max_bytes}).")

                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"Response exceeded max_bytes ({max_bytes}).")
                    chunks.append(chunk)
                body = b"".join(chunks)
                break

    fetched_bytes = len(body)
    if content_type and _VIDEO_TYPE_RE.match(content_type) and not args.allow_video:
        raise ValueError("Refusing to fetch video/* content by default. Set allow_video=true to override.")

    scope = _artifact_scope(getattr(ctx, "tool_context", None))

    if content_type and (content_type.startswith("text/") or "html" in content_type.lower()):
        try:
            from markitdown import MarkItDown
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise ModuleNotFoundError(
                "markitdown is required for web_fetch markdown conversion. Install penguiflow[web]."
            ) from exc

        suffix = ".html" if (content_type and "html" in content_type.lower()) else ".txt"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as fp:
            tmp_path = fp.name
            fp.write(body)
        try:
            md = MarkItDown(enable_plugins=False)
            converted = md.convert(tmp_path)
            markdown = getattr(converted, "text_content", None)
            if not isinstance(markdown, str):
                markdown = str(converted)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        truncated = len(markdown) > int(args.max_preview_chars)
        preview = markdown[: int(args.max_preview_chars)]
        artifact: ArtifactRef | None = None
        if truncated:
            artifact = await ctx.artifacts.put_text(
                markdown,
                mime_type="text/markdown",
                filename="web_fetch.md",
                namespace="web_fetch",
                scope=scope,
                meta={"url": current_url, "content_type": content_type},
            )
            notes.append("stored_full_markdown")
        wrapped = wrap_untrusted_text(preview, tool_name="web_fetch", provider="web_fetch", url=current_url)
        return WebFetchResult(
            url=url,
            final_url=current_url,
            status_code=status_code,
            content_type=content_type,
            fetched_bytes=fetched_bytes,
            markdown_preview=wrapped,
            truncated=truncated,
            artifact=artifact,
            notes=notes,
        )

    # Treat everything else as binary.
    artifact = await ctx.artifacts.put_bytes(
        body,
        mime_type=content_type,
        filename="web_fetch.bin",
        namespace="web_fetch",
        scope=scope,
        meta={"url": current_url, "content_type": content_type},
    )
    notes.append("binary_saved")
    return WebFetchResult(
        url=url,
        final_url=current_url,
        status_code=status_code,
        content_type=content_type,
        fetched_bytes=fetched_bytes,
        markdown_preview=None,
        truncated=False,
        artifact=artifact,
        notes=notes,
    )


__all__ = ["WebFetchArgs", "WebFetchResult", "web_fetch"]
