import asyncio
import logging
from typing import Set
from aiogram import Bot
from app.config import settings

logger = logging.getLogger("telegram_notifier")


async def send_alert_to_subscribers(bot: Bot, chat_ids: Set[int], title: str, text: str):
    """Sends immediate alert notifications to all registered subscribers."""
    all_recipients = set(chat_ids)
    for admin_id in settings.admin_chat_ids_list:
        all_recipients.add(admin_id)

    if not all_recipients:
        logger.warning("No subscribers or admin chat IDs found to send alert")
        return

    logger.info(f"Sending alert '{title}' to {len(all_recipients)} subscribers...")
    for chat_id in all_recipients:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"<b>{title}</b>\n\n{text}",
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Failed to send alert to chat_id {chat_id}: {e}")
