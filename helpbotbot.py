import asyncio
import logging
import os
from typing import Optional
import aiosqlite

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.enums import ParseMode

# Настройка логирования
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
dp = Dispatcher()

DB_PATH = "/app/data/users.db"

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
        result = row[0] if row else None
        logger.info(f"🔍 get_user_chat({user_id}) -> {result}")
        return result

async def save_user(user_id: int, chat_id: int, username: Optional[str], full_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (user_id, chat_id, username, full_name) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, username, full_name)
        )
        await db.commit()
    logger.info(f"💾 Сохранен user_id={user_id}, chat_id={chat_id}, username={username}")

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
    
    logger.info(f"🚀 /start от user_id={user_id}, chat_id={message.chat.id}")
    
    await save_user(user_id, message.chat.id, username, full_name)
    
    await message.answer(
        "Привет! Пиши мне сообщения - они уйдут админу.\n"
        "Админ сможет ответить тебе.",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(F.from_user.id != ADMIN_ID)
async def user_message(message: Message):
    user_id = message.from_user.id
    
    logger.info(f"📨 Сообщение от user_id={user_id}, chat_id={message.chat.id}, username={message.from_user.username}")
    
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
    
    try:
        if message.text:
            text_content = escape_html(message.text)
            await bot.send_message(ADMIN_ID, forward_text + f"\n\n{text_content}", parse_mode=ParseMode.HTML)
        elif message.photo:
            await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=forward_text, parse_mode=ParseMode.HTML)
        elif message.voice:
            await bot.send_voice(ADMIN_ID, message.voice.file_id, caption=forward_text, parse_mode=ParseMode.HTML)
        elif message.video:
            await bot.send_video(ADMIN_ID, message.video.file_id, caption=forward_text, parse_mode=ParseMode.HTML)
        elif message.document:
            await bot.send_document(ADMIN_ID, message.document.file_id, caption=forward_text, parse_mode=ParseMode.HTML)
        else:
            await bot.send_message(ADMIN_ID, forward_text + "\n[Другое сообщение]", parse_mode=ParseMode.HTML)
        
        logger.info(f"✅ Переслано админу {ADMIN_ID}")
    except Exception as e:
        logger.error(f"❌ Ошибка пересылки админу: {e}")

@dp.message(F.from_user.id == ADMIN_ID)
async def admin_message(message: Message):
    logger.info(f"👨‍💼 Сообщение от админа, reply={message.reply_to_message is not None}")
    
    if not message.reply_to_message:
        await message.answer("❗ Ответьте (reply) на сообщение пользователя\nИли /reply <user_id> <текст>")
        return
    
    replied = message.reply_to_message
    text_to_check = replied.text or replied.caption or ""
    
    if "ID: <code>" not in text_to_check:
        await message.answer("❌ Reply на сообщение с ID пользователя")
        return
    
    try:
        start = text_to_check.find("ID: <code>") + 10
        end = text_to_check.find("</code>", start)
        user_id_str = text_to_check[start:end]
        user_id = int(user_id_str)
        chat_id = await get_user_chat(user_id)
        
        logger.info(f"📤 Reply: user_id={user_id}, chat_id={chat_id}")
        
        if not chat_id:
            await message.answer(f"❌ Пользователь {user_id} не найден в БД")
            return
        
        if message.text:
            await bot.send_message(chat_id, f"💬 <b>Ответ от админа:</b>\n\n{message.text}", parse_mode=ParseMode.HTML)
            logger.info(f"✅ Reply отправлен user_id={user_id}")
        
        await message.answer(f"✅ Отправлено пользователю {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка reply: {e}")
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("reply"))
async def admin_reply_command(message: Message):
    if message.from_user.id != ADMIN_ID:
        logger.warning(f"⚠️ Попытка /reply от не-админа: {message.from_user.id}")
        return
    
    logger.info(f"📝 Команда /reply от админа: {message.text}")
    
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("📝 /reply <user_id> <текст>")
        return
    
    try:
        user_id = int(parts[1])
        text = parts[2]
        
        logger.info(f"🎯 Попытка отправки: user_id={user_id}")
        
        if user_id == ADMIN_ID:
            await message.answer("❌ Нельзя отправить себе")
            return
        
        chat_id = await get_user_chat(user_id)
        
        # DEBUG для вас
        await message.answer(f"🔍 DEBUG:\nuser_id={user_id}\nchat_id={chat_id}\nтекст={text[:50]}")
        
        if not chat_id:
            await message.answer(f"❌ chat_id не найден для user_id {user_id}")
            logger.error(f"❌ БД не содержит user_id={user_id}")
            return
        
        # Попытка отправки
        try:
            result = await bot.send_message(
                chat_id, 
                f"💬 <b>Ответ от админа:</b>\n\n{text}", 
                parse_mode=ParseMode.HTML
            )
            logger.info(f"✅ Отправлено! message_id={result.message_id}, chat_id={chat_id}")
            await message.answer(f"✅ Отправлено пользователю {user_id}")
        except Exception as send_err:
            logger.error(f"❌ Telegram API ошибка: {send_err}")
            await message.answer(f"❌ Не удалось отправить:\n{send_err}")
            
    except ValueError as ve:
        logger.error(f"❌ ValueError: {ve}")
        await message.answer("❌ user_id должен быть числом")
    except Exception as e:
        logger.error(f"❌ Общая ошибка /reply: {e}")
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("users"))
async def list_users(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    users = await get_users_list()
    logger.info(f"📋 /users вызван, найдено {len(users)} пользователей")
    
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
    logger.info("🗑️ БД очищена")
    await message.answer("🗑️ БД очищена")

async def main():
    await init_db()
    logger.info(f"🤖 Бот запущен! ADMIN_ID={ADMIN_ID}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
