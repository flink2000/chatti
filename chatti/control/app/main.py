"""Chatti Control — one page that starts the stack, shows its state, picks the
models and lists the last conversations.

Loopback only. See settings.py for why.
"""

import asyncio
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import announce, catalog, config_io, conversations, flash, settings, setup, startup
from .services import _client, snapshot
from .services import local_ipv4s as services_local_ipv4s


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Publishing the mDNS record is a convenience, never a precondition: if it
    # fails the panel still works and the device still reaches a hand-typed
    # address. announce.start() swallows its own errors for that reason.
    await announce.start()
    refresher = asyncio.create_task(announce.refresh_forever())
    try:
        yield
    finally:
        refresher.cancel()
        await announce.stop()


app = FastAPI(title="Chatti Control", docs_url=None, redoc_url=None, lifespan=lifespan)
# --- keeping the browser out of the panel ----------------------------------
# Binding to 127.0.0.1 stops other machines. It does not stop the browser on
# THIS machine: any page the user happens to visit can fire requests at
# 127.0.0.1:8099, and without the two checks below they were answered.
#
# 1. Origin. A cross-site POST that needs no preflight (no JSON content type,
#    no custom header) reaches the server regardless of CORS - the browser only
#    hides the *answer*. That was enough to start the whole stack, or restart
#    the server container, from a random web page. Requests without an Origin
#    header are allowed through: those are curl, scripts and the panel's own
#    non-browser callers, none of which a hostile page can forge.
#
# 2. Host. Without this, an attacker's domain can be made to resolve to
#    127.0.0.1 (DNS rebinding), which makes their page same-origin with the
#    panel - and then the answers are readable too, including the conversation
#    transcripts. Checking the Host header costs nothing and removes that.
ALLOWED_HOSTS = {f"127.0.0.1:{settings.PORT}", f"localhost:{settings.PORT}",
                 "127.0.0.1", "localhost"}
ALLOWED_ORIGINS = {f"http://127.0.0.1:{settings.PORT}",
                   f"http://localhost:{settings.PORT}"}


@app.middleware("http")
async def local_only_guard(request, call_next):
    host = (request.headers.get("host") or "").lower()
    if host and host not in ALLOWED_HOSTS:
        # 421 says "you reached the wrong server", which is exactly the case.
        return JSONResponse({"detail": "Invalid host"}, status_code=421)

    origin = request.headers.get("origin")
    if origin and origin.lower() not in ALLOWED_ORIGINS:
        return JSONResponse({"detail": "Foreign origin rejected"}, status_code=403)

    return await call_next(request)


app.mount("/static", StaticFiles(directory=settings.WEB_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(settings.WEB_DIR, "index.html"))


@app.get("/api/status")
async def api_status():
    """Everything the page polls for. Wrapped in a timeout of its own: even if
    a library ignores its own, the page still gets an answer."""
    try:
        config = config_io.read()
    except Exception as e:  # noqa: BLE001 - a broken YAML must not blank the page
        config = {"error": str(e)}

    try:
        # The configured WebSocket address is what the device dials; the status
        # check compares it against this PC's actual addresses.
        services = await asyncio.wait_for(
            snapshot(config.get("websocket"), config.get("llm")), timeout=12.0
        )
    except asyncio.TimeoutError:
        services = {"error": "the status query timed out"}

    return {
        "app": "chatti-control",
        "services": services,
        "config": config,
        "job": startup.job,
        "restart": startup.restart_job,
        "flash": flash.job,
        "announce": announce.status(),
        "conversations": conversations.stats(),
    }


class ServiceAction(BaseModel):
    action: str = "start"  # "start" or "stop"


@app.post("/api/startup")
async def api_startup(body: ServiceAction | None = None):
    """The whole stack, in dependency order — or back down again, in reverse.

    Only ever from the button; nothing on this server starts anything by itself.
    The body is optional so an older page, which posted nothing at all, still
    means "start" instead of getting a 422.
    """
    action = (body.action if body else "start")
    if action not in ("start", "stop"):
        raise HTTPException(status_code=400, detail="action must be start or stop")
    if not startup.start(mode=action):
        raise HTTPException(status_code=409, detail="An operation is already running")
    return {"started": True, "job": startup.job}


@app.post("/api/service/{key}")
async def api_service(key: str, body: ServiceAction):
    """One service on its own. Same job machinery as /api/startup, so the page
    shows the progress in the same row and a second click gets a 409."""
    if key not in startup.KEYS:
        raise HTTPException(status_code=404, detail=f"Unknown service: {key}")
    if body.action not in ("start", "stop"):
        raise HTTPException(status_code=400, detail="action must be start or stop")
    if not startup.start([key], body.action):
        raise HTTPException(status_code=409, detail="An operation is already running")
    return {"started": True, "job": startup.job}


@app.post("/api/restart")
async def api_restart():
    if not startup.restart():
        raise HTTPException(status_code=409, detail="A restart is already running")
    return {"started": True}


@app.get("/api/catalog")
async def api_catalog():
    """Not part of the poll — fetched once when the page loads."""
    try:
        llms, (voices, asr) = await asyncio.wait_for(
            asyncio.gather(catalog.llms(), catalog.voices_and_asr()), timeout=15.0
        )
    except asyncio.TimeoutError:
        llms, voices, asr = [], [], []
    return {"llms": llms, "voices": voices, "asr": asr}


class Selection(BaseModel):
    llm: str | None = None
    tts_model: str | None = None
    tts_voice: str | None = None
    asr: str | None = None
    prompt: str | None = None
    restart: bool = True


@app.post("/api/selection")
async def api_selection(sel: Selection):
    try:
        changed = await config_io.write(
            llm=sel.llm, tts_model=sel.tts_model, tts_voice=sel.tts_voice,
            asr=sel.asr, prompt=sel.prompt,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Configuration not written: {e}")

    # A new LLM is only half applied by writing it down: LM Studio loads models
    # on demand, so without this the *first question* would trigger the load and
    # run past the device's 120 s patience. config_io reports the key it wrote.
    load_model = "LLM.model_name" in changed

    restarted = False
    if changed and sel.restart:
        restarted = startup.restart(load_model)
    return {"changed": changed, "restarting": restarted, "loading_model": load_model}


class AddressFix(BaseModel):
    host: str


@app.post("/api/fix-address")
async def api_fix_address(body: AddressFix):
    """Point the server at this PC's current address and restart it.

    Only offered when the configured address does not belong to this machine.
    The suggested host comes from the status call, but it is validated again
    here — the panel must not be talked into writing an arbitrary address.
    """
    if body.host not in services_local_ipv4s():
        raise HTTPException(
            status_code=400,
            detail=f"{body.host} is not an address of this PC.",
        )
    try:
        changed = await config_io.set_server_host(body.host)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Not written: {e}")

    restarted = startup.restart() if changed else False
    return {"changed": changed, "restarting": restarted}


@app.post("/api/tts-preview")
async def api_tts_preview(body: dict):
    """Proxy the sample through this app: the browser cannot call Speaches
    directly (different origin) and we want the 24 kHz rate enforced in one
    place."""
    model, voice = body.get("model"), body.get("voice")
    if not model:
        raise HTTPException(status_code=400, detail="model is missing")
    spec = await catalog.preview_url(model, voice)
    try:
        # Synthesis is slow-ish and may wait on a model load, so this one call
        # gets a longer leash than the status checks.
        r = await _client.post(spec["url"], json=spec["body"], timeout=90.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Voice sample failed: {e}")
    return Response(content=r.content, media_type="audio/wav")


@app.get("/api/setup")
async def api_setup():
    """The first-run checklist. Not part of the 3-second poll: it shells out to
    docker and netsh, which is far too heavy to repeat that often."""
    try:
        return await asyncio.wait_for(setup.checklist(), timeout=45.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="The check took too long")


class SetupFix(BaseModel):
    action: str


@app.post("/api/setup/fix")
async def api_setup_fix(body: SetupFix):
    started, why = setup.start(body.action)
    if not started:
        raise HTTPException(status_code=409, detail=why)
    return {"started": True, "job": setup.job}


@app.get("/api/flash")
async def api_flash_info():
    """Everything the flashing panel needs before it can offer the button:
    which ports exist, and which firmwares there are to write.

    "firmware" is the default variant, kept so the page has something to show
    before anything is picked; "variants" is the full list behind the dropdown.
    """
    default = flash.default_variant_id()
    return {"ports": await flash.ports(), "firmware": flash.firmware_info(default),
            "variants": flash.variants(), "default_variant": default,
            "job": flash.job}


class FlashRequest(BaseModel):
    port: str
    # Empty means "whatever the panel would have preselected", so an older
    # page that does not know about variants still flashes the default.
    variant: str = ""


@app.post("/api/flash")
async def api_flash(body: FlashRequest):
    # The port is checked against the list the app itself found, so a stray
    # request cannot make esptool poke at an arbitrary device.
    known = {p["port"] for p in await flash.ports()}
    if body.port not in known:
        raise HTTPException(status_code=400, detail=f"{body.port} is not an existing port.")

    started, why = flash.start(body.port, body.variant)
    if not started:
        raise HTTPException(status_code=409, detail=why)
    return {"started": True, "job": flash.job}


@app.get("/api/conversations")
async def api_conversations(limit: int = 20):
    try:
        return JSONResponse(await asyncio.to_thread(conversations.recent_cached, limit))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))
