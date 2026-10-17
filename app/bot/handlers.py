import logging
import time
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
    WebAppInfo,
)
from app.config import settings
from app.models import SystemTelemetry
from app.state import app_state
from app.collectors.charge_controller import charge_controller

logger = logging.getLogger("telegram_handlers")
router = Router()


def make_progress_bar(percent: float, length: int = 10) -> str:
    clamped = max(0.0, min(100.0, percent))
    filled = int(round((clamped / 100.0) * length))
    empty = length - filled
    return "█" * filled + "░" * empty


def format_power(watts: float | int, show_sign: bool = False) -> str:
    """Formats watts to W or кВт (if >= 1000 W)."""
    abs_w = abs(float(watts))
    sign = "+" if show_sign and watts > 0 else ("-" if watts < 0 else "")
    if abs_w >= 1000.0:
        val_kw = abs_w / 1000.0
        return f"{sign}{val_kw:.2f} кВт"
    return f"{sign}{int(round(abs_w))} Вт"


def get_source_label(source: str) -> str:
    if source == "deye_cloud":
        return "🌐 Deye Cloud"
    elif source == "solarman_api":
        return "🌐 Solarman Cloud"
    elif source == "local_push":
        return "📡 Локальний шлюз"
    return "🧪 Демо-режим (Симуляція)"


# =========================================================================
# DASHBOARD FORMATTERS
# =========================================================================

def format_main_dashboard(t: SystemTelemetry) -> str:
    now_str = datetime.now().strftime("%H:%M:%S")

    # Grid status
    if t.grid.is_online:
        grid_status_line = f"🟢 <b>Мережа 220В: Є СВІТЛО</b> ({t.grid.voltage:.1f} В, {t.grid.frequency:.1f} Гц)"
        if t.grid.power > 30:
            grid_flow = f"   📥 Імпорт з мережі: <b>{format_power(t.grid.power)}</b>"
        elif t.grid.power < -30:
            grid_flow = f"   📤 Експорт в мережу: <b>{format_power(abs(t.grid.power))}</b>"
        else:
            grid_flow = "   ⚖️ Мережа в нульовому балансі"
    else:
        grid_status_line = "🔴 <b>Мережа 220В: БЛЕКАУТ / ВІДКЛЮЧЕНО</b>"
        grid_flow = "   ⚠️ Живлення виключно від акумуляторів і сонця"

    # Battery mode
    batt_w = t.battery.total_power
    if batt_w > 20:
        batt_mode = f"⚡ <b>Заряджається</b> ({format_power(batt_w, show_sign=True)})"
    elif batt_w < -20:
        batt_mode = f"🔻 <b>Розряджається</b> ({format_power(batt_w)})"
    else:
        batt_mode = "💤 <b>Очікування (Standby)</b>"

    time_remain = ""
    if t.battery.estimated_remaining_hours:
        action = "розряду" if batt_w < -20 else "заряду"
        time_remain = f"\n   ⏳ Час до {action}: ~<b>{t.battery.estimated_remaining_hours} год</b>"

    # Batteries overview
    batt_blocks = ""
    for b in t.battery.batteries:
        bar = make_progress_bar(b.soc, 5)
        batt_blocks += f"\n   • <b>{b.name}</b>: [{bar}] <b>{b.soc:.1f}%</b> ({b.voltage:.1f}В, {b.temperature:.1f}°C)"

    return (
        f"⚡ <b>ГОЛОВНА ПАНЕЛЬ DEYE HYBRID</b> | 🕒 {now_str}\n"
        f"<i>Джерело: {get_source_label(t.source)}</i>\n\n"
        f"☀️ <b>СОНЯЧНІ ПАНЕЛІ: {format_power(t.solar.total_power)}</b>\n"
        f"   • PV1: {format_power(t.solar.pv1_power)} | PV2: {format_power(t.solar.pv2_power)}\n"
        f"   • Генерація сьогодні: <b>{t.solar.daily_energy:.2f} кВт⋅год</b>\n\n"
        f"🔋 <b>БАТАРЕЇ (2 БЛОКИ): {t.battery.total_soc:.1f}%</b> [{make_progress_bar(t.battery.total_soc)}]\n"
        f"   • Режим: {batt_mode}\n"
        f"   • Шина DC: {t.battery.voltage:.1f} В (струм: {t.battery.current:.1f} А){time_remain}{batt_blocks}\n\n"
        f"🏠 <b>СПОЖИВАННЯ БУДИНКУ: {format_power(t.load.power)}</b>\n"
        f"   • Витрачено сьогодні: <b>{t.load.daily_energy:.2f} кВт⋅год</b>\n\n"
        f"{grid_status_line}\n"
        f"{grid_flow}\n\n"
        f"🌡️ <b>Інвертор:</b> {t.inverter.temperature:.1f}°C | Режим: <code>{t.inverter.work_mode}</code>"
    )


# Alias for backward compatibility
format_status_message = format_main_dashboard


def format_battery_dashboard(t: SystemTelemetry) -> str:
    now_str = datetime.now().strftime("%H:%M:%S")
    batt_w = t.battery.total_power

    if batt_w > 20:
        action_text = f"⚡ <b>Заряджання:</b> {format_power(batt_w, show_sign=True)}"
    elif batt_w < -20:
        action_text = f"🔻 <b>Розряджання:</b> {format_power(batt_w)}"
    else:
        action_text = "💤 <b>Режим очікування (Standby)</b>"

    est_text = ""
    if t.battery.estimated_remaining_hours:
        action = "розряду" if batt_w < -20 else "заряду"
        est_text = f"⏳ Орієнтовний час до повного {action}: <b>{t.battery.estimated_remaining_hours} год</b>\n"

    cards = ""
    for idx, b in enumerate(t.battery.batteries, 1):
        bar = make_progress_bar(b.soc, 8)
        cards += (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🔋 <b>{b.name} (Блок #{idx})</b>\n"
            f"• Рівень заряду: [{bar}] <b>{b.soc:.1f}%</b>\n"
            f"• Напруга: <b>{b.voltage:.2f} В</b> | Струм: <b>{b.current:.1f} А</b>\n"
            f"• Потужність: <b>{format_power(b.power)}</b>\n"
            f"• Температура банок: <b>{b.temperature:.1f}°C</b>\n"
            f"• Здоров'я батареї (SOH): <b>{b.soh:.0f}%</b> (Ідеальний)\n"
        )

    return (
        f"🔋 <b>ДЕТАЛЬНА ПАНЕЛЬ АКУМУЛЯТОРІВ</b> | 🕒 {now_str}\n\n"
        f"📊 <b>Загальний стан системи:</b>\n"
        f"• Сумарний заряд: [{make_progress_bar(t.battery.total_soc)}] <b>{t.battery.total_soc:.1f}%</b>\n"
        f"• Поточна робота: {action_text}\n"
        f"• Напруга шини DC: <b>{t.battery.voltage:.2f} В</b>\n"
        f"• Загальний струм: <b>{t.battery.current:.2f} А</b>\n"
        f"{est_text}\n"
        f"📈 <b>Денні підсумки батарей:</b>\n"
        f"• Заряджено в АКБ за день: <b>{t.battery.daily_charge:.2f} кВт⋅год</b>\n"
        f"• Віддано на будинок: <b>{t.battery.daily_discharge:.2f} кВт⋅год</b>\n\n"
        f"{cards}"
    )


def format_solar_dashboard(t: SystemTelemetry) -> str:
    now_str = datetime.now().strftime("%H:%M:%S")

    return (
        f"☀️ <b>СОНЯЧНА ГЕНЕРАЦІЯ DEYE</b> | 🕒 {now_str}\n\n"
        f"⚡ <b>Поточна сумарна потужність:</b> <b>{format_power(t.solar.total_power)}</b>\n\n"
        f"📐 <b>Стрінг 1 (PV1 - Схід / Південь):</b>\n"
        f"• Потужність: <b>{format_power(t.solar.pv1_power)}</b>\n"
        f"• Напруга: <b>{t.solar.pv1_voltage:.1f} В</b>\n"
        f"• Струм: <b>{t.solar.pv1_current:.2f} А</b>\n\n"
        f"📐 <b>Стрінг 2 (PV2 - Захід):</b>\n"
        f"• Потужність: <b>{format_power(t.solar.pv2_power)}</b>\n"
        f"• Напруга: <b>{t.solar.pv2_voltage:.1f} В</b>\n"
        f"• Струм: <b>{t.solar.pv2_current:.2f} А</b>\n\n"
        f"📊 <b>Виробіток енергії:</b>\n"
        f"• Згенеровано за сьогодні: <b>{t.solar.daily_energy:.2f} кВт⋅год</b>\n"
        f"• За весь час експлуатації: <b>{t.solar.total_energy:.1f} кВт⋅год</b>"
    )


def format_stats_dashboard(t: SystemTelemetry) -> str:
    now_str = datetime.now().strftime("%H:%M:%S")
    solar_kwh = t.solar.daily_energy
    load_kwh = t.load.daily_energy
    grid_in = t.grid.daily_import
    grid_out = t.grid.daily_export
    batt_in = t.battery.daily_charge
    batt_out = t.battery.daily_discharge

    if load_kwh > 0:
        self_suff = min(100.0, max(0.0, ((load_kwh - grid_in) / load_kwh) * 100.0))
    else:
        self_suff = 100.0

    return (
        f"📊 <b>ЕНЕРГОБАЛАНС ТА АНАЛІТИКА ДНЯ</b> | 🕒 {now_str}\n\n"
        f"🌱 <b>Рівень енергонезалежності сьогодні:</b>\n"
        f"[{make_progress_bar(self_suff, 10)}] <b>{self_suff:.1f}%</b>\n\n"
        f"☀️ <b>Генерація сонця:</b> <b>{solar_kwh:.2f} кВт⋅год</b>\n"
        f"🏠 <b>Споживання будинку:</b> <b>{load_kwh:.2f} кВт⋅год</b>\n\n"
        f"🔋 <b>Акумулятори:</b>\n"
        f"   • Заряджено: <b>+{batt_in:.2f} кВт⋅год</b>\n"
        f"   • Розряджено: <b>-{batt_out:.2f} кВт⋅год</b>\n\n"
        f"🌐 <b>Міська мережа 220В:</b>\n"
        f"   • Куплено з мережі: <b>{grid_in:.2f} кВт⋅год</b>\n"
        f"   • Віддано в мережу: <b>{grid_out:.2f} кВт⋅год</b>"
    )


def format_alerts_dashboard(chat_id: int) -> str:
    is_subbed = chat_id in app_state.subscriber_chat_ids
    status_icon = "🟢 УВІМКНЕНО" if is_subbed else "🔴 ВИМКНЕНО"

    return (
        f"🔔 <b>ЦЕНТР КЕРУВАННЯ СПОВІЩЕННЯМИ</b>\n\n"
        f"Статус підписки: <b>{status_icon}</b>\n\n"
        f"🎯 <b>Активні типи сповіщень:</b>\n"
        f"• 🚨 <b>Вимкнення світла 220В (Блекаут):</b> перевірка 1 хв для захисту від помилкових тривог\n"
        f"• 🔌 <b>Повернення напруги в мережі:</b> повідомлення з тривалістю відключення\n"
        f"• 🪫 <b>Захист від розряду (<20% та <10%):</b> попередження зменшити навантаження\n"
        f"• 🔋 <b>Повний заряд (100%):</b> повідомлення про готовність резерву\n\n"
        f"<i>Натисніть кнопку нижче, щоб увімкнути або вимкнути сповіщення:</i>"
    )


def format_charge_dashboard(t: SystemTelemetry) -> str:
    now_str = datetime.now().strftime("%H:%M:%S")

    if charge_controller.is_force_charging:
        status_text = "⚡ <b>АКТИВНО: Йде примусове заряджання від мережі 220В</b>"
        if charge_controller.force_charge_end_time:
            left_min = max(1, int((charge_controller.force_charge_end_time - time.time()) / 60))
            timer_text = f"⏳ Залишилося часу за таймером: <b>~{left_min} хв</b>\n"
        else:
            timer_text = f"🎯 Цільовий рівень заряду: <b>{charge_controller.force_charge_target_soc:.0f}%</b>\n"
    else:
        status_text = "💤 <b>НЕАКТИВНО (Звичайний автономний режим Self-Use)</b>"
        timer_text = ""

    night_status = "🟢 <b>УВІМКНЕНО (23:00 - 07:00)</b>" if charge_controller.night_charge_enabled else "⚪ <b>ВИМКНЕНО</b>"

    return (
        f"🔌 <b>КЕРУВАННЯ ЗАРЯДЖАННЯМ ВІД МЕРЕЖІ 220В</b> | 🕒 {now_str}\n\n"
        f"🔋 <b>Поточний заряд батарей:</b> [{make_progress_bar(t.battery.total_soc)}] <b>{t.battery.total_soc:.1f}%</b>\n"
        f"⚡ <b>Стан заряду від мережі:</b> {status_text}\n"
        f"{timer_text}"
        f"🌙 <b>Нічний тариф (Нічний заряд):</b> {night_status}\n\n"
        f"💡 <i>Ви можете примусово підзарядити акумулятори перед очікуваним відключенням світла або активувати нічний заряд за 50% тарифом. Оберіть дію нижче:</i>"
    )


# =========================================================================
# KEYBOARDS
# =========================================================================

def get_safe_web_url() -> str:
    url = settings.WEB_BASE_URL.strip()
    if not url or "localhost" in url or not url.startswith("https://"):
        return "https://solarsystem67.onrender.com"
    return url


def get_charge_inline_keyboard() -> InlineKeyboardMarkup:
    night_label = "🌙 Вимкнути нічний тариф" if charge_controller.night_charge_enabled else "🌙 Увімкнути нічний тариф (23:00 - 07:00)"
    stop_label = "⏹️ Зупинити заряд від 220В" if charge_controller.is_force_charging else "⏹️ Режим Self-Use (Стандарт)"

    rows = [
        [
            InlineKeyboardButton(text="⚡ Зарядити зараз (до 100%)", callback_data="charge_now_100"),
        ],
        [
            InlineKeyboardButton(text="⏱️ Заряд на 1 год", callback_data="charge_hours_1"),
            InlineKeyboardButton(text="⏱️ Заряд на 2 год", callback_data="charge_hours_2"),
        ],
        [
            InlineKeyboardButton(text=night_label, callback_data="toggle_night_charge"),
        ],
        [
            InlineKeyboardButton(text=stop_label, callback_data="charge_stop"),
        ],
        [
            InlineKeyboardButton(text="⚡ Головна панель", callback_data="tab_main"),
            InlineKeyboardButton(text="🔋 Батареї", callback_data="tab_battery"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_panel_inline_keyboard(active_tab: str = "main") -> InlineKeyboardMarkup:
    web_url = get_safe_web_url()

    # Tab labels with active indicator
    tab_main = "⚡ Головна" if active_tab != "main" else "• ⚡ Головна •"
    tab_battery = "🔋 Батареї" if active_tab != "battery" else "• 🔋 Батареї •"
    tab_solar = "☀️ Сонце" if active_tab != "solar" else "• ☀️ Сонце •"
    tab_stats = "📊 Баланс" if active_tab != "stats" else "• 📊 Баланс •"

    # Telegram requires HTTPS for WebAppInfo
    if web_url.startswith("https://"):
        web_button = InlineKeyboardButton(
            text="📱 Відкрити WebApp дашборд",
            web_app=WebAppInfo(url=web_url),
        )
    else:
        web_button = InlineKeyboardButton(
            text="🌐 Відкрити дашборд",
            url=web_url,
        )

    rows = [
        # Action Row
        [
            InlineKeyboardButton(text="🔄 Оновити дані", callback_data=f"refresh_{active_tab}"),
            InlineKeyboardButton(text="🔌 Заряд від 220В", callback_data="tab_charge"),
        ],
        # Tabs Row
        [
            InlineKeyboardButton(text=tab_main, callback_data="tab_main"),
            InlineKeyboardButton(text=tab_battery, callback_data="tab_battery"),
        ],
        [
            InlineKeyboardButton(text=tab_solar, callback_data="tab_solar"),
            InlineKeyboardButton(text=tab_stats, callback_data="tab_stats"),
        ],
        [
            InlineKeyboardButton(text="🔔 Сповіщення", callback_data="tab_alerts"),
        ],
        # WebApp / Link Button
        [web_button],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text="⚡ Головна панель"),
            KeyboardButton(text="🔋 Батареї (2 шт)"),
        ],
        [
            KeyboardButton(text="🔌 Заряд від мережі"),
            KeyboardButton(text="📊 Енергобаланс"),
        ],
        [
            KeyboardButton(text="☀️ Сонячні панелі"),
            KeyboardButton(text="🔔 Центр сповіщень"),
        ],
        [
            KeyboardButton(text="📱 Відкрити сайт"),
        ],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


# =========================================================================
# BOT COMMANDS & MESSAGE HANDLERS
# =========================================================================

@router.message(CommandStart())
async def cmd_start(message: Message):
    chat_id = message.chat.id
    app_state.add_subscriber(chat_id)

    intro = (
        f"👋 <b>Вітаю, {message.from_user.first_name or 'власнику'}!</b>\n\n"
        f"Ваш персональний бот для моніторингу сонячної станції <b>Deye Hybrid + 2 АКБ</b> підключено!\n\n"
        f"✅ <b>Автосповіщення про блекаути активовано.</b>\n"
        f"Використовуйте зручну інформаційну панель та кнопки нижче для швидкої навігації 👇"
    )

    t = app_state.latest_telemetry
    content = format_main_dashboard(t) if t else "<i>Завантаження даних станції...</i>"

    await message.answer(
        f"{intro}\n\n{content}",
        parse_mode="HTML",
        reply_markup=get_main_reply_keyboard(),
    )


@router.message(Command("status"))
@router.message(F.text == "⚡ Головна панель")
async def cmd_status(message: Message):
    t = app_state.latest_telemetry
    if not t:
        await message.answer("⏳ Очікуємо надходження даних з інвертора...")
        return
    await message.answer(
        format_main_dashboard(t),
        parse_mode="HTML",
        reply_markup=get_panel_inline_keyboard("main"),
    )


@router.message(Command("battery"))
@router.message(F.text == "🔋 Батареї (2 шт)")
async def cmd_battery(message: Message):
    t = app_state.latest_telemetry
    if not t:
        await message.answer("⏳ Дані по батареях завантажуються...")
        return
    await message.answer(
        format_battery_dashboard(t),
        parse_mode="HTML",
        reply_markup=get_panel_inline_keyboard("battery"),
    )


@router.message(Command("solar"))
@router.message(F.text == "☀️ Сонячні панелі")
async def cmd_solar(message: Message):
    t = app_state.latest_telemetry
    if not t:
        await message.answer("⏳ Дані сонячних панелей завантажуються...")
        return
    await message.answer(
        format_solar_dashboard(t),
        parse_mode="HTML",
        reply_markup=get_panel_inline_keyboard("solar"),
    )


@router.message(Command("stats"))
@router.message(F.text == "📊 Енергобаланс")
async def cmd_stats(message: Message):
    t = app_state.latest_telemetry
    if not t:
        await message.answer("⏳ Підрахунок денної статистики...")
        return
    await message.answer(
        format_stats_dashboard(t),
        parse_mode="HTML",
        reply_markup=get_panel_inline_keyboard("stats"),
    )


@router.message(Command("charge"))
@router.message(F.text == "🔌 Заряд від мережі")
async def cmd_charge(message: Message):
    t = app_state.latest_telemetry
    if not t:
        await message.answer("⏳ Очікуємо надходження даних з інвертора...")
        return
    await message.answer(
        format_charge_dashboard(t),
        parse_mode="HTML",
        reply_markup=get_charge_inline_keyboard(),
    )


@router.message(Command("notify"))
@router.message(F.text == "🔔 Центр сповіщень")
async def cmd_notify(message: Message):
    chat_id = message.chat.id
    is_subbed = chat_id in app_state.subscriber_chat_ids
    toggle_text = "🔕 Вимкнути сповіщення" if is_subbed else "🔔 Увімкнути сповіщення"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data="toggle_alerts")],
            [InlineKeyboardButton(text="⚡ До головної панелі", callback_data="tab_main")],
        ]
    )
    await message.answer(
        format_alerts_dashboard(chat_id),
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.message(F.text == "📱 Відкрити сайт")
async def cmd_website(message: Message):
    web_url = get_safe_web_url()

    rows = []
    if web_url.startswith("https://"):
        rows.append([
            InlineKeyboardButton(
                text="🚀 Відкрити WebApp прямо тут",
                web_app=WebAppInfo(url=web_url),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="🌐 Відкрити у браузері",
            url=web_url,
        )
    ])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer(
        f"🌐 <b>Онлайн-дашборд сонячної станції:</b>\n<code>{web_url}</code>",
        parse_mode="HTML",
        reply_markup=kb,
    )


# =========================================================================
# INTERACTIVE CALLBACK QUERY HANDLERS (TAB SWITCHING IN-PLACE)
# =========================================================================

@router.callback_query(F.data == "tab_main")
async def cb_tab_main(call: CallbackQuery):
    t = app_state.latest_telemetry
    if not t:
        await call.answer("Очікуємо дані...")
        return
    try:
        await call.message.edit_text(
            format_main_dashboard(t),
            parse_mode="HTML",
            reply_markup=get_panel_inline_keyboard("main"),
        )
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data == "tab_battery")
async def cb_tab_battery(call: CallbackQuery):
    t = app_state.latest_telemetry
    if not t:
        await call.answer("Очікуємо дані...")
        return
    try:
        await call.message.edit_text(
            format_battery_dashboard(t),
            parse_mode="HTML",
            reply_markup=get_panel_inline_keyboard("battery"),
        )
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data == "tab_solar")
async def cb_tab_solar(call: CallbackQuery):
    t = app_state.latest_telemetry
    if not t:
        await call.answer("Очікуємо дані...")
        return
    try:
        await call.message.edit_text(
            format_solar_dashboard(t),
            parse_mode="HTML",
            reply_markup=get_panel_inline_keyboard("solar"),
        )
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data == "tab_stats")
async def cb_tab_stats(call: CallbackQuery):
    t = app_state.latest_telemetry
    if not t:
        await call.answer("Очікуємо дані...")
        return
    try:
        await call.message.edit_text(
            format_stats_dashboard(t),
            parse_mode="HTML",
            reply_markup=get_panel_inline_keyboard("stats"),
        )
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data == "tab_alerts")
async def cb_tab_alerts(call: CallbackQuery):
    chat_id = call.message.chat.id
    is_subbed = chat_id in app_state.subscriber_chat_ids
    toggle_text = "🔕 Вимкнути сповіщення" if is_subbed else "🔔 Увімкнути сповіщення"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data="toggle_alerts")],
            [InlineKeyboardButton(text="⚡ До головної панелі", callback_data="tab_main")],
        ]
    )
    try:
        await call.message.edit_text(
            format_alerts_dashboard(chat_id),
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data.startswith("refresh_"))
async def cb_refresh_tab(call: CallbackQuery):
    tab = call.data.replace("refresh_", "")
    t = app_state.latest_telemetry
    if not t:
        await call.answer("Дані завантажуються...")
        return

    text = ""
    if tab == "battery":
        text = format_battery_dashboard(t)
    elif tab == "solar":
        text = format_solar_dashboard(t)
    elif tab == "stats":
        text = format_stats_dashboard(t)
    else:
        text = format_main_dashboard(t)

    try:
        await call.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=get_panel_inline_keyboard(tab),
        )
        await call.answer("✅ Дані оновлено!")
    except Exception:
        await call.answer("Показники вже актуальні")


@router.callback_query(F.data == "toggle_alerts")
async def cb_toggle_alerts(call: CallbackQuery):
    chat_id = call.message.chat.id
    if chat_id in app_state.subscriber_chat_ids:
        app_state.remove_subscriber(chat_id)
        msg_pop = "🔕 Сповіщення про світло вимкнено"
    else:
        app_state.add_subscriber(chat_id)
        msg_pop = "🔔 Сповіщення увімкнено! Ви отримуватимете алерти про блекаут."

    is_subbed = chat_id in app_state.subscriber_chat_ids
    toggle_text = "🔕 Вимкнути сповіщення" if is_subbed else "🔔 Увімкнути сповіщення"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data="toggle_alerts")],
            [InlineKeyboardButton(text="⚡ До головної панелі", callback_data="tab_main")],
        ]
    )
    try:
        await call.message.edit_text(
            format_alerts_dashboard(chat_id),
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception:
        pass
    await call.answer(msg_pop, show_alert=True)


# =========================================================================
# CHARGE CONTROLLER CALLBACKS
# =========================================================================

@router.callback_query(F.data == "tab_charge")
async def cb_tab_charge(call: CallbackQuery):
    t = app_state.latest_telemetry
    if not t:
        await call.answer("Очікуємо дані...")
        return
    try:
        await call.message.edit_text(
            format_charge_dashboard(t),
            parse_mode="HTML",
            reply_markup=get_charge_inline_keyboard(),
        )
    except Exception:
        pass
    await call.answer()


@router.callback_query(F.data == "charge_now_100")
async def cb_charge_now_100(call: CallbackQuery):
    await charge_controller.start_force_charge(target_soc=100.0)
    t = app_state.latest_telemetry
    if t:
        try:
            await call.message.edit_text(
                format_charge_dashboard(t),
                parse_mode="HTML",
                reply_markup=get_charge_inline_keyboard(),
            )
        except Exception:
            pass
    await call.answer("⚡ Заряджання розпочато! Батареї заряджаються від 220В до 100%.", show_alert=True)


@router.callback_query(F.data == "charge_hours_1")
async def cb_charge_hours_1(call: CallbackQuery):
    await charge_controller.start_force_charge(target_soc=100.0, duration_hours=1.0)
    t = app_state.latest_telemetry
    if t:
        try:
            await call.message.edit_text(
                format_charge_dashboard(t),
                parse_mode="HTML",
                reply_markup=get_charge_inline_keyboard(),
            )
        except Exception:
            pass
    await call.answer("⏱️ Заряджання на 1 годину активовано! Зупиниться автоматично.", show_alert=True)


@router.callback_query(F.data == "charge_hours_2")
async def cb_charge_hours_2(call: CallbackQuery):
    await charge_controller.start_force_charge(target_soc=100.0, duration_hours=2.0)
    t = app_state.latest_telemetry
    if t:
        try:
            await call.message.edit_text(
                format_charge_dashboard(t),
                parse_mode="HTML",
                reply_markup=get_charge_inline_keyboard(),
            )
        except Exception:
            pass
    await call.answer("⏱️ Заряджання на 2 години активовано! Зупиниться автоматично.", show_alert=True)


@router.callback_query(F.data == "toggle_night_charge")
async def cb_toggle_night_charge(call: CallbackQuery):
    enabled = charge_controller.toggle_night_schedule()
    t = app_state.latest_telemetry
    if t:
        try:
            await call.message.edit_text(
                format_charge_dashboard(t),
                parse_mode="HTML",
                reply_markup=get_charge_inline_keyboard(),
            )
        except Exception:
            pass
    msg = "🌙 Нічний заряд увімкнено (23:00 - 07:00)" if enabled else "⚪ Нічний заряд вимкнено"
    await call.answer(msg, show_alert=True)


@router.callback_query(F.data == "charge_stop")
async def cb_charge_stop(call: CallbackQuery):
    await charge_controller.stop_force_charge()
    t = app_state.latest_telemetry
    if t:
        try:
            await call.message.edit_text(
                format_charge_dashboard(t),
                parse_mode="HTML",
                reply_markup=get_charge_inline_keyboard(),
            )
        except Exception:
            pass
    await call.answer("⏹️ Заряд від мережі зупинено. Інвертор повернуто в Self-Use.", show_alert=True)
