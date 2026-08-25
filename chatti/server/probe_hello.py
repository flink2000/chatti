"""Connect to the local xiaozhi-server like the ESP32 does and print the
server's hello reply — specifically the audio_params it advertises."""

import asyncio
import json

import websockets


async def main():
    url = "ws://127.0.0.1:8000/xiaozhi/v1/"
    headers = {
        "device-id": "aa:bb:cc:dd:ee:ff",  # any MAC-shaped id; the server only echoes it
        "client-id": "31fdf49b-7ee6-4f8d-a272-1c9300f8c9ff",
        "protocol-version": "1",
    }
    async with websockets.connect(url, additional_headers=headers) as ws:
        await ws.send(json.dumps({
            "type": "hello",
            "version": 1,
            "transport": "websocket",
            "audio_params": {
                "format": "opus",
                "sample_rate": 16000,
                "channels": 1,
                "frame_duration": 60,
            },
        }))
        for _ in range(3):
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            if isinstance(msg, bytes):
                print(f"<binary {len(msg)} bytes>")
                continue
            data = json.loads(msg)
            print(json.dumps(data, ensure_ascii=False))
            if data.get("type") == "hello":
                print(">>> server advertises sample_rate:",
                      data.get("audio_params", {}).get("sample_rate"))
                break


asyncio.run(main())
