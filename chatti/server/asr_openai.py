# Modified copy of core/providers/asr/openai.py
# from xiaozhi-esp32-server (https://github.com/xinnan-tech/xiaozhi-esp32-server),
# MIT licence, Copyright (c) xinnan-tech and contributors.
#
# Mounted over the original inside the container; see chatti/server/README.md for
# what was changed and why. Take a fresh template with `docker cp` from the
# running container, never from a checkout - the files differ.

import time
import os
from config.logger import setup_logging
from typing import Optional, Tuple, List
from core.providers.asr.dto.dto import InterfaceType
from core.providers.asr.base import ASRProviderBase

import requests

TAG = __name__
logger = setup_logging()

class ASRProvider(ASRProviderBase):
    def __init__(self, config: dict, delete_audio_file: bool):
        self.interface_type = InterfaceType.NON_STREAM
        self.api_key = config.get("api_key")
        self.api_url = config.get("base_url")
        self.model = config.get("model_name")
        self.output_dir = config.get("output_dir")
        self.delete_audio_file = delete_audio_file
        # chatti patch: pin the spoken language.
        # Upstream never sends "language", so Whisper has to guess it from the
        # audio. On short utterances it guesses wrong — a German "Hallo, wer bist
        # du?" came back as Arabic ("حلو وربستو"), which then reached the LLM.
        # Empty value keeps upstream behaviour (auto-detect).
        self.language = config.get("language", "")

        os.makedirs(self.output_dir, exist_ok=True)

    def requires_file(self) -> bool:
        return True

    async def speech_to_text(self, opus_data: List[bytes], session_id: str, artifacts=None) -> Tuple[Optional[str], Optional[str]]:
        file_path = None
        try:
            if artifacts is None:
                return "", None
            file_path = artifacts.file_path
                
            logger.bind(tag=TAG).info(f"file path: {file_path}")
            headers = {
                "Authorization": f"Bearer {self.api_key}",
            }
            
            # 使用data参数传递模型名称
            data = {
                "model": self.model
            }
            # chatti patch: see __init__
            if self.language:
                data["language"] = self.language


            with open(file_path, "rb") as audio_file:  # 使用with语句确保文件关闭
                files = {
                    "file": audio_file
                }

                start_time = time.time()
                response = requests.post(
                    self.api_url,
                    files=files,
                    data=data,
                    headers=headers
                )
                logger.bind(tag=TAG).debug(
                    f"语音识别耗时: {time.time() - start_time:.3f}s | 结果: {response.text}"
                )

            if response.status_code == 200:
                text = response.json().get("text", "")
                return text, file_path
            else:
                raise Exception(f"API请求失败: {response.status_code} - {response.text}")
                
        except Exception as e:
            logger.bind(tag=TAG).error(f"语音识别失败: {e}")
            return "", None
        
