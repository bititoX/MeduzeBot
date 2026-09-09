"""
Отдельный бот-челлендж: команды на комбинации слот-машины (BAR, виноград,
лимоны, 777) и на дайсы (дартс, боулинг, баскетбол, кубик).

Для дартса/боулинга/баскетбола/кубика админ теперь САМ выбирает,
какое именно значение считается попаданием (не всегда "6").

Для каждого типа есть ДВЕ версии команды:
  - обычная (СУММАРНЫЙ счёт, промахи не мешают): /Darts значение count
  - "подряд" (промах обнуляет счётчик до нуля): /DartsPodryad значение count

Челлендж не завершается после первой победы — работает, пока админ
не остановит его командой /stop. Каждый участник может побеждать
до MAX_WINS_PER_USER раз, дальше его броски не считаются.

Установка:
    pip install aiogram

Запуск:
    python challenge_bot.py

Токен бота — переменная окружения BOT_TOKEN.
Никогда не вписывай токен прямо в этот файл.
"""

import asyncio
import logging
import os
import re
import sqlite3
import time
from contextlib import closing
from html import escape as html_escape

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message

# ==== НАСТРОЙКИ ====
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ОТ_BOTFATHER")

ADMIN_USERNAMES = {"nexoraizfuck", "Meduza_owner"}
ADMINS_LINE = " ".join(f"@{u}" for u in ADMIN_USERNAMES)

# ---- ТУРНИР (учитывает только прокруты слота 🎰, отдельно от челленджей) ----
# путь к базе данных. На Railway контейнер эфемерный — подключи Volume и
# укажи DB_PATH внутри него, иначе база обнулится при каждом деплое.
DB_PATH = os.getenv("DB_PATH", "challenge_bot.db")

STARS_PER_SPIN = 2          # звёзды начисляются за ЛЮБОЙ прокрут слота
TOURNAMENT_DAYS = 7         # сколько дней длится турнир

PRIZE_1 = "NFT"
PRIZE_2 = "100"
PRIZE_3 = "50"

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ---------------------------------------------------------------------------
# ПРЕМИУМ-ЭМОДЗИ. Реально отображаются с анимацией только если у ВЛАДЕЛЬЦА
# этого бота есть Telegram Premium (или куплен username через Fragment).
# ---------------------------------------------------------------------------
PREMIUM_EMOJI_IDS = {
    "party": "5208541126583136130",      # 🎉
    "check": "5465665580050717956",      # ✔️
    "blue_dot": "5465250866598547954",   # 🔵
    "pin": "5465298472016059249",        # 📌
    "seven": "4938373072185984758",      # 7️⃣
    "star": "5924870095925942277",       # ⭐
    "slot_spin": "5915833712368424979",  # 🎰
    "gift_smile": "5240487046086169983", # 😀
    "sad": "5456580397074778248",        # 😢 (после "один промах — счётчик обнуляется")
    "cube_icon": "5260547274957672345",  # 🎲 (общая иконка кубика)
}

# Полный набор ID для КАЖДОГО значения каждого дайса.
DIGIT_PREMIUM_IDS = {
    "🏀": {
        1: "5888765728957402475",
        2: "5890782306297187597",
        3: "5890945283126202225",
        4: "5888994487505536067",
        5: "5891181665241271999",
    },
    "🎳": {
        1: "5890902196014288686",
        2: "5891039922730569230",
        3: "5888582857839874068",
        4: "5891006361856118397",
        5: "5891265223830015247",
        6: "5891120371762990493",
    },
    "🎯": {
        1: "5891075592433962392",
        2: "5890981489700506545",
        3: "5890896071390924854",
        4: "5888919733599735824",
        5: "5891052704553242318",
        6: "5891181334528789506",
    },
    "🎲": {
        1: "5890885351152553528",
        2: "5891165086667509023",
        3: "5888803657813594137",
        4: "5890915785290813565",
        5: "5891205850202115734",
        6: "5891226736628076283",
    },
}

# У каждого типа дайса и каждого значения — своя фраза (не одна общая на все).
BASKETBALL_PHRASES = {
    1: "промахнуться",
    2: "промахнуться",
    3: "Мячик застрял",
    4: "Попасть",
    5: "Попасть в кольцо идеально",
}
DARTS_PHRASES = {
    1: "Отскок",
    2: "Попасть на край",
    3: "Попасть на край",
    4: "Ближе к центру",
    5: "Очень близко",
    6: "Попасть ровно в центр",
}
BOWLING_PHRASES = {
    1: "Выбить 0 кегль",
    2: "Выбить 1 кегль",
    3: "Выбить 3 кегль",
    4: "Выбить 4 кегль",
    5: "Выбить 5 кегль",
    6: "Выбить 6 кегль",
}
CUBE_PHRASES = {
    1: "Выбить 1",
    2: "Выбить 2",
    3: "Выбить 3",
    4: "Выбить 4",
    5: "Выбить 5",
    6: "Выбить 6",
}
DICE_PHRASES = {"🏀": BASKETBALL_PHRASES, "🎯": DARTS_PHRASES, "🎳": BOWLING_PHRASES, "🎲": CUBE_PHRASES}


def tg(key: str, fallback: str) -> str:
    emoji_id = PREMIUM_EMOJI_IDS.get(key)
    if not emoji_id:
        return fallback
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def keycap(value: int) -> str:
    """Обычная цифра в стиле Telegram-keycap, например 6 -> '6️⃣'."""
    return f"{value}\ufe0f\u20e3"


def digit_html(emoji: str, value: int) -> str:
    fallback = keycap(value)
    emoji_id = DIGIT_PREMIUM_IDS.get(emoji, {}).get(value)
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback


REEL_SYMBOLS = ["BAR", "🍇", "🍋", "7️⃣"]
SEVEN = "7️⃣"

COMBO_BY_SYMBOL = {"BAR": "bar", "🍇": "grapes", "🍋": "lemons", SEVEN: "seven"}
COMBO_DISPLAY_NAMES = {
    "bar": "BAR BAR BAR 🍫🍫🍫",
    "grapes": "виноград 🍇🍇🍇",
    "lemons": "лимоны 🍋🍋🍋",
    "seven": f"{tg('seven', SEVEN)}{tg('seven', SEVEN)}{tg('seven', SEVEN)}",
}
SLOT_COMMAND_TO_COMBO = {"Bar": "bar", "Seven": "seven", "Lemons": "lemons", "Grapes": "grapes"}

# Диапазон допустимых значений для каждого типа дайса.
DICE_VALUE_RANGE = {"🎯": (1, 6), "🎳": (1, 6), "🏀": (1, 5), "🎲": (1, 6)}
DICE_COMMAND_TO_EMOJI = {"Darts": "🎯", "Bowling": "🎳", "Basketball": "🏀", "Cube": "🎲"}
DICE_PODRYAD_COMMAND_TO_EMOJI = {
    "DartsPodryad": "🎯", "BowlingPodryad": "🎳", "BasketballPodryad": "🏀", "CubePodryad": "🎲",
}


def target_description(emoji: str, value: int) -> str:
    """Человекочитаемое описание цели для конкретного дайса и значения."""
    digit = digit_html(emoji, value)
    if emoji == "🎲":
        phrase = CUBE_PHRASES.get(value, "Выбить")
        return f"{phrase} {digit} {tg('cube_icon', '🎲')}"
    phrase = DICE_PHRASES.get(emoji, {}).get(value, "Попасть")
    return f"{phrase} {digit}"


DICE_SHORT_NAME = {"🎯": "дартс", "🎳": "боулинг", "🏀": "баскетбол", "🎲": "кубик"}

# ---------------------------------------------------------------------------
# БАЗА ДАННЫХ ТУРНИРА (sqlite) — считает прокруты слота 🎰 по чатам
# ---------------------------------------------------------------------------


def db_connect() -> sqlite3.Connection:
    folder = os.path.dirname(DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    with closing(db_connect()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                chat_id  INTEGER NOT NULL,
                user_id  INTEGER NOT NULL,
                username TEXT,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS spins (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id  INTEGER NOT NULL,
                user_id  INTEGER NOT NULL,
                ts       INTEGER NOT NULL,
                stars    INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                chat_id INTEGER NOT NULL,
                key     TEXT NOT NULL,
                value   TEXT,
                PRIMARY KEY (chat_id, key)
            )
            """
        )


def upsert_tournament_user(chat_id: int, user_id: int, name: str) -> None:
    with closing(db_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO users(chat_id, user_id, username) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, user_id) DO UPDATE SET username=excluded.username",
            (chat_id, user_id, name),
        )


def add_tournament_spin(chat_id: int, user_id: int, stars: int) -> None:
    with closing(db_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO spins(chat_id, user_id, ts, stars) VALUES (?,?,?,?)",
            (chat_id, user_id, int(time.time()), stars),
        )


def get_tournament_setting(chat_id: int, key: str):
    with closing(db_connect()) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE chat_id=? AND key=?", (chat_id, key)
        ).fetchone()
        return row[0] if row else None


def set_tournament_setting(chat_id: int, key: str, value) -> None:
    with closing(db_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO settings(chat_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, key) DO UPDATE SET value=excluded.value",
            (chat_id, key, str(value)),
        )


def start_tournament(chat_id: int) -> int:
    now = int(time.time())
    set_tournament_setting(chat_id, "tournament_start", now)
    set_tournament_setting(chat_id, "tournament_end", "")
    set_tournament_setting(chat_id, "tournament_active", 1)
    return now


def end_tournament(chat_id: int) -> None:
    set_tournament_setting(chat_id, "tournament_end", int(time.time()))
    set_tournament_setting(chat_id, "tournament_active", 0)


def get_tournament_state(chat_id: int):
    """Возвращает (start_ts | None, end_ts | None, active: bool)."""
    start = get_tournament_setting(chat_id, "tournament_start")
    end = get_tournament_setting(chat_id, "tournament_end")
    active = get_tournament_setting(chat_id, "tournament_active")
    start_ts = int(start) if start else None
    end_ts = int(end) if end else None
    is_active = bool(active) and active == "1"
    return start_ts, end_ts, is_active


def get_tournament_leaderboard(chat_id: int, limit: int = 3):
    """Топ по количеству прокрутов слота (звёзды — доп. инфо, на место не влияют)."""
    start_ts, end_ts, _active = get_tournament_state(chat_id)
    if start_ts is None:
        return []
    window_end = end_ts if end_ts else int(time.time())

    with closing(db_connect()) as conn:
        rows = conn.execute(
            """
            SELECT u.user_id, u.username,
                   COALESCE(SUM(s.stars), 0)  AS stars,
                   COUNT(s.id)                AS spins
            FROM users u
            JOIN spins s ON s.user_id = u.user_id AND s.chat_id = u.chat_id
            WHERE u.chat_id = ? AND s.ts >= ? AND s.ts <= ?
            GROUP BY u.user_id
            HAVING spins > 0
            ORDER BY spins DESC, stars DESC
            LIMIT ?
            """,
            (chat_id, start_ts, window_end, limit),
        ).fetchall()
    return rows


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return few
    return many


def stars_word(n: int) -> str:
    return plural(n, "звезда", "звезды", "звёзд")


def spins_word(n: int) -> str:
    return plural(n, "прокрут", "прокрута", "прокрутов")


def days_word(n: int) -> str:
    return plural(n, "день", "дня", "дней")


def hours_word(n: int) -> str:
    return plural(n, "час", "часа", "часов")


TOURNAMENT_MEDALS = [
    '<tg-emoji emoji-id="5440539497383087970">🥇</tg-emoji>',
    '<tg-emoji emoji-id="5447203607294265305">🥈</tg-emoji>',
    '<tg-emoji emoji-id="5453902265922376865">🥉</tg-emoji>',
]
T_FIRE = '<tg-emoji emoji-id="5463154755054349837">🔥</tg-emoji>'
T_DIAMOND = '<tg-emoji emoji-id="5280858699286471614">💎</tg-emoji>'
T_GIFT = '<tg-emoji emoji-id="5436006606078769970">🎁</tg-emoji>'
T_CUP = '<tg-emoji emoji-id="5280769763398671636">🏆</tg-emoji>'
T_ROCKET = '<tg-emoji emoji-id="5283080528818360566">🚀</tg-emoji>'
T_STAR = '<tg-emoji emoji-id="5924870095925942277">⭐️</tg-emoji>'
T_CHART = '<tg-emoji emoji-id="5436331451635245129">📈</tg-emoji>'
T_SLOT = '<tg-emoji emoji-id="5915833712368424979">🎰</tg-emoji>'
T_SMILE = '<tg-emoji emoji-id="5461117441612462242">🙂</tg-emoji>'
T_HOURGLASS = '<tg-emoji emoji-id="5386367538735104399">⌛</tg-emoji>'


def tournament_user_link(user_id: int, name: str) -> str:
    safe_name = html_escape(name or "Без имени")
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>'


def build_tournament_time_left_line(chat_id: int) -> str:
    start_ts, end_ts, active = get_tournament_state(chat_id)

    if start_ts is None:
        return f"{T_HOURGLASS} Турнир ещё не запущен. Ждите объявления от администрации!"

    if not active:
        return f"{T_HOURGLASS} Турнир завершён! Ждите начала нового 🏁"

    target_end = start_ts + TOURNAMENT_DAYS * 86400
    remaining = target_end - int(time.time())
    if remaining <= 0:
        return f"{T_HOURGLASS} Рейтинг обновляется. Турнир вот-вот завершится!"

    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    if days > 0:
        left = f"{days} {days_word(days)} {hours} {hours_word(hours)}"
    else:
        minutes = (remaining % 3600) // 60
        if hours > 0:
            left = f"{hours} {hours_word(hours)} {minutes} мин"
        else:
            left = f"{minutes} мин"
    return f"{T_HOURGLASS} Рейтинг обновляется. До конца турнира осталось: {left}!"


def build_tournament_top_text(chat_id: int) -> str:
    rows = get_tournament_leaderboard(chat_id, limit=3)

    lines = [
        f"{T_FIRE} Встречайте ТОП пользователей за эту неделю {T_DIAMOND}",
        "",
        f"{TOURNAMENT_MEDALS[0]} место — {PRIZE_1} {T_GIFT}",
        f"{TOURNAMENT_MEDALS[1]} место — {PRIZE_2}{T_CUP}",
        f"{TOURNAMENT_MEDALS[2]} место — {PRIZE_3}{T_ROCKET}",
        "",
        f"{T_STAR} Напоминаем: чем больше прокрутов слота — тем выше твоё место {T_GIFT}",
        "",
        f"{T_CHART} Текущий рейтинг лидеров:",
    ]

    if not rows:
        lines.append(f"Пока никто не крутил слот {T_SMILE}")
    else:
        for i, (user_id, username, stars, spins) in enumerate(rows):
            lines.append(f"{TOURNAMENT_MEDALS[i]} {tournament_user_link(user_id, username)}")
            lines.append(
                f"{T_SLOT} {spins} {spins_word(spins)} | "
                f"{T_STAR} {stars} {stars_word(stars)}"
            )
            lines.append("")

    lines.append(build_tournament_time_left_line(chat_id))
    return "\n".join(lines).strip()


# Активный челлендж в чате: chat_id -> {
#   "category": "slot" | "dice",
#   "key": "bar"/"seven"/"lemons"/"grapes"  ИЛИ  "🎯"/"🎳"/"🏀"/"🎲",
#   "value": int | None (нужное значение дайса, только для category="dice"),
#   "mode": "cumulative" | "streak",
#   "target": int,
# }
active_challenge: dict[int, dict] = {}
progress: dict[int, dict] = {}

MAX_WINS_PER_USER = 2
win_counts: dict[int, dict] = {}


def is_admin(user) -> bool:
    if user is None or user.username is None:
        return False
    return user.username.lower() in {u.lower() for u in ADMIN_USERNAMES}


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def decode_slot_symbols(value: int) -> tuple[str, str, str]:
    v = value - 1
    return (REEL_SYMBOLS[v % 4], REEL_SYMBOLS[(v // 4) % 4], REEL_SYMBOLS[(v // 16) % 4])


def get_slot_combo(symbols: tuple[str, str, str]) -> str | None:
    if symbols[0] == symbols[1] == symbols[2]:
        return COMBO_BY_SYMBOL.get(symbols[0])
    return None


def has_wins_left(chat_id: int, user) -> bool:
    if is_admin(user):
        return True  # у админов нет лимита побед
    return win_counts.get(chat_id, {}).get(user.id, 0) < MAX_WINS_PER_USER


def start_challenge(chat_id: int, challenge: dict) -> None:
    active_challenge[chat_id] = challenge
    progress[chat_id] = {}


async def announce_win(message: Message, user, challenge: dict) -> None:
    who = mention(user.id, user.full_name)
    chat_wins = win_counts.setdefault(message.chat.id, {})
    chat_wins[user.id] = chat_wins.get(user.id, 0) + 1

    if challenge["category"] == "slot":
        label = COMBO_DISPLAY_NAMES[challenge["key"]]
    else:
        label = target_description(challenge["key"], challenge["value"])

    text = (
        f"{who}, {tg('party', '🎉')} Выигрыш! Условие выполнено: {label}\n\n"
        f"На этом веселье не заканчивается! Продолжайте и получайте звезды {tg('star', '⭐')}\n\n"
        f"За выдачей пишите: {ADMINS_LINE}"
    )
    await message.reply(text)
    # Челлендж НЕ завершается — сбрасываем прогресс только этому пользователю.
    progress.setdefault(message.chat.id, {})[user.id] = 0


# ---------------------------------------------------------------------------
# КОМАНДЫ ЗАПУСКА (только админы)
# ---------------------------------------------------------------------------

@dp.message(F.text.regexp(r"(?i)^/(Bar|Seven|Lemons|Grapes)(?:@\S+)?(?:\s+(\d+))?"))
async def cmd_slot_challenge(message: Message):
    if not is_admin(message.from_user):
        await message.reply("Эта команда доступна только админам.")
        return

    match = re.match(r"(?i)^/(Bar|Seven|Lemons|Grapes)(?:@\S+)?(?:\s+(\d+))?", message.text)
    command_name = match.group(1)
    canonical = next((c for c in SLOT_COMMAND_TO_COMBO if c.lower() == command_name.lower()), None)
    combo = SLOT_COMMAND_TO_COMBO.get(canonical)
    args_str = match.group(2)

    if combo is None or not args_str or int(args_str) <= 0:
        await message.reply(f"Пример: /{command_name} 3 — сколько раз суммарно нужно выбить комбинацию.")
        return

    target = int(args_str)
    start_challenge(message.chat.id, {"category": "slot", "key": combo, "value": None, "mode": "cumulative", "target": target})
    await message.reply(
        f"{tg('blue_dot', '🔵')}Лудка запущена {tg('check', '✔️')}\n"
        f"{tg('pin', '📌')}Цель: {COMBO_DISPLAY_NAMES[combo]} - {target} раз"
    )


@dp.message(F.text.regexp(r"(?i)^/(DartsPodryad|BowlingPodryad|BasketballPodryad|CubePodryad)(?:@\S+)?(?:\s+(\d+)\s+(\d+))?"))
async def cmd_dice_challenge_streak(message: Message):
    if not is_admin(message.from_user):
        await message.reply("Эта команда доступна только админам.")
        return

    match = re.match(
        r"(?i)^/(DartsPodryad|BowlingPodryad|BasketballPodryad|CubePodryad)(?:@\S+)?(?:\s+(\d+)\s+(\d+))?",
        message.text,
    )
    command_name = match.group(1)
    canonical = next((c for c in DICE_PODRYAD_COMMAND_TO_EMOJI if c.lower() == command_name.lower()), None)
    emoji = DICE_PODRYAD_COMMAND_TO_EMOJI.get(canonical)
    value_str, count_str = match.group(2), match.group(3)

    if emoji is None or not value_str or not count_str:
        lo, hi = DICE_VALUE_RANGE[emoji] if emoji else (1, 6)
        await message.reply(
            f"Пример: /{command_name} {hi} 2 — нужное значение ({lo}-{hi}) и сколько раз ПОДРЯД."
        )
        return

    value, target = int(value_str), int(count_str)
    lo, hi = DICE_VALUE_RANGE[emoji]
    if not (lo <= value <= hi) or target <= 0:
        await message.reply(f"Значение должно быть от {lo} до {hi}, а число попаданий — больше нуля.")
        return

    start_challenge(message.chat.id, {"category": "dice", "key": emoji, "value": value, "mode": "streak", "target": target})
    await message.reply(
        f"{tg('blue_dot', '🔵')}Лудка запущена {tg('check', '✔️')}\n"
        f"{tg('pin', '📌')}Цель: {target_description(emoji, value)} - {target} раз ПОДРЯД. "
        f"Один промах — и счётчик обнуляется! {tg('sad', '😢')}"
    )


@dp.message(F.text.regexp(r"(?i)^/(Darts|Bowling|Basketball|Cube)(?:@\S+)?(?:\s+(\d+)\s+(\d+))?"))
async def cmd_dice_challenge_cumulative(message: Message):
    if not is_admin(message.from_user):
        await message.reply("Эта команда доступна только админам.")
        return

    match = re.match(r"(?i)^/(Darts|Bowling|Basketball|Cube)(?:@\S+)?(?:\s+(\d+)\s+(\d+))?", message.text)
    command_name = match.group(1)
    canonical = next((c for c in DICE_COMMAND_TO_EMOJI if c.lower() == command_name.lower()), None)
    emoji = DICE_COMMAND_TO_EMOJI.get(canonical)
    value_str, count_str = match.group(2), match.group(3)

    if emoji is None or not value_str or not count_str:
        lo, hi = DICE_VALUE_RANGE[emoji] if emoji else (1, 6)
        await message.reply(
            f"Пример: /{command_name} {hi} 3 — нужное значение ({lo}-{hi}) и сколько раз суммарно."
        )
        return

    value, target = int(value_str), int(count_str)
    lo, hi = DICE_VALUE_RANGE[emoji]
    if not (lo <= value <= hi) or target <= 0:
        await message.reply(f"Значение должно быть от {lo} до {hi}, а число попаданий — больше нуля.")
        return

    start_challenge(message.chat.id, {"category": "dice", "key": emoji, "value": value, "mode": "cumulative", "target": target})
    await message.reply(
        f"{tg('blue_dot', '🔵')}Лудка запущена {tg('check', '✔️')}\n"
        f"{tg('pin', '📌')}Цель: {target_description(emoji, value)} - {target} раз (суммарно)"
    )


@dp.message(F.text.regexp(r"(?i)^/(?:StopChallenge|stop)(?:@\S+)?"))
async def cmd_stop_challenge(message: Message):
    if not is_admin(message.from_user):
        await message.reply("Эта команда доступна только админам.")
        return
    if active_challenge.pop(message.chat.id, None) is None:
        await message.reply("Сейчас нет активного челленджа.")
        return
    progress.pop(message.chat.id, None)
    await message.reply("Челлендж остановлен.")


@dp.message(F.text.regexp(r"(?i)^/ResetWins(?:@\S+)?"))
async def cmd_reset_wins(message: Message):
    if not is_admin(message.from_user):
        await message.reply("Эта команда доступна только админам.")
        return
    win_counts.pop(message.chat.id, None)
    await message.reply(f"Счётчик побед сброшен. Лимит снова {MAX_WINS_PER_USER} на пользователя.")


# ---------------------------------------------------------------------------
# КОМАНДЫ ТУРНИРА (учитывает прокруты слота 🎰, отдельно от челленджей)
# ---------------------------------------------------------------------------

@dp.message(F.text.regexp(r"(?i)^/start_tournament(?:@\S+)?"))
async def cmd_start_tournament(message: Message) -> None:
    if not is_admin(message.from_user):
        await message.reply("Эта команда доступна только админам.")
        return
    start_tournament(message.chat.id)
    await message.answer(
        f"✅ Турнир запущен! Он продлится {TOURNAMENT_DAYS} "
        f"{days_word(TOURNAMENT_DAYS)}. Напишите «топ», чтобы увидеть рейтинг."
    )


@dp.message(F.text.regexp(r"(?i)^/end_tournament(?:@\S+)?"))
async def cmd_end_tournament(message: Message) -> None:
    if not is_admin(message.from_user):
        await message.reply("Эта команда доступна только админам.")
        return
    end_tournament(message.chat.id)
    await message.answer("🏁 Турнир завершён. Итоговый рейтинг заморожен — напишите «топ», чтобы его увидеть.")


@dp.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "топ"))
async def cmd_tournament_top(message: Message) -> None:
    await message.answer(build_tournament_top_text(message.chat.id))


# ---------------------------------------------------------------------------
# ОБРАБОТКА БРОСКОВ ДАЙСОВ
# ---------------------------------------------------------------------------

@dp.message(F.dice)
async def handle_dice(message: Message):
    if message.forward_origin is not None or message.forward_date is not None:
        return

    dice = message.dice
    user = message.from_user

    # Учёт в турнире: любой прокрут слота 🎰 засчитывается независимо от
    # активного челленджа (админские прокруты в статистику не идут).
    if dice.emoji == "🎰" and user is not None and not is_admin(user):
        upsert_tournament_user(message.chat.id, user.id, user.full_name)
        add_tournament_spin(message.chat.id, user.id, STARS_PER_SPIN)

    challenge = active_challenge.get(message.chat.id)
    if challenge is None:
        return

    if not has_wins_left(message.chat.id, user):
        return

    chat_progress = progress.setdefault(message.chat.id, {})
    target = challenge["target"]

    if challenge["category"] == "slot" and dice.emoji == "🎰":
        symbols = decode_slot_symbols(dice.value)
        combo = get_slot_combo(symbols)
        if combo != challenge["key"]:
            return

        count = chat_progress.get(user.id, 0) + 1
        chat_progress[user.id] = count

        if count >= target:
            await announce_win(message, user, challenge)
        else:
            await message.reply(
                f"{mention(user.id, user.full_name)}, выбил {COMBO_DISPLAY_NAMES[combo]}! "
                f"Прогресс: {count}/{target}"
            )

    elif challenge["category"] == "dice" and dice.emoji == challenge["key"]:
        hit = dice.value == challenge["value"]

        if challenge["mode"] == "cumulative":
            if not hit:
                return
            count = chat_progress.get(user.id, 0) + 1
            chat_progress[user.id] = count
            if count >= target:
                await announce_win(message, user, challenge)
            else:
                await message.reply(
                    f"{mention(user.id, user.full_name)}, {tg('party', '🎉')} Отличный результат! "
                    f"{target_description(challenge['key'], challenge['value'])}\n\n"
                    f"Прогресс: {count}/{target}"
                )
        else:  # streak
            if hit:
                count = chat_progress.get(user.id, 0) + 1
                chat_progress[user.id] = count
                if count >= target:
                    await announce_win(message, user, challenge)
                else:
                    await message.reply(
                        f"{mention(user.id, user.full_name)}, попал! Серия подряд: {count}/{target}. "
                        f"Не промахнись!"
                    )
            else:
                had_progress = chat_progress.get(user.id, 0) > 0
                chat_progress[user.id] = 0
                if had_progress:
                    await message.reply(
                        f"{mention(user.id, user.full_name)}, промах! Серия сброшена на 0/{target}. "
                        f"Начинай заново."
                    )


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
