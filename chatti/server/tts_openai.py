# Modified copy of core/providers/tts/openai.py
# from xiaozhi-esp32-server (https://github.com/xinnan-tech/xiaozhi-esp32-server),
# MIT licence, Copyright (c) xinnan-tech and contributors.
#
# Mounted over the original inside the container; see chatti/server/README.md for
# what was changed and why. Take a fresh template with `docker cp` from the
# running container, never from a checkout - the files differ.

import requests
from core.utils.util import check_model_key
from core.providers.tts.base import TTSProviderBase
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()


class TTSProvider(TTSProviderBase):
    TTS_PARAM_CONFIG = [
        ("ttsRate", "speed", 0.25, 4, 1, lambda v: round(float(v), 2)),
    ]

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.api_key = config.get("api_key")
        self.api_url = config.get("api_url", "https://api.openai.com/v1/audio/speech")
        self.model = config.get("model", "tts-1")
        if config.get("private_voice"):
            self.voice = config.get("private_voice")
        else:
            self.voice = config.get("voice", "alloy")
        self.audio_file_type = config.get("format", "wav")
        # chatti patch: request an explicit sample rate.
        # Speaches only resamples when asked. Without this it stamps 24000 Hz
        # into the WAV header while leaving 16 kHz Piper data underneath, so
        # every "low" voice plays back 1.5x too fast and sounds squeaky.
        # Empty value keeps upstream behaviour.
        self.sample_rate = config.get("sample_rate", "")

        # 处理空字符串的情况
        speed = config.get("speed", "1.0")
        self.speed = float(speed) if speed else 1.0

        # 应用百分比调整（如果存在），否则使用公有化配置
        self._apply_percentage_params(config)

        self.output_file = config.get("output_dir", "tmp/")
        model_key_msg = check_model_key("TTS", self.api_key)
        if model_key_msg:
            logger.bind(tag=TAG).error(model_key_msg)

    async def text_to_speak(self, text, output_file):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = {
            "model": self.model,
            "input": text,
            "voice": self.voice,
            "response_format": self.audio_file_type,
            "speed": self.speed,
        }
        # chatti patch: see __init__
        if self.sample_rate:
            data["sample_rate"] = int(self.sample_rate)
        response = requests.post(self.api_url, json=data, headers=headers)
        if response.status_code == 200:
            if output_file:
                with open(output_file, "wb") as audio_file:
                    audio_file.write(response.content)
            else:
                return response.content
        else:
            raise Exception(
                f"OpenAI TTS请求失败: {response.status_code} - {response.text}"
            )
