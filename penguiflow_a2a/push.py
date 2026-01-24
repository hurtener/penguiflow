from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, cast

from .models import AuthenticationInfo, PushNotificationConfig, StreamResponse

_BLOCKED_HOSTNAMES = {"localhost"}


def _is_blocked_ip(address: str | bytes | int | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    ip = address
    if not isinstance(address, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        ip = ipaddress.ip_address(cast(str | bytes | int, address))
    return any(
        (
            getattr(ip, "is_private", False),
            getattr(ip, "is_loopback", False),
            getattr(ip, "is_link_local", False),
            getattr(ip, "is_multicast", False),
            getattr(ip, "is_reserved", False),
            getattr(ip, "is_unspecified", False),
        )
    )


def _resolve_addresses(host: str) -> Iterable[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        infos = socket.getaddrinfo(host, None)
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        if sockaddr:
            addresses.append(ipaddress.ip_address(sockaddr[0]))
    return addresses


def is_safe_webhook_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    normalized = hostname.lower()
    if normalized in _BLOCKED_HOSTNAMES or normalized.endswith(".localhost"):
        return False
    try:
        for address in _resolve_addresses(hostname):
            if _is_blocked_ip(address):
                return False
    except socket.gaierror:
        return False
    return True


def _build_auth_header(auth: AuthenticationInfo | None) -> str | None:
    if auth is None or not auth.schemes or not auth.credentials:
        return None
    scheme = auth.schemes[0]
    if scheme.lower() == "bearer":
        return f"Bearer {auth.credentials}"
    if scheme.lower() == "basic":
        return f"Basic {auth.credentials}"
    return f"{scheme} {auth.credentials}"


class PushNotificationSender(Protocol):
    async def send(self, config: PushNotificationConfig, event: StreamResponse) -> None: ...


@dataclass(slots=True)
class HttpPushNotificationSender:
    timeout_s: float = 10.0
    max_retries: int = 2
    backoff_s: float = 0.5

    async def send(self, config: PushNotificationConfig, event: StreamResponse) -> None:
        if not is_safe_webhook_url(config.url):
            raise ValueError("Webhook URL is not allowed")

        payload = event.model_dump(by_alias=True, exclude_none=True)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        headers = {"Content-Type": "application/a2a+json"}
        if config.token:
            headers["X-A2A-Notification-Token"] = config.token
        auth_header = _build_auth_header(config.authentication)
        if auth_header is not None:
            headers["Authorization"] = auth_header

        for attempt in range(self.max_retries + 1):
            try:
                await asyncio.to_thread(self._send_once, config.url, body, headers)
                return
            except Exception:
                if attempt >= self.max_retries:
                    raise
                await asyncio.sleep(self.backoff_s * (2**attempt))

    def _send_once(self, url: str, body: bytes, headers: dict[str, str]) -> None:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            status = response.status
            if status < 200 or status >= 300:
                raise RuntimeError(f"Webhook delivery failed with status {status}")


__all__ = ["HttpPushNotificationSender", "PushNotificationSender", "is_safe_webhook_url"]
