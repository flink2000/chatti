"""Is something listening on a port? Used by chatti-control.cmd.

    python waitport.py 8099          -> exit 0 if listening right now
    python waitport.py 8099 --wait   -> wait up to 20 s for it, then exit 0/1

Kept as its own file because the same check inline in a .cmd is unreadable.
"""

import socket
import sys
import time


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    port = int(sys.argv[1])
    if "--wait" not in sys.argv:
        return 0 if listening(port) else 1
    deadline = time.time() + 20
    while time.time() < deadline:
        if listening(port):
            return 0
        time.sleep(0.4)
    return 1


if __name__ == "__main__":
    sys.exit(main())
