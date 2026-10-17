import asyncio
import logging
import time
from collections import deque
from datetime import datetime
from typing import Callable, Coroutine, Dict, List, Optional, Set
from fastapi import WebSocket
from app.models import SystemTelemetry, TelemetryHistoryPoint

logger = logging.getLogger("state")


class AppState:
    def __init__(self):
        self.latest_telemetry: Optional[SystemTelemetry] = None
        # Keep rolling history for graphs (last 120 points, e.g. every 15s = 30 minutes, or downsampled)
        self.history: deque[TelemetryHistoryPoint] = deque(maxlen=240)
        self.ws_connections: Set[WebSocket] = set()
        
        # Telegram subscribers for alerts
        self.subscriber_chat_ids: Set[int] = set()

        # State transition tracking for alerts
        self.last_grid_online: Optional[bool] = None
        self.alerted_batt_low_20: bool = False
        self.alerted_batt_low_10: bool = False
        self.alerted_batt_full: bool = False

        # Blackout debouncing: wait 60 seconds to confirm true outage
        self.blackout_pending_task: Optional[asyncio.Task] = None
        self.blackout_confirmed: bool = False
        self.blackout_start_time: Optional[float] = None

        # Alert callback hook: async def hook(chat_ids: Set[int], title: str, text: str)
        self.alert_callback: Optional[Callable[[Set[int], str, str], Coroutine]] = None

    async def connect_ws(self, websocket: WebSocket):
        await websocket.accept()
        self.ws_connections.add(websocket)
        logger.info(f"WebSocket client connected. Total clients: {len(self.ws_connections)}")
        # Send latest telemetry immediately
        if self.latest_telemetry:
            try:
                await websocket.send_json(self.latest_telemetry.model_dump())
            except Exception:
                pass

    def disconnect_ws(self, websocket: WebSocket):
        self.ws_connections.discard(websocket)
        logger.info(f"WebSocket client disconnected. Total clients: {len(self.ws_connections)}")

    async def broadcast_ws(self, data: dict):
        if not self.ws_connections:
            return
        dead = []
        for ws in self.ws_connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ws_connections.discard(ws)

    def add_subscriber(self, chat_id: int):
        self.subscriber_chat_ids.add(chat_id)

    def remove_subscriber(self, chat_id: int):
        self.subscriber_chat_ids.discard(chat_id)

    async def update_telemetry(self, telemetry: SystemTelemetry):
        self.latest_telemetry = telemetry

        # Record history point
        time_str = datetime.fromisoformat(telemetry.timestamp).strftime("%H:%M:%S")
        pt = TelemetryHistoryPoint(
            time=time_str,
            solar_power=telemetry.solar.total_power,
            battery_power=telemetry.battery.total_power,
            load_power=telemetry.load.power,
            grid_power=telemetry.grid.power,
            battery_soc=telemetry.battery.total_soc,
        )
        self.history.append(pt)

        # Broadcast to all live WebSockets
        payload = telemetry.model_dump()
        asyncio.create_task(self.broadcast_ws(payload))

        # Check for alert conditions
        await self._check_alerts(telemetry)

    async def _confirm_blackout_after_delay(self, delay_seconds: int = 60):
        """Waits delay_seconds and checks if grid is still offline before alerting."""
        try:
            logger.info(f"Grid offline detected. Waiting {delay_seconds}s to verify true outage...")
            await asyncio.sleep(delay_seconds)

            if self.latest_telemetry and not self.latest_telemetry.grid.is_online:
                self.blackout_confirmed = True
                telemetry = self.latest_telemetry
                soc = telemetry.battery.total_soc
                load_val = telemetry.load.power
                load_str = f"{(load_val / 1000.0):.2f} кВт" if load_val >= 1000.0 else f"{int(round(load_val))} Вт"
                hours_str = f"{telemetry.battery.estimated_remaining_hours} год" if telemetry.battery.estimated_remaining_hours else "кілька годин"

                title = "⚠️ ЗНИКЛО СВІТЛО В МЕРЕЖІ!"
                text = (
                    f"🚨 <b>УВАГА: Мережа 220В вимкнена (Блекаут)!</b>\n"
                    f"<i>(Відсутність напруги підтверджено > 1 хв)</i>\n\n"
                    f"⚡ Інвертор перейшов на автономне живлення від акумуляторів.\n"
                    f"🔋 Поточний заряд: <b>{soc:.1f}%</b>\n"
                    f"🏠 Навантаження будинку: <b>{load_str}</b>\n"
                    f"⏳ Орієнтовний час роботи: ~<b>{hours_str}</b>"
                )
                logger.info("Blackout confirmed (>60s). Sending alert to subscribers.")
                if self.alert_callback and self.subscriber_chat_ids:
                    await self.alert_callback(self.subscriber_chat_ids, title, text)
            else:
                logger.info("Grid restored within 60s. Outage notification suppressed.")
        except asyncio.CancelledError:
            logger.info("Blackout verification task cancelled (power quickly restored).")
        finally:
            self.blackout_pending_task = None

    async def _check_alerts(self, telemetry: SystemTelemetry):
        if not self.alert_callback or not self.subscriber_chat_ids:
            return

        grid_online = telemetry.grid.is_online
        soc = telemetry.battery.total_soc

        # First run initialization
        if self.last_grid_online is None:
            self.last_grid_online = grid_online
            return

        # 1. Grid Disconnected (from online -> offline)
        if self.last_grid_online is True and grid_online is False:
            self.blackout_start_time = time.time()
            if self.blackout_pending_task and not self.blackout_pending_task.done():
                self.blackout_pending_task.cancel()
            self.blackout_pending_task = asyncio.create_task(self._confirm_blackout_after_delay(60))

        # 2. Grid Restored (from offline -> online)
        elif self.last_grid_online is False and grid_online is True:
            # If outage lasted less than 60s, cancel pending alert (suppresses false alarm!)
            if self.blackout_pending_task and not self.blackout_pending_task.done():
                self.blackout_pending_task.cancel()
                self.blackout_pending_task = None
                logger.info("Micro-outage (<60s) resolved. False alarm suppressed.")

            # Only notify if outage was confirmed and announced
            if self.blackout_confirmed:
                self.blackout_confirmed = False
                dur_text = ""
                if self.blackout_start_time:
                    elapsed_min = int((time.time() - self.blackout_start_time) / 60)
                    if elapsed_min >= 60:
                        h = elapsed_min // 60
                        m = elapsed_min % 60
                        dur_text = f"\n⏱️ Тривалість відключення: <b>{h} год {m} хв</b>"
                    elif elapsed_min > 0:
                        dur_text = f"\n⏱️ Тривалість відключення: <b>~{elapsed_min} хв</b>"

                title = "✅ СВІТЛО ПОВЕРНУЛОСЯ!"
                text = (
                    f"🎉 <b>Чудова новина: Напруга в мережі відновлена!</b>\n\n"
                    f"🔌 Напруга мережі: <b>{telemetry.grid.voltage:.1f} В</b> ({telemetry.grid.frequency:.1f} Гц)\n"
                    f"🔋 Заряд акумуляторів: <b>{soc:.1f}%</b>\n"
                    f"⚡ Інвертор розпочав синхронізацію та живлення будинку.{dur_text}"
                )
                asyncio.create_task(self.alert_callback(self.subscriber_chat_ids, title, text))

            self.blackout_start_time = None

        self.last_grid_online = grid_online

        # 3. Battery Low Warning (<20%)
        if soc <= 20.0 and not self.alerted_batt_low_20:
            self.alerted_batt_low_20 = True
            title = "🪫 НИЗЬКИЙ ЗАРЯД БАТАРЕЇ (<20%)"
            text = (
                f"⚠️ <b>Рівень заряду батарей впав до {soc:.1f}%!</b>\n\n"
                f"Будь ласка, зменшіть навантаження в будинку для збереження резерву."
            )
            asyncio.create_task(self.alert_callback(self.subscriber_chat_ids, title, text))
        elif soc > 25.0:
            self.alerted_batt_low_20 = False

        # 4. Battery Critical Warning (<10%)
        if soc <= 10.0 and not self.alerted_batt_low_10:
            self.alerted_batt_low_10 = True
            title = "🚨 КРИТИЧНИЙ ЗАРЯД БАТАРЕЇ (<10%)"
            text = (
                f"🛑 <b>Критичний рівень заряду: {soc:.1f}%!</b>\n\n"
                f"Скоро відбудеться захисне вимкнення інвертора. Терміново вимкніть споживачі!"
            )
            asyncio.create_task(self.alert_callback(self.subscriber_chat_ids, title, text))
        elif soc > 15.0:
            self.alerted_batt_low_10 = False

        # 5. Battery Full (100%)
        if soc >= 99.5 and not self.alerted_batt_full:
            self.alerted_batt_full = True
            title = "🔋 БАТАРЕЇ ПОВНІСТЮ ЗАРЯДЖЕНІ"
            text = (
                f"⚡ <b>Акумулятори заряджено на 100%!</b>\n\n"
                f"Усі надлишки сонячної генерації тепер можуть спрямовуватися на додаткові потреби або в мережу."
            )
            asyncio.create_task(self.alert_callback(self.subscriber_chat_ids, title, text))
        elif soc < 95.0:
            self.alerted_batt_full = False


app_state = AppState()
