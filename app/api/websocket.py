import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.state import app_state

logger = logging.getLogger("websocket")
router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await app_state.connect_ws(websocket)
    try:
        while True:
            # Keep connection alive, listen for ping or client requests
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        app_state.disconnect_ws(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        app_state.disconnect_ws(websocket)
