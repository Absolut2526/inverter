import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
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
from app.state import app_state

logger = logging.getLogger("deye_cloud")


class DeyeCloudClient:
    """Official Deye Cloud API client (https://developer.deyecloud.com)."""

    def __init__(self):
        self.app_id = settings.DEYE_APP_ID or settings.SOLARMAN_APP_ID
        self.app_secret = settings.DEYE_APP_SECRET or settings.SOLARMAN_APP_SECRET
        self.email = settings.DEYE_EMAIL or settings.SOLARMAN_EMAIL
        self.password = settings.DEYE_PASSWORD or settings.SOLARMAN_PASSWORD
        self.device_sn = settings.DEYE_DEVICE_SN or settings.SOLARMAN_INVERTER_SN
        self._deye = None
        self._auto_device_sn: Optional[str] = None
        self._auto_station_id: Optional[int] = None

    def _get_client(self):
        if not (self.app_id and self.app_secret and self.email and self.password):
            return None
        if self._deye is None:
            try:
                from deyecloud import DeyeCloud
                self._deye = DeyeCloud(
                    app_id=self.app_id,
                    app_secret=self.app_secret,
                    email=self.email,
                    password=self.password,
                )
                logger.info(f"Initialized DeyeCloud SDK for user {self.email}")
            except Exception as e:
                logger.error(f"Failed to initialize DeyeCloud SDK: {e}", exc_info=True)
                return None
        return self._deye

    def discover_device_sn(self) -> Optional[str]:
        """Discovers inverter SN from stations if not already resolved."""
        if self.device_sn:
            if not app_state.inverter_sn:
                app_state.inverter_sn = self.device_sn
            return self.device_sn
        if self._auto_device_sn:
            if not app_state.inverter_sn:
                app_state.inverter_sn = self._auto_device_sn
            return self._auto_device_sn
        client = self._get_client()
        if not client:
            return None
        try:
            stations = list(client.station.list())
            if stations:
                first_st = stations[0]
                self._auto_station_id = first_st.station_id
                devices = list(first_st.devices())
                if devices:
                    dev_info = devices[0]
                    sn = getattr(dev_info, "device_sn", None) or getattr(dev_info, "deviceSn", None)
                    if isinstance(dev_info, dict):
                        sn = dev_info.get("device_sn") or dev_info.get("deviceSn")
                    if sn:
                        self._auto_device_sn = str(sn)
                        app_state.inverter_sn = str(sn)
                        logger.info(f"Discovered Inverter SN: {self._auto_device_sn}")
                        return self._auto_device_sn
        except Exception as e:
            logger.warning(f"Device discovery failed: {e}")
        return None

    async def fetch_current_data(self) -> Optional[SystemTelemetry]:
        """Runs the blocking Deye Cloud fetch in a background thread."""
        return await asyncio.to_thread(self._fetch_sync)

    def _fetch_sync(self) -> Optional[SystemTelemetry]:
        client = self._get_client()
        if not client:
            return None

        try:
            device = None
            station_latest = None

            # 1. Discover stations and devices if SN not explicitly configured or to get station totals
            try:
                stations = list(client.station.list())
                if stations:
                    first_st = stations[0]
                    self._auto_station_id = first_st.station_id
                    station_latest = first_st.latest()
                    logger.info(f"Found Deye Station: {first_st.station_name} (ID: {first_st.station_id})")

                    # If device_sn not set, discover from station devices
                    if not self.device_sn and not self._auto_device_sn:
                        devices = list(first_st.devices())
                        if devices:
                            dev_info = devices[0]
                            # Usually dev_info is a dict or Device object
                            self._auto_device_sn = getattr(dev_info, "device_sn", None) or getattr(dev_info, "deviceSn", None)
                            if isinstance(dev_info, dict):
                                self._auto_device_sn = dev_info.get("device_sn") or dev_info.get("deviceSn")
                            logger.info(f"Auto-discovered Inverter SN: {self._auto_device_sn}")
            except Exception as e:
                logger.warning(f"Station discovery error (non-fatal): {e}")

            target_sn = self.device_sn or self._auto_device_sn
            if target_sn and not app_state.inverter_sn:
                app_state.inverter_sn = str(target_sn)

            # 2. Fetch device telemetry
            if target_sn:
                try:
                    device = client.device(target_sn)
                    device._fetch()
                except Exception as e:
                    logger.warning(f"Could not fetch device {target_sn}: {e}")

            # Merge data from device and/or station
            d: Dict[str, Any] = {}
            if device and hasattr(device, "__dict__"):
                d.update(device.__dict__)
            if station_latest and hasattr(station_latest, "__dict__"):
                for k, v in station_latest.__dict__.items():
                    if k not in d or d[k] is None:
                        d[k] = v

            if not d:
                logger.warning("No data retrieved from Deye Cloud")
                return None

            def get_f(keys: List[str], default: float = 0.0) -> float:
                for k in keys:
                    v = d.get(k)
                    if v is not None:
                        try:
                            return float(v)
                        except (ValueError, TypeError):
                            pass
                return default

            # Solar PV
            pv1_power = get_f(["pv1_power", "pv1_p", "pv1p"], 0.0)
            pv1_volt = get_f(["pv1_volt", "pv1_v", "pv1_voltage"], 0.0)
            pv1_curr = get_f(["pv1_curr", "pv1_i", "pv1_current"], 0.0)

            pv2_power = get_f(["pv2_power", "pv2_p", "pv2p"], 0.0)
            pv2_volt = get_f(["pv2_volt", "pv2_v", "pv2_voltage"], 0.0)
            pv2_curr = get_f(["pv2_curr", "pv2_i", "pv2_current"], 0.0)

            total_pv = get_f(
                ["total_pv", "generation_power", "pv_power", "active_power", "total_power"],
                pv1_power + pv2_power,
            )
            daily_pv = get_f(["daily_generation", "daily_production", "e_today", "day_generation"], 0.0)
            total_pv_energy = get_f(["total_generation", "e_total", "total_energy"], 0.0)

            # Battery
            total_soc = get_f(["soc", "battery_soc", "b_soc"], 50.0)
            batt_power = get_f(["battery_power", "batt_power", "p_battery", "b_power"], 0.0)
            batt_volt = get_f(["battery_volt", "batt_volt", "v_battery", "b_volt"], 51.2)
            batt_curr = get_f(["battery_curr", "batt_curr", "i_battery", "b_curr"], 0.0)
            batt_temp = get_f(["battery_temp", "batt_temp", "b_temp"], 25.0)

            batt_status = "charging" if batt_power > 20 else ("discharging" if batt_power < -20 else "standby")

            b1_soc = get_f(["b1_soc", "battery1_soc"], total_soc)
            b2_soc = get_f(["b2_soc", "battery2_soc"], total_soc)

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
            grid_volt = get_f(["grid_volt", "ac_volt", "grid_voltage", "vac1"], 230.0)
            grid_freq = get_f(["grid_freq", "fac", "grid_frequency"], 50.0)
            grid_power = get_f(["grid_power", "feed_in_power", "active_power_grid", "p_grid"], 0.0)
            daily_grid_import = get_f(["daily_grid_buy", "e_grid_buy_today", "day_grid_buy"], 0.0)
            daily_grid_export = get_f(["daily_grid_sell", "e_grid_sell_today", "day_grid_sell"], 0.0)
            is_grid_online = grid_volt > 150.0

            # Load
            load_power = get_f(["total_load_power", "load_power", "p_load", "house_load", "consumption_power"], 0.0)
            if load_power == 0.0:
                load_power = max(0.0, total_pv - batt_power + grid_power)
            daily_load = get_f(["daily_consumption", "e_load_today", "daily_load", "day_consumption"], 0.0)

            # Inverter
            inv_temp = get_f(["inverter_temp", "inv_temp", "radiator_temp", "t_inv"], 35.0)
            device_model = str(d.get("device_model") or d.get("device_type") or "Deye Hybrid 1-Phase")

            return SystemTelemetry(
                timestamp=datetime.now().isoformat(),
                source="deye_cloud",
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
                    model=device_model,
                    serial_number=target_sn or "DEYE-CLOUD-CONNECTED",
                    work_mode="Self-Use" if is_grid_online else "Backup EPS",
                    temperature=inv_temp,
                ),
            )
        except Exception as e:
            logger.error(f"Error fetching from Deye Cloud: {e}", exc_info=True)
            return None
