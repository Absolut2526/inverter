import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router as api_router
from app.api.websocket import router as ws_router
from app.bot.handlers import router as bot_router
from app.bot.notifier import send_alert_to_subscribers
from app.collectors.simulator import TelemetrySimulator
from app.collectors.solarman_api import SolarmanApiClient
from app.config import settings
from app.state import app_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Global references for lifespan management
bot_instance: Bot | None = None
dp_instance: Dispatcher | None = None
collector_task: asyncio.Task | None = None
polling_task: asyncio.Task | None = None


async def run_telemetry_loop():
    """Background polling loop for Deye telemetry."""
    simulator = TelemetrySimulator()
    solarman = SolarmanApiClient() if settings.is_solarman_configured else None

    # Pre-populate some history points for the chart
    for _ in range(30):
        dummy = simulator.generate()
        app_state.history.append(
            from_telemetry_to_history(dummy)
        )

    logger.info(
        f"Telemetry loop started. Mode: {'Solarman Open API' if solarman else 'Simulator/Push'}"
    )

    interval = 15 if solarman else 3  # Faster updates in demo/push mode

    while True:
        try:
            telemetry = None
            if solarman and settings.SOLARMAN_ENABLED:
                telemetry = await solarman.fetch_current_data()
                if not telemetry:
                    logger.warning("Solarman fetch failed, keeping previous state")

            if not telemetry:
                # Use simulator if Solarman not enabled or as fallback
                telemetry = simulator.generate()

            await app_state.update_telemetry(telemetry)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in telemetry loop: {e}", exc_info=True)

        await asyncio.sleep(interval)


def from_telemetry_to_history(t):
    from datetime import datetime
    from app.models import TelemetryHistoryPoint
    return TelemetryHistoryPoint(
        time=datetime.fromisoformat(t.timestamp).strftime("%H:%M:%S"),
        solar_power=t.solar.total_power,
        battery_power=t.battery.total_power,
        load_power=t.load.power,
        grid_power=t.grid.power,
        battery_soc=t.battery.total_soc,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_instance, dp_instance, collector_task, polling_task

    # 1. Start Telegram Bot if token provided
    if settings.TELEGRAM_BOT_TOKEN:
        logger.info("Initializing Telegram Bot with aiogram 3...")
        bot_instance = Bot(
            token=settings.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        dp_instance = Dispatcher()
        dp_instance.include_router(bot_router)

        # Wire up alert callback to the Telegram bot instance
        async def alert_hook(chat_ids, title, text):
            if bot_instance:
                await send_alert_to_subscribers(bot_instance, chat_ids, title, text)

        app_state.alert_callback = alert_hook

        # Start bot polling in background
        polling_task = asyncio.create_task(dp_instance.start_polling(bot_instance))
        logger.info("Telegram Bot polling started successfully.")
    else:
        logger.warning(
            "TELEGRAM_BOT_TOKEN not set. Telegram Bot will be disabled until token is configured in .env."
        )

    # 2. Start telemetry collector
    collector_task = asyncio.create_task(run_telemetry_loop())

    yield

    # Clean shutdown
    logger.info("Shutting down application...")
    if collector_task:
        collector_task.cancel()
    if polling_task:
        polling_task.cancel()
    if bot_instance:
        await bot_instance.session.close()


app = FastAPI(
    title="Deye Solar & Battery Real-Time Monitor",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount static files
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Include Routers
app.include_router(api_router)
app.include_router(ws_router)


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Renders the main dashboard."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "web_url": settings.WEB_BASE_URL,
            "has_bot": bool(settings.TELEGRAM_BOT_TOKEN),
        },
    )


@app.get("/health")
async def health_check():
    """Health check endpoint for Render."""
    return {
        "status": "healthy",
        "has_telemetry": app_state.latest_telemetry is not None,
        "active_ws_clients": len(app_state.ws_connections),
        "subscribers": len(app_state.subscriber_chat_ids),
    }
