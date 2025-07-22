import asyncio
from os import getenv
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from openai import OpenAI
import httpx
from collections import defaultdict, deque
from db import init_db, log_request, log_writer, load_recent_messages, NAME_DB
import re
import sqlite3

# Память диалога: user_id -> очередь из 10 сообщений
dialogues = defaultdict(lambda: deque(maxlen = 10))

load_dotenv() # Загружаем .env

TOKEN = getenv("BOT_TOKEN")
OPENROUTER_KEY = getenv("CHATBOT_KEY")
NEWS_API_KEY = getenv("NEWS_API_KEY")

user_roles = defaultdict(lambda: "standard") # По умолчанию – стандартный режим

client = OpenAI(
    base_url = "https://openrouter.ai/api/v1",
    api_key = OPENROUTER_KEY,
    http_client = httpx.Client(timeout = httpx.Timeout(20.0))
)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Меню с кнопками
menu_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text = "🤖 Спросить ИИ", callback_data = "ask_ai")],
    [InlineKeyboardButton(text = "📰 Популярные новости", callback_data = "news")],
    [InlineKeyboardButton(text = "🎲 Случайный факт", callback_data = "fact")],
    [InlineKeyboardButton(text = "🎭 Выбрать роль", callback_data = "role")],
    [InlineKeyboardButton(text = "❓ Помощь", callback_data = "help")],
    [InlineKeyboardButton(text = "🧹 Очистить память", callback_data = "clear_memory")]
])

# Кнопки выбора ролей
role_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text = "Стандартный", callback_data = "role_standard")],
    [InlineKeyboardButton(text = "Философ", callback_data = "role_philosopher")],
    [InlineKeyboardButton(text = "Программист", callback_data = "role_programmer")],
    [InlineKeyboardButton(text = "Комик", callback_data = "role_comedian")],
    [InlineKeyboardButton(text = "🔙 Назад", callback_data = "back_to_menu")],
])

role_prompts = {
    "standard": "Ты – полезный ассистент.",
    "philosopher": "Ты – мудрый философ, дающий глубокие размышления.",
    "programmer": "Ты – опытный программист, объясняющий технические темы просто.",
    "comedian": "Ты – комик, который отвечает с юмором и шутками.",
}

async def show_role_menu(msg):
    await msg.answer("Выберите роль ИИ: ", reply_markup = role_keyboard)

def strip_html_links(text):
    return re.sub(r'<a href="([^"]+)">([^<]+)</a>', r'\2 — \1', text)

def restore_all_dialogues():
    conn = sqlite3.connect(NAME_DB)
    c = conn.cursor()
    c.execute("""
        SELECT DISTINCT user_id FROM logs
        WHERE event_type IN ('message', 'response')
    """)
    user_ids = [row[0] for row in c.fetchall()]
    conn.close()

    for user_id in user_ids:
        messages = load_recent_messages(user_id)
        dq = deque(messages[-10:], maxlen = 10) # ограничиваем до 10 сообщений
        dialogues[user_id] = dq    

async def send_news_and_remember(user_id: int, msg_func, raw_source: str = "news"):
    news = await get_top_news()
    log_request(user_id, "response", raw_source, news)
    # Добавим новости в память (без HTML тегов)
    plain_news = strip_html_links(news)
    dialogues[user_id].append({
        "role": "user",
        "content": "Покажи свежие новости"
    })
    dialogues[user_id].append({
        "role": "assistant",
        "content": plain_news
    })
    await msg_func(f"📰 Топ-новости:\n\n{news}", parse_mode = "HTML", disable_web_page_preview=True)

# Command handler
@dp.message(Command("start"))
async def command_start_handler(message: Message) -> None:
    user_id = message.from_user.id
    log_request(user_id, "command", "/start", "showed menu")
    await message.answer("Выбери действие: ", reply_markup = menu_keyboard)

@dp.message(Command("help"))
async def help_command(message: Message):
    help_text = (
        "📘 *Справка по боту*\n\n"
        "Этот бот создан разработчиком @Tailogs для помощи в диалогах, переводах, "
        "поиске фактов, новостей и имитации ролей (программист, философ и др).\n\n"
        "💡 *Бот умеет:*\n"
        "• Поддерживать связный диалог\n"
        "• Переводить тексты на разные языки\n"
        "• Рассказывать случайные факты\n"
        "• Выдавать свежие новости\n"
        "• Работать в разных режимах ролей\n\n"
        "🔧 *Доступные команды:*\n"
        "`/translate <язык> <текст>` — перевести текст\n"
        "`/clear` — очистить память диалога\n"
        "`/menu` — открыть меню\n"
        "`/fact` — случайный факт\n"
        "`/role` — выбираете роль для ИИ\n"
        "`/help` — эта справка"
    )
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("menu"))
async def menu_command(message: Message):
    await message.answer("📋 Выбери действие:", reply_markup=menu_keyboard)

@dp.message(Command("clear"))
async def clear_command(message: Message):
    user_id = message.from_user.id
    dialogues[user_id].clear()
    await message.answer("🧠 Память диалога очищена.")

@dp.message(Command("fact"))
async def random_fact_command(message: Message):
    await handle_request(message, "Расскажи интересный факт")

@dp.message(Command("news"))
async def news_command(message: Message):
    await send_news_and_remember(message.from_user.id, message.answer, "/news")

@dp.message(Command("role"))
async def role_command(message: Message):
    user_id = message.from_user.id 
    try:
        await message.delete()
    except Exception as e:
        error_text = f"Не удалось удалить сообщение: {e}"
        print(error_text)
        log_request(user_id, "error", "delete_message", error_text)
    await show_role_menu(message)

async def send_long_message(message: Message, text: str):
    # Делим текст на куски по 4096 символов (Telegram лимит)
    for i in range(0, len(text), 4096):
        chunk = text[i : i + 4096]
        await message.answer(chunk)

async def safe_chat_completion(messages: list, model: str = "deepseek/deepseek-r1-0528-qwen3-8b:free"):
    loop = asyncio.get_running_loop()
    try:
        # Запускаем синхронный метод в отдельном потоке
        response = await loop.run_in_executor(None, lambda: client.chat.completions.create(
            model = model,
            messages = messages,
        ))
        return response
    except Exception as e:
        raise RuntimeError(f"Ошибка при обращении к AI: {e}")

async def get_top_news():
    url = (
        "https://newsapi.org/v2/everything?"
        "q=новости&language=ru&pageSize=10&sortBy=publishedAt"
    )
    headers = {"Authorization": NEWS_API_KEY}

    try:
        async with httpx.AsyncClient(timeout = 10) as client:
            response = await client.get(url, headers = headers)
            response.raise_for_status() # Если 403/500 и т.п.
            data = response.json()
    except Exception as e:
        return f"❌ Ошибка при получении новостей: {e}"

    articles = data.get("articles", [])
    if not articles:
        return "❌ Не удалось найти новости."

    news_items = []
    for article in articles:
        title = article.get("title", "Без названия")
        url = article.get("url", "#")
        news_items.append(f"• <a href=\"{url}\">{title}</a>")

    return "\n\n".join(news_items)

@dp.message()
async def ai_reply(message: Message) -> None:
    user_id = message.from_user.id
    user_text = message.text or ""

    # Игнорирование команды, кроме /translate
    if user_text.startswith("/") and not user_text.startswith("/translate"):
        return # Молча игнорировать неизвестные команды
    
    log_request(user_id, "message", user_text, "") # Логируем только валидный ввод

    # Проверим, команда ли это /translate
    if user_text.startswith("/translate"):
        parts = user_text.split(maxsplit = 2) # ['/translate', 'en', 'текст']
        if len(parts) < 3:
            log_request(user_id, "translate", user_text, "❌ Недостаточно аргументов")
            await message.answer("Использование: /translate <код языка, en и т.д.> <текст>")
            return

        target_lang = parts[1]
        text_to_translate = parts[2]

        # Формотируем prompt для перехода
        prompt = (
            f"Translate the following text to {target_lang}. "
            "Only return the translation, without explanations. "
            "If there are multiple possible translations, list them each on a new line, "
            "each starting with a bullet point like this: • Translation.\n\n"
            f"{text_to_translate}"
        )

        try:
            completion = await safe_chat_completion(
                messages = [{"role": "user", "content": prompt}]
            )
            translation = completion.choices[0].message.content
            log_request(user_id, "translate", text_to_translate, translation)
            await send_long_message(message, translation)
        except Exception as e:
            error_text = str(e)
            log_request(user_id, "error", "translate", error_text)
            await message.answer(f"Ошибка при переводе: {error_text}")
        return # Чтобы не идти дальше в общий AI ответ

    # Получаем роль пользователя
    role = user_roles[user_id]

    # Формируем список сообщений с системным сообщением роли в начале
    system_message = {"role": "system", "content": role_prompts.get(role, role_prompts["standard"])}
    user_msgs = list(dialogues[user_id])
    messages = [system_message] + user_msgs + [{"role": "user", "content": user_text}]

    # Добавим сообщение пользователя в память
    dialogues[user_id].append({"role": "user", "content": user_text})

    try:
        # Отправляем всю историю
        completion = await safe_chat_completion(messages)
        reply = completion.choices[0].message.content
        log_request(user_id, "response", user_text, reply)    
        # Добавим ответ ИИ в память
        dialogues[user_id].append({"role": "assistant", "content": reply})

        await send_long_message(message, reply)

    except Exception as e:
        error_text = str(e)
        log_request(user_id, "error", user_text, error_text)
        await message.answer(f"Ошибка: {error_text}")

@dp.callback_query()
async def handle_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data
    log_request(user_id, "callback", data, "")

    if data == "role":
        # Удалим текущее сообщение, если возможно
        try:
            await callback.message.delete()
        except Exception as e:
            log_request(user_id, "error", "delete_message", str(e))
        await show_role_menu(callback.message)

    if data == "role":
        await show_role_menu(callback.message)
    elif data.startswith("role_"):
        role = data[len("role_"):] # Например, "philosopher"
        user_roles[user_id] = role
        await callback.message.answer(f"Режим ИИ изменен на: {role}")
        await callback.answer()
        return
    elif data == "ask_ai":
        await callback.message.answer("Напиши мне сообщение, и я отвечу с помощью ИИ.")
    elif data == "news":
        await send_news_and_remember(user_id, callback.message.answer, "news_request")
    elif data == "fact":
        try:
            prompt = "Расскажи короткий интересный случайный факт."

            # Добавляем как новый запрос
            dialogues[user_id].append({"role": "user", "content": prompt})

            completion = await safe_chat_completion(list(dialogues[user_id]))
            fact = completion.choices[0].message.content
            log_request(user_id, "response", prompt, fact)
            dialogues[user_id].append({"role": "assistant", "content": fact})

            await callback.message.answer(f"🎲 Случайный факт:\n\n{fact}")
        except Exception as e:
            error_text = str(e)
            log_request(user_id, "error", "fact callback", error_text)
            await callback.message.answer(f"Ошибка при получении факта: {error_text}")
    elif data == "help":
        await help_command(callback.message)
    elif data == "clear_memory":
        dialogues[user_id].clear()
        role = user_roles[user_id]
        system_msg = {"role": "system", "content": role_prompts.get(role, role_prompts["standard"])}
        dialogues[user_id].append(system_msg)
        await callback.message.answer("🧹 Память очищена.")
    elif data == "back_to_menu":
        await callback.message.answer("Выбери действие: ", reply_markup = menu_keyboard)

    await callback.answer()  # обязательно, чтобы Telegram не показывал "загрузка"

# Run the bot
async def main() -> None:
    init_db()
    restore_all_dialogues()
    shutdown_event = asyncio.Event()
    # Запускаем логгер в фоне
    task = asyncio.create_task(log_writer(shutdown_event))
    try:
        await dp.start_polling(bot)
    finally:
        shutdown_event.set()
        await task

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"FATAL ERROR: {e}")