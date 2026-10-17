import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional
from app.config import settings
from app.state import app_state

logger = logging.getLogger("charge_controller")


class ChargeController:
    """Controls grid charging schedules and instant force-charge for Deye inverters."""

    def __init__(self):
        self.is_force_charging: bool = False
        self.force_charge_target_soc: float = 100.0
        self.force_charge_end_time: Optional[float] = None
        self.night_charge_enabled: bool = False
        self.night_start: str = "23:00"
        self.night_end: str = "07:00"
        self._monitor_task: Optional[asyncio.Task] = None

    def _get_deye_client(self):
        try:
            from app.collectors.deye_cloud import DeyeCloudClient
            client = DeyeCloudClient()
            return client._get_client(), client.device_sn or client._auto_device_sn
        except Exception as e:
            logger.warning(f"Could not initialize Deye client for charging command: {e}")
            return None, None

    async def start_force_charge(self, target_soc: float = 100.0, duration_hours: Optional[float] = None) -> Dict[str, Any]:
        """Starts immediate battery charging from grid."""
        self.is_force_charging = True
        self.force_charge_target_soc = target_soc
        if duration_hours:
            self.force_charge_end_time = time.time() + (duration_hours * 3600)
        else:
            self.force_charge_end_time = None

        logger.info(f"Starting force charge from grid to target SOC: {target_soc}%, duration: {duration_hours}h")

        # 1. Send command to physical Deye Inverter via Deye Cloud if configured
        deye, device_sn = self._get_deye_client()
        command_sent = False
        if deye and device_sn:
            try:
                # Set energy pattern to BATTERY_FIRST to prioritize charging batteries from grid/solar
                await asyncio.to_thread(deye.order.energy_pattern, device_sn=device_sn, energy_pattern="BATTERY_FIRST")
                command_sent = True
                logger.info(f"Sent BATTERY_FIRST command to Deye inverter {device_sn}")
            except Exception as e:
                logger.error(f"Failed to send BATTERY_FIRST command to Deye Cloud: {e}")

        # 2. Start background monitor to auto-stop when target SOC is reached or time expires
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        self._monitor_task = asyncio.create_task(self._auto_stop_monitor())

        return {
            "status": "charging_started",
            "target_soc": target_soc,
            "duration_hours": duration_hours,
            "cloud_command_sent": command_sent,
        }

    async def stop_force_charge(self) -> Dict[str, Any]:
        """Stops grid charging and restores standard Self-Use mode."""
        self.is_force_charging = False
        self.force_charge_end_time = None
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()

        logger.info("Stopping force charge. Restoring LOAD_FIRST (Self-Use) mode.")

        # Send command to physical Deye Inverter
        deye, device_sn = self._get_deye_client()
        command_sent = False
        if deye and device_sn:
            try:
                await asyncio.to_thread(deye.order.energy_pattern, device_sn=device_sn, energy_pattern="LOAD_FIRST")
                command_sent = True
                logger.info(f"Restored LOAD_FIRST on Deye inverter {device_sn}")
            except Exception as e:
                logger.error(f"Failed to restore LOAD_FIRST on Deye Cloud: {e}")

        return {"status": "charging_stopped", "cloud_command_sent": command_sent}

    def toggle_night_schedule(self, enable: Optional[bool] = None) -> bool:
        """Toggles automatic night charge (23:00 - 07:00)."""
        if enable is None:
            self.night_charge_enabled = not self.night_charge_enabled
        else:
            self.night_charge_enabled = enable
        logger.info(f"Night charging schedule set to: {self.night_charge_enabled}")
        return self.night_charge_enabled

    async def _auto_stop_monitor(self):
        """Monitors SOC and duration in background, auto-stops when finished."""
        try:
            while self.is_force_charging:
                await asyncio.sleep(15)
                now = time.time()

                # Check duration expiry
                if self.force_charge_end_time and now >= self.force_charge_end_time:
                    logger.info("Force charge duration expired. Auto-stopping...")
                    await self.stop_force_charge()
                    if app_state.alert_callback and app_state.subscriber_chat_ids:
                        title = "⏱️ ЗАРЯДЖАННЯ ЗАВЕРШЕНО ЗА ТАЙМЕРОМ"
                        text = "⚡ Встановлений час заряджання від мережі минув. Інвертор повернуто в автономний режим Self-Use."
                        asyncio.create_task(app_state.alert_callback(app_state.subscriber_chat_ids, title, text))
                    break

                # Check SOC target
                if app_state.latest_telemetry:
                    current_soc = app_state.latest_telemetry.battery.total_soc
                    if current_soc >= (self.force_charge_target_soc - 0.5):
                        logger.info(f"Target SOC {self.force_charge_target_soc}% reached. Auto-stopping...")
                        await self.stop_force_charge()
                        if app_state.alert_callback and app_state.subscriber_chat_ids:
                            title = "🔋 ЗАРЯДЖАННЯ ВІД МЕРЕЖІ ЗАВЕРШЕНО!"
                            text = (
                                f"🎉 <b>Акумулятори успішно заряджено до {current_soc:.1f}%!</b>\n\n"
                                f"Інвертор автоматично переведено у звичайний режим Self-Use."
                            )
                            asyncio.create_task(app_state.alert_callback(app_state.subscriber_chat_ids, title, text))
                        break
        except asyncio.CancelledError:
            pass


charge_controller = ChargeController()
