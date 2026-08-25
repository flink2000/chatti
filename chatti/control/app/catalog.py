"""What can be selected: LLMs from LM Studio, voices and Whisper models from
Speaches.

Only what is already downloaded is offered. Speaches also knows a registry of
~155 remote models, but listing those invites a click that starts a multi-minute
download while holding the filelock that blocks every other request. Downloads
stay a deliberate act on the command line.

Verified response shapes (2026-08-15, both services live):
  GET :8100/v1/models  -> {"data": [{"id", "task", "language": [..],
                                     "sample_rate", "voices": [{"id","name"}]}]}
      task is "text-to-speech" or "automatic-speech-recognition"
  GET :1234/api/v0/models -> {"data": [{"id", "type", "state"}]}   (LM Studio)
  GET :1234/v1/models     -> {"data": [{"id"}]}                    (fallback)
"""

from .services import _client
from . import settings


async def llms():
    """LM Studio's own REST API knows the type and whether a model is loaded;
    the OpenAI-compatible one only knows ids."""
    try:
        r = await _client.get(f"{settings.LMSTUDIO_URL}/api/v0/models")
        if r.status_code == 200:
            out = []
            for m in r.json().get("data", []):
                if m.get("type") not in (None, "llm", "vlm"):
                    continue  # embeddings are not chat models
                out.append({
                    "id": m.get("id"),
                    "label": m.get("id"),
                    "loaded": m.get("state") == "loaded",
                })
            if out:
                return out
    except Exception:
        pass
    try:
        r = await _client.get(f"{settings.LMSTUDIO_URL}/v1/models")
        if r.status_code == 200:
            return [{"id": m.get("id"), "label": m.get("id"), "loaded": False}
                    for m in r.json().get("data", [])]
    except Exception:
        pass
    return []


async def _speaches_models():
    r = await _client.get(f"{settings.SPEACHES_URL}/v1/models")
    r.raise_for_status()
    payload = r.json()
    return payload.get("data", payload if isinstance(payload, list) else [])


def _voice_label(model_id, voice_id):
    """`speaches-ai/piper-en_US-lessac-medium` -> `lessac — medium`.
    The owner prefix and the language are noise once the list is one language."""
    tail = model_id.rsplit("/", 1)[-1]
    quality = tail.rsplit("-", 1)[-1] if "-" in tail else ""
    owner = model_id.split("/", 1)[0] if "/" in model_id else ""
    label = voice_id or tail
    if quality:
        label += f" — {quality}"
    if owner and owner != "speaches-ai":
        label += f" ({owner})"
    return label


async def voices_and_asr(language=settings.SPEECH_LANGUAGE):
    """Both lists come from the same call, so fetch once and split."""
    try:
        models = await _speaches_models()
    except Exception:
        return [], []

    voices, asr = [], []
    for m in models:
        task = m.get("task")
        langs = m.get("language") or []
        if task == "text-to-speech":
            if language and langs and language not in langs:
                continue
            for v in (m.get("voices") or [{"id": None}]):
                voices.append({
                    "id": f"{m['id']}|{v.get('id')}",   # model and voice belong together
                    "model": m["id"],
                    "voice": v.get("id"),
                    "label": _voice_label(m["id"], v.get("id")),
                })
        elif task == "automatic-speech-recognition":
            asr.append({"id": m["id"], "label": m["id"].rsplit("/", 1)[-1]})

    voices.sort(key=lambda x: x["label"].lower())
    asr.sort(key=lambda x: x["label"])
    return voices, asr


async def preview_url(model, voice):
    """Speaches synthesises a sample. Always at 24000 Hz — at any other rate the
    comparison is worthless, because the device plays back at 24 kHz and the
    "low" voices otherwise come out 1.5x too fast (see chatti/server/README.md).

    The sentence is spoken by the voice being auditioned, so it has to be in the
    language the voice was trained on — see settings.SPEECH_LANGUAGE.
    """
    return {
        "url": f"{settings.SPEACHES_URL}/v1/audio/speech",
        "body": {
            "model": model,
            "voice": voice,
            "input": "Hello, I am Chatti. This is what I sound like.",
            "response_format": "wav",
            "sample_rate": 24000,
        },
    }
