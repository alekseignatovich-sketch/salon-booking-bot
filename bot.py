# -*- coding: utf-8 -*-
"""
Telegram-бот для записи на услуги (салон, тренер, репетитор)
Версия: 2.2 — полная поддержка 7 дней, 10-20, один мастер, надёжная отмена
"""

import os
import re
import base64
import json
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery
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

# === ГЕНЕРАЦИЯ СЛОТОВ (10:00–20:00, 1 час) ===
def get_available_times(date_str: str) -> list:
    """Возвращает свободные слоты с 10:00 до 20:00 (1 час), исключая занятые (любая услуга)"""
    all_slots = [f"{h:02d}:00" for h in range(10, 20)]  # 10:00–19:00
    
    try:
        records = sheet.get_all_records()
        target_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
        booked = set(row["Время"] for row in records if row.get("Дата") == target_date)
        return [slot for slot in all_slots if slot not in booked]
    except Exception as e:
        logging.error(f"Ошибка проверки слотов: {e}")
        return all_slots

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

# === /start ===
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="✂️ Стрижка", callback_data="service:Стрижка")
    kb.button(text="🎨 Окрашивание", callback_data="service:Окрашивание")
    kb.button(text="💅 Маникюр", callback_data="service:Маникюр")
    kb.button(text="❌ Отменить запись", callback_data="action:cancel")
    kb.adjust(1)
    await message.answer(
        "👋 Привет! Я помогу записаться на услугу.\n\nВыберите, пожалуйста:",
        reply_markup=kb.as_markup()
    )
    await state.set_state(BookingStates.choosing_service)

# === ВЫБОР ДАТЫ (7 дней вперёд) ===
@router.callback_query(BookingStates.choosing_service, F.data.startswith("service:"))
async def choose_date(callback: CallbackQuery, state: FSMContext):
    service = callback.data.split(":", 1)[1]
    await state.update_data(chosen_service=service)

    next_7 = [datetime.now().date() + timedelta(days=i) for i in range(1, 8)]
    available_dates = [d.strftime("%Y-%m-%d") for d in next_7]

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
    times = get_available_times(date_str)
    
    if not times:
        await callback.message.edit_text("❌ На эту дату нет свободных слотов.")
        return

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
    next_7 = [datetime.now().date() + timedelta(days=i) for i in range(1, 8)]
    available_dates = [d.strftime("%Y-%m-%d") for d in next_7]
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
        await message.answer("❌ Имя не может быть пустым.")
        return
    await state.update_data(client_name=name)
    await message.answer("📞 Введите ваш телефон (например, +375291234567):")
    await state.set_state(BookingStates.entering_phone)

# === СОХРАНЕНИЕ ===
@router.message(BookingStates.entering_phone)
async def save_booking(message: Message, state: FSMContext):
    phone_input = message.text.strip()
    if not phone_input:
        await message.answer("❌ Введите телефон.")
        return

    # Сохраняем ТОЛЬКО ЦИФРЫ (надёжно для поиска)
    phone_digits = re.sub(r"\D", "", phone_input)
    if len(phone_digits) < 9:
        await message.answer("❌ Слишком короткий номер. Попробуйте снова.")
        return

    data = await state.get_data()
    service = data.get("chosen_service")
    date_str = data.get("chosen_date")
    time_str = data.get("chosen_time")
    name = data.get("client_name")

    if not all([service, date_str, time_str, name]):
        await message.answer("❌ Ошибка. Начните с /start")
        return

    date_readable = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")

    try:
        sheet.append_row([
            date_readable,
            time_str,
            service,
            name,
            phone_digits,  # ← только цифры!
            str(message.from_user.id),
            datetime.now().strftime("%d.%m.%Y %H:%M")
        ])
    except Exception as e:
        logging.error(f"Ошибка записи: {e}")
        await message.answer("❌ Не удалось сохранить запись.")
        return

    await message.answer(
        f"✅ **Вы записаны!**\n"
        f"📅 {date_readable} в {time_str}\n"
        f"💇‍♀️ {service}\n"
        f"👤 {name}\n"
        f"📞 {phone_input}\n\n"
        f"ℹ️ Чтобы отменить — отправьте /start → «Отменить запись»."
    )
    await state.clear()

# === ОТМЕНА: ЗАПРОС ТЕЛЕФОНА ===
@router.callback_query(F.data == "action:cancel")
async def start_cancel(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BookingStates.cancel_by_phone)
    await callback.message.edit_text(
        "📞 Введите ваш телефон для поиска записей:",
        reply_markup=None
    )

# === ОТМЕНА: ПОИСК И УДАЛЕНИЕ ===
@router.message(BookingStates.cancel_by_phone)
async def handle_cancel_phone(message: Message, state: FSMContext):
    phone_input = message.text.strip()
    if not phone_input:
        await message.answer("❌ Введите телефон.")
        return

    user_digits = re.sub(r"\D", "", phone_input)
    if len(user_digits) < 9:
        await message.answer("❌ Неверный формат.")
        return

    try:
        records = sheet.get_all_records()
        user_bookings = []
        for idx, row in enumerate(records, start=2):
            raw_phone = str(row.get("Телефон", "")).strip()
            # Убираем апостроф Google Sheets
            if raw_phone.startswith("'"):
                raw_phone = raw_phone[1:]
            table_digits = re.sub(r"\D", "", raw_phone)
            if user_digits == table_digits:
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
        logging.error(f"Ошибка поиска записей: {e}")
        await message.answer("❌ Не удалось найти записи.")
        await state.clear()

# === УДАЛЕНИЕ ЗАПИСИ ===
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
