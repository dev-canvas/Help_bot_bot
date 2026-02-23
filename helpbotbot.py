import asyncio
import logging
import os
import re
from typing import Optional
import aiosqlite

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None

if not API_TOKEN or not ADMIN_ID:
    raise ValueError("Укажите TELEGRAM_BOT_TOKEN и ADMIN_ID в переменных окружения")

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

DB_PATH = "/app/data/users.db"

# FSM для ответов
class ReplyState(StatesGroup):
    waiting_reply = State()

async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT
            )
        """)
        await db.commit()
    logger.info("✅ База данных инициализирована")

async def get_user_chat(user_id: int) -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT chat_id FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else None

async def save_user(user_id: int, chat_id: int, username: Optional[str], full_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (user_id, chat_id, username, full_name) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, username, full_name)
        )
        await db.commit()

async def get_users_list() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id, chat_id, username, full_name FROM users")
        return await cursor.fetchall()

def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

@dp.message(Command("start"))
async def start_handler(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name
    
    await save_user(user_id, message.chat.id, username, full_name)
    
    await message.answer(
        "Привет! Пиши мне сообщения - они уйдут админу.\n"
        "Админ сможет ответить тебе.",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(F.from_user.id != ADMIN_ID)
async def user_message(message: Message):
    user_id = message.from_user.id
    
    await save_user(
        user_id, message.chat.id,
        message.from_user.username,
        message.from_user.full_name
    )
    
    username = escape_html(message.from_user.username or "нет")
    full_name = escape_html(message.from_user.full_name)
    
    forward_text = (
        f"👤 <b>Пользователь:</b>\n"
        f"ID: <code>{user_id}</code>\n"
        f"@{username}\n"
        f"{full_name}"
    )
    
    # КНОПКА "Ответить"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Ответить", callback_data=f"reply_{user_id}")]
    ])
    
    try:
        if message.text:
            text_content = escape_html(message.text)
            await bot.send_message(
                ADMIN_ID, 
                forward_text + f"\n\n{text_content}", 
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        elif message.photo:
            await bot.send_photo(
                ADMIN_ID, 
                message.photo[-1].file_id, 
                caption=forward_text, 
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        elif message.voice:
            await bot.send_voice(ADMIN_ID, message.voice.file_id, caption=forward_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        elif message.video:
            await bot.send_video(ADMIN_ID, message.video.file_id, caption=forward_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        elif message.document:
            await bot.send_document(ADMIN_ID, message.document.file_id, caption=forward_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        else:
            await bot.send_message(ADMIN_ID, forward_text + "\n[Другое сообщение]", parse_mode=ParseMode.HTML, reply_markup=keyboard)
        
        logger.info(f"✅ Переслано админу от user_id={user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка пересылки: {e}")

# Обработчик кнопки "Ответить"
@dp.callback_query(F.data.startswith("reply_"))
async def reply_button_clicked(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Только для админа")
        return
    
    user_id = int(callback.data.split("_")[1])
    
    # Сохраняем user_id в состояние
    await state.update_data(reply_to_user=user_id)
    await state.set_state(ReplyState.waiting_reply)
    
    await callback.message.answer(
        f"✍️ Напишите ответ для пользователя {user_id}:\n\n"
        f"Отправьте текст, фото, видео или документ.\n"
        f"Для отмены: /cancel"
    )
    await callback.answer()
    logger.info(f"🔘 Админ нажал 'Ответить' для user_id={user_id}")

# Обработчик сообщения-ответа
@dp.message(ReplyState.waiting_reply)
async def process_reply_message(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = data.get("reply_to_user")
    
    if not user_id:
        await message.answer("❌ Ошибка: не найден user_id")
        await state.clear()
        return
    
    chat_id = await get_user_chat(user_id)
    
    if not chat_id:
        await message.answer(f"❌ Пользователь {user_id} не найден в БД")
        await state.clear()
        return
    
    try:
        if message.text:
            await bot.send_message(chat_id, f"💬 <b>Ответ от админа:</b>\n\n{message.text}", parse_mode=ParseMode.HTML)
        elif message.photo:
            caption = message.caption or ""
            await bot.send_photo(chat_id, message.photo[-1].file_id, caption=f"💬 <b>Ответ от админа:</b>\n\n{caption}", parse_mode=ParseMode.HTML)
        elif message.video:
            caption = message.caption or ""
            await bot.send_video(chat_id, message.video.file_id, caption=f"💬 <b>Ответ от админа:</b>\n\n{caption}", parse_mode=ParseMode.HTML)
        elif message.document:
            caption = message.caption or ""
            await bot.send_document(chat_id, message.document.file_id, caption=f"💬 <b>Ответ от админа:</b>\n\n{caption}", parse_mode=ParseMode.HTML)
        elif message.voice:
            await bot.send_voice(chat_id, message.voice.file_id)
        else:
            await message.answer("❌ Неподдерживаемый тип сообщения")
            await state.clear()
            return
        
        await message.answer(f"✅ Отправлено пользователю {user_id}")
        logger.info(f"✅ Ответ отправлен user_id={user_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки: {e}")
        await message.answer(f"❌ Ошибка: {e}")
    
    await state.clear()

@dp.message(Command("cancel"))
async def cancel_reply(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("❌ Нет активного ответа")
        return
    
    await state.clear()
    await message.answer("❌ Ответ отменён")

@dp.message(Command("users"))
async def list_users(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    users = await get_users_list()
    if not users:
        await message.answer("👥 Нет пользователей")
        return
    
    text = "👥 <b>Пользователи:</b>\n\n"
    for uid, cid, un, fn in users[:20]:
        username = escape_html(un or "нет")
        fullname = escape_html(fn)
        text += f"• ID <code>{uid}</code> (@{username}) {fullname}\n"
    
    if len(users) > 20:
        text += f"\n... и ещё {len(users)-20}"
    
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(Command("clear_users"))
async def clear_users(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM users")
        await db.commit()
    await message.answer("🗑️ БД очищена")

async def main():
    await init_db()
    logger.info(f"🤖 Бот запущен! ADMIN_ID={ADMIN_ID}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
