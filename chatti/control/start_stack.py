"""Kick off the startup sequence from the command line.

**Not wired into anything.** chatti-control.cmd used to call this, which meant
opening the panel started Docker, both containers and a 6 GB model whether you
wanted them or not. Starting the stack is a decision, so it now happens only
from the buttons on the page — this file stays for the case where somebody
deliberately wants it automated (a scheduled task, a login script).

Idempotent: if the stack is already up, every step reports "was already running"; if a
run is already going, the service answers 409 and this exits quietly.

    python start_stack.py 8099
"""

import json
import sys
import urllib.error
import urllib.request


def main() -> int:
    port = int(sys.argv[1])
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/startup", method="POST"
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            json.load(response)
        print("Startup is running, the progress is shown on the page.")
        return 0
    except urllib.error.HTTPError as e:
        if e.code == 409:
            print("Startup is already running.")
            return 0
        print(f"Startup rejected: HTTP {e.code}")
        return 1
    except Exception as e:  # noqa: BLE001 - the page still opens either way
        print(f"Startup not triggered: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
