from typing import List
from fastapi import APIRouter, Header, HTTPException, status
from app.config import settings
from app.models import SystemTelemetry, TelemetryHistoryPoint
from app.state import app_state

router = APIRouter(prefix="/api", tags=["telemetry"])


@router.get("/status", response_model=SystemTelemetry)
async def get_current_status():
    """Returns the latest telemetry data."""
    if not app_state.latest_telemetry:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telemetry data not available yet",
        )
    return app_state.latest_telemetry


@router.get("/history", response_model=List[TelemetryHistoryPoint])
async def get_history():
    """Returns telemetry history for charts."""
    return list(app_state.history)


@router.post("/telemetry")
async def post_telemetry(
    payload: SystemTelemetry,
    x_api_key: str = Header(None, alias="X-API-Key"),
):
    """Allows pushing telemetry directly from a home bridge or ESP32."""
    if settings.SECRET_PUSH_TOKEN and x_api_key != settings.SECRET_PUSH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header",
        )

    payload.source = "local_push"
    await app_state.update_telemetry(payload)
    return {"status": "ok", "timestamp": payload.timestamp}


@router.post("/test/toggle-grid")
async def test_toggle_grid():
    """Test helper to simulate blackout / grid restoration."""
    if not app_state.latest_telemetry:
        return {"error": "No telemetry"}
    
    current_grid = app_state.latest_telemetry.grid.is_online
    app_state.latest_telemetry.grid.is_online = not current_grid
    if not app_state.latest_telemetry.grid.is_online:
        app_state.latest_telemetry.grid.voltage = 0.0
        app_state.latest_telemetry.grid.power = 0.0
    else:
        app_state.latest_telemetry.grid.voltage = 230.0
    
    await app_state.update_telemetry(app_state.latest_telemetry)
    return {
        "status": "toggled",
        "grid_is_online": app_state.latest_telemetry.grid.is_online,
    }
