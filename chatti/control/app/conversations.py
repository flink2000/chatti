"""Read the transcript written by chatti_log.py in the container.

The writer stays dumb — append-only, no state. All grouping happens here, so
the rules can change without touching the container.

A conversation is: same session id, and no more than 30 minutes since the
previous turn. The session id alone is not enough, because it lives as long as
the WebSocket connection does and a device left switched on keeps it for hours.
"""

import json
import os
import time

from . import settings

GAP_SECONDS = 30 * 60
MAX_FILES = 14  # two weeks back is plenty for "the last conversations"


def _read_lines(limit_files=MAX_FILES):
    try:
        names = sorted(
            (e.name for e in os.scandir(settings.CONVERSATION_DIR)
             if e.is_file() and e.name.endswith(".jsonl")),
            reverse=True,
        )
    except FileNotFoundError:
        return []

    rows = []
    for name in names[:limit_files]:
        path = os.path.join(settings.CONVERSATION_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        # A half-written last line is the normal case when the
                        # container is stopped mid-write. Skip it silently.
                        continue
        except OSError:
            continue
    return rows


def recent(limit=20):
    rows = [r for r in _read_lines() if r.get("text")]
    rows.sort(key=lambda r: r.get("ts", 0))

    groups = []
    for row in rows:
        ts = row.get("ts", 0)
        session = row.get("session")
        if (groups
                and groups[-1]["session"] == session
                and ts - groups[-1]["ended"] <= GAP_SECONDS):
            g = groups[-1]
        else:
            g = {"session": session, "device": row.get("device"),
                 "started": ts, "ended": ts, "turns": []}
            groups.append(g)
        g["ended"] = ts
        g["turns"].append({
            "ts": ts,
            "role": row.get("role"),
            "text": row.get("text"),
            "duration_ms": row.get("duration_ms"),
        })

    for g in groups:
        first_user = next((t["text"] for t in g["turns"] if t["role"] == "user"), None)
        title = first_user or (g["turns"][0]["text"] if g["turns"] else "")
        g["title"] = title[:70] + ("…" if len(title) > 70 else "")
        g["turn_count"] = len(g["turns"])

    groups.reverse()  # newest first
    return groups[:limit]


def stats():
    try:
        files = [e for e in os.scandir(settings.CONVERSATION_DIR)
                 if e.is_file() and e.name.endswith(".jsonl")]
        return {"files": len(files), "bytes": sum(e.stat().st_size for e in files)}
    except FileNotFoundError:
        return {"files": 0, "bytes": 0}


def touched_at():
    """Newest mtime across the transcript files, for a cheap cache check."""
    try:
        return max((e.stat().st_mtime for e in os.scandir(settings.CONVERSATION_DIR)
                    if e.is_file()), default=0.0)
    except FileNotFoundError:
        return 0.0


_cache = {"mtime": -1.0, "limit": -1, "value": None, "at": 0.0}


def recent_cached(limit=20):
    m = touched_at()
    if _cache["value"] is not None and _cache["mtime"] == m and _cache["limit"] == limit:
        return _cache["value"]
    value = recent(limit)
    _cache.update({"mtime": m, "limit": limit, "value": value, "at": time.time()})
    return value
