import sqlite3
import asyncio
from datetime import datetime

NAME_DB = "log.db"

# Буфер логов в памяти
log_queue = asyncio.Queue()

shutdown_event = asyncio.Event()

# Создание таблицы
def init_db():
    conn = sqlite3.connect(NAME_DB)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            user_id INTEGER,
            event_type TEXT,
            prompt TEXT,
            response TEXT
        )
    """)
    conn.commit()
    conn.close()

def load_recent_messages(user_id: int, limit: int = 10):
    conn = sqlite3.connect(NAME_DB)
    c = conn.cursor()

    c.execute("""
        SELECT event_type, prompt, response FROM logs
        WHERE user_id = ? AND event_type IN ('message', 'response')
        ORDER BY id DESC
        LIMIT ?
    """, (user_id, limit * 2)) # умножаем на 2, так как один диалог = вопрос + ответ

    rows = c.fetchall()
    conn.close()

    # Восстановим сообщения в порядке: старое -> новое
    messages = []
    for event_type, prompt, response in reversed(rows):
        if event_type == "message":
            messages.append({"role": "user", "content": prompt})
        elif event_type == "response":
            messages.append({"role": "user", "content": response })
    return messages

async def log_writer(shutdown_event: asyncio.Event):
    """Асинхронный воркер, который собирает логи из очереди и пишет пачков в БД"""
    while not shutdown_event.is_set():
        batch = []

        try:
            # Ждем первое сообщения в пачку
            item = await log_queue.get()
            batch.append(item)
            # Пытаемся собрать остальные из очереди без ожидания, максимум 100 штук
            for _ in range(100):
                try:
                    item = log_queue.get_nowait()
                    batch.append(item)
                except asyncio.QueueEmpty:
                    break

            conn = sqlite3.connect(NAME_DB)
            c = conn.cursor()

            # Консольный вывод
            for (timestamp, user_id, event_type, prompt, response) in batch:
                prompt_trim = (prompt[:1000] + '...') if len(prompt) > 1000 else prompt
                response_trim = (response[:1000] + '...') if len(response) > 1000 else response
                print(f"[{timestamp}] [{event_type.upper()}] user_id = {user_id} prompt = '{prompt_trim}' response = '{response_trim}'")

            c.executemany("""
                INSERT INTO logs (timestamp, user_id, event_type, prompt, response)
                VALUES (?, ?, ?, ?, ?)
            """, batch)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Ошибка записи логов в БД: {e}")

        await asyncio.sleep(1)
    while not log_queue.empty():
        batch = []
        try:
            for i in range(100):
                batch.append(log_queue.get_nowait())
        except asyncio.QueueEmpty:
            pass

        if batch:
            conn = sqlite3.connect(NAME_DB)
            c = conn.cursor()
            c.executemany("""
                INSERT INTO logs (timestamp, user_id, event_type, prompt, response)
                VALUES (?, ?, ?, ?, ?)
            """, batch)
            conn.commit()
            conn.close()    

# Функция логирования
def log_request(user_id: int, event_type: str, prompt: str, response: str):
    """Функция кладет запись в очередь, возвращается мгновенно"""
    timestamp = datetime.utcnow().isoformat() # по UTC
    # Не обрезаем данные тут, пусть воркер выводит
    log_queue.put_nowait((timestamp, user_id, event_type, prompt, response))    