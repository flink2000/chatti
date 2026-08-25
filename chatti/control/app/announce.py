"""Announce the server on the LAN so nobody has to type an IP address.

The problem this removes: the device learns where to talk from its OTA URL,
which lives in NVS and has to be typed into the setup page by hand. That is one
of the four setup steps, and it is the one that silently breaks later — when
the router hands this PC a different address, the device keeps dialling the old
one and nothing in the stack notices (it happened on 2026-08-15).

With mDNS the device asks the network instead of remembering. Nothing about the
protocol changes: we advertise the *OTA* port, the device fetches
/xiaozhi/ota/ as it always did, and the reply still carries the WebSocket
address. So this is discovery only, bolted on in front of the existing flow.

Service type is `_chatti._tcp` rather than a generic `_http._tcp` on purpose —
the device must not stumble into a printer.
"""

import asyncio
import socket

from zeroconf import IPVersion, ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from . import settings
from .services import local_ipv4s, outbound_ipv4

SERVICE_TYPE = "_chatti._tcp.local."
SERVICE_NAME = "chatti-server"

_azc: AsyncZeroconf | None = None
_info: ServiceInfo | None = None
_state = {"active": False, "addresses": [], "error": None}


def status() -> dict:
    return dict(_state)


def _addresses() -> list[str]:
    """Only addresses the device could actually reach.

    Dropped on purpose: 169.254.x (APIPA — there is no network), and
    172.16–31.x, where Docker and WSL keep their virtual adapters. A device on
    the real LAN can never reach those, and advertising them would just make it
    try the wrong one first.
    """
    out = []
    for a in local_ipv4s():
        if a.startswith("169.254."):
            continue
        parts = a.split(".")
        if parts[0] == "172" and 16 <= int(parts[1]) <= 31:
            continue
        out.append(a)

    # Put the address used for outbound traffic first — with several NICs that
    # is the one on the LAN the device is on.
    primary = outbound_ipv4()
    if primary in out:
        out.remove(primary)
        out.insert(0, primary)
    return out


async def start() -> None:
    """Publish the record. Safe to call again; it republishes."""
    global _azc, _info
    await stop()

    addrs = _addresses()
    if not addrs:
        _state.update(active=False, addresses=[],
                      error="This PC has no usable IPv4 address.")
        return

    _info = ServiceInfo(
        SERVICE_TYPE,
        f"{SERVICE_NAME}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(a) for a in addrs],
        # The OTA/HTTP port. That is the one the device needs first; the
        # WebSocket address comes out of the reply.
        port=int(settings.XIAOZHI_HTTP_URL.rsplit(":", 1)[1]),
        properties={
            # Spelled out so a future firmware does not have to hardcode paths.
            "path": "/xiaozhi/ota/",
            "ws": "/xiaozhi/v1/",
            "ws_port": "8000",
            "version": "1",
        },
        server=f"{SERVICE_NAME}.local.",
    )

    try:
        # Bound to the LAN addresses explicitly, not to "every interface".
        # This machine also carries Docker and WSL adapters (172.x), and a
        # multicast announcement that leaves through one of those reaches
        # nothing - least of all a device on the WLAN.
        _azc = AsyncZeroconf(interfaces=addrs, ip_version=IPVersion.V4Only)
        await _azc.async_register_service(_info)
        _state.update(active=True, addresses=addrs, error=None)
    except Exception as e:  # noqa: BLE001 - discovery is a convenience, never fatal
        _state.update(active=False, addresses=[], error=str(e))
        _azc = None


async def stop() -> None:
    global _azc, _info
    if _azc is not None:
        try:
            if _info is not None:
                await _azc.async_unregister_service(_info)
            await _azc.async_close()
        except Exception:  # noqa: BLE001
            pass
    _azc, _info = None, None
    _state.update(active=False)


# How often the record goes out unasked. See the reasoning in
# `refresh_forever` - this is what makes discovery work without an
# administrator having to open the firewall first.
ANNOUNCE_INTERVAL = 10.0
# Re-reading the interface list is cheap but pointless every 10 s.
ADDRESS_CHECK_EVERY = 6


async def refresh_forever(interval: float = ANNOUNCE_INTERVAL) -> None:
    """Keep announcing, and re-publish when this PC's addresses change.

    Two separate reasons to be in here:

    1. **The address can change under a running server.** That is the failure
       from 2026-08-15 - the router handed this PC a new address and the device
       kept dialling the old one.

    2. **Announcing beats answering, because of the Windows firewall.** mDNS
       normally works by question and answer: the device multicasts a query, we
       reply. But an incoming query is unsolicited inbound traffic, and Windows
       blocks that for any program without a rule - the allow-rules it ships for
       UDP 5353 belong to svchost.exe, its own responder (verified 2026-08-16).
       Adding our own rule needs elevation, which is a poor thing to ask of
       someone who just wants to try this out.
       Outbound multicast is allowed by default, so we simply say it without
       being asked. The device picks the record up passively, and no rule is
       needed. `async_update_service` re-broadcasts without sending the goodbye
       packets an unregister would - those would make the device forget us.

    The interval is a trade: the device's query window is ~3 s, so a shorter
    interval means it hears us sooner. 10 s costs one small multicast packet and
    lands inside a query window often enough that the retry loop on the device
    catches it within a minute.
    """
    ticks = 0
    while True:
        await asyncio.sleep(interval)
        ticks += 1

        if ticks % ADDRESS_CHECK_EVERY == 0 and _addresses() != _state.get("addresses"):
            await start()
            continue

        if _azc is not None and _info is not None:
            try:
                await _azc.async_update_service(_info)
            except Exception as e:  # noqa: BLE001 - never let this kill the loop
                _state["error"] = str(e)
