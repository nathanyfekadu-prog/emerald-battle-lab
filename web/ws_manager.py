from __future__ import annotations

import asyncio
from typing import Any


class WSManager:
    def __init__(self) -> None:
        self.active: set[Any] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    async def connect(self, websocket: Any) -> None:
        await websocket.accept()
        self.loop = asyncio.get_running_loop()
        self.active.add(websocket)

    def disconnect(self, websocket: Any) -> None:
        self.active.discard(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        disconnected = []
        for websocket in list(self.active):
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.append(websocket)
        for websocket in disconnected:
            self.disconnect(websocket)

    def broadcast_sync(self, message: dict[str, Any]) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)
