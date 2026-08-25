"""Starting and stopping the stack — all of it at once, or one service alone.

Nothing in here ever runs by itself. Every job needs a button press: opening the
page must not start containers, load a 6 GB model or wake Docker Desktop, and
until 2026-08-16 the desktop shortcut did exactly that (`start_stack.py` was
called from chatti-control.cmd). Starting a service is a decision, not a side
effect of looking at its state.

The same job structure serves both directions and both scopes: a job carries a
`mode` ("start" or "stop") and one step per service it touches. "Start everything"
is the job with all five steps, a single row's button the job with one — so the
page, the 409-guard against a second run and the progress display all work
without knowing which of the two it is looking at.

The job state lives in a module global. uvicorn runs single-worker here, so
that is safe — and it means a second browser tab sees the same progress
instead of starting a second run.
"""

import asyncio
import os
import time

import httpx

from . import proc, settings
from .services import _client

_STEPS = [
    ("docker", "Docker Desktop"),
    ("lmstudio", "LM Studio"),
    ("speaches", "Speech (Speaches)"),
    ("xiaozhi", "Chatti server"),
    ("model", "Load language model"),
]

KEYS = [k for k, _ in _STEPS]

# How long the loaded model stays resident without being used. LM Studio's own
# default is an hour; after that the first conversation would pay the loading
# time again and run into the device's 120 s timeout. A day means "until the PC
# is restarted", which is what we want for a device that sits on the desk.
_MODEL_TTL_SECONDS = 86400

# Shutting Docker Desktop down takes noticeably longer than any `docker` call:
# it stops both containers and then the VM behind them.
_STOP_DOCKER_TIMEOUT = 180.0
_COMPOSE_STOP_TIMEOUT = 90.0

job = {"state": "idle", "mode": "start", "started_at": 0.0, "steps": [], "error": None}


def _reset(keys, mode):
    job["steps"] = [{"key": k, "label": l, "state": "pending", "detail": ""}
                    for k, l in _STEPS if k in keys]
    job["state"] = "running"
    job["mode"] = mode
    job["started_at"] = time.time()
    job["error"] = None


def _step(key):
    return next(s for s in job["steps"] if s["key"] == key)


def _has(key):
    return any(s["key"] == key for s in job["steps"])


def _set(key, state, detail=""):
    s = _step(key)
    s["state"] = state
    s["detail"] = detail


def _fail(key, detail, error=None):
    """Mark one step failed and give the job a headline. Returns False so a step
    function can `return _fail(...)` in one line."""
    _set(key, "failed", detail)
    if error and not job["error"]:
        job["error"] = error
    return False


async def _wait_for(check, timeout, key, waiting_text):
    """Poll `check` until it returns True. Reports remaining seconds so the
    page can show something moving during a two-minute wait."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if await check():
                return True
        except Exception:
            pass
        left = int(deadline - time.time())
        _set(key, "running", f"{waiting_text} ({left} s left)")
        await asyncio.sleep(2)
    return False


async def compose_up(service: str):
    """Bring one service up: create it if it does not exist, start it if it is
    stopped, do nothing if it already runs.

    This replaced `docker start <name>`, which cannot help anyone who has never
    run Chatti before - there is nothing to start yet. The compose file is also
    the only place the bind mounts live now, so a container created this way can
    never be missing a patch.
    """
    return await proc.run(
        ["docker", "compose", "-f", settings.COMPOSE_FILE, "up", "-d", service],
        settings.COMPOSE_TIMEOUT,
    )


async def compose_stop(service: str):
    """Stop one container without removing it. `stop`, never `down`: `down`
    removes the containers, and a removed container is the one thing that can
    silently lose a bind mount (see chatti/server/README.md)."""
    return await proc.run(
        ["docker", "compose", "-f", settings.COMPOSE_FILE, "stop", service],
        _COMPOSE_STOP_TIMEOUT,
    )


async def _docker_up():
    r = await proc.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                       settings.DOCKER_TIMEOUT)
    return r.ok


async def _http_ok(url, expect=None):
    try:
        r = await _client.get(url)
        if r.status_code != 200:
            return False
        return expect is None or r.text.startswith(expect)
    except (httpx.HTTPError, OSError):
        return False


async def _docker_available(key):
    """A container step started on its own cannot assume the daemon is up.

    Only checked when this job does not start Docker itself — inside the full
    sequence the previous step already proved it, and `docker info` costs up to
    several seconds on a loaded machine.
    """
    if _has("docker") or await _docker_up():
        return True
    return _fail(key, "Docker Desktop is not running",
                 "Docker Desktop is not running — start that one first")


# --- starting --------------------------------------------------------------

async def _start_docker():
    # The daemon needs roughly a minute from cold.
    _set("docker", "running", "checking")
    if await _docker_up():
        _set("docker", "ok", "was already running")
        return True
    try:
        proc.spawn([settings.DOCKER_DESKTOP])
    except Exception as e:  # noqa: BLE001
        return _fail("docker", f"start failed: {e}",
                     "Docker Desktop could not be started")
    if not await _wait_for(_docker_up, settings.WAIT_DOCKER_DAEMON,
                           "docker", "daemon is coming up"):
        return _fail("docker", "daemon did not come up",
                     "the Docker daemon is not answering")
    _set("docker", "ok", "running")
    return True


async def _start_lmstudio():
    # Missing lms.exe is not fatal for the rest, and neither is an API that
    # stays quiet: everything except the model step works without LM Studio.
    _set("lmstudio", "running", "starting")
    if not os.path.exists(settings.LMS_EXE):
        _set("lmstudio", "skipped", "lms.exe not found")
        return True
    if await _http_ok(f"{settings.LMSTUDIO_URL}/v1/models"):
        _set("lmstudio", "ok", "was already running")
        return True
    await proc.run([settings.LMS_EXE, "server", "start"], settings.LMS_TIMEOUT)
    if await _wait_for(lambda: _http_ok(f"{settings.LMSTUDIO_URL}/v1/models"),
                       settings.WAIT_LMSTUDIO, "lmstudio", "waiting for the API"):
        _set("lmstudio", "ok", "running")
    else:
        _set("lmstudio", "failed", "the API is not answering")
    return True


async def _start_speaches():
    # First start after a reboot loads the models, that takes a while.
    _set("speaches", "running", "starting the container")
    if not await _docker_available("speaches"):
        return False
    r = await compose_up(settings.CONTAINER_SPEACHES)
    if not r.ok:
        return _fail("speaches", r.err[:160] or "docker start failed",
                     "Speaches could not be started")
    if not await _wait_for(lambda: _http_ok(f"{settings.SPEACHES_URL}/health"),
                           settings.WAIT_SPEACHES, "speaches", "loading models"):
        return _fail("speaches", "not answering", "Speaches did not become ready")
    _set("speaches", "ok", "ready")
    return True


async def _start_xiaozhi():
    _set("xiaozhi", "running", "starting the container")
    if not await _docker_available("xiaozhi"):
        return False
    r = await compose_up(settings.CONTAINER_SERVER)
    if not r.ok:
        return _fail("xiaozhi", r.err[:160] or "docker start failed",
                     "the Chatti server could not be started")
    if not await _wait_for(
            lambda: _http_ok(f"{settings.XIAOZHI_WS_URL}/", "Server is running"),
            settings.WAIT_XIAOZHI, "xiaozhi", "starting"):
        return _fail("xiaozhi", "not answering", "the Chatti server did not become ready")
    _set("xiaozhi", "ok", "ready")
    return True


async def _start_model():
    # Preload the language model. Without this the *question* triggers the load,
    # and on 2026-08-15 that cost the whole conversation: the firmware hung up
    # 120 s after connecting while LM Studio was still reading 6.33 GB. Last in
    # the sequence on purpose — everything else is already usable while this runs.
    await _preload_model()
    return True


# --- stopping --------------------------------------------------------------

async def _stop_docker():
    """Stopping Docker Desktop takes both containers with it — which is the
    point of the button, and why the other two rows go dark right after."""
    _set("docker", "running", "checking")
    if not await _docker_up():
        _set("docker", "off", "was not running")
        return True
    _set("docker", "running", "shutting down")
    r = await proc.run(["docker", "desktop", "stop"], _STOP_DOCKER_TIMEOUT)
    if not r.ok:
        return _fail("docker", r.err[:160] or "shutdown failed",
                     "Docker Desktop could not be shut down")
    _set("docker", "off", "stopped")
    return True


async def _stop_lmstudio():
    _set("lmstudio", "running", "shutting down")
    if not os.path.exists(settings.LMS_EXE):
        _set("lmstudio", "skipped", "lms.exe not found")
        return True
    r = await proc.run([settings.LMS_EXE, "server", "stop"], settings.LMS_TIMEOUT)
    if not r.ok:
        return _fail("lmstudio", (r.err or r.out or "shutdown failed")[:160],
                     "LM Studio could not be shut down")
    # Only the API server is stopped here, not the LM Studio window: `lms` has
    # no command for the app itself, and closing someone's open program from a
    # web page would be a step too far.
    _set("lmstudio", "off", "server stopped")
    return True


async def _stop_container(key, name, label):
    _set(key, "running", "stopping")
    if not await _docker_up():
        # No daemon means the container is not running either. Nothing to do —
        # reporting that as a failure would send someone looking for a problem
        # that does not exist.
        _set(key, "off", "Docker is not running")
        return True
    r = await compose_stop(name)
    if not r.ok:
        return _fail(key, r.err[:160] or "stop failed",
                     f"{label} could not be stopped")
    _set(key, "off", "stopped")
    return True


async def _stop_speaches():
    return await _stop_container("speaches", settings.CONTAINER_SPEACHES, "Speaches")


async def _stop_xiaozhi():
    return await _stop_container("xiaozhi", settings.CONTAINER_SERVER, "the Chatti server")


async def _stop_model():
    """Unload the configured model to give the 4 GB card back.

    By identifier, never `lms unload --all`: LM Studio may well hold a model
    somebody loaded for something else, and this button is about Chatti's.
    """
    from . import config_io

    _set("model", "running", "checking")
    if not os.path.exists(settings.LMS_EXE):
        _set("model", "skipped", "lms.exe not found")
        return True
    try:
        model = config_io.read().get("llm")
    except Exception as e:  # noqa: BLE001
        _set("model", "skipped", f"configuration not readable: {e}")
        return True
    if not model:
        _set("model", "skipped", "no model configured")
        return True
    if not await _model_loaded(model):
        _set("model", "off", "was not loaded")
        return True

    _set("model", "running", f"unloading {model}")
    r = await proc.run([settings.LMS_EXE, "unload", model], settings.LMS_TIMEOUT)
    if await _model_loaded(model):
        return _fail("model", (r.err or r.out or "unload failed")[:160],
                     "the model could not be unloaded")
    _set("model", "off", "unloaded")
    return True


_STARTERS = {
    "docker": _start_docker,
    "lmstudio": _start_lmstudio,
    "speaches": _start_speaches,
    "xiaozhi": _start_xiaozhi,
    "model": _start_model,
}

_STOPPERS = {
    "docker": _stop_docker,
    "lmstudio": _stop_lmstudio,
    "speaches": _stop_speaches,
    "xiaozhi": _stop_xiaozhi,
    "model": _stop_model,
}


async def run_job(keys, mode):
    table = _STARTERS if mode == "start" else _STOPPERS
    try:
        for key in keys:
            if not await table[key]():
                job["state"] = "failed"
                if not job["error"]:
                    step = _step(key)
                    job["error"] = f"{step['label']}: {step['detail']}".strip(": ")
                return
        # A step may fail without stopping the chain — LM Studio is deliberately
        # not fatal for the rest. The job still has to say so.
        broken = next((s for s in job["steps"] if s["state"] == "failed"), None)
        if broken:
            job["state"] = "failed"
            if not job["error"]:
                job["error"] = f"{broken['label']}: {broken['detail']}"
        else:
            job["state"] = "done"
    except Exception as e:  # noqa: BLE001
        job["state"] = "failed"
        job["error"] = f"unexpected error: {e}"


async def _model_loaded(model_id):
    """Is exactly this model resident? `lms ps` would do too, but the HTTP API
    answers in milliseconds and needs no subprocess."""
    try:
        r = await _client.get(f"{settings.LMSTUDIO_URL}/api/v0/models")
        if r.status_code != 200:
            return False
        return any(m.get("id") == model_id and m.get("state") == "loaded"
                   for m in r.json().get("data", []))
    except (httpx.HTTPError, OSError):
        return False


async def _preload_model(report=None):
    """Load the configured LLM into memory so the first question does not.

    Deliberately does not unload anything else: on 4 GB of VRAM LM Studio
    decides the split between GPU and CPU itself, and second-guessing that from
    outside is how you end up with a model that no longer fits at all.

    `report(state, detail)` is where the progress goes. It defaults to the
    startup job's own step, but the same routine also runs after a model change
    was applied — see _run_restart — and reports into that job instead.
    """
    from . import config_io

    if report is None:
        def report(state, detail=""):
            _set("model", state, detail)

    report("running", "checking")
    try:
        model = config_io.read().get("llm")
    except Exception as e:  # noqa: BLE001
        report("skipped", f"configuration not readable: {e}")
        return

    if not model:
        report("skipped", "no model configured")
        return
    if not os.path.exists(settings.LMS_EXE):
        report("skipped", "lms.exe not found")
        return
    if await _model_loaded(model):
        report("ok", f"{model} was already loaded")
        return

    report("running", f"loading {model} (takes 1–2 minutes)")
    r = await proc.run(
        [settings.LMS_EXE, "load", model, "-y", "--ttl", str(_MODEL_TTL_SECONDS)],
        settings.WAIT_MODEL_LOAD,
    )
    if await _model_loaded(model):
        report("ok", f"{model} loaded")
        return
    if r.rc == -1:
        report("failed", "timed out while loading")
    else:
        report("failed", (r.err or r.out or "load failed")[:160])


def start(keys=None, mode="start") -> bool:
    """Kick off a job. `keys=None` means the whole stack.

    Returns False if a run is already in progress. Check and set happen without
    an await in between, so no lock is needed.
    """
    if job["state"] == "running":
        return False
    # Filtered through KEYS rather than used as given: this keeps the steps in
    # their dependency order no matter what the caller passed.
    chosen = list(KEYS) if keys is None else [k for k in KEYS if k in keys]
    if not chosen:
        return False
    # Stopping runs the dependency order backwards: the model goes before LM
    # Studio, the containers before the daemon that hosts them.
    if mode == "stop":
        chosen.reverse()
    _reset(chosen, mode)
    asyncio.create_task(run_job(chosen, mode))
    return True


# --- restarting the server after a settings change -------------------------

restart_job = {"state": "idle", "detail": "", "at": 0.0}


async def _run_restart(load_model=False):
    restart_job.update({"state": "running", "detail": "container is restarting",
                        "at": time.time()})
    r = await proc.run(["docker", "restart", settings.CONTAINER_SERVER],
                       settings.DOCKER_TIMEOUT + 20)
    if not r.ok:
        restart_job.update({"state": "failed", "detail": r.err[:160] or "error"})
        return

    deadline = time.time() + settings.WAIT_RESTART
    ready = False
    while time.time() < deadline:
        if await _http_ok(f"{settings.XIAOZHI_WS_URL}/", "Server is running"):
            ready = True
            break
        await asyncio.sleep(2)
    if not ready:
        restart_job.update({"state": "failed", "detail": "did not come back"})
        return

    if not load_model:
        restart_job.update({"state": "done", "detail": "ready"})
        return

    # A newly chosen model is not resident yet, and leaving that to the first
    # question is what cost a whole conversation on 2026-08-15: the firmware
    # hangs up 120 s after connecting, while a cold 6.33 GB load takes longer.
    # So the restart is not finished until the model can actually answer.
    outcome = {"state": "ok", "detail": ""}

    def report(state, detail=""):
        outcome.update(state=state, detail=detail)
        if detail:
            restart_job["detail"] = detail

    await _preload_model(report)
    if outcome["state"] == "failed":
        restart_job.update({"state": "failed", "detail": outcome["detail"]})
    else:
        restart_job.update({"state": "done",
                            "detail": outcome["detail"] or "ready"})


def restart(load_model=False) -> bool:
    if restart_job["state"] == "running":
        return False
    asyncio.create_task(_run_restart(load_model))
    return True
