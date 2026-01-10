# -*- coding: utf-8 -*-
"""
Telegram-бот для записи на услуги (салон, тренер, репетитор)
Версия: 2.0 (с отменой по телефону и разделённым вводом данных)
"""

import os
import re
import base64
import json
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from dotenv import load_dotenv

# === ЗАГРУЗКА НАСТРОЕК ===
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS_B64 = os.getenv("GOOGLE_CREDENTIALS")

if not all([BOT_TOKEN, SPREADSHEET_ID, GOOGLE_CREDENTIALS_B64]):
    raise ValueError("❌ Не заданы переменные окружения в .env файле!")

# Декодируем Google Credentials из base64
try:
    b64_clean = GOOGLE_CREDENTIALS_B64.strip()
    padding_needed = len(b64_clean) % 4
    if padding_needed:
        b64_clean += '=' * (4 - padding_needed)
    credentials_json = base64.b64decode(b64_clean).decode('utf-8')
    creds_dict = json.loads(credentials_json)
except Exception as e:
    raise ValueError("❌ Ошибка при декодировании GOOGLE_CREDENTIALS: " + str(e))

# Настройка доступа к Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SPREADSHEET_ID).sheet1

# === РАСПИСАНИЕ УСЛУГ ===
SCHEDULE = {
    "Стрижка": {
        "2026-01-08": ["10:00", "11:00", "14:00"],
        "2026-01-09": ["12:00", "15:00"],
        "2026-01-10": ["09:00", "13:00", "16:00"],
    },
    "Окрашивание": {
        "2026-01-08": ["12:00", "15:00"],
        "2026-01-11": ["10:00", "14:00"],
    },
    "Маникюр": {
        "2026-01-09": ["09:00", "13:00"],
        "2026-01-10": ["11:00", "15:00"],
        "2026-01-12": ["10:00", "16:00"],
    }
}

def get_next_7_days():
    today = datetime.now().date()
    return [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

# === FSM СОСТОЯНИЯ ===
class BookingStates(StatesGroup):
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()
    entering_name = State()
    entering_phone = State()
    cancel_by_phone = State()

# === ИНИЦИАЛИЗАЦИЯ ===
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# === ОСНОВНОЙ ХЕНДЛЕР /start ===
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    for service in SCHEDULE.keys():
        kb.button(text=f"✂️ {service}", callback_data=f"service:{service}")
    kb.button(text="❌ Отменить запись", callback_data="action:cancel")
    kb.adjust(1)
    await message.answer(
        "👋 Привет! Я помогу записаться на услугу.\n\nВыберите, пожалуйста:",
        reply_markup=kb.as_markup()
    )
    await state.set_state(BookingStates.choosing_service)

# === ВЫБОР ДАТЫ ===
@router.callback_query(BookingStates.choosing_service, F.data.startswith("service:"))
async def choose_date(callback: CallbackQuery, state: FSMContext):
    service = callback.data.split(":", 1)[1]
    if service not in SCHEDULE:
        await callback.answer("❌ Услуга не найдена.")
        return
    await state.update_data(chosen_service=service)
    next_7 = set(get_next_7_days())
    available_dates = sorted(set(SCHEDULE[service].keys()) & next_7)
    if not available_dates:
        await callback.message.edit_text("📅 Нет доступных дат на ближайшие 7 дней.")
        return
    kb = InlineKeyboardBuilder()
    for d in available_dates:
        readable = datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m.%Y")
        kb.button(text=readable, callback_data=f"date:{d}")
    kb.button(text="↩️ Назад", callback_data="back_to_start")
    kb.adjust(2)
    await callback.message.edit_text(
        f"📆 Вы выбрали: *{service}*\n\nВыберите дату:",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )
    await state.set_state(BookingStates.choosing_date)
    await callback.answer()

@router.callback_query(F.data == "back_to_start")
async def back_to_start(callback: CallbackQuery, state: FSMContext):
    await cmd_start(callback.message, state)

# === ВЫБОР ВРЕМЕНИ ===
@router.callback_query(BookingStates.choosing_date, F.data.startswith("date:"))
async def choose_time(callback: CallbackQuery, state: FSMContext):
    date_str = callback.data.split(":", 1)[1]
    data = await state.get_data()
    service = data.get("chosen_service")
    if not service or date_str not in SCHEDULE.get(service, {}):
        await callback.answer("❌ Некорректная дата.")
        return
    times = SCHEDULE[service][date_str]
    kb = InlineKeyboardBuilder()
    for t in times:
        kb.button(text=f"⏰ {t}", callback_data=f"time:{t}")
    kb.button(text="↩️ Назад", callback_data="back_to_service")
    kb.adjust(2)
    readable_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
    await callback.message.edit_text(
        f"🕗 Дата: *{readable_date}*\nВыберите время:",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )
    await state.update_data(chosen_date=date_str)
    await state.set_state(BookingStates.choosing_time)
    await callback.answer()

@router.callback_query(F.data == "back_to_service")
async def back_to_service(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    service = data.get("chosen_service")
    if not service:
        await cmd_start(callback.message, state)
        return
    next_7 = set(get_next_7_days())
    available_dates = sorted(set(SCHEDULE[service].keys()) & next_7)
    kb = InlineKeyboardBuilder()
    for d in available_dates:
        readable = datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m.%Y")
        kb.button(text=readable, callback_data=f"date:{d}")
    kb.button(text="↩️ Назад", callback_data="back_to_start")
    kb.adjust(2)
    await callback.message.edit_text(
        f"📆 Вы выбрали: *{service}*\n\nВыберите дату:",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )
    await state.set_state(BookingStates.choosing_date)
    await callback.answer()

# === ВВОД ИМЕНИ ===
@router.callback_query(BookingStates.choosing_time, F.data.startswith("time:"))
async def enter_name(callback: CallbackQuery, state: FSMContext):
    time_str = callback.data.split(":", 1)[1]
    await state.update_data(chosen_time=time_str)
    await callback.message.edit_text("👤 Введите ваше имя:")
    await state.set_state(BookingStates.entering_name)
    await callback.answer()

# === ВВОД ТЕЛЕФОНА ===
@router.message(BookingStates.entering_name)
async def enter_phone(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("❌ Имя не может быть пустым. Попробуйте снова:")
        return
    await state.update_data(client_name=name)
    await message.answer("📞 Введите ваш телефон (например, +375291234567):")
    await state.set_state(BookingStates.entering_phone)

# === СОХРАНЕНИЕ ЗАПИСИ ===
@router.message(BookingStates.entering_phone)
async def save_booking(message: Message, state: FSMContext):
    phone_input = message.text.strip()
    phone_clean = re.sub(r"[^\d+]", "", phone_input)
    if not re.match(r"^\+375\d{9}$|^\+7\d{10}$|^\+3\d{9,12}$", phone_clean):
        await message.answer(
            "❌ Неверный формат телефона.\nПример: `+375291234567`",
            parse_mode="Markdown"
        )
        return

    data = await state.get_data()
    service = data.get("chosen_service")
    date_str = data.get("chosen_date")
    time_str = data.get("chosen_time")
    name = data.get("client_name")

    if not all([service, date_str, time_str, name]):
        await message.answer("❌ Произошла ошибка. Начните сначала: /start")
        return

    date_readable = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")

    # Сохраняем в Google Таблицу
    try:
        sheet.append_row([
            date_readable,
            time_str,
            service,
            name,
            phone_clean,  # сохраняем очищенный номер
            str(message.from_user.id),
            datetime.now().strftime("%d.%m.%Y %H:%M")
        ])
    except Exception as e:
        logging.error(f"Ошибка записи в таблицу: {e}")
        await message.answer("❌ Не удалось сохранить запись. Попробуйте позже.")
        return

    await message.answer(
        f"✅ **Вы записаны!**\n\n"
        f"📅 **Дата**: {date_readable}\n"
        f"🕗 **Время**: {time_str}\n"
        f"💇‍♀️ **Услуга**: {service}\n"
        f"👤 **Имя**: {name}\n"
        f"📞 **Телефон**: {phone_input}\n\n"
        f"ℹ️ Чтобы отменить запись — отправьте /start и выберите «Отменить запись»."
    )
    await state.clear()

# === ОТМЕНА: ЗАПРОС ТЕЛЕФОНА ===
@router.callback_query(F.data == "action:cancel")
async def start_cancel(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BookingStates.cancel_by_phone)
    await callback.message.edit_text(
        "📞 Чтобы отменить запись, введите ваш телефон (например, +375291234567):",
        reply_markup=None
    )

# === ОТМЕНА: ПОИСК И УДАЛЕНИЕ ===
@router.message(BookingStates.cancel_by_phone)
async def handle_cancel_phone(message: Message, state: FSMContext):
    phone_input = message.text.strip()
    phone_clean = re.sub(r"[^\d+]", "", phone_input)
    if not re.match(r"^\+375\d{9}$|^\+7\d{10}$|^\+3\d{9,12}$", phone_clean):
        await message.answer("❌ Неверный формат. Попробуйте снова:")
        return

    try:
        records = sheet.get_all_records()
        user_bookings = []
        for idx, row in enumerate(records, start=2):
            row_phone = re.sub(r"[^\d+]", "", str(row.get("Телефон", "")))
            if phone_clean == row_phone:
                user_bookings.append({
                    "row": idx,
                    "date": row["Дата"],
                    "time": row["Время"],
                    "service": row["Услуга"]
                })

        if not user_bookings:
            await message.answer("❌ У вас нет активных записей.")
            await state.clear()
            return

        kb = InlineKeyboardBuilder()
        for booking in user_bookings:
            text = f"❌ {booking['date']} в {booking['time']} ({booking['service']})"
            kb.button(
                text=text,
                callback_data=f"del:{booking['row']}:{booking['date']}:{booking['time']}"
            )
        kb.adjust(1)
        await message.answer("Ваши записи:", reply_markup=kb.as_markup())

    except Exception as e:
        logging.error(f"Ошибка при поиске записей: {e}")
        await message.answer("❌ Не удалось найти записи. Попробуйте позже.")
        await state.clear()

# === УДАЛЕНИЕ КОНКРЕТНОЙ ЗАПИСИ ===
@router.callback_query(F.data.startswith("del:"))
async def delete_booking(callback: CallbackQuery):
    parts = callback.data.split(":", 3)
    if len(parts) != 4:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    _, row_str, date, time = parts
    try:
        row = int(row_str)
        sheet.delete_rows(row)
        await callback.message.edit_text(f"✅ Запись на {date} в {time} отменена!")
    except Exception as e:
        logging.error(f"Ошибка удаления: {e}")
        await callback.answer("Не удалось отменить запись.", show_alert=True)

# === ЗАПУСК ===
async def main():
    logging.basicConfig(level=logging.INFO)
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
