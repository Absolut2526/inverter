import os
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = Field(default="", description="Token from @BotFather")
    TELEGRAM_ADMIN_CHAT_IDS: str = Field(default="", description="Comma-separated chat IDs for alerts")

    # Web Dashboard & Public URL
    HOST: str = Field(default="0.0.0.0")
    PORT: int = Field(default=8000)
    WEB_BASE_URL: str = Field(default="http://localhost:8000", description="Public URL of the dashboard")
    SECRET_PUSH_TOKEN: str = Field(default="deye-secure-token-12345", description="Token for POST /api/telemetry")

    # Solarman Cloud API
    SOLARMAN_ENABLED: bool = Field(default=False)
    SOLARMAN_APP_ID: str = Field(default="")
    SOLARMAN_APP_SECRET: str = Field(default="")
    SOLARMAN_EMAIL: str = Field(default="")
    SOLARMAN_PASSWORD: str = Field(default="")
    SOLARMAN_INVERTER_SN: str = Field(default="")
    SOLARMAN_API_URL: str = Field(default="https://globalapi.solarmanpv.com")

    # Polling & Simulation
    POLL_INTERVAL_SECONDS: int = Field(default=15)
    SIMULATION_MODE: bool = Field(default=False)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def admin_chat_ids_list(self) -> List[int]:
        if not self.TELEGRAM_ADMIN_CHAT_IDS.strip():
            return []
        ids = []
        for x in self.TELEGRAM_ADMIN_CHAT_IDS.split(","):
            x = x.strip()
            if x.isdigit() or (x.startswith("-") and x[1:].isdigit()):
                ids.append(int(x))
        return ids

    @property
    def is_solarman_configured(self) -> bool:
        return bool(
            self.SOLARMAN_APP_ID
            and self.SOLARMAN_APP_SECRET
            and self.SOLARMAN_EMAIL
            and self.SOLARMAN_PASSWORD
            and self.SOLARMAN_INVERTER_SN
        )


settings = Settings()
