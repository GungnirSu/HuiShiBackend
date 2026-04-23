from collections import defaultdict
from typing import DefaultDict, List

from fastapi import WebSocket


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: DefaultDict[str, List[WebSocket]] = defaultdict(list)

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[session_id].append(websocket)

    def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        if session_id in self._connections and websocket in self._connections[session_id]:
            self._connections[session_id].remove(websocket)
            if not self._connections[session_id]:
                self._connections.pop(session_id, None)

    async def broadcast(self, session_id: str, message: dict) -> None:
        connections = list(self._connections.get(session_id, []))
        for websocket in connections:
            try:
                await websocket.send_json(message)
            except Exception:
                self.disconnect(session_id, websocket)


ws_manager = WebSocketManager()
