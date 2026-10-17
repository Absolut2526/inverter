import asyncio
from app.collectors.simulator import TelemetrySimulator
from app.bot.handlers import format_status_message
from app.state import app_state
from app.api.routes import router as api_router
from fastapi.testclient import TestClient
from app.main import app

def test_simulator_and_formatter():
    sim = TelemetrySimulator()
    telemetry = sim.generate()
    
    assert telemetry.solar.total_power >= 0
    assert len(telemetry.battery.batteries) == 2
    assert telemetry.battery.batteries[0].name == "Акумулятор 1 (Master)"
    assert telemetry.battery.batteries[1].name == "Акумулятор 2 (Slave)"
    assert telemetry.grid.voltage > 0
    
    # Check Ukrainian Telegram message formatter
    msg = format_status_message(telemetry)
    assert "СТАНЦІЯ DEYE HYBRID" in msg
    assert "СОНЯЧНІ ПАНЕЛІ" in msg
    assert "БАТАРЕЇ" in msg
    assert "Акумулятор 1" in msg
    assert "Акумулятор 2" in msg
    assert "Мережа 220В" in msg
    print("Formatting test passed!")

def test_api_endpoints():
    with TestClient(app) as client:
        # Check health
        res = client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"

        # Check HTML Dashboard
        res = client.get("/")
        assert res.status_code == 200
        assert "Deye Solar Monitor" in res.text
        assert "Батареї (2 шт)" in res.text

        # Check API status
        res = client.get("/api/status")
        assert res.status_code == 200
        status_data = res.json()
        assert "solar" in status_data
        assert "battery" in status_data
        assert len(status_data["battery"]["batteries"]) == 2

        # Check History
        res = client.get("/api/history")
        assert res.status_code == 200
        history_data = res.json()
        assert isinstance(history_data, list)

        # Check Blackout Toggle
        res = client.post("/api/test/toggle-grid")
        assert res.status_code == 200
        assert res.json()["status"] == "toggled"
        print("API endpoints test passed!")

if __name__ == "__main__":
    test_simulator_and_formatter()
    test_api_endpoints()
    print("All tests passed successfully!")
