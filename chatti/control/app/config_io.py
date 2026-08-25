"""Read and write data/.config.yaml without destroying it.

That file earns its keep through its comments — the reasons behind
`max_tokens`, `sample_rate`, `language` are written down nowhere else. PyYAML
would drop every one of them on the first dump, so this module uses ruamel in
round-trip mode.

`width` is the setting that matters most: without it ruamel re-wraps long lines
at column 80, and a one-value change shows up as twenty changed lines in the
diff.
"""

import asyncio
import io
import os
import shutil
from urllib.parse import urlparse, urlunparse

from ruamel.yaml import YAML

from . import settings

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096

# Two clicks must not overtake each other mid-write.
_lock = asyncio.Lock()

# Which provider section holds what we edit. Read from `selected_module` rather
# than hardcoded, so switching provider in the YAML does not make this app edit
# the wrong block.
_DEFAULTS = {"LLM": "LMStudioLLM", "TTS": "SpeachesTTS", "ASR": "SpeachesASR"}


def _load():
    with open(settings.CONFIG_LIVE, "r", encoding="utf-8") as f:
        return _yaml.load(f)


def _provider(doc, kind):
    return (doc.get("selected_module") or {}).get(kind) or _DEFAULTS[kind]


def read():
    """Current selection, plus the provider names it was read from."""
    doc = _load()
    llm_p, tts_p, asr_p = (_provider(doc, k) for k in ("LLM", "TTS", "ASR"))
    llm = (doc.get("LLM") or {}).get(llm_p) or {}
    tts = (doc.get("TTS") or {}).get(tts_p) or {}
    asr = (doc.get("ASR") or {}).get(asr_p) or {}
    return {
        "llm": llm.get("model_name"),
        "tts_model": tts.get("model"),
        "tts_voice": tts.get("voice"),
        "asr": asr.get("model_name"),
        "prompt": doc.get("prompt"),
        "providers": {"LLM": llm_p, "TTS": tts_p, "ASR": asr_p},
        # The address the device is told to dial. Checked against this PC's
        # real addresses — a stale one here looks exactly like a dead device.
        "websocket": (doc.get("server") or {}).get("websocket"),
        "ota": (doc.get("server") or {}).get("ota"),
    }


async def set_server_host(new_host):
    """Point `server.websocket` and `server.ota` at a different address.

    Only the host is replaced; ports and paths stay exactly as they were, so a
    non-standard port or path survives this.

    Note what this does *not* fix: the device stores its OTA address in NVS, and
    that is what it dials first. If the PC's address changed, that stored value
    is stale too and has to be re-entered on the device itself (WLAN config
    mode). This only repairs the server side.
    """
    async with _lock:
        return await asyncio.to_thread(_set_host_sync, new_host)


def _replace_host(url, new_host):
    if not url:
        return url, False
    parsed = urlparse(url)
    if not parsed.hostname or parsed.hostname == new_host:
        return url, False
    netloc = new_host if parsed.port is None else f"{new_host}:{parsed.port}"
    if parsed.username:
        auth = parsed.username + (f":{parsed.password}" if parsed.password else "")
        netloc = f"{auth}@{netloc}"
    return urlunparse(parsed._replace(netloc=netloc)), True


def _set_host_sync(new_host):
    doc = _load()
    server = doc.get("server")
    if server is None:
        return []

    changed = []
    for key in ("websocket", "ota"):
        new_url, did = _replace_host(server.get(key), new_host)
        if did:
            server[key] = new_url
            # Plain ASCII: this string also travels through logs and consoles
            # that are not UTF-8 on this machine.
            changed.append(f"server.{key} = {new_url}")

    if changed:
        _persist(doc)
    return changed


async def write(*, llm=None, tts_model=None, tts_voice=None, asr=None, prompt=None):
    """Apply the given values. Everything else in the file stays untouched,
    comments and key order included.

    Returns the list of keys that actually changed — an empty list means there
    is nothing to restart for.
    """
    async with _lock:
        return await asyncio.to_thread(
            _write_sync, llm, tts_model, tts_voice, asr, prompt
        )


def _write_sync(llm, tts_model, tts_voice, asr, prompt):
    doc = _load()
    changed = []

    def setval(section, kind, key, value):
        if value is None:
            return
        block = (doc.get(section) or {}).get(_provider(doc, kind))
        if block is None:
            return
        if block.get(key) != value:
            block[key] = value
            changed.append(f"{section}.{key}")

    setval("LLM", "LLM", "model_name", llm)
    setval("TTS", "TTS", "model", tts_model)
    setval("TTS", "TTS", "voice", tts_voice)
    setval("ASR", "ASR", "model_name", asr)

    if prompt is not None and str(doc.get("prompt", "")).strip() != prompt.strip():
        # Keep the block-literal style the file uses; ruamel decides that from
        # the string itself, so a trailing newline is what keeps `prompt: |`.
        doc["prompt"] = prompt.strip() + "\n"
        changed.append("prompt")

    if not changed:
        return []

    _persist(doc)
    return changed


def _persist(doc):
    """Write the document back: backup, atomic replace, mirror to the repo.

    Atomic replace is safe here **only** because .config.yaml lives inside a
    *directory* mount — doing this to one of the single-file mounts
    (asr_openai.py and friends) would sever the mount instead.
    """
    buf = io.StringIO()
    _yaml.dump(doc, buf)
    data = buf.getvalue()

    if os.path.exists(settings.CONFIG_LIVE):
        shutil.copy2(settings.CONFIG_LIVE, settings.CONFIG_LIVE + ".bak")
    tmp = settings.CONFIG_LIVE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(data)
    os.replace(tmp, settings.CONFIG_LIVE)

    # Keep the repo master in step, so a setting changed here lands in
    # `git diff` and the master cannot quietly go stale.
    try:
        _write_master(data)
    except Exception:
        pass  # the live file is written; a stale master must never break a save


def _write_master(data: str) -> None:
    """Update the repo copy - as a *template*, not as a byte-for-byte backup.

    Everything the user chose (model, voice, prompt, timeouts) is carried over
    verbatim, because that is what makes the master useful in a diff. The two
    server addresses are not: the master is what a fresh install is seeded from
    (setup.py), and this machine's LAN address works on exactly one network in
    the world. They are written as the mDNS name that announce.py publishes.

    Round-tripped through ruamel rather than string-replaced, so the comments
    that explain every value survive - they are the reason this file is worth
    keeping in git at all.
    """
    doc = _yaml.load(data)
    server = doc.get("server")
    if server is not None:
        if "websocket" in server:
            server["websocket"] = settings.MASTER_WEBSOCKET
        if "ota" in server:
            server["ota"] = settings.MASTER_OTA
    buf = io.StringIO()
    _yaml.dump(doc, buf)
    with open(settings.CONFIG_MASTER, "w", encoding="utf-8", newline="\n") as f:
        f.write(buf.getvalue())
