"""chatti patch: append every conversation turn to a file on the host.

Nothing upstream keeps a transcript. `Memory` is set to `nomem`, the dialogue
lives only in the RAM of one WebSocket connection (core/connection.py), and the
built-in report path (core/handle/reportHandle.py) returns immediately unless a
manager-api is configured. So after the device disconnects the conversation is
gone. Our control panel wants to show the last conversations, hence this file.

It is mounted into the container as core/utils/chatti_log.py and called from
core/handle/sendAudioHandle.py — that file is already a chatti patch and both
directions pass through it, so no additional upstream file has to be mounted.

Design notes:

* Written into data/conversations/, and data/ is already a *directory* mount.
  A new conversation file therefore appears on the host without touching the
  container's mount list, and `docker rm` cannot lose the transcripts.
* One file per day, one JSON object per line. Not one file per conversation
  (hundreds of tiny files) and not one growing file (has to be read whole).
* `ts` is epoch seconds and is the authoritative timestamp. The container very
  likely runs on UTC, so any local time written here would be off by hours.
  Formatting for humans is the browser's job. `iso` is UTC and exists only so
  the file stays readable with a text editor; nothing parses it.
* Every failure is swallowed. A broken transcript writer must never keep the
  device from answering — same rule as the duration_ms patch.
"""

import json
import os
import time

TAG = __name__

# Inside the container. data/ is the host mount, see module docstring.
_LOG_DIR = "/opt/xiaozhi-esp32-server/data/conversations"


_DEVICE_FILE = "/opt/xiaozhi-esp32-server/data/chatti-devices.json"


def remember_device(device_id, client_ip):
    """Note that this device was here, with its address and the time.

    The firmware only opens the WebSocket while a conversation is running
    (application.cc: HandleToggleChatEvent -> ContinueOpenAudioChannel, closed
    again afterwards). Between conversations there is nothing to observe, so
    "is the chatti connected?" cannot be answered from live connections alone —
    it would read "no" almost always. This file is what lets the control panel
    say "last seen 5 minutes ago" and ping the device's address instead.

    Persisted rather than kept in memory so it survives a container restart.
    Never raises.
    """
    try:
        if not device_id:
            return
        data = {}
        if os.path.exists(_DEVICE_FILE):
            with open(_DEVICE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        entry = data.get(device_id) or {}
        entry["ip"] = client_ip
        entry["last_seen"] = round(time.time(), 3)
        entry["first_seen"] = entry.get("first_seen", entry["last_seen"])
        data[device_id] = entry
        tmp = _DEVICE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _DEVICE_FILE)
    except Exception:
        pass


def log_turn(conn, role, text, duration_ms=None):
    """Append one conversation turn. Never raises.

    conn:        the ConnectionHandler, for device_id / session_id
    role:        "user" or "assistant"
    text:        what was said
    duration_ms: speaking length, assistant turns only (measured in tts/base.py)
    """
    try:
        if not text or not str(text).strip():
            return

        os.makedirs(_LOG_DIR, exist_ok=True)
        now = time.time()
        entry = {
            "ts": round(now, 3),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "session": getattr(conn, "session_id", None),
            "device": getattr(conn, "device_id", None),
            "role": role,
            "text": str(text).strip(),
        }
        if duration_ms:
            entry["duration_ms"] = int(duration_ms)

        path = os.path.join(_LOG_DIR, time.strftime("%Y-%m-%d", time.gmtime(now)) + ".jsonl")
        # One write() per line: a single append below 4 KB from a single process
        # is effectively atomic, so no lock is needed.
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        try:
            conn.logger.bind(tag=TAG).warning(f"chatti_log failed, turn not recorded: {e}")
        except Exception:
            pass
