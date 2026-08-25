# Server side (M2)

Everything the ESP32 talks to runs on the PC. Nothing leaves the machine.

```
ESP32 ──WebSocket──> xiaozhi-server (Docker, :8000)
                          ├── STT  ─> Speaches (:8100)  faster-whisper-medium
                          ├── LLM  ─> LM Studio (:1234) google/gemma-4-e4b
                          └── TTS  ─> Speaches (:8100)  ufozone/piper-de_DE-miro-medium
```

## Starting everything

```powershell
# 1. Docker Desktop — lives on D:, not under Program Files. Daemon needs ~55 s.
Start-Process 'D:\Docker\Docker\Docker Desktop.exe'

# 2. LLM
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server start

# 3. STT/TTS and the server itself — from this directory
docker compose up -d
```

`docker-compose.yml` is the whole PC-side stack: both containers, their ports,
the environment and every bind mount below. It replaced creating containers by
hand on 2026-08-16, and it replaced `docker start <name>` with it: `compose up
-d` creates what is missing, starts what is stopped and does nothing when
everything already runs. A container made this way cannot be missing a patch,
which is what `docker rm` used to cost.

Runtime state (`.config.yaml`, conversations, recordings) lives wherever
`CHATTI_DATA` in `.env` points, and in `./data` next to the compose file when
that is unset. `.env` is machine-specific and not in git; copy `.env.example`.

Two things in that file are load-bearing and easy to lose in an edit:

- `container_name:` — Chatti Control looks the containers up by name.
- `hf-hub-cache` declared `external: true` — it holds 3 GB of downloaded
  models. Without it compose creates an empty `chatti_hf-hub-cache` beside the
  real one and every model downloads again.

Health check before blaming the device:

```powershell
Invoke-WebRequest http://localhost:8100/health                       # Speaches
Invoke-RestMethod  http://localhost:1234/v1/models                   # LM Studio
Invoke-WebRequest 'http://localhost:8003/xiaozhi/ota/'               # xiaozhi-server
docker exec xiaozhi-server sh -c "grep -c 'chatti patch' /opt/xiaozhi-esp32-server/core/providers/asr/openai.py"
```

## Files here — this directory is the master copy

| File | Belongs at | Purpose |
|---|---|---|
| `config.yaml` | `…\xiaozhi-server\data\.config.yaml` | our overrides; stock `config.yaml` untouched |
| `chatti-prompt.txt` | `…\data\chatti-prompt.txt` | slim prompt template (419 chars instead of 6350) |
| `asr_openai.py` | mount over `core/providers/asr/openai.py` | sends `language` |
| `tts_openai.py` | mount over `core/providers/tts/openai.py` | sends `sample_rate` |
| `helloHandle.py` | mount over `core/handle/helloHandle.py` | keeps our output sample rate |
| `opus_encoder_utils.py` | mount over `core/utils/opus_encoder_utils.py` | bitrate from `CHATTI_OPUS_BITRATE` |
| `tts_base.py` | mount over `core/providers/tts/base.py` | measures `duration_ms` per sentence |
| `sendAudioHandle.py` | mount over `core/handle/sendAudioHandle.py` | puts `duration_ms` into `sentence_start`, records the transcript |
| `chatti_log.py` | mount **as** `core/utils/chatti_log.py` | writes every turn to `data/conversations/` |
| `websocket_server.py` | mount over `core/websocket_server.py` | keeps a registry of live device connections |
| `http_server.py` | mount over `core/http_server.py` | serves that registry as `/chatti/status` |
| `probe_hello.py` | `…\data\probe_hello.py` | prints what the server advertises in its hello |

`chatti_log.py` is the only one that is not a patched copy of an upstream file
but a file of our own — it has no upstream counterpart, so it can never
conflict on a merge. `sendAudioHandle.py` calls it in three lines.

Edit here, copy across, restart.

⚠️ **Take the template from the running container, not from `D:\chatti\server`.**
The repo checkout and the image drift apart (`sha256sum` differs). Patch a copy of
what actually runs:

```powershell
docker cp xiaozhi-server:/opt/xiaozhi-esp32-server/core/providers/tts/base.py .\tts_base.py
```

## Recreating the container (mounts are not optional)

`docker restart` keeps the patches. `docker rm` loses them — recreate with every
mount and the env var:

```powershell
docker run -d --name xiaozhi-server `
  -p 8000:8000 -p 8003:8003 `
  -e CHATTI_OPUS_BITRATE=48000 `
  -v "D:\chatti\server\main\xiaozhi-server\data:/opt/xiaozhi-esp32-server/data" `
  -v "D:\chatti\firmware\chatti\server\asr_openai.py:/opt/xiaozhi-esp32-server/core/providers/asr/openai.py:ro" `
  -v "D:\chatti\firmware\chatti\server\tts_openai.py:/opt/xiaozhi-esp32-server/core/providers/tts/openai.py:ro" `
  -v "D:\chatti\firmware\chatti\server\helloHandle.py:/opt/xiaozhi-esp32-server/core/handle/helloHandle.py:ro" `
  -v "D:\chatti\firmware\chatti\server\opus_encoder_utils.py:/opt/xiaozhi-esp32-server/core/utils/opus_encoder_utils.py:ro" `
  -v "D:\chatti\firmware\chatti\server\tts_base.py:/opt/xiaozhi-esp32-server/core/providers/tts/base.py:ro" `
  -v "D:\chatti\firmware\chatti\server\sendAudioHandle.py:/opt/xiaozhi-esp32-server/core/handle/sendAudioHandle.py:ro" `
  -v "D:\chatti\firmware\chatti\server\chatti_log.py:/opt/xiaozhi-esp32-server/core/utils/chatti_log.py:ro" `
  -v "D:\chatti\firmware\chatti\server\websocket_server.py:/opt/xiaozhi-esp32-server/core/websocket_server.py:ro" `
  -v "D:\chatti\firmware\chatti\server\http_server.py:/opt/xiaozhi-esp32-server/core/http_server.py:ro" `
  --restart unless-stopped `
  ghcr.io/xinnan-tech/xiaozhi-esp32-server:server_latest
```

**Do not drop the `-e` or any mount.** `CHATTI_OPUS_BITRATE` and the
`opus_encoder_utils.py` mount were missing from this file once and would have
been lost on the next recreate — read them off the running container with
`docker inspect xiaozhi-server --format '{{json .HostConfig.Binds}}'` before
removing it.

Single-file mounts are unreliable on Docker Desktop — always verify:

```powershell
foreach ($f in @("core/providers/asr/openai.py","core/providers/tts/openai.py",
                 "core/handle/helloHandle.py","core/utils/opus_encoder_utils.py",
                 "core/providers/tts/base.py","core/handle/sendAudioHandle.py",
                 "core/utils/chatti_log.py","core/websocket_server.py",
                 "core/http_server.py")) {
  "$f : " + (docker exec xiaozhi-server sh -c "grep -c 'chatti patch' /opt/xiaozhi-esp32-server/$f")
}
```

Counts must be 2, 2, 1, 2, 5, 5, 2, 4, 2 (checked 2026-08-15).

## Why each patch exists

**`language` (ASR).** Upstream sends only `model`, so Whisper guesses the
language. A German "Hallo, wer bist du?" came back as Arabic (`حلو وربستو`) and
went to the LLM like that.

**`sample_rate` (TTS).** Speaches only resamples when asked. Without the
parameter it stamps 24000 Hz into the WAV header while leaving 16 kHz Piper data
underneath — every "low" voice then plays 1.5x too fast and sounds squeaky.
Verified by duration: the same sentence took 2.41 s without the parameter and
3.98 s with it.

**`audio_params` (hello).** The device announces `sample_rate: 16000` — that is
its *microphone* Opus rate — but plays back at 24000. Upstream copies the whole
block into its reply, so the server claims to send 16 kHz while still encoding
at 24 kHz from config. Result: dull, noisy playback and a warning in the device
log. The patch keeps the configured output rate and takes the rest from the
client. Check with `probe_hello.py`; it must print 24000.

## Voices

German Piper voices above "low" quality are scarce: the only large free German
speech corpus is Thorsten Müller's public-domain recordings, so `thorsten` exists
in low/medium/high while the female voices (`eva_k`, `kerstin`, `ramona`) are
LibriVox-derived and stop at `low`. Beyond the official `speaches-ai` set the
registry also carries community voices — `ufozone/*` and `systemofapwne/*`
(GLaDOS) — several of them `medium`/`high`. 155 TTS models total; do not stop
reading the registry after the first page.

Current choice: `ufozone/piper-de_DE-miro-medium`, picked by ear.

To audition voices, always pass `sample_rate: 24000` — otherwise the comparison
is worthless (see above).

## Performance knobs — what was tried

Speaches runs with `WHISPER__CPU_THREADS=12`. Measured on the same 5 s clip with
`faster-whisper-medium`, warm (first run always includes model loading):

| Setting | Time |
|---|---|
| float32, threads auto (stock) | 18.6 s |
| **int8**, 12 threads | **22.8 s** — slower, do not use |
| float32, 12 threads | **17.2 s** — current |

int8 quantisation backfires on this CPU: the Ryzen 5 2600 is Zen+ and has no
VNNI instructions, so the conversion costs more than the narrower arithmetic
saves. Worth remembering — int8 is the standard advice for CPU inference and is
simply wrong here.

Other findings: Docker has no CPU or memory limit set (12 CPUs, 20 GB), and
Windows already runs the "Höchstleistung" power plan, so neither is a lever.

The real lever is the GPU, and it is full: the 5 GB LLM occupies 3720 of 4096 MB.
Whisper on GPU would be roughly 10x faster (~2 s), but only if the LLM moves
entirely to CPU — which trades 15 s off recognition for 20-35 s onto generation.

## Measured timings

| Step | Time |
|---|---|
| Recognition, whisper-**small**, CPU | ~13 s |
| Recognition, whisper-**medium**, CPU | ~20–25 s |
| LLM (reasoning model, ~170 thinking tokens) | ~25–35 s |
| Speech synthesis per sentence | 0.3–0.5 s |

Slow by hardware, not by defect: the 5 GB model does not fit into 4 GB of VRAM.
Acceptable per decision 8; the wait gets a "thinking" face in M3.

## `duration_ms` — pacing the subtitle against the voice (M4)

Two more patches, `tts_base.py` and `sendAudioHandle.py`, add one field to the
`sentence_start` message:

```json
{"type": "tts", "state": "sentence_start", "text": "...", "duration_ms": 20364}
```

**Why.** The device shows the sentence when its audio starts and scrolls it if it
does not fit on 240 x 284 px. Without the length, that scroll runs on a fixed
timer (22 px/s) that has nothing to do with the speaking rate — after a few
seconds the visible lines no longer match what is being said. With it the scroll
is a single pass timed to the voice and arrives at the bottom as the sentence
ends.

**How.** `to_tts_stream()` already holds the finished audio (bytes or temp file)
before it queues `SentenceType.FIRST`. pydub parses it there anyway, so
`_chatti_duration_ms()` measures the length for a second time — a few ms next to
a 30 s LLM round trip. The value travels as a 5th element in the audio queue
tuple; 3- and 4-tuples from every other provider keep working unchanged.

**Failure mode is silent and safe.** If the measurement throws, the field is
omitted and the firmware falls back to the free-running scroll. Verify from the
device log:

```
<< das Sie in Flaschen kaufen, ... (20364 ms)
Subtitle overflows by 352 px, scrolling over 18964 ms (paced by voice)
```

`free running` instead of `paced by voice` means the field did not arrive.

## Transcript and device status — for the control panel

Two more patches, both added for `chatti/control`.

**Transcript.** `chatti_log.py` appends one JSON line per turn to
`data/conversations/YYYY-MM-DD.jsonl`. `sendAudioHandle.py` calls it from
`send_stt_message` (the question) and from `send_tts_message` on
`sentence_start` (the answer, with `duration_ms`).

Why here and not in `core/utils/dialogue.py`, which sees every message: that
class knows neither the session nor the device, and `sendAudioHandle.py` is
mounted anyway — so this costs no additional upstream file. One answer produces
exactly one line, because sentence segmentation is off (`punctuations` is empty
in `tts_base.py`).

`ts` is epoch seconds and is the authoritative timestamp; the container runs on
UTC and any local time written here would be off by hours. `iso` is only there
so the file reads well in an editor. Every failure is swallowed — a broken
recorder must never keep the device from answering.

**Device status.** Upstream keeps no list of live connections:
`_handle_connection` creates a `ConnectionHandler`, hands it over and drops the
reference. `websocket_server.py` therefore holds a module-level
`CHATTI_CONNECTIONS` set, and `http_server.py` serves it.

⚠️ **A live connection means "talking", not "switched on".** The firmware opens
the WebSocket only when the button is pressed
(`application.cc:745-750`, `HandleToggleChatEvent` → `ContinueOpenAudioChannel`)
and closes it again when the conversation ends. Between conversations there is
nothing to see, so `connected: false` is the normal resting state of a perfectly
healthy device. That is why `websocket_server.py` also calls
`remember_device()` on every connect: it persists device id, IP and timestamp to
`data/chatti-devices.json`, which is what lets the control panel say "last heard
5 minutes ago" instead of pretending the device is gone.

```powershell
Invoke-RestMethod http://localhost:8003/chatti/status
# {"connected": false, "devices": []}     device off
# {"connected": true,  "devices": [{"device_id": "...", "client_ip": "...", ...}]}
```

`app.py` stays untouched — `http_server.py` imports the set directly rather than
having it passed in.
