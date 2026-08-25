# Chatti Control

One page in the browser that sets the whole thing up, starts it, shows what is
running, puts firmware on the device, picks model and voice, and lists the last
conversations.

```
Browser (127.0.0.1:8099)
   │  polls /api/status every 3 s
   ▼
Chatti Control  (FastAPI on the host)
   ├── docker compose up -d / stop / ps      containers speaches, xiaozhi-server
   ├── docker desktop start / stop           the daemon itself
   ├── lms.exe server start / stop           LM Studio :1234
   ├── lms.exe load / unload                 the LLM in and out of memory
   ├── GET :1234/api/v0/models               LLM list
   ├── GET :8100/v1/models                   voices and Whisper models
   ├── GET :8003/chatti/status               is the device connected?
   ├── ruamel.yaml round-trip                data/.config.yaml
   ├── data/conversations/*.jsonl            transcript
   ├── python -m esptool write-flash         firmware onto the device (COM port)
   └── zeroconf _chatti._tcp                 announces the server on the LAN
```

Three of those exist so that someone else can run this without reading a word
of documentation:

- **Setup checklist** (`setup.py`, `/api/setup`) — not "what is running" but
  "what is still missing": containers not created yet, no `.config.yaml`, no
  speech model downloaded, firewall never asked. Each item comes with the
  action that fixes it. Not part of the 3 s poll; it shells out to docker and
  netsh and takes seconds.
- **Flashing** (`flash.py`, `/api/flash`) — `esptool` from this app's venv, so
  nobody needs the 7.8 GB ESP-IDF toolchain to get firmware onto a board. Host
  side rather than WebSerial in the browser: no vendored JavaScript, works with
  no network at all, and pyserial comes along to tell an ESP32 (USB VID
  `0x303A`) from a Bluetooth dongle.
- **Announcement** (`announce.py`) — publishes `_chatti._tcp` so the device can
  ask the network where the server is instead of having an address typed into
  it. Advertises the OTA port; the protocol is unchanged, this only sits in
  front of it.

Paths are not hardcoded: `settings._data_dir()` reads `CHATTI_DATA` from
`../server/.env` and otherwise uses `../server/data`, the same rule the compose
file follows. Panel and container therefore cannot disagree about where
`.config.yaml` lives.

It runs **on the host, not in a container**: it has to start Docker Desktop and
LM Studio, and from inside a container that only works through the Docker
socket and a chain of workarounds.

## Starting it

```
setup.cmd            once, creates .venv and puts the shortcut on the desktop
chatti-control.cmd   every time — what that shortcut points at
create-shortcut.ps1  makes the shortcut again, should it get lost
```

The shortcut is not checked in: a `.lnk` stores an absolute path, so a
committed one would point at whatever machine it was made on. `setup.cmd`
generates it from wherever the clone sits.

The second double-click does not start a second service; it notices the port is
taken and only opens the browser. **The minimised console window is the off
switch** — closing it stops the service. That is why it runs `python.exe` and
not `pythonw.exe`.

**Neither the shortcut nor the page starts anything by itself.** Opening Chatti
Control gives you a status page and nothing more: no container is created, no
Docker Desktop wakes up, no 6 GB model is pulled into memory. The stack comes up
when you press a button — *Alles starten* for the whole chain in dependency
order, or the button on a single row. Every row also stops again, so the machine
can be given its GPU and its six cores back without closing the panel. Once all
five rows are up that same big button reads *Alles stoppen* and takes the chain
back down in reverse order; it drops the inviting primary colour while it does,
because shutting the stack down should not be the most tempting thing on screen.

Until 2026-08-16 the shortcut called `start_stack.py` and did start everything.
That file is still there for anyone who deliberately wants it automated (a
scheduled task, a login script) — it is simply not wired to anything.

Bound to `127.0.0.1` on purpose: this app starts containers and rewrites the
server configuration, and the WLAN profile on this machine is "Public".

## Starting and stopping services

One job structure serves both directions and both scopes (`startup.py`): a job
carries a `mode` and one step per service it touches. *Alles starten* is the job
with five steps, a row's button the job with one — so progress display, the
409 against a second run and the "is anything busy" check need no special case.
Only one job at a time; a second request is refused rather than queued.

| Row | Start | Stop |
|---|---|---|
| Docker | `Docker Desktop.exe`, then wait for `docker info` | `docker desktop stop` — takes both containers with it |
| LM Studio | `lms server start` | `lms server stop` — the API server, not the window: `lms` has no command for the app, and closing someone's open program from a web page is a step too far |
| Sprache / Chatti-Server | `docker compose up -d <service>` | `docker compose stop <service>` — never `down`, which removes the container and is the one operation that can lose a bind mount |

The Docker row reports **our two containers**, not how many exist. `docker ps -a`
has to ask for all of them so the two rows below can tell "no such container"
from "container is stopped" — but counting that list made the row read
"8 Container" on a machine that also hosts an unrelated stopped stack, which
reads as if Chatti had eight. It has two (`_ours_summary` in `services.py`).
| Sprachmodell | `lms load --ttl 86400` | `lms unload <id>` — by identifier, never `--all`: LM Studio may hold a model loaded for something else |

Starting runs in dependency order, stopping runs it backwards. A container
started on its own first checks that the daemon is up and says so plainly
instead of letting `compose` fail with a Windows pipe error.

⚠️ **A stopped container is not a busy one.** After `compose stop`,
`127.0.0.1:8100` keeps *accepting* connections for minutes — Docker Desktop's
port proxy lingers — so the health check times out instead of being refused.
`services.py` used to read that timeout together with its "seen working
recently" memory and report *"erkennt oder spricht gerade"* about a container
the user had just switched off, for the full five minutes of `OK_MEMORY`. The
rule now: **`docker ps` is the authority for a containerised service**; if it
says the container is not running, the port is not asked at all.

## What it changes, and what happens then

| Setting | Key in `data/.config.yaml` | Takes effect |
|---|---|---|
| Sprachmodell | `LLM.<provider>.model_name` | after the server restart |
| Stimme | `TTS.<provider>.model` + `.voice` | after the server restart, model loads on first use |
| Spracherkennung | `ASR.<provider>.model_name` | after the server restart, model loads on first use |
| Anweisung | `prompt` | after the server restart |

Provider names are read from `selected_module`, not hardcoded — switching
provider in the YAML does not make the panel edit the wrong block.

Saving writes the file, copies it over the repo master
(`chatti/server/config.yaml`, kept byte-identical) and runs
`docker restart xiaozhi-server`, roughly 15 s. A conversation in flight is cut off.

**Why a restart at all:** the server reads its configuration exactly once at
start and caches it (`config/config_loader.py`). The WebSocket `update_config`
message is not an alternative — it goes through `get_config_from_api_async`,
which requires a manager-api we deliberately do not run, and its handler
returns immediately unless `read_config_from_api` is set.

## Editing the YAML is safe

`ruamel.yaml` in round-trip mode with `width = 4096`. Without that width ruamel
re-wraps long lines at column 80 and a one-value change shows up as twenty
changed lines. Verified: loading and writing back without any change produces a
byte-identical file.

Writes go to a temp file and are then moved into place; the previous version
stays as `.config.yaml.bak`. That is safe **only** because `.config.yaml` sits
inside the `data/` *directory* mount. Doing the same to one of the single-file
mounts (`asr_openai.py` and friends) would sever the mount — which is why this
app never touches a mounted `.py`.

## Two server patches this depends on

Both live in `chatti/server/`, see the README there.

* **`/chatti/status`** (`websocket_server.py`, `http_server.py`) — upstream keeps
  no list of live connections, so nothing could answer "is the chatti online?".
* **transcript** (`chatti_log.py`, called from `sendAudioHandle.py`) — nothing
  upstream writes conversations to disk.

Without them the panel still works: the connection line reads "Verbindungsstatus
unbekannt" and the conversation list stays empty.

## What the connection line means

The firmware opens the WebSocket **only while a conversation is running**
(`application.cc:745-750`) and closes it afterwards. "No live connection" is
therefore the normal resting state of a healthy device, and the panel says:

| Line | Means |
|---|---|
| **spricht gerade** | a WebSocket is open — the device is talking to the server right now |
| **bereit** | server up, the configured address is this PC; the device will reach it when you press the button. Below: when it was last heard |
| **für den Chatti nicht erreichbar** | the address the device dials is not this PC. Either the PC has no network address at all (`169.254.x.x` = no DHCP answer), or the address in the config is stale |
| **Server läuft nicht** | the container is down |

That last case is the one worth knowing about: the device dials
`server.websocket` from the configuration, and if this PC's IP changed or the
WLAN dropped, **nothing else in the stack notices** — Docker, Speaches and the
server all report "bereit" while the device talks into the void. The panel
compares the configured address against this machine's real addresses on every
poll and names which of the two cases it is.

### The "Auf x.x.x.x umstellen" button

Appears only when the configured address is wrong **and** a usable one exists.
It rewrites `server.websocket` and `server.ota` — host only, ports and paths
untouched — and restarts the server.

Which address it offers is deliberately picky, because a button that looks like
a fix and cannot be one is worse than no button:

| Address | Offered? |
|---|---|
| same /24 as the configured one | first choice — the PC just moved within the same network |
| the outbound address Windows itself picks | second |
| other 192.168.x / 10.x | third |
| 169.254.x | never — that is "no DHCP answer", i.e. no network at all |
| 172.16–31.x | never, unless the configured address is in that range too — that is where Docker and WSL live, and the device can never reach a virtual adapter |

No candidate means no button; the hint then says to fix the network instead.

⚠️ **This repairs the server side only.** The device dials the OTA address it
has stored in NVS and learns the WebSocket address from the answer. If the PC's
IP changed, that stored value is stale as well and has to be re-entered on the
device (WLAN config mode). A DHCP reservation for this PC in the router avoids
the whole problem.

## When something looks wrong

| Symptom | Cause |
|---|---|
| „Die Umgebung fehlt" although `.venv` exists | the `.cmd` lost its CRLF line endings; `cmd.exe` then mis-parses it. `.gitattributes` pins `*.cmd text eol=crlf` |
| „Container fehlt" | `docker rm` happened. Recreate it with the `docker run` block in `chatti/server/README.md` — do **not** let anything recreate it automatically, the mounts must be set by hand |
| „Sprache: startet" for minutes | Speaches is loading or downloading a model. `docker logs speaches` |
| LM Studio „läuft, Modell wird bei der ersten Frage geladen" | Normal. Only the first answer is slower |
| Voice list has one entry | Speaches is not answering; only the configured value is offered |
| Port 8099 taken by something else | The page says so — `app` in `/api/status` is checked. Change `PORT` in `settings.py` and `chatti-control.cmd` |
| Everything hangs | It should not: every outgoing call has a short timeout and the app never calls transcription or synthesis, which are the two that block behind Speaches' filelock |

## Deliberately not included

Continuing a conversation, chatting from the browser, deleting transcripts,
access from the WLAN, autostart (removed on 2026-08-16 — see above), logins,
model downloads, a log viewer, and recreating the container. Each one is either a mount risk or a different
program's job.
