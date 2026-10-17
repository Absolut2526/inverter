import logging
from datetime import datetime
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from app.config import settings
from app.models import SystemTelemetry
from app.state import app_state

logger = logging.getLogger("telegram_handlers")
router = Router()


def make_progress_bar(percent: float, length: int = 10) -> str:
    clamped = max(0.0, min(100.0, percent))
    filled = int(round((clamped / 100.0) * length))
    empty = length - filled
    return "█" * filled + "░" * empty


def format_status_message(t: SystemTelemetry) -> str:
    now_str = datetime.now().strftime("%H:%M:%S")

    # Grid icon & label
    if t.grid.is_online:
        grid_line = f"🟢 <b>Мережа 220В: Є СВІТЛО</b> ({t.grid.voltage:.1f} В, {t.grid.frequency:.1f} Гц)"
        if t.grid.power > 30:
            grid_flow = f"   📥 Споживання з мережі: <b>{int(t.grid.power)} Вт</b>"
        elif t.grid.power < -30:
            grid_flow = f"   📤 Експорт в мережу: <b>{int(abs(t.grid.power))} Вт</b>"
        else:
            grid_flow = "   ⚖️ Мережа в нульовому балансі"
    else:
        grid_line = "🔴 <b>Мережа 220В: БЛЕКАУТ / ВІДКЛЮЧЕНО</b>"
        grid_flow = "   ⚠️ Живлення виключно від акумуляторів і сонця"

    # Battery Status
    batt_w = int(t.battery.total_power)
    if batt_w > 20:
        batt_mode = f"⚡ <b>Заряджається</b> (+{batt_w} Вт)"
    elif batt_w < -20:
        batt_mode = f"🔻 <b>Розряджається</b> ({abs(batt_w)} Вт)"
    else:
        batt_mode = "💤 <b>Очікування (Standby)</b>"

    time_remain = ""
    if t.battery.estimated_remaining_hours:
        action = "розряду" if batt_w < -20 else "заряду"
        time_remain = f"\n   ⏳ Час до {action}: ~<b>{t.battery.estimated_remaining_hours} год</b>"

    # Sub-batteries
    batt_details = ""
    for b in t.battery.batteries:
        b_bar = make_progress_bar(b.soc, 6)
        batt_details += f"\n   • <b>{b.name}</b>: [{b_bar}] <b>{b.soc:.1f}%</b> ({b.voltage:.1f}В, {b.temperature:.1f}°C)"

    # Solar power
    solar_total = int(t.solar.total_power)
    pv1_w = int(t.solar.pv1_power)
    pv2_w = int(t.solar.pv2_power)

    # Home load
    load_w = int(t.load.power)

    # Source tag
    if t.source == "deye_cloud":
        source_tag = "🌐 Deye Cloud"
    elif t.source == "solarman_api":
        source_tag = "🌐 Solarman Cloud"
    elif t.source == "local_push":
        source_tag = "📡 Локальний шлюз"
    else:
        source_tag = "🧪 Демо-режим (Симуляція)"

    msg = (
        f"☀️ <b>СТАНЦІЯ DEYE HYBRID</b> | 🕒 {now_str}\n"
        f"<i>Джерело: {source_tag}</i>\n\n"
        f"☀️ <b>СОНЯЧНІ ПАНЕЛІ: {solar_total} Вт</b>\n"
        f"   • PV1 (Схід/Південь): {pv1_w} Вт ({t.solar.pv1_voltage:.1f}В, {t.solar.pv1_current:.1f}А)\n"
        f"   • PV2 (Захід): {pv2_w} Вт ({t.solar.pv2_voltage:.1f}В, {t.solar.pv2_current:.1f}А)\n"
        f"   • Генерація за сьогодні: <b>{t.solar.daily_energy:.2f} кВт⋅год</b>\n\n"
        f"🔋 <b>БАТАРЕЇ: {t.battery.total_soc:.1f}%</b> [{make_progress_bar(t.battery.total_soc)}]\n"
        f"   • Режим: {batt_mode}\n"
        f"   • Загальна напруга: {t.battery.voltage:.1f} В (струм: {t.battery.current:.1f} А){time_remain}{batt_details}\n\n"
        f"🏠 <b>СПОЖИВАННЯ БУДИНКУ: {load_w} Вт</b>\n"
        f"   • Спожито за день: <b>{t.load.daily_energy:.2f} кВт⋅год</b>\n\n"
        f"{grid_line}\n"
        f"{grid_flow}\n\n"
        f"🌡️ <b>Інвертор:</b> {t.inverter.temperature:.1f}°C | Режим: <code>{t.inverter.work_mode}</code>"
    )
    return msg


def get_status_inline_keyboard() -> InlineKeyboardMarkup:
    web_url = settings.WEB_BASE_URL.strip()
    buttons = [
        [
            InlineKeyboardButton(text="🔄 Оновити дані", callback_data="refresh_status"),
            InlineKeyboardButton(text="📊 Денні підсумки", callback_data="view_stats"),
        ],
    ]
    if web_url and web_url.startswith("http"):
        buttons.append([
            InlineKeyboardButton(text="🌐 Відкрити веб-панель", url=web_url),
        ])
    buttons.append([
        InlineKeyboardButton(text="🔔 Налаштування сповіщень", callback_data="toggle_alerts"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text="⚡ Поточний статус"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🔔 Сповіщення"), KeyboardButton(text="🌐 Посилання на сайт")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


@router.message(CommandStart())
async def cmd_start(message: Message):
    chat_id = message.chat.id
    app_state.add_subscriber(chat_id)

    intro = (
        f"👋 <b>Привіт, {message.from_user.first_name or 'власнику'}!</b>\n\n"
        f"Я твій бот для моніторингу сонячної електростанції <b>Deye</b> та 2 акумуляторів.\n\n"
        f"✅ <b>Ви підключені до сповіщень:</b>\n"
        f"• 🚨 Миттєве повідомлення, якщо вимкнуть світло 220В (Блекаут)\n"
        f"• 🔌 Сповіщення, коли міська мережа знову з'явиться\n"
        f"• 🪫 Попередження при розряді батарей (<20%)\n"
        f"• 🔋 Повідомлення про 100% заряд батарей\n\n"
        f"Ось поточний стан станції прямо зараз:"
    )

    t = app_state.latest_telemetry
    if t:
        text = f"{intro}\n\n{format_status_message(t)}"
    else:
        text = f"{intro}\n\n<i>Зчитування перших даних з інвертора... Зачекайте декілька секунд.</i>"

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=get_main_reply_keyboard(),
    )


@router.message(Command("status"))
@router.message(F.text == "⚡ Поточний статус")
async def cmd_status(message: Message):
    t = app_state.latest_telemetry
    if not t:
        await message.answer("⏳ Очікуємо надходження даних з інвертора...")
        return

    await message.answer(
        format_status_message(t),
        parse_mode="HTML",
        reply_markup=get_status_inline_keyboard(),
    )


@router.message(Command("stats"))
@router.message(F.text == "📊 Статистика")
async def cmd_stats(message: Message):
    t = app_state.latest_telemetry
    if not t:
        await message.answer("⏳ Немає даних для формування статистики.")
        return

    # Energy calculations
    solar_kwh = t.solar.daily_energy
    load_kwh = t.load.daily_energy
    batt_in = t.battery.daily_charge
    batt_out = t.battery.daily_discharge
    grid_in = t.grid.daily_import
    grid_out = t.grid.daily_export

    # Self-sufficiency calculation
    if load_kwh > 0:
        self_suff = min(100.0, max(0.0, ((load_kwh - grid_in) / load_kwh) * 100.0))
    else:
        self_suff = 100.0

    msg = (
        f"📊 <b>ДЕННА СТАТИСТИКА ЕНЕРГІЇ</b>\n\n"
        f"☀️ <b>Згенеровано сонячними панелями:</b> {solar_kwh:.2f} кВт⋅год\n"
        f"🏠 <b>Спожито будинком:</b> {load_kwh:.2f} кВт⋅год\n\n"
        f"🔋 <b>Батареї:</b>\n"
        f"   • Заряджено: {batt_in:.2f} кВт⋅год\n"
        f"   • Віддано на будинок: {batt_out:.2f} кВт⋅год\n\n"
        f"🌐 <b>Мережа 220В:</b>\n"
        f"   • Куплено з мережі: {grid_in:.2f} кВт⋅год\n"
        f"   • Віддано в мережу: {grid_out:.2f} кВт⋅год\n\n"
        f"🌱 <b>Рівень енергонезалежності сьогодні:</b> <b>{self_suff:.1f}%</b>\n"
    )

    await message.answer(msg, parse_mode="HTML", reply_markup=get_status_inline_keyboard())


@router.message(Command("notify"))
@router.message(F.text == "🔔 Сповіщення")
async def cmd_notify(message: Message):
    chat_id = message.chat.id
    if chat_id in app_state.subscriber_chat_ids:
        app_state.remove_subscriber(chat_id)
        status_text = "🔕 <b>Сповіщення вимкнено.</b> Ви більше не отримуватимете автоповідомлень про зникнення світла."
    else:
        app_state.add_subscriber(chat_id)
        status_text = "🔔 <b>Сповіщення увімкнено!</b> Ви миттєво дізнаватиметеся про блекаути, повернення напруги та низький заряд батареї."

    await message.answer(status_text, parse_mode="HTML")


@router.message(F.text == "🌐 Посилання на сайт")
async def cmd_website(message: Message):
    url = settings.WEB_BASE_URL.strip()
    if url.startswith("http"):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🚀 Відкрити веб-дашборд", url=url)]]
        )
        await message.answer(f"🌐 Посилання на ваш онлайн-моніторинг:\n{url}", reply_markup=kb)
    else:
        await message.answer(f"Адреса моніторингу: <code>{url}</code>")


@router.callback_query(F.data == "refresh_status")
async def cb_refresh_status(call: CallbackQuery):
    t = app_state.latest_telemetry
    if not t:
        await call.answer("Очікуємо дані...")
        return
    text = format_status_message(t)
    try:
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=get_status_inline_keyboard(),
        )
        await call.answer("✅ Дані оновлено!")
    except Exception:
        await call.answer("Дані вже актуальні")


@router.callback_query(F.data == "view_stats")
async def cb_view_stats(call: CallbackQuery):
    t = app_state.latest_telemetry
    if not t:
        await call.answer("Очікуємо дані...")
        return
    solar_kwh = t.solar.daily_energy
    load_kwh = t.load.daily_energy
    grid_in = t.grid.daily_import
    self_suff = min(100.0, max(0.0, ((load_kwh - grid_in) / load_kwh) * 100.0)) if load_kwh > 0 else 100.0

    msg = (
        f"📊 <b>ДЕННІ ПІДСУМКИ:</b>\n\n"
        f"☀️ Генерація сонця: <b>{solar_kwh:.2f} кВт⋅год</b>\n"
        f"🏠 Споживання будинку: <b>{load_kwh:.2f} кВт⋅год</b>\n"
        f"🔋 Заряд батарей: <b>{t.battery.daily_charge:.2f} кВт⋅год</b>\n"
        f"🌐 Імпорт з мережі: <b>{grid_in:.2f} кВт⋅год</b>\n"
        f"🌱 Автономність: <b>{self_suff:.1f}%</b>"
    )
    await call.message.answer(msg, parse_mode="HTML", reply_markup=get_status_inline_keyboard())
    await call.answer()


@router.callback_query(F.data == "toggle_alerts")
async def cb_toggle_alerts(call: CallbackQuery):
    chat_id = call.message.chat.id
    if chat_id in app_state.subscriber_chat_ids:
        app_state.remove_subscriber(chat_id)
        await call.answer("🔕 Сповіщення вимкнено", show_alert=True)
    else:
        app_state.add_subscriber(chat_id)
        await call.answer("🔔 Сповіщення увімкнено! Ви отримуватимете алерти про світло.", show_alert=True)
