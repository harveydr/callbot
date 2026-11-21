"""SIP target checks. Trunks cannot be pointed at loopback or private networks."""

from __future__ import annotations

import ipaddress
import socket

from app.errors import ApiError

_BLOCKED_NAMES = {"localhost", "metadata.google.internal"}


def assert_public_sip_host(host: str) -> None:
    name = host.strip().lower().rstrip(".")
    if not name or name in _BLOCKED_NAMES or name.endswith(".local") or name.endswith(".internal"):
        raise ApiError(400, "BAD_REQUEST", "sip_host is not a public address")
    try:
        resolved = socket.getaddrinfo(name, None)
    except socket.gaierror as exc:
        raise ApiError(400, "TRUNK_UNREACHABLE", "sip_host does not resolve") from exc
    addresses = {item[4][0] for item in resolved}
    if not addresses:
        raise ApiError(400, "TRUNK_UNREACHABLE", "sip_host does not resolve")
    for raw in addresses:
        ip = ipaddress.ip_address(raw)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ApiError(400, "BAD_REQUEST", "sip_host must not point at a private network")
