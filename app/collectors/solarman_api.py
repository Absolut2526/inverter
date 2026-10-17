import asyncio
import hashlib
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
import httpx
from app.config import settings
from app.models import (
    BatterySystem,
    BatteryUnit,
    GridSystem,
    InverterStatus,
    LoadSystem,
    SolarSystem,
    SystemTelemetry,
)

logger = logging.getLogger("solarman_api")


class SolarmanApiClient:
    """Client for Solarman Open API v1.0 (Deye / Solarman Cloud)."""

    def __init__(self):
        self.app_id = settings.SOLARMAN_APP_ID
        self.app_secret = settings.SOLARMAN_APP_SECRET
        self.email = settings.SOLARMAN_EMAIL
        self.password = settings.SOLARMAN_PASSWORD
        self.inverter_sn = settings.SOLARMAN_INVERTER_SN
        self.base_url = settings.SOLARMAN_API_URL.rstrip("/")

        self.access_token: Optional[str] = None
        self.token_expiry: float = 0.0

    def _hash_password(self, pwd: str) -> str:
        # Solarman expects SHA256 of password
        return hashlib.sha256(pwd.encode("utf-8")).hexdigest()

    async def get_token(self) -> Optional[str]:
        """Obtains or refreshes access token from Solarman."""
        if self.access_token and time.time() < (self.token_expiry - 60):
            return self.access_token

        url = f"{self.base_url}/account/v1.0/token?appId={self.app_id}&language=en"
        body = {
            "appSecret": self.app_secret,
            "email": self.email,
            "password": self._hash_password(self.password),
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=body)
                data = res.json()
                if res.status_code == 200 and data.get("access_token"):
                    self.access_token = data["access_token"]
                    expires_in = data.get("expires_in", 7200)
                    self.token_expiry = time.time() + float(expires_in)
                    logger.info("Successfully authenticated with Solarman Open API")
                    return self.access_token
                else:
                    logger.error(f"Solarman token error: {data}")
                    return None
        except Exception as e:
            logger.error(f"Failed to connect to Solarman API: {e}")
            return None

    async def fetch_current_data(self) -> Optional[SystemTelemetry]:
        """Fetches real-time inverter & battery data from Solarman Cloud."""
        token = await self.get_token()
        if not token:
            return None

        url = f"{self.base_url}/device/v1.0/currentData"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = {"deviceSn": self.inverter_sn}

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                res = await client.post(url, headers=headers, json=body)
                data = res.json()
                if not data.get("success", False) and data.get("code") != "0":
                    logger.warning(f"Solarman currentData response code: {data.get('code')}: {data.get('msg')}")
                    # Some endpoints return data directly or in dataList
                
                raw_list = data.get("dataList") or data.get("data") or []
                return self._parse_telemetry(raw_list)
        except Exception as e:
            logger.error(f"Exception fetching Solarman device data: {e}")
            return None

    def _parse_telemetry(self, data_list: List[Dict[str, Any]]) -> SystemTelemetry:
        """Parses key-value data list from Solarman API into SystemTelemetry."""
        kv = {}
        for item in data_list:
            key = str(item.get("key", "")).strip().lower()
            val = item.get("value")
            try:
                kv[key] = float(val) if val is not None else 0.0
            except (ValueError, TypeError):
                kv[key] = val

        # Helper to find value from possible keys
        def find_val(candidates: List[str], default: float = 0.0) -> float:
            for c in candidates:
                c_low = c.lower()
                for k, v in kv.items():
                    if c_low in k:
                        if isinstance(v, (int, float)):
                            return float(v)
            return default

        # Solar PV
        pv1_power = find_val(["pv1_power", "pv1_p", "pv1 power", "pv1p"])
        pv1_volt = find_val(["pv1_volt", "pv1_v", "pv1 volt"])
        pv1_curr = find_val(["pv1_curr", "pv1_i", "pv1 current"])

        pv2_power = find_val(["pv2_power", "pv2_p", "pv2 power", "pv2p"])
        pv2_volt = find_val(["pv2_volt", "pv2_v", "pv2 volt"])
        pv2_curr = find_val(["pv2_curr", "pv2_i", "pv2 current"])

        total_pv = find_val(["total_pv", "generation_power", "pv_power", "p_pv"], pv1_power + pv2_power)
        daily_pv = find_val(["daily_generation", "daily_production", "etdy_ge", "e_today"], 0.0)
        total_pv_energy = find_val(["total_generation", "et_ge", "e_total"], 0.0)

        # Battery
        total_soc = find_val(["soc", "battery_soc", "b_soc", "soh"], 50.0)
        batt_power = find_val(["battery_power", "batt_power", "p_battery", "b_power"], 0.0)
        batt_volt = find_val(["battery_volt", "batt_volt", "v_battery", "b_volt"], 51.2)
        batt_curr = find_val(["battery_curr", "batt_curr", "i_battery", "b_curr"], 0.0)
        batt_temp = find_val(["battery_temp", "batt_temp", "b_temp"], 25.0)

        # Determine battery state
        if batt_power > 20:
            batt_status = "charging"
        elif batt_power < -20:
            batt_status = "discharging"
        else:
            batt_status = "standby"

        # 2 Batteries (Deye system usually pairs 2x 48V batteries in parallel)
        # Check if individual telemetry is exposed, otherwise split evenly
        b1_soc = find_val(["b1_soc", "battery1_soc"], total_soc)
        b2_soc = find_val(["b2_soc", "battery2_soc"], total_soc)

        batt1 = BatteryUnit(
            id=1,
            name="Акумулятор 1",
            soc=b1_soc,
            voltage=batt_volt,
            current=round(batt_curr / 2.0, 2),
            power=round(batt_power / 2.0, 1),
            temperature=batt_temp,
            status=batt_status,
        )

        batt2 = BatteryUnit(
            id=2,
            name="Акумулятор 2",
            soc=b2_soc,
            voltage=batt_volt,
            current=round(batt_curr / 2.0, 2),
            power=round(batt_power / 2.0, 1),
            temperature=batt_temp,
            status=batt_status,
        )

        # Remaining hours estimate
        total_capacity_wh = 10240.0
        est_hours = None
        if batt_status == "discharging" and abs(batt_power) > 50:
            usable_wh = (total_soc - 10.0) / 100.0 * total_capacity_wh
            est_hours = round(max(0.1, usable_wh / abs(batt_power)), 1)
        elif batt_status == "charging" and batt_power > 50:
            needed_wh = (100.0 - total_soc) / 100.0 * total_capacity_wh
            est_hours = round(max(0.1, needed_wh / batt_power), 1)

        # Grid
        grid_volt = find_val(["grid_volt", "ac_volt", "grid_v", "vac1"], 230.0)
        grid_freq = find_val(["grid_freq", "fac", "grid frequency"], 50.0)
        grid_power = find_val(["grid_power", "feed_in_power", "activepower", "p_grid"], 0.0)
        daily_grid_import = find_val(["daily_grid_buy", "e_grid_buy_today"], 0.0)
        daily_grid_export = find_val(["daily_grid_sell", "e_grid_sell_today"], 0.0)
        is_grid_online = grid_volt > 150.0

        # Load
        load_power = find_val(["total_load_power", "load_power", "p_load", "house_load"], 0.0)
        if load_power == 0.0:
            # Power balance: Load = Solar - Battery - Grid
            load_power = max(0.0, total_pv - batt_power + grid_power)
        daily_load = find_val(["daily_consumption", "e_load_today", "daily_load"], 0.0)

        # Inverter
        inv_temp = find_val(["inverter_temp", "inv_temp", "radiator_temp", "t_inv"], 35.0)

        return SystemTelemetry(
            timestamp=datetime.now().isoformat(),
            source="solarman_api",
            solar=SolarSystem(
                pv1_power=pv1_power,
                pv1_voltage=pv1_volt,
                pv1_current=pv1_curr,
                pv2_power=pv2_power,
                pv2_voltage=pv2_volt,
                pv2_current=pv2_curr,
                total_power=total_pv,
                daily_energy=daily_pv,
                total_energy=total_pv_energy,
            ),
            battery=BatterySystem(
                total_soc=total_soc,
                total_power=batt_power,
                voltage=batt_volt,
                current=batt_curr,
                status=batt_status,
                estimated_remaining_hours=est_hours,
                batteries=[batt1, batt2],
            ),
            grid=GridSystem(
                is_online=is_grid_online,
                power=grid_power,
                voltage=grid_volt,
                frequency=grid_freq,
                daily_import=daily_grid_import,
                daily_export=daily_grid_export,
            ),
            load=LoadSystem(
                power=round(load_power, 1),
                daily_energy=daily_load,
            ),
            inverter=InverterStatus(
                model="Deye 1-Phase Hybrid",
                serial_number=self.inverter_sn or "DEYE-CONFIGURED",
                work_mode="Self-Use" if is_grid_online else "Backup EPS",
                temperature=inv_temp,
            ),
        )
