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


from app.collectors.charge_controller import charge_controller, ChargeSchedule
from pydantic import BaseModel

class AddScheduleRequest(BaseModel):
    day_type: str = "today"
    start_time: str
    end_time: str
    target_soc: float = 100.0

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


@router.get("/charge/schedules", response_model=List[ChargeSchedule])
async def get_charge_schedules():
    """Returns all active and saved charge schedules."""
    return charge_controller.schedules


@router.post("/charge/schedule", response_model=ChargeSchedule)
async def create_charge_schedule(req: AddScheduleRequest):
    """Creates a new battery charge schedule."""
    return charge_controller.add_schedule(
        day_type=req.day_type,
        start_time=req.start_time,
        end_time=req.end_time,
        target_soc=req.target_soc,
    )


@router.delete("/charge/schedule/{sched_id}")
async def remove_charge_schedule(sched_id: str):
    """Deletes a charge schedule by ID."""
    success = charge_controller.delete_schedule(sched_id)
    if not success:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"status": "deleted", "id": sched_id}


@router.post("/charge/force-start")
async def api_force_start_charge(target_soc: float = 100.0, hours: float | None = None):
    """Manually triggers battery charging from grid."""
    return await charge_controller.start_force_charge(target_soc=target_soc, duration_hours=hours)


@router.post("/charge/force-stop")
async def api_force_stop_charge():
    """Manually stops grid charging and returns to Self-Use mode."""
    return await charge_controller.stop_force_charge()

