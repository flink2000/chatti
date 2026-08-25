"""Windows wrapper around scripts/build.py.

Why this exists
---------------
scripts/build.py invokes the ESP-IDF frontend as a bare executable:

    subprocess.run(["idf.py", ...])

That works on Linux/macOS, where idf.py is a shebang script marked executable.
On Windows it fails with:

    OSError: [WinError 193] %1 is not a valid Win32 application

because idf.py is a plain Python script. CreateProcess looks for the exact name
"idf.py", finds the script in $IDF_PATH/tools, and refuses to execute it. The
official Windows shim is named idf.py.exe — cmd.exe finds it through PATHEXT,
but subprocess does not, since it never appends extensions to a name that
already has one. In the PowerShell environment idf.py is merely a shell
function, which subprocess cannot see either.

Rather than patching scripts/build.py (an upstream file that changes often and
would conflict on every `git merge upstream/main`), this wrapper redirects those
calls to the current Python interpreter and then runs build.py unmodified.

Usage — from anywhere, inside an initialized ESP-IDF environment:

    . chatti\\idf-init.ps1
    python chatti\\build-win.py waveshare/esp32-s3-touch-lcd-1.83 --language de-DE

All arguments are passed through to scripts/build.py verbatim.
"""

import os
import runpy
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_PY = REPO_ROOT / "scripts" / "build.py"

idf_path = os.environ.get("IDF_PATH")
if not idf_path:
    sys.exit(
        "IDF_PATH is not set. Initialize the ESP-IDF environment first:\n"
        "    . chatti\\idf-init.ps1"
    )

IDF_PY = Path(idf_path) / "tools" / "idf.py"
if not IDF_PY.is_file():
    sys.exit(f"idf.py not found at {IDF_PY}")

_original_run = subprocess.run


def _run(command, *args, **kwargs):
    """Rewrite ["idf.py", ...] into [sys.executable, "<IDF_PATH>/tools/idf.py", ...]."""
    if isinstance(command, (list, tuple)) and command and command[0] == "idf.py":
        command = [sys.executable, str(IDF_PY), *command[1:]]
    return _original_run(command, *args, **kwargs)


subprocess.run = _run

# build.py resolves paths like "main/CMakeLists.txt" relative to the working
# directory, so it must run from the repository root.
os.chdir(REPO_ROOT)

sys.argv = [str(BUILD_PY), *sys.argv[1:]]
runpy.run_path(str(BUILD_PY), run_name="__main__")
