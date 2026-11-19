"""``web_fetch`` tool: fetch a URL with SSRF protections."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import re
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, HttpUrl

from penguiflow.artifacts import ArtifactRef

from .untrusted import wrap_untrusted_text

if TYPE_CHECKING:
    import aiohttp

    from penguiflow.planner.context import ToolContext


class WebFetchArgs(BaseModel):
    """Arguments for the ``web_fetch`` tool."""

    url: HttpUrl
    max_bytes: int = Field(default=5_000_000, ge=1, le=50_000_000)
    timeout_s: float = Field(default=20.0, ge=1.0, le=300.0)
    follow_redirects: bool = True
    max_redirects: int = Field(default=5, ge=0, le=20)
    max_preview_chars: int = Field(default=8_000, ge=200, le=50_000)
    allow_private_networks: bool = False
    allow_video: bool = False


class WebFetchResult(BaseModel):
    """Result of the ``web_fetch`` tool."""

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


@dataclass(frozen=True, slots=True)
class _Fetched:
    """A downloaded response body and where it came from."""

    final_url: str
    status_code: int
    content_type: str | None
    body: bytes


def _is_ip_disallowed(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or getattr(ip, "is_reserved", False)
    )


def _check_literal_ip(host: str, *, allow_private_networks: bool) -> bool:
    """Validate ``host`` if it is a literal IP; return whether it was one."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if not allow_private_networks and _is_ip_disallowed(ip):
        msg = (
            f"Refusing to fetch private/internal IP {host} (SSRF protection)."
        )
        raise ValueError(msg)
    return True


async def _resolve_and_validate_host(
    host: str, *, allow_private_networks: bool
) -> None:
    host = host.strip()
    if not host:
        msg = "Invalid URL host"
        raise ValueError(msg)

    if host.lower() in {"localhost"} and not allow_private_networks:
        msg = (
            "Refusing to fetch localhost (SSRF protection). Set "
            "allow_private_networks=true to override."
        )
        raise ValueError(msg)

    # Literal IP fast-path.
    if _check_literal_ip(host, allow_private_networks=allow_private_networks):
        return

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        msg = f"DNS resolution failed for host '{host}': {exc}"
        raise ValueError(msg) from exc

    if allow_private_networks:
        return

    # INET/INET6 lookups yield the host address first in each sockaddr.
    for addr in {str(info[4][0]) for info in infos}:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_ip_disallowed(ip):
            msg = (
                f"Refusing to fetch host '{host}' resolving to disallowed "
                f"IP {addr} (SSRF protection)."
            )
            raise ValueError(msg)


def _validate_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        msg = "Only http/https URLs are allowed."
        raise ValueError(msg)
    if parts.username or parts.password:
        msg = "Refusing to fetch URL with embedded credentials."
        raise ValueError(msg)


def _normalize_url(url: str) -> str:
    parts = urlsplit(url)
    # Normalize: strip fragments (do not affect fetch); keep query.
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, parts.query, "")
    )


async def _checked_url(url: str, args: WebFetchArgs) -> str:
    """Normalize ``url`` and apply the URL and host SSRF checks."""
    url = _normalize_url(url)
    _validate_url(url)
    await _resolve_and_validate_host(
        urlsplit(url).hostname or "",
        allow_private_networks=bool(args.allow_private_networks),
    )
    return url


async def _redirect_target(
    current_url: str, location: str | None, args: WebFetchArgs
) -> str:
    """Resolve and check a redirect's ``Location`` against ``current_url``."""
    from yarl import URL

    if not location:
        msg = "Redirect response missing Location header."
        raise ValueError(msg)
    return await _checked_url(str(URL(current_url).join(URL(location))), args)


async def _read_limited(resp: aiohttp.ClientResponse, max_bytes: int) -> bytes:
    """Read the body, refusing anything larger than ``max_bytes``."""
    length_hdr = resp.headers.get("Content-Length")
    if length_hdr is not None:
        try:
            declared: int | None = int(length_hdr)
        except ValueError:
            declared = None
        if declared is not None and declared > max_bytes:
            msg = (
                "Response too large (Content-Length "
                f"{declared} > max_bytes {max_bytes})."
            )
            raise ValueError(msg)

    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.content.iter_chunked(64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            msg = f"Response exceeded max_bytes ({max_bytes})."
            raise ValueError(msg)
        chunks.append(chunk)
    return b"".join(chunks)


async def _download(url: str, args: WebFetchArgs) -> _Fetched:
    """GET ``url``, following checked redirects, with a size limit."""
    try:
        import aiohttp
    except ModuleNotFoundError as exc:  # pragma: no cover
        msg = (
            "aiohttp is required for web_fetch. Install penguiflow-lite[web]."
        )
        raise ModuleNotFoundError(msg) from exc

    timeout = aiohttp.ClientTimeout(total=float(args.timeout_s))
    redirects_remaining = int(args.max_redirects)
    current_url = url
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            async with session.get(current_url, allow_redirects=False) as resp:
                status_code = int(resp.status)
                if 300 <= status_code < 400 and args.follow_redirects:
                    if redirects_remaining <= 0:
                        msg = "Too many redirects."
                        raise ValueError(msg)
                    redirects_remaining -= 1
                    current_url = await _redirect_target(
                        current_url, resp.headers.get("Location"), args
                    )
                    continue

                if status_code >= 400:
                    text = await resp.text(errors="replace")
                    msg = f"Fetch failed with HTTP {status_code}: {text[:500]}"
                    raise RuntimeError(msg)

                return _Fetched(
                    final_url=current_url,
                    status_code=status_code,
                    content_type=resp.headers.get("Content-Type"),
                    body=await _read_limited(resp, int(args.max_bytes)),
                )


def _to_markdown(body: bytes, content_type: str) -> str:
    """Convert an HTML or text body to markdown with markitdown."""
    try:
        from markitdown import MarkItDown
    except ModuleNotFoundError as exc:  # pragma: no cover
        msg = (
            "markitdown is required for web_fetch markdown "
            "conversion. Install penguiflow-lite[web]."
        )
        raise ModuleNotFoundError(msg) from exc

    suffix = ".html" if "html" in content_type.lower() else ".txt"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as fp:
        tmp_path = Path(fp.name)
        fp.write(body)
    try:
        converted = MarkItDown(enable_plugins=False).convert(str(tmp_path))
        markdown = getattr(converted, "text_content", None)
        return markdown if isinstance(markdown, str) else str(converted)
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink()


async def web_fetch(args: WebFetchArgs, ctx: ToolContext) -> WebFetchResult:
    """Fetch a URL and return markdown, or store binary/large content."""
    url = await _checked_url(str(args.url), args)
    fetched = await _download(url, args)
    content_type = fetched.content_type
    if (
        content_type
        and _VIDEO_TYPE_RE.match(content_type)
        and not args.allow_video
    ):
        msg = (
            "Refusing to fetch video/* content by default. Set "
            "allow_video=true to override."
        )
        raise ValueError(msg)

    meta = {"url": fetched.final_url, "content_type": content_type}
    notes: list[str] = []
    if content_type and (
        content_type.startswith("text/") or "html" in content_type.lower()
    ):
        markdown = _to_markdown(fetched.body, content_type)
        max_preview = int(args.max_preview_chars)
        truncated = len(markdown) > max_preview
        artifact: ArtifactRef | None = None
        if truncated:
            artifact = await ctx.artifacts.upload(
                markdown,
                mime_type="text/markdown",
                filename="web_fetch.md",
                namespace="web_fetch",
                meta=meta,
            )
            notes.append("stored_full_markdown")
        preview = wrap_untrusted_text(
            markdown[:max_preview],
            tool_name="web_fetch",
            provider="web_fetch",
            url=fetched.final_url,
        )
    else:
        # Treat everything else as binary.
        truncated = False
        preview = None
        artifact = await ctx.artifacts.upload(
            fetched.body,
            mime_type=content_type,
            filename="web_fetch.bin",
            namespace="web_fetch",
            meta=meta,
        )
        notes.append("binary_saved")

    return WebFetchResult(
        url=url,
        final_url=fetched.final_url,
        status_code=fetched.status_code,
        content_type=content_type,
        fetched_bytes=len(fetched.body),
        markdown_preview=preview,
        truncated=truncated,
        artifact=artifact,
        notes=notes,
    )


__all__ = ["WebFetchArgs", "WebFetchResult", "web_fetch"]
