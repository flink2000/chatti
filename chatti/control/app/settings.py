"""Every path, port and timeout in one place.

Nothing else in the app hardcodes a location, so moving Docker or renaming a
container is a one-line change here.
"""

import os

# --- our own service -------------------------------------------------------
# Loopback only, on purpose: this app starts containers, rewrites the server
# configuration and flashes firmware. None of that belongs on a network - and a
# laptop's WLAN profile is often "Public" without anyone noticing.
# Binding here is only half the job; main.py also checks Host and Origin,
# because the browser on this machine can reach 127.0.0.1 too.
HOST = "127.0.0.1"
PORT = 8099

# --- the stack we watch ----------------------------------------------------
LMSTUDIO_URL = "http://127.0.0.1:1234"
SPEACHES_URL = "http://127.0.0.1:8100"
XIAOZHI_WS_URL = "http://127.0.0.1:8000"    # plain GET answers "Server is running"
XIAOZHI_HTTP_URL = "http://127.0.0.1:8003"  # OTA + our /chatti/status

CONTAINER_SERVER = "xiaozhi-server"
CONTAINER_SPEACHES = "speaches"

# --- where we are ----------------------------------------------------------
# Everything below is derived from this file's own location, so a clone runs
# wherever it is unpacked. The layout it relies on is fixed by the repository:
#
#   <repo>/chatti/control/app/settings.py   <- this file
#   <repo>/chatti/control/                  = _CONTROL_DIR
#   <repo>/chatti/server/                   = SERVER_DIR
#   <repo>/build/                           = FIRMWARE_BUILD_DIR
_CONTROL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHATTI_DIR = os.path.dirname(_CONTROL_DIR)
REPO_DIR = os.path.dirname(_CHATTI_DIR)

SERVER_DIR = os.path.join(_CHATTI_DIR, "server")

# The whole PC-side stack in one file. Everything goes through compose rather
# than `docker start <name>`: on a machine that has never run Chatti the
# containers do not exist yet, and `docker start` can only start what is
# already there. `compose up -d` creates them if needed and is a no-op if they
# are already running, so one command covers first run and every run after.
COMPOSE_FILE = os.path.join(SERVER_DIR, "docker-compose.yml")
# Generous because the very first run downloads both images (~2 GB). Once they
# are local the same call returns in a second or two.
COMPOSE_TIMEOUT = 1800.0


def _find_docker_desktop() -> str:
    """Where Docker Desktop was installed - which is not a given.

    The default is under Program Files, but the installer lets you move it to
    another drive, and the machine this was written on keeps it on D:. Guessing
    a single path made the panel report Docker as missing on every PC that had
    it somewhere else. Candidates are tried in turn; CHATTI_DOCKER_DESKTOP
    overrides all of them.

    Falls back to the standard location rather than an empty string, so an
    error message names a path a person can act on.
    """
    override = os.environ.get("CHATTI_DOCKER_DESKTOP")
    if override:
        return override
    tail = os.path.join("Docker", "Docker", "Docker Desktop.exe")
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramW6432"),        # 64-bit path seen from 32-bit
        os.environ.get("ProgramFiles(x86)"),
    ]
    candidates = [os.path.join(r, tail) for r in roots if r]
    # Installed onto a data drive: not standard, but common wherever the system
    # disk is small - which is exactly why it sits on D: here.
    candidates += [os.path.join(d + ":" + os.sep, tail) for d in ("D", "E", "F")]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return os.path.join(os.environ.get("ProgramFiles", "C:" + os.sep + "Program Files"), tail)


DOCKER_DESKTOP = _find_docker_desktop()
LMS_EXE = os.path.expandvars(r"%USERPROFILE%\.lmstudio\bin\lms.exe")

# --- files -----------------------------------------------------------------


def _data_dir() -> str:
    """Where the server keeps its runtime state.

    Derived from the compose setup rather than written down twice: CHATTI_DATA
    from .env if it is set, otherwise ./data next to docker-compose.yml. The
    panel and the container therefore cannot disagree about where .config.yaml
    lives — and on a machine that has never run Chatti the default is a
    directory that compose creates by itself.
    """
    env_file = os.path.join(SERVER_DIR, ".env")
    try:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == "CHATTI_DATA" and value.strip():
                    return os.path.normpath(value.strip().strip('"').strip("'"))
    except OSError:
        pass
    return os.path.join(SERVER_DIR, "data")


DATA_DIR = _data_dir()
# The live configuration the container reads. It sits inside a *directory*
# mount (data/), which is why it can be replaced atomically — see config_io.
CONFIG_LIVE = os.path.join(DATA_DIR, ".config.yaml")
# The master copy in the firmware repo. Kept byte-identical to the live file so
# the change shows up in `git diff` and the repo copy cannot go stale. On a
# fresh install it is also the template the live file is seeded from.
CONFIG_MASTER = os.path.join(SERVER_DIR, "config.yaml")
PROMPT_MASTER = os.path.join(SERVER_DIR, "chatti-prompt.txt")
# The language Chatti listens and speaks in. One place, because three different
# things have to agree on it: the voice list this panel offers (catalog.py), the
# voice a fresh install downloads (setup.py DEFAULT_TTS) and the `language` key
# the patched ASR provider sends to Whisper. Whisper mis-detects short
# utterances when it is left to guess, so this is never empty.
SPEECH_LANGUAGE = "en"
# What the master template says the server can be reached at. Deliberately the
# mDNS name and not an IP: the repo copy is what a stranger's first install is
# seeded from, and a hardcoded address there would be the developer's, working
# on exactly one network in the world. announce.py publishes this very name
# (SERVICE_NAME), and the firmware already defaults to it via CONFIG_OTA_URL.
MASTER_WEBSOCKET = "ws://chatti-server.local:8000/xiaozhi/v1/"
MASTER_OTA = "http://chatti-server.local:8003/xiaozhi/ota/"
# Written by chatti_log.py inside the container, visible here through data/.
CONVERSATION_DIR = os.path.join(DATA_DIR, "conversations")
DEVICE_FILE = os.path.join(DATA_DIR, "chatti-devices.json")
# Written only by this app: the LAN address of the device that last answered a
# ping. Kept apart from the file above so there is no writer to race with.
PRESENCE_FILE = os.path.join(DATA_DIR, "chatti-presence.json")

WEB_DIR = os.path.join(_CONTROL_DIR, "web")

# --- flashing --------------------------------------------------------------
# A build directory is the better source when there is one, because its images
# can be written to their own offsets and leave NVS alone. Measured 2026-08-16:
# merged-binary.bin spans 0x0..0x9344A1 and carries 16 KB of 0xFF right across
# the NVS partition at 0x9000, so writing it wipes WLAN credentials and the
# stored server address. Fine for a first install, wrong for an update.
FIRMWARE_BUILD_DIR = os.path.join(REPO_DIR, "build")
# The shipping firmwares, one directory per language, written by
# chatti/build-releases.ps1. build/ only ever holds whichever language was
# compiled last, so it cannot answer "give me the English one" - these can.
FIRMWARE_RELEASES_DIR = os.path.join(REPO_DIR, "releases")
# Which one the panel preselects. English, because it is the one language a
# stranger cloning this repository is certain to read; German is one click away.
FIRMWARE_DEFAULT_LANGUAGE = "en-US"
# The single-file fallback: what a release ships, and all a stranger has. It
# always clears the settings - the UI says so before the button is pressed.
FIRMWARE_CANDIDATES = [
    os.path.join(_CONTROL_DIR, "firmware", "chatti-firmware.bin"),
    os.path.join(FIRMWARE_BUILD_DIR, "merged-binary.bin"),
]
# Espressif's native USB-Serial-JTAG. Verified on this board in session 1:
# VID_303A / PID_1001, no driver needed.
ESP_USB_VID = 0x303A
# Faster than the 460800 the IDF uses by default; the native USB link is not a
# real UART, so the number is close to meaningless — but lower values do slow
# a 9.6 MB image down measurably.
FLASH_BAUD = 921600
# A full image takes ~40 s. Ten times that is not patience, it is "the cable
# was pulled and esptool is still waiting".
FLASH_TIMEOUT = 400

# --- timeouts --------------------------------------------------------------
# Short on purpose. A status page that hangs is worse than one that says
# "don't know" — Speaches in particular can block for minutes behind its
# filelock while a model loads, so we only ever call endpoints that answer
# immediately (/health, /v1/models), never transcription or synthesis.
#
# Not *too* short, though: while a question is being processed, whisper has all
# six cores and the answer to a health check arrives late. A service that is
# genuinely down refuses the connection instantly, so a generous read timeout
# costs nothing in that case — it only buys patience while the machine is busy.
# Everything that still runs into a timeout is reported as "busy", not "off"
# (see services.py).
HTTP_CONNECT_TIMEOUT = 0.6
HTTP_READ_TIMEOUT = 3.0
DOCKER_TIMEOUT = 10.0       # `docker ps` while the daemon boots, or under load
LMS_TIMEOUT = 25.0

# How long each startup step may take before it is called failed.
WAIT_DOCKER_DAEMON = 240
WAIT_LMSTUDIO = 60
WAIT_SPEACHES = 180
WAIT_XIAOZHI = 120
WAIT_RESTART = 90
# Loading 6.33 GB onto a 4 GB card takes well over a minute from cold disk.
WAIT_MODEL_LOAD = 300
