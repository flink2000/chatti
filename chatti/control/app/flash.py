"""Put the firmware on the device from the control panel.

Why this exists: installing ESP-IDF is 7.8 GB and the single biggest hurdle for
anyone who just wants to run Chatti. Flashing itself needs none of it — one
image file at address 0x0 and esptool, which is a pip package.

Why esptool on the host and not WebSerial in the browser: WebSerial would mean
vendoring a JavaScript port of esptool, and this app is meant to work with no
network at all. esptool is already a dependency of the toolchain we trust, it
runs offline, and it comes with pyserial — which is also how the port list
below knows which COM port is an ESP32 and which is a Bluetooth dongle.

The device does *not* have to be in download mode: the ESP32-S3's USB-Serial-
JTAG puts the chip there by itself when esptool toggles DTR/RTS. That stops
working the day the firmware takes the USB pins for something else (see the M7
section in CLAUDE.md); until then, plugging the cable in is enough.
"""

import asyncio
import json
import os
import re
import sys
import time

from . import proc, settings

# Same shape as startup.job so the page can render both with one function.
job = {
    "state": "idle",       # idle | running | done | failed
    "started_at": 0.0,
    "port": "",
    "variant": "",       # which firmware, so the page can say what it wrote
    "label": "",
    "percent": 0,
    "detail": "",
    "error": None,
    "log": [],
}

_MAX_LOG = 200

# Two shapes in the wild, and it costs nothing to accept both: esptool 4 wrote
# "Writing at 0x0002c000... (17 %)", esptool 5.3 draws a bar and writes
# "[====>  ]  98.5% 2539520/2577550 bytes...". Measured on 2026-08-16 - the
# parenthesised form alone left the progress sitting at 0 % through a whole
# flash. The percentage is the only part worth showing; the addresses mean
# nothing to someone who just bought the board.
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


# --- what can be flashed ---------------------------------------------------
# Three sources, offered in this order:
#
#   releases/<dir>/  one finished firmware per language, written by
#                    chatti/build-releases.ps1. This is why the panel has a
#                    dropdown at all: the language is compiled in, so build/
#                    can only ever hold whichever one was built last.
#   build/           the workbench. Only listed when a build exists, and named
#                    after the language its own sdkconfig says it carries.
#   a single .bin    what a release ships and all a stranger has. Writes over
#                    NVS, so it comes last.
#
# All three answer the same shape, so the page renders them with one function.

_MANIFEST = "chatti-firmware.json"
BUILD_VARIANT_ID = "build"
FILE_VARIANT_ID = "file"


def _images_from(args_file: str) -> list[str]:
    """The file names listed in flash_args - lines are '<offset> <file>'."""
    names = []
    try:
        with open(args_file, encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2 and parts[0].startswith("0x"):
                    names.append(parts[1])
    except OSError:
        pass
    return names


def _size_of(directory: str, images: list[str]) -> int:
    return sum(os.path.getsize(os.path.join(directory, f))
               for f in images
               if os.path.isfile(os.path.join(directory, f)))


def _built_at(directory: str, args_file: str) -> float:
    """When the app image was linked - the one date a person cares about."""
    app = os.path.join(directory, "xiaozhi.bin")
    try:
        return os.path.getmtime(app if os.path.isfile(app) else args_file)
    except OSError:
        return 0.0


def _language_of_build() -> str:
    """Which language build/ was last compiled with.

    Read out of the build's own sdkconfig rather than remembered from the last
    build command: the build directory outlives this process, and a wrong
    language on a flash button is worse than none at all.
    """
    path = os.path.join(settings.FIRMWARE_BUILD_DIR, "config", "sdkconfig.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return ""
    for key, value in cfg.items():
        if key.startswith("LANGUAGE_") and value is True:
            parts = key[len("LANGUAGE_"):].split("_")
            if len(parts) == 2:
                return f"{parts[0].lower()}-{parts[1].upper()}"
    return ""


def _release_variant(path: str) -> dict | None:
    """One directory under releases/, or None if it holds no flashable build."""
    args = os.path.join(path, "flash_args")
    if not os.path.isfile(args):
        return None

    meta = {}
    try:
        with open(os.path.join(path, _MANIFEST), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        # A directory without a manifest still flashes, it only reads worse.
        pass

    name = meta.get("name") or os.path.basename(path)
    label = meta.get("label")
    version = meta.get("version")
    title = f"{label} - {name}" if label else name
    if version:
        title += f" V {version}"

    return {"id": os.path.basename(path), "label": title,
            "language": meta.get("language", ""), "found": True,
            "path": args, "size": _size_of(path, _images_from(args)),
            "built_at": _built_at(path, args), "keeps_settings": True,
            "_dir": path}


def _catalog() -> list[dict]:
    """Every flashable firmware, best first.

    Carries private keys; variants() is what leaves the process.
    """
    found = []

    releases = settings.FIRMWARE_RELEASES_DIR
    if os.path.isdir(releases):
        for entry in sorted(os.listdir(releases)):
            v = _release_variant(os.path.join(releases, entry))
            if v:
                found.append(v)
        # The default language first, the rest in the order they sorted. Only
        # the order is settled here; the preselection is default_variant_id().
        found.sort(key=lambda v: v["language"] != settings.FIRMWARE_DEFAULT_LANGUAGE)

    build_args = os.path.join(settings.FIRMWARE_BUILD_DIR, "flash_args")
    if os.path.isfile(build_args):
        language = _language_of_build()
        found.append({
            "id": BUILD_VARIANT_ID,
            "label": f"Current build ({language})" if language else "Current build",
            "language": language, "found": True, "path": build_args,
            "size": _size_of(settings.FIRMWARE_BUILD_DIR, _images_from(build_args)),
            "built_at": _built_at(settings.FIRMWARE_BUILD_DIR, build_args),
            "keeps_settings": True,
            "_dir": settings.FIRMWARE_BUILD_DIR,
        })

    for p in settings.FIRMWARE_CANDIDATES:
        if os.path.isfile(p):
            st = os.stat(p)
            found.append({
                "id": FILE_VARIANT_ID,
                "label": f"Single image ({os.path.basename(p)})",
                "language": "", "found": True, "path": p, "size": st.st_size,
                "built_at": st.st_mtime, "keeps_settings": False, "_dir": None,
            })
            break

    return found


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if not k.startswith("_")}


def variants() -> list[dict]:
    """The catalog as the page sees it, without the private keys."""
    return [_public(entry) for entry in _catalog()]


def default_variant_id() -> str:
    """What the dropdown preselects: the default language if it was built,
    otherwise simply the best entry there is."""
    catalog = _catalog()
    for entry in catalog:
        if entry["language"] == settings.FIRMWARE_DEFAULT_LANGUAGE:
            return entry["id"]
    return catalog[0]["id"] if catalog else ""


def firmware_info(variant_id: str = "") -> dict:
    """What would be written, and whether it keeps the device's settings."""
    wanted = variant_id or default_variant_id()
    for entry in _catalog():
        if entry["id"] == wanted:
            return _public(entry)
    return {"id": "", "label": "", "language": "", "found": False,
            "path": "", "size": 0, "built_at": 0.0, "keeps_settings": False}


def _list_ports_sync() -> list[dict]:
    try:
        from serial.tools import list_ports
    except ImportError:
        # esptool pulls pyserial in; if it is missing the whole feature is off.
        return []

    found = []
    for p in list_ports.comports():
        is_esp = p.vid == settings.ESP_USB_VID
        found.append({
            "port": p.device,
            "description": p.description or "",
            "is_esp": is_esp,
            # USB-Serial-JTAG reports no serial number on some revisions, so
            # this is for display only and never used to pick a port.
            "serial": p.serial_number or "",
        })
    # Chatti first, then everything else, each alphabetically.
    found.sort(key=lambda d: (not d["is_esp"], d["port"]))
    return found


async def ports() -> list[dict]:
    return await asyncio.to_thread(_list_ports_sync)


def _log(line: str) -> None:
    job["log"].append(line)
    if len(job["log"]) > _MAX_LOG:
        del job["log"][:-_MAX_LOG]


def _on_line(line: str) -> None:
    _log(line)

    # esptool counts from zero again for every image, so a straight reading of
    # its percentage makes the bar jump backwards four times (seen on the device
    # 2026-08-16: 5 -> 100 -> 26 -> 76 -> 100). Fold the per-image figure into a
    # single run across all of them.
    if line.startswith("Wrote "):
        job["_done_images"] = job.get("_done_images", 0) + 1

    m = _PERCENT.search(line)
    if m:
        total = max(1, job.get("_total_images", 1))
        done = job.get("_done_images", 0)
        current = min(100.0, float(m.group(1)))
        job["percent"] = min(100, int((done * 100 + current) / total))
        job["detail"] = (f"writing the firmware ({min(done + 1, total)}/{total})"
                         if total > 1 else "writing the firmware")
        return
    low = line.lower()
    if "connecting" in low:
        job["detail"] = "connecting to the chip"
    elif low.startswith("chip is"):
        # Kept because it proves the right board answered. The "Features:" line
        # right after it is three times as long and says nothing a user needs.
        job["detail"] = line.strip()[:60]
    elif "hash of data verified" in low:
        job["detail"] = "written and verified"
    elif "hard resetting" in low:
        job["percent"] = 100
        job["detail"] = "restarting"


def _flash_sync(port: str, image: str, cwd: str | None) -> None:
    # `python -m esptool` rather than the esptool.exe shim: the shim only exists
    # if the venv's Scripts directory is on PATH, and sys.executable is always
    # the interpreter this app is actually running under.
    args = [
        sys.executable, "-m", "esptool",
        "--chip", "esp32s3",
        "--port", port,
        "--baud", str(settings.FLASH_BAUD),
        "--before", "default-reset",
        "--after", "hard-reset",
        "write-flash",
    ]
    if cwd:
        # Every image at its own offset, straight from the build's own argument
        # file. The gap where NVS lives is simply not written, so WLAN and the
        # server address survive the update.
        args.append("@" + os.path.basename(image))
    else:
        args += ["--flash-mode", "dio", "--flash-size", "16MB",
                 "--flash-freq", "80m", "0x0", image]

    _log("$ " + " ".join(args[1:]))
    r = proc.stream(args, _on_line, settings.FLASH_TIMEOUT, cwd=cwd)

    if r.ok:
        job["percent"] = 100
        job["state"] = "done"
        job["detail"] = "done — the Chatti is restarting"
        return

    job["state"] = "failed"
    if r.rc == -1 and "timed out" in (r.err or ""):
        job["error"] = ("Timed out. Is the cable still plugged in? "
                        "Otherwise hold BOOT, plug the cable back in, release BOOT.")
    elif "could not open" in r.out.lower() or "access is denied" in r.out.lower():
        job["error"] = (f"{port} cannot be opened. Is a serial capture or a monitor "
                        "still running on this port?")
    elif "no serial data" in r.out.lower() or "failed to connect" in r.out.lower():
        job["error"] = ("The chip is not answering. Hold BOOT, plug the cable back "
                        "in, release BOOT — then try again.")
    else:
        tail = [l for l in job["log"][-6:] if l.strip()]
        job["error"] = r.err or ("\n".join(tail) if tail else f"esptool exited with code {r.rc}")


def start(port: str, variant_id: str = "") -> tuple[bool, str]:
    """Kick off a flash of one variant. Returns (started, reason-if-not)."""
    if job["state"] == "running":
        return False, "A flash is already running."

    catalog = _catalog()
    if not catalog:
        return False, ("No firmware found. Build one with "
                       "chatti" + os.sep + "build-releases.ps1, or put an image at "
                       + " or ".join(settings.FIRMWARE_CANDIDATES))

    wanted = variant_id or default_variant_id()
    entry = next((e for e in catalog if e["id"] == wanted), None)
    if entry is None:
        return False, f"{wanted} is not one of the firmwares on offer."
    if not port:
        return False, "No port selected."

    # A directory means the build's own images, each at its own offset, which
    # leaves the NVS gap unwritten; a bare file is the whole flash at 0x0.
    image, cwd = entry["path"], entry["_dir"]
    total_images = len(_images_from(image)) if cwd else 1
    job.update({"state": "running", "started_at": time.time(), "port": port,
                "percent": 0, "detail": "esptool is starting", "error": None, "log": [],
                "variant": entry["id"], "label": entry["label"],
                "_total_images": total_images, "_done_images": 0})

    async def _run():
        try:
            await asyncio.to_thread(_flash_sync, port, image, cwd)
        except Exception as e:  # noqa: BLE001 - the panel must survive anything here
            job["state"] = "failed"
            job["error"] = str(e)

    asyncio.get_running_loop().create_task(_run())
    return True, ""
