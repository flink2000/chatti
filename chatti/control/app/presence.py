"""Is the chatti switched on right now?

Nothing in the stack answers this. The firmware opens the WebSocket only while a
conversation runs (application.cc: HandleToggleChatEvent -> ContinueOpenAudioChannel)
and closes it afterwards, so between conversations the device is invisible to the
server. Showing blinking eyes because *the PC side* is healthy reads like "the
chatti is there" and is simply not true.

So we ask the network instead, and we can do it without touching the firmware:

* The device-id the firmware sends is its **MAC address** — that is what ends up
  as the key in data/chatti-devices.json.
* The IP recorded there is useless: Docker Desktop NATs published ports, so the
  server sees 172.17.0.1 for every device. The address has to come from the host.
* The host's ARP table maps that MAC to the current LAN address. It is only an
  address book though, not a presence signal — entries survive the device being
  switched off for hours (verified 2026-08-15: the entry stood while the device
  was off and did not answer a single ping).
* The presence signal is the **ping**. lwIP answers ICMP echo out of the box, so
  a reply means the device is powered and on the WLAN.

Three answers, never two: on / off / unknown. "I cannot tell" is a different
thing from "it is off" and must not be dressed up as one.
"""

import asyncio
import json
import os
import re
import time

from . import proc, settings

# How long a result stays good. The page polls every 3 s; re-pinging that often
# would spawn two processes per poll for no gain.
TTL = 8.0
# Measured 2026-08-16 against the powered-on, idle device. Its WiFi power save
# makes a *single* echo request a coin flip — and the losses are not slow
# replies that a longer -w would catch, they are silence:
#
#   ping -n 1, six times, 5 s apart   3 of 6      (replies 164-362 ms)
#   ping -n 2, four times, 8 s apart  1 of 4
#   ping -n 3, four times, 8 s apart  4 of 4
#   ping -n 6, once                   6 of 6, no loss at all
#
# So the burst has to be inside *one* ping call: separate invocations spaced
# apart keep hitting the same asleep window, while consecutive packets 1 s
# apart get through once the path is awake. Three is the smallest count that
# was reliable; the first two attempts cost nothing extra because Windows sends
# all of them regardless.
#
# This matters more than it looks: reporting a running chatti as "switched off"
# is the worst answer this module can give, and the naive one-ping version did
# exactly that about half the time.
PING_COUNT = 3
PING_TIMEOUT_MS = 1200

# ...and even three are not enough, because silence does not mean "switched off".
# Observed 2026-08-16 with the display visibly on and the eyes moving: 0 of 20
# echoes answered, and Windows aged the ARP entry out entirely. The cause is in
# the firmware, not the network: every transition into idle calls
# SetPowerSaveLevel(LOW_POWER) (application.cc:333, 400, 547, 1096), which ends
# up as esp_wifi_set_ps(WIFI_PS_MAX_MODEM) in
# managed_components/78__esp-wifi-connect/wifi_station.cc:313. The station then
# sleeps through the broadcast ARP requests it would have to answer first, so
# the whole LAN loses sight of it.
#
# Reachability degrades with idle time, measured in one sitting:
#   during a conversation   10-24 ms, no loss
#   shortly after           164-362 ms, ~50% of single echoes lost
#   a few minutes idle      0 of 20, ARP entry gone
#
# Fixed in the firmware on the same day rather than papered over here: the board
# now raises LOW_POWER to BALANCED before passing it on, so the radio wakes at
# every DTIM beacon (esp32-s3-touch-lcd-1.83.cc, SetPowerSaveLevel). Measured
# after flashing, with the device idle: 12 of 12, single echoes included.
#
# The switch stays because the guarantee it protects is worth naming — silence
# only means "off" as long as an idle device answers. If the radio is ever
# allowed to sleep deeply again, turn this off in the same commit: claiming
# "switched off" about a device that sits there blinking is the one mistake this
# module must not make.
PING_PROVES_OFF = True
ARP_TIMEOUT = 4.0
# Three packets one second apart, plus process start: ~2.5 s measured.
PING_TIMEOUT = 8.0

# "192.168.1.42          aa-bb-cc-dd-ee-ff     dynamisch". Matched by shape,
# not by column headings — those are localised, the numbers are not.
_ARP_LINE = re.compile(
    r"(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})"
)

_cache = {"at": 0.0, "value": None}
# The single in-flight refresh. Guarding by task rather than by a lock keeps
# callers from queueing up behind a ping they do not need to wait for.
_refresh_task = None


def _normalise_mac(value):
    """aa:bb:cc:dd:ee:ff -> aa-bb-cc-dd-ee-ff, the form `arp -a` prints."""
    if not value:
        return None
    mac = re.sub(r"[^0-9a-fA-F]", "", str(value)).lower()
    if len(mac) != 12:
        return None
    return "-".join(mac[i:i + 2] for i in range(0, 12, 2))


def _known_device():
    """The most recently seen device from the file the server writes."""
    try:
        with open(settings.DEVICE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return None, None
    best_id, best = None, None
    for device_id, entry in data.items():
        if best is None or (entry.get("last_seen") or 0) > (best.get("last_seen") or 0):
            best_id, best = device_id, entry
    return best_id, best


def _remembered_ip(mac):
    """The address that last answered, from our own little file.

    Written by us, not by the container, so there is no writer to race with.
    It matters when the device has been off long enough for Windows to drop the
    ARP entry: without it we would fall back to "cannot tell" instead of the
    truthful "not reachable".
    """
    try:
        with open(settings.PRESENCE_FILE, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get(mac, {}).get("ip")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def _remember_ip(mac, ip):
    if not mac or not ip:
        return
    try:
        data = {}
        if os.path.exists(settings.PRESENCE_FILE):
            with open(settings.PRESENCE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        if data.get(mac, {}).get("ip") == ip:
            return  # nothing changed, no write
        data[mac] = {"ip": ip, "at": round(time.time(), 3)}
        tmp = settings.PRESENCE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, settings.PRESENCE_FILE)
    except OSError:
        pass  # a status page must never fail over a cache file


async def _arp_lookup(mac):
    """Current LAN addresses for this MAC, newest table order preserved."""
    r = await proc.run(["arp", "-a"], ARP_TIMEOUT)
    if not r.ok:
        return []
    found = []
    for ip, found_mac in _ARP_LINE.findall(r.out):
        if found_mac.lower() == mac and ip not in found:
            found.append(ip)
    return found


async def _ping(ip):
    """Does this address answer? A burst of PING_COUNT echoes — see above for
    why one is not enough and why the burst must be a single call.

    Windows `ping` exits 0 as soon as *any* packet came back, so the plain exit
    code already means "at least one reply". `TTL=` is checked on top of it
    because a router answering "host unreachable" also exits 0.
    """
    r = await proc.run(
        ["ping", "-n", str(PING_COUNT), "-w", str(PING_TIMEOUT_MS), ip],
        PING_TIMEOUT,
    )
    return r.rc == 0 and "TTL=" in r.out.upper()


async def _check():
    device_id, entry = _known_device()
    mac = _normalise_mac(device_id)
    result = {"state": "unknown", "ip": None, "mac": mac, "detail": ""}

    if not mac:
        result["detail"] = ("No device known yet — the Chatti has to have connected "
                            "at least once.")
        return result

    candidates = await _arp_lookup(mac)
    remembered = _remembered_ip(mac)
    if remembered and remembered not in candidates:
        candidates.append(remembered)
    # The address the container recorded is worth a try only if it is not the
    # Docker gateway, which it almost always is.
    recorded = (entry or {}).get("ip")
    if recorded and not recorded.startswith("172.") and recorded not in candidates:
        candidates.append(recorded)

    if not candidates:
        result["detail"] = ("The Chatti's address is unknown (it has never been in "
                            "this PC's ARP table).")
        return result

    for ip in candidates:
        if await _ping(ip):
            _remember_ip(mac, ip)
            result.update(state="on", ip=ip, detail=f"answers at {ip}")
            return result

    if PING_PROVES_OFF:
        result.update(state="off", ip=candidates[0],
                      detail=f"{candidates[0]} is not answering")
    else:
        result.update(state="unknown", ip=candidates[0],
                      detail="it is not answering — while idle it powers its Wi-Fi "
                             "down far enough that this means nothing")
    return result


async def _refresh():
    try:
        value = await _check()
    except Exception as e:  # noqa: BLE001
        value = {"state": "unknown", "ip": None, "mac": None, "detail": str(e)}
    _cache.update(at=time.monotonic(), value=value)
    return value


def _start_refresh():
    global _refresh_task
    if _refresh_task is None or _refresh_task.done():
        _refresh_task = asyncio.ensure_future(_refresh())
    return _refresh_task


async def check():
    """Cached presence. Never raises — every caller is a status page.

    A stale entry is answered *immediately* with the previous value while the
    ping runs in the background. The burst takes ~2.5 s, and the page polls
    every 3 s: waiting for it would make every few refreshes visibly stall for
    an answer that changes maybe once an hour. Only the very first call, when
    there is nothing to show yet, waits.
    """
    if _cache["value"] is None:
        return await _start_refresh()
    if time.monotonic() - _cache["at"] > TTL:
        _start_refresh()
    return _cache["value"]


def note_seen(ip=None):
    """Called when the device is provably there (an open WebSocket). Skips the
    next ping — an answer we already have is better than one we pay for."""
    value = dict(_cache["value"] or {"mac": None, "ip": None, "detail": ""})
    value.update(state="on", detail="in a conversation")
    if ip:
        value["ip"] = ip
    _cache.update(at=time.monotonic(), value=value)
