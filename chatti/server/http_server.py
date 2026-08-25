# Modified copy of core/http_server.py
# from xiaozhi-esp32-server (https://github.com/xinnan-tech/xiaozhi-esp32-server),
# MIT licence, Copyright (c) xinnan-tech and contributors.
#
# Mounted over the original inside the container; see chatti/server/README.md for
# what was changed and why. Take a fresh template with `docker cp` from the
# running container, never from a checkout - the files differ.

import asyncio
from aiohttp import web
from config.logger import setup_logging
from core.api.ota_handler import OTAHandler
from core.api.vision_handler import VisionHandler

TAG = __name__


class SimpleHttpServer:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging()
        self.ota_handler = OTAHandler(config)
        self.vision_handler = VisionHandler(config)

    # chatti patch: report which devices are currently connected.
    # Our control panel shows whether the chatti is online; nothing upstream
    # answers that question. The connection registry lives in
    # core/websocket_server.py (imported lazily to keep module import order
    # untouched) and holds live ConnectionHandler objects.
    async def handle_chatti_status(self, request):
        from core.websocket_server import CHATTI_CONNECTIONS

        devices = []
        for handler in list(CHATTI_CONNECTIONS):
            try:
                devices.append(
                    {
                        "device_id": getattr(handler, "device_id", None),
                        "session_id": getattr(handler, "session_id", None),
                        "client_ip": getattr(handler, "client_ip", None),
                        # milliseconds since the epoch, as connection.py stores them
                        "connected_since": getattr(handler, "first_activity_time", 0),
                        "last_activity": getattr(handler, "last_activity_time", 0),
                    }
                )
            except Exception:
                # A handler tearing down while we read it must never break the
                # status call — skip it, the next poll is 3 seconds away.
                continue
        return web.json_response(
            {"connected": len(devices) > 0, "devices": devices},
            headers={"Access-Control-Allow-Origin": "*"},
        )

    def _get_websocket_url(self, local_ip: str, port: int) -> str:
        """获取websocket地址

        Args:
            local_ip: 本地IP地址
            port: 端口号

        Returns:
            str: websocket地址
        """
        server_config = self.config["server"]
        websocket_config = server_config.get("websocket")

        if websocket_config and "你" not in websocket_config:
            return websocket_config
        else:
            return f"ws://{local_ip}:{port}/xiaozhi/v1/"

    async def start(self):
        try:
            server_config = self.config["server"]
            read_config_from_api = self.config.get("read_config_from_api", False)
            host = server_config.get("ip", "0.0.0.0")
            port = int(server_config.get("http_port", 8003))

            if port:
                app = web.Application()

                if not read_config_from_api:
                    # 如果没有开启智控台，只是单模块运行，就需要再添加简单OTA接口，用于下发websocket接口
                    app.add_routes(
                        [
                            web.get("/xiaozhi/ota/", self.ota_handler.handle_get),
                            web.post("/xiaozhi/ota/", self.ota_handler.handle_post),
                            web.options(
                                "/xiaozhi/ota/", self.ota_handler.handle_options
                            ),
                            # 下载接口，仅提供 data/bin/*.bin 下载
                            web.get(
                                "/xiaozhi/ota/download/{filename}",
                                self.ota_handler.handle_download,
                            ),
                            web.options(
                                "/xiaozhi/ota/download/{filename}",
                                self.ota_handler.handle_options,
                            ),
                        ]
                    )
                # 添加路由
                app.add_routes(
                    [
                        web.get("/mcp/vision/explain", self.vision_handler.handle_get),
                        web.post(
                            "/mcp/vision/explain", self.vision_handler.handle_post
                        ),
                        web.options(
                            "/mcp/vision/explain", self.vision_handler.handle_options
                        ),
                        # chatti patch: see handle_chatti_status above
                        web.get("/chatti/status", self.handle_chatti_status),
                    ]
                )

                # 运行服务
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, host, port)
                await site.start()

                # 保持服务运行
                while True:
                    await asyncio.sleep(3600)  # 每隔 1 小时检查一次
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"HTTP服务器启动失败: {e}")
            import traceback

            self.logger.bind(tag=TAG).error(f"错误堆栈: {traceback.format_exc()}")
            raise
