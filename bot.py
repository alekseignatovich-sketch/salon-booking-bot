# -*- coding: utf-8 -*-
"""
Telegram-бот для записи на услуги (салон, тренер, репетитор)
MVP-версия: выбор услуги → даты → времени → ввод контакта → сохранение в Google Таблицу
Автор: шаблон для продажи
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

# === ДЕКОДИРОВАНИЕ GOOGLE CREDENTIALS С АВТОИСПРАВЛЕНИЕМ PADDING ===
try:
    # Очищаем строку от пробелов и переносов
    b64_clean = GOOGLE_CREDENTIALS_B64.strip()
    # Исправляем padding (base64 должен быть кратен 4)
    padding_needed = len(b64_clean) % 4
    if padding_needed:
        b64_clean += '=' * (4 - padding_needed)
    # Декодируем
    credentials_json = base64.b64decode(b64_clean).decode('utf-8')
    creds_dict = json.loads(credentials_json)
except Exception as e:
    raise ValueError("❌ Ошибка при декодировании GOOGLE_CREDENTIALS: " + str(e))

# === НАСТРОЙКА ДОСТУПА К GOOGLE SHEETS ===
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SPREADSHEET_ID).sheet1  # Первый лист

# === НАСТРОЙКИ РАСПИСАНИЯ ===
# Покупатель редактирует этот блок под себя!
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

# Генерируем список дат на 7 дней вперёд (для отображения)
def get_next_7_days():
    today = datetime.now().date()
    return [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

# === FSM СОСТОЯНИЯ ===
class BookingStates(StatesGroup):
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()
    entering_contact = State()

# === ИНИЦИАЛИЗАЦИЯ ===
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# === ОСНОВНЫЕ ХЕНДЛЕРЫ ===

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    for service in SCHEDULE.keys():
        kb.button(text=f"✂️ {service}", callback_data=f"service:{service}")
    kb.adjust(1)
    await message.answer(
        "👋 Привет! Я помогу записаться на услугу.\n\nВыберите, пожалуйста:",
        reply_markup=kb.as_markup()
    )
    await state.set_state(BookingStates.choosing_service)

@router.callback_query(BookingStates.choosing_service, F.data.startswith("service:"))
async def choose_date(callback: CallbackQuery, state: FSMContext):
    service = callback.data.split(":", 1)[1]
    if service not in SCHEDULE:
        await callback.answer("❌ Услуга не найдена.")
        return

    await state.update_data(chosen_service=service)

    # Показываем только даты, где есть слоты и которые в ближайшие 7 дней
    next_7 = set(get_next_7_days())
    available_dates = sorted(set(SCHEDULE[service].keys()) & next_7)

    if not available_dates:
        await callback.message.edit_text("📅 Нет доступных дат на ближайшие 7 дней.")
        return

    kb = InlineKeyboardBuilder()
    for d in available_dates:
        readable = datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m.%Y")
        kb.button(text=readable, callback_data=f"date:{d}")
    kb.button(text="↩️ Назад", callback_data="back_to_service")
    kb.adjust(2)

    await callback.message.edit_text(
        f"📆 Вы выбрали: *{service}*\n\nВыберите дату:",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )
    await state.set_state(BookingStates.choosing_date)
    await callback.answer()

@router.callback_query(BookingStates.choosing_date, F.data == "back_to_service")
async def back_to_service(callback: CallbackQuery, state: FSMContext):
    await cmd_start(callback.message, state)

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
    kb.button(text="↩️ Назад", callback_data="back_to_date")
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

@router.callback_query(BookingStates.choosing_time, F.data == "back_to_date")
async def back_to_date(callback: CallbackQuery, state: FSMContext):
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
    kb.button(text="↩️ Назад", callback_data="back_to_service")
    kb.adjust(2)

    await callback.message.edit_text(
        f"📆 Вы выбрали: *{service}*\n\nВыберите дату:",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )
    await state.set_state(BookingStates.choosing_date)
    await callback.answer()

@router.callback_query(BookingStates.choosing_time, F.data.startswith("time:"))
async def enter_contact(callback: CallbackQuery, state: FSMContext):
    time_str = callback.data.split(":", 1)[1]
    await state.update_data(chosen_time=time_str)

    await callback.message.edit_text(
        "📞 Пожалуйста, напишите **ваше имя и телефон** в формате:\n\n"
        "`Имя, +375291234567`\n\n"
        "Пример: `Анна, +375291234567`",
        parse_mode="Markdown"
    )
    await state.set_state(BookingStates.entering_contact)
    await callback.answer()

@router.message(BookingStates.entering_contact)
async def save_booking(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text or "," not in text:
        await message.answer("❌ Неверный формат.\nПожалуйста, напишите: `Имя, +375291234567`", parse_mode="Markdown")
        return

    parts = [p.strip() for p in text.split(",", 1)]
    if len(parts) != 2:
        await message.answer("❌ Неверный формат. Нужно ровно два элемента: имя и телефон.")
        return

    name, phone = parts

    # Простая проверка телефона (Беларусь, Россия, ЕС)
    phone_clean = re.sub(r"[^\d+]", "", phone)
    if not re.match(r"^\+375\d{9}$|^\+7\d{10}$|^\+3\d{9,12}$", phone_clean):
        await message.answer(
            "❌ Похоже, телефон указан некорректно.\n"
            "Пожалуйста, используйте формат:\n"
            "`+375291234567` (Беларусь)\n"
            "`+79123456789` (Россия)\n"
            "`+3...` (ЕС и др.)",
            parse_mode="Markdown"
        )
        return

    data = await state.get_data()
    service = data.get("chosen_service")
    date_str = data.get("chosen_date")
    time_str = data.get("chosen_time")

    if not all([service, date_str, time_str]):
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
            phone,
            str(message.from_user.id),
            datetime.now().strftime("%d.%m.%Y %H:%M")
        ])
    except Exception as e:
        logging.error(f"Ошибка записи в таблицу: {e}")
        await message.answer("❌ Не удалось сохранить запись. Попробуйте позже.")
        return

    # Подтверждение пользователю
    await message.answer(
        f"✅ **Вы записаны!**\n\n"
        f"📅 **Дата**: {date_readable}\n"
        f"🕗 **Время**: {time_str}\n"
        f"💇‍♀️ **Услуга**: {service}\n"
        f"📞 **Телефон**: {phone}\n\n"
        f"💬 За час до визита пришлю напоминание!",
        parse_mode="Markdown"
    )
    await state.clear()

# === ЗАПУСК ===
async def main():
    logging.basicConfig(level=logging.INFO)
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())