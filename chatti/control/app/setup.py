"""First-run checklist: not "what is running" but "what is still missing".

The status panel next to this one answers whether the stack is up. That is the
wrong question on a machine where Chatti has never run: there the containers do
not exist, .config.yaml has never been written, no speech model has been
downloaded and the firewall has never been asked. "Start everything" cannot help
with any of those.

So every check here comes with the action that fixes it, and the actions are
the ones nobody should have to look up in a README.

Deliberately *not* automated: installing Docker Desktop and LM Studio (both are
interactive installers), and the firewall rule (needs elevation - we print the
exact command instead of asking for admin rights the panel does not need for
anything else).
"""

import asyncio
import os
import shutil
import sys

import httpx

from . import config_io, flash, proc, settings
from .services import _client

# Used when .config.yaml has nothing to say yet, i.e. on a fresh install.
# Both are the ones the project settled on; see CLAUDE.md. The Whisper models
# are multilingual, so only the voice is tied to settings.SPEECH_LANGUAGE.
DEFAULT_ASR = "Systran/faster-whisper-medium"
DEFAULT_TTS = "speaches-ai/piper-en_US-lessac-high"

# Ports the device dials from the LAN. The WLAN profile on the dev machine is
# "Public", where Windows blocks inbound by default.
INBOUND_PORTS = [8000, 8003]
FIREWALL_RULE = "Chatti (ESP32)"

# Separate rule, because it protects something else: the device's multicast
# query for the server. Windows ships allow-rules for UDP 5353, but they are
# bound to svchost.exe (its own responder) - verified 2026-08-16. Our Python
# process has none, so the query never reaches it and discovery silently fails.
MDNS_RULE = "Chatti mDNS"
MDNS_PORT = 5353

# Measured on 2026-08-16: `docker info` takes ~3.5 s idle and 7.8 s while the
# machine is busy compiling. The 10 s used elsewhere is fine for a status poll
# that may say "don't know", but here a timeout would offer a button that
# starts an already running Docker. Ask patiently instead, and never conclude
# "not installed" from silence.
DOCKER_PROBE_TIMEOUT = 40.0

job = {"state": "idle", "action": "", "detail": "", "error": None}


def _item(key, label, ok, detail, fix=None, hint="", optional=False):
    """`optional` items are shown but do not hold "ready" back — used where the
    check can only prove the good case, never the bad one."""
    return {"key": key, "label": label, "ok": ok, "detail": detail,
            "fix": fix, "hint": hint, "optional": optional}


async def _docker_state():
    """(state, detail) with state one of ok | busy | off.

    Three outcomes, not two: a daemon that is merely slow must not be reported
    as missing, or the checklist offers to start what is already running.
    """
    r = await proc.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                       DOCKER_PROBE_TIMEOUT)
    if r.ok:
        return "ok", f"Version {r.out.strip()}"
    if r.rc == -1 and "timed out" in (r.err or ""):
        return "busy", "not answering right now — the system is busy"
    return "off", (r.err or r.out or "the daemon is not answering")[:120]


async def _containers():
    """Which of our two services exist at all, and do they run."""
    r = await proc.run(
        ["docker", "compose", "-f", settings.COMPOSE_FILE, "ps",
         "--all", "--format", "{{.Service}}={{.State}}"],
        DOCKER_PROBE_TIMEOUT)
    found = {}
    if r.ok:
        for line in r.out.splitlines():
            if "=" in line:
                name, _, state = line.partition("=")
                found[name.strip()] = state.strip()
    return found


async def _speaches_models():
    try:
        r = await _client.get(f"{settings.SPEACHES_URL}/v1/models")
        if r.status_code != 200:
            return None
        return {m.get("id") for m in r.json().get("data", [])}
    except (httpx.HTTPError, OSError):
        return None


async def _lmstudio_models():
    try:
        r = await _client.get(f"{settings.LMSTUDIO_URL}/v1/models")
        if r.status_code != 200:
            return None
        return [m.get("id") for m in r.json().get("data", [])]
    except (httpx.HTTPError, OSError):
        return None


async def _rule_exists(name: str, needle: str):
    """Does a named inbound rule exist and mention `needle`?

    `netsh` is read-only here; adding a rule needs elevation and is left to the
    user with a copyable command - this panel needs no admin rights for
    anything else and should not start asking for them.
    """
    r = await proc.run(
        ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
        DOCKER_PROBE_TIMEOUT)
    return r.ok and needle in r.out


def _wanted_models():
    try:
        cfg = config_io.read()
    except Exception:  # noqa: BLE001 - a broken config must not blank the checklist
        cfg = {}
    return (cfg.get("asr") or DEFAULT_ASR, cfg.get("tts_model") or DEFAULT_TTS)


async def checklist() -> dict:
    """Everything that must be true before a stranger can talk to the device."""
    items = []

    state, detail = await _docker_state()
    ok = state == "ok"
    items.append(_item(
        "docker", "Docker running", ok, detail,
        # No button while it is merely busy — it is running, just slow.
        fix="docker" if state == "off" else None,
        hint="Docker Desktop has to be installed." if state == "off" else ""))

    if state != "off":
        found = await _containers()
        have = [s for s in ("speaches", "xiaozhi-server") if s in found]
        all_there = len(have) == 2
        items.append(_item(
            "containers", "Containers created", all_there,
            ", ".join(f"{k}: {v}" for k, v in found.items()) or "none",
            fix=None if all_there else "containers",
            hint="The first time, about 2 GB of images are downloaded."))
    else:
        items.append(_item("containers", "Containers created", False,
                           "unknown, Docker is not running", fix=None))

    have_cfg = os.path.isfile(settings.CONFIG_LIVE)
    items.append(_item(
        "config", "Configuration present", have_cfg,
        settings.CONFIG_LIVE if have_cfg else "not created yet",
        fix=None if have_cfg else "config",
        hint="Created from the template in the repo."))

    asr, tts = _wanted_models()
    installed = await _speaches_models()
    if installed is None:
        items.append(_item("models", "Speech models downloaded", False,
                           "Speaches is not answering", fix=None,
                           hint="Start the containers first."))
    else:
        missing = [m for m in (asr, tts) if m not in installed]
        items.append(_item(
            "models", "Speech models downloaded", not missing,
            "complete" if not missing else "missing: " + ", ".join(missing),
            fix=None if not missing else "models",
            hint="Recognition and voice, a few minutes depending on the connection."))

    llms = await _lmstudio_models()
    items.append(_item(
        "llm", "Language model (LM Studio)", bool(llms),
        f"{len(llms)} model(s)" if llms else "LM Studio is not answering",
        fix=None,
        hint="" if llms else "Start LM Studio and load at least one model."))

    # Advisory only. This can prove the good case (our rule exists) but not the
    # bad one: the ports may well be reachable through some other rule or
    # profile — on this machine they were, long before any rule of ours. Letting
    # it block "ready" would leave a working install permanently marked broken.
    # Without this the device cannot find the server by itself: its multicast
    # query never reaches our process. Advisory rather than blocking, because a
    # hand-typed server address works without it.
    mdns = await _rule_exists(MDNS_RULE, str(MDNS_PORT))
    items.append(_item(
        "mdns", "Automatic discovery", mdns,
        f"immediate — rule “{MDNS_RULE}” exists" if mdns
        else "works without a rule, may take a few attempts",
        fix=None, optional=True,
        hint="" if mdns else (
            "The server calls its address out onto the network every 10 seconds by "
            "itself and the device listens in — nothing else is needed for that.\n"
            "Windows only lets a direct query through with a rule of its own. To have "
            "discovery work immediately instead of after a few attempts, run this once "
            "as an administrator:\n"
            f'netsh advfirewall firewall add rule name="{MDNS_RULE}" dir=in '
            f'action=allow protocol=UDP localport={MDNS_PORT} '
            f'program="{sys.executable}"')))

    fw = await _rule_exists(FIREWALL_RULE, "8000")
    ports = ",".join(str(p) for p in INBOUND_PORTS)
    items.append(_item(
        "firewall", "Firewall", fw,
        f"own rule “{FIREWALL_RULE}” exists" if fw
        else "no rule of our own — access may well be allowed anyway",
        fix=None, optional=True,
        hint="" if fw else (
            "If the device cannot reach the server, run this once as an "
            "administrator:\n"
            f'netsh advfirewall firewall add rule name="{FIREWALL_RULE}" '
            f"dir=in action=allow protocol=TCP localport={ports}")))

    fw_info = flash.firmware_info()
    ports_found = await flash.ports()
    esp = [p for p in ports_found if p["is_esp"]]
    items.append(_item(
        "device", "Chatti connected", bool(esp) and fw_info["found"],
        (f"{esp[0]['port']} detected" if esp else "no device on USB")
        + (", firmware ready" if fw_info["found"] else ", no firmware file"),
        fix=None,
        hint="Use the “Device” section to flash it."))

    open_items = [i for i in items if not i["ok"] and not i["optional"]]
    return {"items": items, "ready": not open_items, "open": len(open_items),
            "job": job}


# --- the fixes -------------------------------------------------------------

async def _fix_docker():
    if not os.path.exists(settings.DOCKER_DESKTOP):
        raise RuntimeError(f"Docker Desktop not found at {settings.DOCKER_DESKTOP}")
    proc.spawn([settings.DOCKER_DESKTOP])
    job["detail"] = "Docker Desktop is starting, that takes about a minute."


async def _fix_containers():
    job["detail"] = "downloading images and creating containers"
    r = await proc.run(
        ["docker", "compose", "-f", settings.COMPOSE_FILE, "up", "-d"],
        settings.COMPOSE_TIMEOUT)
    if not r.ok:
        raise RuntimeError((r.err or r.out or "docker compose failed")[:300])
    job["detail"] = "containers running"


async def _fix_config():
    """Seed the live configuration from the copy in the repo.

    Only ever creates; an existing .config.yaml is never touched - it holds
    choices someone made, and the comments explaining them.
    """
    if os.path.isfile(settings.CONFIG_LIVE):
        job["detail"] = "already existed, nothing changed"
        return
    if not os.path.isfile(settings.CONFIG_MASTER):
        raise RuntimeError(f"template missing: {settings.CONFIG_MASTER}")

    os.makedirs(settings.DATA_DIR, exist_ok=True)
    await asyncio.to_thread(shutil.copyfile, settings.CONFIG_MASTER, settings.CONFIG_LIVE)

    prompt_target = os.path.join(settings.DATA_DIR, os.path.basename(settings.PROMPT_MASTER))
    if os.path.isfile(settings.PROMPT_MASTER) and not os.path.isfile(prompt_target):
        await asyncio.to_thread(shutil.copyfile, settings.PROMPT_MASTER, prompt_target)
    job["detail"] = f"created: {settings.CONFIG_LIVE}"


async def _fix_models():
    """Ask Speaches to download what the configuration asks for.

    POST /v1/models/{id} is the download; it blocks until the model is on disk,
    which is why this gets its own long timeout rather than the status one.
    """
    asr, tts = _wanted_models()
    installed = await _speaches_models() or set()
    for model in (asr, tts):
        if model in installed:
            continue
        job["detail"] = f"downloading {model}"
        try:
            r = await _client.post(f"{settings.SPEACHES_URL}/v1/models/{model}",
                                   timeout=1800.0)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(f"{model} not downloaded: {e}")
    job["detail"] = "models present"


_FIXES = {
    "docker": _fix_docker,
    "containers": _fix_containers,
    "config": _fix_config,
    "models": _fix_models,
}


def start(action: str) -> tuple[bool, str]:
    if job["state"] == "running":
        return False, f"Already running: {job['action']}"
    fn = _FIXES.get(action)
    if fn is None:
        return False, f"Unknown action: {action}"

    job.update({"state": "running", "action": action, "detail": "", "error": None})

    async def _run():
        try:
            await fn()
            job["state"] = "done"
        except Exception as e:  # noqa: BLE001
            job["state"] = "failed"
            job["error"] = str(e)

    asyncio.get_running_loop().create_task(_run())
    return True, ""
