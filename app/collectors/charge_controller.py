import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import zoneinfo
from pydantic import BaseModel, Field

from app.config import settings
from app.state import app_state

logger = logging.getLogger("charge_controller")

KYIV_TZ = zoneinfo.ZoneInfo("Europe/Kyiv")
SCHEDULES_FILE = Path(__file__).resolve().parent.parent.parent / "schedules.json"


class ChargeSchedule(BaseModel):
    id: str
    day_type: str  # "today", "tomorrow", "daily", "monday", "tuesday", etc.
    day_label: str  # "Сьогодні", "Завтра", "Щодня", "Понеділок"
    start_time: str  # "HH:MM" e.g. "14:00"
    end_time: str    # "HH:MM" e.g. "16:00"
    target_soc: float = 100.0
    is_active: bool = True
    is_currently_running: bool = False
    created_date: str = Field(default_factory=lambda: datetime.now(KYIV_TZ).strftime("%Y-%m-%d"))


class ChargeController:
    """Controls grid charging schedules and instant force-charge for Deye inverters."""

    def __init__(self):
        self.is_force_charging: bool = False
        self.force_charge_target_soc: float = 100.0
        self.force_charge_end_time: Optional[float] = None
        self.night_charge_enabled: bool = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._schedule_loop_task: Optional[asyncio.Task] = None
        self.schedules: List[ChargeSchedule] = []
        self._load_schedules()

    def _load_schedules(self):
        if SCHEDULES_FILE.exists():
            try:
                data = json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
                self.schedules = [ChargeSchedule(**item) for item in data]
                logger.info(f"Loaded {len(self.schedules)} charge schedules from disk")
            except Exception as e:
                logger.error(f"Failed to load schedules: {e}")
                self.schedules = []

    def _save_schedules(self):
        try:
            data = [s.model_dump() for s in self.schedules]
            SCHEDULES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save schedules: {e}")

    def _get_deye_client(self):
        try:
            client = getattr(app_state, "active_deye_client", None)
            if not client:
                from app.collectors.deye_cloud import DeyeCloudClient
                client = DeyeCloudClient()
            device_sn = getattr(app_state, "inverter_sn", None) or client.device_sn or getattr(client, "_auto_device_sn", None)
            if not device_sn and hasattr(client, "discover_device_sn"):
                device_sn = client.discover_device_sn()
            return client._get_client(), device_sn
        except Exception as e:
            logger.warning(f"Could not initialize Deye client for charging command: {e}")
            return None, None

    def start_scheduler_loop(self):
        """Starts the background loop that triggers scheduled charging."""
        if self._schedule_loop_task is None or self._schedule_loop_task.done():
            self._schedule_loop_task = asyncio.create_task(self._run_schedule_checker())
            logger.info("Charge schedule checker loop started")

    async def start_force_charge(self, target_soc: float = 100.0, duration_hours: Optional[float] = None) -> Dict[str, Any]:
        """Starts immediate battery charging from grid."""
        self.is_force_charging = True
        self.force_charge_target_soc = target_soc
        if duration_hours:
            self.force_charge_end_time = time.time() + (duration_hours * 3600)
        else:
            self.force_charge_end_time = None

        logger.info(f"Starting force charge from grid to target SOC: {target_soc}%, duration: {duration_hours}h")

        deye, device_sn = self._get_deye_client()
        command_sent = False
        order_id = None
        error_msg = None

        is_cloud_mode = bool(settings.DEYE_ENABLED and settings.is_deye_configured)

        if is_cloud_mode:
            if not deye or not device_sn:
                error_msg = "Не вдалося отримати серійний номер інвертора. Вкажіть DEYE_DEVICE_SN на Render."
                logger.error(error_msg)
            else:
                try:
                    # 1. Send GRID_CHARGE enable command with exact OpenAPI schema:
                    # {"deviceSn": ..., "batteryModeType": "GRID_CHARGE", "action": "on"}
                    body = {
                        "deviceSn": str(device_sn),
                        "batteryModeType": "GRID_CHARGE",
                        "action": "on",
                    }
                    order = await asyncio.to_thread(
                        deye.post,
                        "v1.0/order/battery/modeControl",
                        json=body,
                    )
                    order_id = getattr(order, "order_id", None)
                    command_sent = True
                    logger.info(f"Sent GRID_CHARGE enable command to Deye inverter {device_sn} (Order ID: {order_id})")

                    # 2. Set BATTERY_FIRST to prioritize battery for incoming energy
                    try:
                        await asyncio.to_thread(
                            deye.post,
                            "v1.0/order/sys/energyPattern/update",
                            json={"deviceSn": str(device_sn), "energyPattern": "BATTERY_FIRST"},
                        )
                    except Exception as ep_err:
                        logger.warning(f"BATTERY_FIRST secondary command warning: {ep_err}")

                except Exception as e:
                    error_msg = f"Deye Cloud API error: {e}"
                    logger.error(f"Failed to send GRID_CHARGE command: {e}", exc_info=True)

        # Start background monitor to auto-stop when target SOC is reached or time expires
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        self._monitor_task = asyncio.create_task(self._auto_stop_monitor())

        success = command_sent if is_cloud_mode else True

        return {
            "status": "charging_started" if success else "error",
            "success": success,
            "target_soc": target_soc,
            "duration_hours": duration_hours,
            "cloud_command_sent": command_sent,
            "order_id": order_id,
            "device_sn": device_sn,
            "is_simulator": not is_cloud_mode,
            "error_message": error_msg,
        }

    async def stop_force_charge(self) -> Dict[str, Any]:
        """Stops grid charging and restores standard Self-Use mode."""
        self.is_force_charging = False
        self.force_charge_end_time = None
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()

        logger.info("Stopping force charge. Disabling GRID_CHARGE and restoring LOAD_FIRST.")

        deye, device_sn = self._get_deye_client()
        command_sent = False
        order_id = None
        error_msg = None

        is_cloud_mode = bool(settings.DEYE_ENABLED and settings.is_deye_configured)

        if is_cloud_mode and deye and device_sn:
            try:
                # 1. Disable GRID_CHARGE
                body = {
                    "deviceSn": str(device_sn),
                    "batteryModeType": "GRID_CHARGE",
                    "action": "off",
                }
                order = await asyncio.to_thread(
                    deye.post,
                    "v1.0/order/battery/modeControl",
                    json=body,
                )
                order_id = getattr(order, "order_id", None)
                command_sent = True
                logger.info(f"Disabled GRID_CHARGE on Deye inverter {device_sn} (Order ID: {order_id})")

                # 2. Restore LOAD_FIRST (Self-Use)
                try:
                    await asyncio.to_thread(
                        deye.post,
                        "v1.0/order/sys/energyPattern/update",
                        json={"deviceSn": str(device_sn), "energyPattern": "LOAD_FIRST"},
                    )
                except Exception as ep_err:
                    logger.warning(f"LOAD_FIRST restore warning: {ep_err}")

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Failed to disable GRID_CHARGE on Deye Cloud: {e}", exc_info=True)

        return {
            "status": "charging_stopped",
            "cloud_command_sent": command_sent,
            "order_id": order_id,
            "error_message": error_msg,
        }

    def add_schedule(self, day_type: str, start_time: str, end_time: str, target_soc: float = 100.0) -> ChargeSchedule:
        """Adds a new charging schedule."""
        labels = {
            "today": "Сьогодні",
            "tomorrow": "Завтра",
            "daily": "Щодня",
            "monday": "Щопонеділка",
            "tuesday": "Щовівторка",
            "wednesday": "Щосереди",
            "thursday": "Щочетверга",
            "friday": "Щоп'ятниці",
            "saturday": "Щосуботи",
            "sunday": "Щонеділі",
        }
        day_label = labels.get(day_type, day_type)
        sched_id = f"sch_{int(time.time())}_{len(self.schedules) + 1}"

        sched = ChargeSchedule(
            id=sched_id,
            day_type=day_type,
            day_label=day_label,
            start_time=start_time,
            end_time=end_time,
            target_soc=target_soc,
            is_active=True,
            is_currently_running=False,
        )
        self.schedules.append(sched)
        self._save_schedules()
        logger.info(f"Added charge schedule: {day_label} {start_time}-{end_time} (SOC: {target_soc}%)")
        return sched

    def delete_schedule(self, sched_id: str) -> bool:
        """Deletes a schedule by ID."""
        initial_count = len(self.schedules)
        self.schedules = [s for s in self.schedules if s.id != sched_id]
        if len(self.schedules) < initial_count:
            self._save_schedules()
            logger.info(f"Deleted charge schedule {sched_id}")
            return True
        return False

    def toggle_night_schedule(self, enable: Optional[bool] = None) -> bool:
        """Toggles standard night schedule (daily 23:00 - 07:00)."""
        if enable is None:
            self.night_charge_enabled = not self.night_charge_enabled
        else:
            self.night_charge_enabled = enable

        # Check if night schedule already exists in list
        existing = [s for s in self.schedules if s.day_type == "daily" and s.start_time == "23:00" and s.end_time == "07:00"]
        if self.night_charge_enabled:
            if not existing:
                self.add_schedule("daily", "23:00", "07:00", 100.0)
        else:
            for s in existing:
                self.delete_schedule(s.id)

        return self.night_charge_enabled

    async def _run_schedule_checker(self):
        """Periodically checks active schedules against Ukrainian local time."""
        weekday_map = {
            0: "monday",
            1: "tuesday",
            2: "wednesday",
            3: "thursday",
            4: "friday",
            5: "saturday",
            6: "sunday",
        }

        while True:
            try:
                now_kyiv = datetime.now(KYIV_TZ)
                now_date_str = now_kyiv.strftime("%Y-%m-%d")
                now_time_str = now_kyiv.strftime("%H:%M")
                now_weekday = weekday_map.get(now_kyiv.weekday())

                for sched in list(self.schedules):
                    if not sched.is_active:
                        continue

                    # 1. Determine if schedule applies to today
                    applies_today = False
                    if sched.day_type == "daily":
                        applies_today = True
                    elif sched.day_type == "today":
                        applies_today = (sched.created_date == now_date_str)
                    elif sched.day_type == "tomorrow":
                        created_dt = datetime.strptime(sched.created_date, "%Y-%m-%d").date()
                        tomorrow_dt = created_dt + timedelta(days=1)
                        applies_today = (tomorrow_dt == now_kyiv.date())
                    elif sched.day_type == now_weekday:
                        applies_today = True

                    if not applies_today:
                        continue

                    # 2. Check time interval
                    # Handle overnight interval (e.g. 23:00 to 07:00) vs same day (e.g. 14:00 to 16:00)
                    is_in_window = False
                    if sched.start_time <= sched.end_time:
                        is_in_window = (sched.start_time <= now_time_str < sched.end_time)
                    else:
                        # Overnight window
                        is_in_window = (now_time_str >= sched.start_time or now_time_str < sched.end_time)

                    # 3. Trigger Start
                    if is_in_window and not sched.is_currently_running and not self.is_force_charging:
                        sched.is_currently_running = True
                        logger.info(f"Triggering scheduled charge: {sched.day_label} {sched.start_time}-{sched.end_time}")
                        await self.start_force_charge(target_soc=sched.target_soc)
                        self._save_schedules()

                        if app_state.alert_callback and app_state.subscriber_chat_ids:
                            title = "⚡ РОЗПОЧАТО ЗАРЯДЖАННЯ ЗА РОЗКЛАДОМ!"
                            text = (
                                f"🔌 <b>Інвертор Deye розпочав заряджання батарей від мережі 220В.</b>\n\n"
                                f"📅 Графік: <b>{sched.day_label} ({sched.start_time} - {sched.end_time})</b>\n"
                                f"🎯 Ціль: <b>до {sched.target_soc:.0f}%</b>"
                            )
                            asyncio.create_task(app_state.alert_callback(app_state.subscriber_chat_ids, title, text))

                    # 4. Trigger Stop
                    elif not is_in_window and sched.is_currently_running:
                        sched.is_currently_running = False
                        logger.info(f"Ending scheduled charge: {sched.day_label} {sched.start_time}-{sched.end_time}")
                        await self.stop_force_charge()

                        # If one-off schedule (today/tomorrow), deactivate after run
                        if sched.day_type in ["today", "tomorrow"]:
                            sched.is_active = False

                        self._save_schedules()

                        if app_state.alert_callback and app_state.subscriber_chat_ids:
                            title = "✅ ЗАПЛАНОВАНЕ ЗАРЯДЖАННЯ ЗАВЕРШЕНО"
                            text = (
                                f"⚡ Час за графіком <b>{sched.start_time} - {sched.end_time}</b> минув.\n"
                                f"Інвертор повернуто в автономний режим Self-Use."
                            )
                            asyncio.create_task(app_state.alert_callback(app_state.subscriber_chat_ids, title, text))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in charge schedule loop: {e}", exc_info=True)

            await asyncio.sleep(20)

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
