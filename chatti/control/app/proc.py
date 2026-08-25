"""Subprocess calls with a hard timeout.

Deliberately `subprocess.run` in a worker thread rather than
`asyncio.create_subprocess_exec`: on Windows the asyncio variant only works on
the Proactor event loop, and which loop uvicorn installs depends on its
version. The thread pool sidesteps that question entirely and brings
`timeout=` along for free.

Every command in this app goes through here — nothing else may spawn a
process, so there is exactly one place where a hanging `docker` can be killed.
"""

import asyncio
import subprocess
import time
from dataclasses import dataclass


@dataclass
class Result:
    rc: int          # -1 means the call timed out and the process was killed
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


def _run_sync(args, timeout: float) -> Result:
    try:
        p = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            # No console window flashing up for every `docker ps`.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return Result(p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip())
    except subprocess.TimeoutExpired:
        return Result(-1, "", f"timed out after {timeout:.0f}s")
    except FileNotFoundError:
        return Result(-1, "", f"not found: {args[0]}")
    except Exception as e:  # noqa: BLE001 - a status page must never crash
        return Result(-1, "", str(e))


async def run(args, timeout: float) -> Result:
    return await asyncio.to_thread(_run_sync, args, timeout)


def stream(args, on_line, timeout: float, cwd: str | None = None) -> Result:
    """Run a command and hand every output line to `on_line` as it arrives.

    Blocking, meant to be called from a worker thread. `run()` above collects
    output only at the end, which is fine for `docker ps` but useless for a
    40-second flash where the whole point is watching the percentage move.

    stderr is folded into stdout because esptool writes its progress there and
    its errors here, and the caller wants both in order.
    """
    lines: list[str] = []
    try:
        p = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=cwd,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        return Result(-1, "", f"not found: {args[0]}")
    except Exception as e:  # noqa: BLE001
        return Result(-1, "", str(e))

    deadline = time.monotonic() + timeout
    try:
        for raw in p.stdout:  # type: ignore[union-attr]
            line = raw.rstrip("\r\n")
            if line:
                lines.append(line)
                try:
                    on_line(line)
                except Exception:  # noqa: BLE001 - a bad callback must not kill the flash
                    pass
            if time.monotonic() > deadline:
                p.kill()
                return Result(-1, "\n".join(lines), f"timed out after {timeout:.0f}s")
        rc = p.wait(timeout=max(1.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        p.kill()
        return Result(-1, "\n".join(lines), f"timed out after {timeout:.0f}s")
    except Exception as e:  # noqa: BLE001
        p.kill()
        return Result(-1, "\n".join(lines), str(e))

    return Result(rc, "\n".join(lines), "")


def spawn(args) -> None:
    """Start a program and do not wait for it (Docker Desktop, which runs for
    the rest of the session)."""
    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
