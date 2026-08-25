"""Health of the four services, plus whether the device is connected.

Five states per service, not two: ok / busy / starting / off / error. "Off" and
"cannot tell" are genuinely different things, and a page that conflates them
sends you looking in the wrong place.

**busy** is the one that had to be added afterwards. While a question is being
processed, faster-whisper takes all six cores and LM Studio the GPU; a health
check then answers late or not within its timeout, and the page announced
"server not running" in the middle of a perfectly healthy conversation. The
distinction that fixes it is not a longer timeout but the *kind* of failure:

* connection refused  -> nobody is listening. Instant, unambiguous, "off".
* timeout             -> something is listening but has no time for us. If the
                         same endpoint answered within the last few minutes,
                         that is "busy", never "off".

Hence the small memory below: which probes have been seen working, and when.
"""

import asyncio
import json
import socket
import time
from urllib.parse import urlparse

import httpx

from . import presence, proc, settings

# monotonic timestamp of the last successful probe, per endpoint.
_last_ok = {}
# How long a working probe vouches for the endpoint. Generously long: the worst
# measured pause is the ~45 s of a full round trip, and a service that has died
# in the meantime refuses connections rather than timing out, so it is caught by
# the other branch anyway.
OK_MEMORY = 300.0


def _remember_ok(key):
    _last_ok[key] = time.monotonic()


def _was_ok(key, within=OK_MEMORY):
    seen = _last_ok.get(key)
    return seen is not None and (time.monotonic() - seen) <= within

# One client for the whole app. trust_env=False matters: a set HTTP_PROXY would
# otherwise send even loopback calls through a proxy.
_client = httpx.AsyncClient(
    timeout=httpx.Timeout(
        connect=settings.HTTP_CONNECT_TIMEOUT,
        read=settings.HTTP_READ_TIMEOUT,
        write=settings.HTTP_READ_TIMEOUT,
        pool=1.0,
    ),
    trust_env=False,
)


def _state(state, detail=""):
    return {"state": state, "detail": detail}


async def _get(url, **kw):
    return await _client.get(url, **kw)


_last_containers = {}


async def docker_overview():
    """One `docker ps -a` answers three questions at once: is the daemon up,
    do our containers exist, are they running."""
    global _last_containers

    r = await proc.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}|{{.State}}"],
        settings.DOCKER_TIMEOUT,
    )
    if not r.ok:
        err = (r.err or "").lower()
        if "not found" in err:
            return _state("error", "docker.exe not found"), {}
        if "npipe" in err or "cannot connect" in err or "daemon" in err:
            return _state("off", "Docker Desktop is not running"), {}
        if r.rc == -1:
            # Timed out. Under full load the CLI alone needs seconds to start —
            # if we have seen the daemon work, keep the last container list
            # rather than declaring everything dead.
            if _was_ok("docker"):
                return (_state("busy", "not answering right now — the PC is busy"),
                        dict(_last_containers))
            return _state("starting", "Docker is not answering yet"), {}
        return _state("error", r.err[:200] or "unknown error"), {}

    containers = {}
    for line in r.out.splitlines():
        if "|" in line:
            name, _, state = line.partition("|")
            containers[name.strip()] = state.strip()
    _remember_ok("docker")
    _last_containers = containers
    return _state("ok", _ours_summary(containers)), containers


def _ours_summary(containers):
    """How our two containers are doing — deliberately not how many exist.

    `docker ps -a` is asked for *all* containers because the two rows below need
    to tell "no such container" from "container is stopped". Counting that list
    made the row read "8 Container" on a PC that also hosts an unrelated stopped
    stack, which reads as if Chatti had eight. It has two, and only those two
    belong in this line.
    """
    ours = [settings.CONTAINER_SERVER, settings.CONTAINER_SPEACHES]
    running = sum(1 for name in ours if containers.get(name) == "running")
    if running == len(ours):
        return "both containers running"
    if running == 0:
        # Neutral on purpose: they may be stopped or not exist at all, and the
        # two rows below say which. This line only speaks for the daemon.
        return "running — no Chatti container active"
    return f"running — {running} of {len(ours)} containers"


def _running(state):
    """Both "answering" and "too busy to answer" mean the thing is alive."""
    return state.get("state") in ("ok", "busy")


def _container_state(containers, name):
    raw = containers.get(name)
    if raw is None:
        return _state("error", "container missing — see chatti/server/README.md")
    if raw == "running":
        return _state("ok", "running")
    if raw in ("restarting", "created"):
        return _state("starting", raw)
    return _state("off", "stopped" if raw == "exited" else raw)


async def _loaded_models():
    """Which LLMs are resident in LM Studio right now. None = cannot tell."""
    try:
        r = await _get(f"{settings.LMSTUDIO_URL}/api/v0/models")
        if r.status_code != 200:
            return None
        _remember_ok("lmstudio")
        return [m.get("id") for m in r.json().get("data", [])
                if m.get("type") in (None, "llm", "vlm") and m.get("state") == "loaded"]
    except Exception:
        return None


async def model_state(configured_model, loaded):
    """Whether the configured model is in memory — its own line, because this
    is not the same question as "is LM Studio running".

    Measured on 2026-08-15: the device gave up before the first answer because
    the question itself had triggered the load of a 6.33 GB model. The firmware
    closes the channel 120 s after connecting and recognition had already used
    43 s of that. An unloaded model therefore costs the first conversation
    after every start — a warning, not a detail.
    """
    if loaded is None:
        # Generating an answer is exactly when LM Studio is least likely to
        # answer a side question in time — and exactly when it is most alive.
        if _was_ok("lmstudio"):
            return _state("busy", "LM Studio is busy")
        return _state("off", "LM Studio is not answering")
    if not configured_model:
        return _state("warn", "no model configured")
    if configured_model in loaded:
        return _state("ok", f"{configured_model} is in memory")
    return _state("warn", "not loaded — the first question then runs into a "
                          "timeout. The start button preloads it.")


async def lmstudio():
    try:
        r = await _get(f"{settings.LMSTUDIO_URL}/api/v0/models")
        if r.status_code == 200:
            _remember_ok("lmstudio")
            return _state("ok", "running")
    except httpx.TimeoutException:
        if _was_ok("lmstudio"):
            return _state("busy", "busy right now")
    except Exception:
        pass
    try:
        r = await _get(f"{settings.LMSTUDIO_URL}/v1/models")
        if r.status_code == 200:
            _remember_ok("lmstudio")
            return _state("ok", "running")
        return _state("error", f"HTTP {r.status_code}")
    except httpx.TimeoutException:
        if _was_ok("lmstudio"):
            return _state("busy", "busy right now")
        return _state("off", "not reachable")
    except Exception:
        return _state("off", "not reachable")


# A container that docker reports as not running is the end of the question —
# do not probe its port, and above all do not fall back to the "busy" memory.
# Measured on 2026-08-16 while building the per-service stop button: after
# `compose stop`, 127.0.0.1:8100 keeps *accepting* connections (Docker Desktop's
# port proxy lingers), so the health check times out instead of being refused,
# the timeout branch found _was_ok() still warm, and the row read "recognising
# or speaking" about a container the user had just switched off — for the
# full five minutes of OK_MEMORY.
def _container_says_down(container_state):
    return container_state["state"] in ("off", "error")


async def speaches(container_state):
    if _container_says_down(container_state):
        return dict(container_state)
    try:
        r = await _get(f"{settings.SPEACHES_URL}/health")
        if r.status_code == 200:
            _remember_ok("speaches")
            return _state("ok", "ready")
        return _state("error", f"HTTP {r.status_code}")
    except httpx.TimeoutException:
        # Transcription runs on the CPU and takes every core it can get. A
        # health check that arrives late during it says nothing is wrong.
        if _was_ok("speaches") or _running(container_state):
            return _state("busy", "recognising or speaking right now")
        return _state("off", "not reachable")
    except Exception:
        # Container up but not answering yet = still loading, not dead.
        if _running(container_state):
            return _state("starting", "container running, still loading")
        return _state("off", "not reachable")


async def xiaozhi(container_state):
    if _container_says_down(container_state):
        return dict(container_state)
    try:
        r = await _get(f"{settings.XIAOZHI_WS_URL}/")
        if r.status_code == 200 and r.text.startswith("Server is running"):
            _remember_ok("xiaozhi")
            return _state("ok", "ready")
        return _state("error", f"unexpected answer: {r.text[:60]}")
    except httpx.TimeoutException:
        # This is the one that used to read "Server is not running" while the
        # server was busy answering a question. Its HTTP reply shares the event
        # loop with the whole conversation, so it is the first thing to arrive
        # late.
        if _was_ok("xiaozhi") or _running(container_state):
            return _state("busy", "busy — processing right now")
        return _state("off", "not reachable")
    except Exception:
        if _running(container_state):
            return _state("starting", "container running, still starting")
        return _state("off", "not reachable")


def local_ipv4s():
    """Every IPv4 address this machine currently holds, APIPA included — an
    address starting with 169.254 is the tell-tale of "no DHCP answer", which
    means the device cannot reach us at all."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:
        # The address actually used for outbound traffic; no packet is sent.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1, guaranteed unroutable
            found.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127."))


def outbound_ipv4():
    """The address Windows would use to reach the network. No packet is sent."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1, guaranteed unroutable
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def preferred_ipv4(configured_host=None):
    """The address the device should be pointed at, or None if there is none.

    Ranking matters here, because this machine holds several addresses and only
    one of them is the one the chatti can reach:
      1. same /24 as the address currently configured — if the PC just moved
         from .236 to .50, that is obviously the right one
      2. the outbound address Windows itself would pick
      3. an ordinary LAN range (192.168.x, 10.x)

    Two ranges are excluded outright rather than merely ranked low, because
    suggesting them would produce a button that looks like a fix and cannot be
    one: APIPA (169.254.x) means no DHCP answer at all, and 172.16-31.x is where
    Docker and WSL live — the chatti can never reach a virtual adapter. The
    exception is a configured address inside 172.16-31.x, which means that
    really is this network. No candidate left -> no suggestion, no button.
    """
    def virtual(addr):
        parts = addr.split(".")
        return (addr.startswith("172.") and len(parts) == 4 and parts[1].isdigit()
                and 16 <= int(parts[1]) <= 31)

    configured_is_virtual = bool(configured_host) and virtual(configured_host)
    candidates = [
        a for a in local_ipv4s()
        if not a.startswith("169.254.") and (configured_is_virtual or not virtual(a))
    ]
    if not candidates:
        return None

    outbound = outbound_ipv4()
    prefix = ".".join(configured_host.split(".")[:3]) + "." if configured_host and \
        configured_host.count(".") == 3 else None

    def rank(addr):
        if prefix and addr.startswith(prefix):
            return 0
        if addr == outbound:
            return 1
        if addr.startswith("192.168.") or addr.startswith("10."):
            return 2
        return 3

    return sorted(candidates, key=lambda a: (rank(a), a))[0]


def address_check(configured_url):
    """Does the address the device is told to dial still belong to this PC?

    This is the failure that looks like a broken device: the server keeps
    running happily on a host whose IP has changed, and the chatti dials an
    address that no longer exists. Nothing in the stack notices.
    """
    result = {"configured": None, "local": local_ipv4s(), "ok": None,
              "detail": "", "suggested": None}
    if not configured_url:
        return result
    try:
        host = urlparse(configured_url).hostname
    except Exception:
        return result
    result["configured"] = host
    if not host:
        return result

    if host in result["local"]:
        result["ok"] = True
        result["detail"] = f"The Chatti dials {host} — that is this PC."
        return result

    result["ok"] = False
    result["suggested"] = preferred_ipv4(host)
    # 169.254.x.x means the adapter asked for an address and got no answer —
    # the machine is not on the network at all. That is a different fix from a
    # merely outdated address, so say which one it is.
    if any(a.startswith("169.254.") for a in result["local"]):
        result["detail"] = (
            "This PC is not on any network right now (it only has a "
            "169.254 address, so it got no answer from the router). "
            f"The Chatti dials {host} and finds nobody. Connect the Wi-Fi."
        )
    else:
        result["detail"] = (
            f"The Chatti dials {host}, but this PC is reachable at "
            f"{', '.join(result['local']) or 'no address'} — "
            "the address in the configuration is out of date."
        )
    return result


def last_seen():
    """From data/chatti-devices.json, written by the server on every connect."""
    try:
        with open(settings.DEVICE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return None
    best = None
    for device_id, entry in data.items():
        if best is None or (entry.get("last_seen") or 0) > (best.get("last_seen") or 0):
            best = dict(entry, device_id=device_id)
    return best


# monotonic time at which a WebSocket was last seen open.
_last_talking = 0.0
# How long that vouches for "still in a conversation" when the status endpoint
# itself has become too busy to answer. A full round trip is ~45 s measured, so
# 90 s covers a slow one without keeping a finished conversation on screen.
TALKING_MEMORY = 90.0


async def device(configured_ws_url=None):
    """Four different questions, kept apart on purpose:

    * is a WebSocket open right now -> the device is *talking*, not merely on
    * is it powered on at all       -> presence.check(), a ping over the LAN
    * when did it last talk         -> from the persisted device file
    * can it reach us at all        -> address check

    The firmware only opens the channel while a conversation runs, so "no live
    connection" is the normal resting state and must not be reported as a fault
    — and equally must not be dressed up as "connected", which is what the page
    used to do by animating the eyes whenever the *server* was healthy.
    """
    global _last_talking

    live, known, busy = [], False, False
    try:
        r = await _get(f"{settings.XIAOZHI_HTTP_URL}/chatti/status")
        if r.status_code == 200:
            payload = r.json()
            live = payload.get("devices", [])
            known = True
    except httpx.TimeoutException:
        busy = True
    except Exception:
        pass

    talking, stale = bool(live), False
    if talking:
        _last_talking = time.monotonic()
    elif busy and (time.monotonic() - _last_talking) <= TALKING_MEMORY:
        # The endpoint shares its event loop with the conversation it would be
        # reporting on. Too busy to answer, moments after it said "talking", is
        # not the same as "the conversation ended".
        talking, stale = True, True

    if talking:
        presence.note_seen()  # an open channel is proof, no ping needed

    return {
        "known": known,
        "talking": talking,
        "talking_stale": stale,
        "devices": live,
        "last_seen": last_seen(),
        "present": await presence.check(),
        "address": address_check(configured_ws_url),
    }


async def snapshot(configured_ws_url=None, configured_model=None):
    """Everything at once, in parallel. Worst case is one read timeout, not the
    sum of four."""
    docker_state, containers = await docker_overview()
    speaches_c = _container_state(containers, settings.CONTAINER_SPEACHES)
    xiaozhi_c = _container_state(containers, settings.CONTAINER_SERVER)

    if not _running(docker_state):
        # No daemon means no containers; asking their ports would only cost
        # time. LM Studio is independent of Docker, so it is still worth asking.
        # A merely *busy* daemon is a different case: the containers are still
        # there and their ports answer, so fall through to the normal path.
        lm, dev, loaded = await asyncio.gather(
            lmstudio(), device(configured_ws_url), _loaded_models()
        )
        return {
            "docker": docker_state,
            "lmstudio": lm,
            "speaches": _state("off", "Docker off"),
            "xiaozhi": _state("off", "Docker off"),
            "model": await model_state(configured_model, loaded),
            "device": dev,
        }

    lm, sp, xz, dev, loaded = await asyncio.gather(
        lmstudio(),
        speaches(speaches_c),
        xiaozhi(xiaozhi_c),
        device(configured_ws_url),
        _loaded_models(),
    )
    return {
        "docker": docker_state,
        "lmstudio": lm,
        "speaches": sp,
        "xiaozhi": xz,
        "model": await model_state(configured_model, loaded),
        "device": dev,
    }
