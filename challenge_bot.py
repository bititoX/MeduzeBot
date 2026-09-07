"""
Отдельный бот-челлендж: команды на комбинации слот-машины (BAR, виноград,
лимоны, 777) — считаются СУММАРНО, и на дартс/боулинг — считаются ТОЛЬКО
ПОДРЯД (если промазал — счётчик обнуляется до нуля).

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

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message

# ==== НАСТРОЙКИ ====
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ОТ_BOTFATHER")

# Username админов (без @), которым разрешено запускать/останавливать челленджи
ADMIN_USERNAMES = {"nexoraizfuck", "Meduza_owner"}

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

REEL_SYMBOLS = ["BAR", "🍇", "🍋", "7️⃣"]
SEVEN = "7️⃣"

COMBO_BY_SYMBOL = {"BAR": "bar", "🍇": "grapes", "🍋": "lemons", SEVEN: "seven"}
COMBO_DISPLAY_NAMES = {
    "bar": "BAR BAR BAR 🍫🍫🍫",
    "grapes": "виноград 🍇🍇🍇",
    "lemons": "лимоны 🍋🍋🍋",
    "seven": "777 7️⃣7️⃣7️⃣",
}
SLOT_COMMAND_TO_COMBO = {
    "Bar": "bar",
    "Seven": "seven",
    "Lemons": "lemons",
    "Grapes": "grapes",
}

# Дартс/боулинг: значение 6 = яблочко (дартс) / страйк (боулинг).
STREAK_TARGET_VALUE = 6
STREAK_EMOJI_NAMES = {"🎯": "яблочко в дартс 🎯", "🎳": "страйк в боулинг 🎳"}
STREAK_COMMAND_TO_EMOJI = {"Darts": "🎯", "Bowling": "🎳"}

# Активный челлендж в чате: chat_id -> {
#   "kind": "slot" | "streak",
#   "combo": "bar"/"seven"/"lemons"/"grapes"   (только для kind="slot")
#   "emoji": "🎯"/"🎳"                          (только для kind="streak")
#   "target": int
# }
active_challenge: dict[int, dict] = {}
# Прогресс: chat_id -> {user_id: текущее_число}
# Для "slot" — это сумма попаданий за всё время.
# Для "streak" — это текущая ПОДРЯДная серия (промах = сброс на 0).
progress: dict[int, dict] = {}

# Сколько раз ОДИН пользователь может побеждать в челленджах этого чата.
# После достижения лимита его броски больше не засчитываются вообще.
MAX_WINS_PER_USER = 2
# chat_id -> {user_id: сколько раз уже выиграл}
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


def start_challenge(chat_id: int, challenge: dict, announce: str) -> None:
    active_challenge[chat_id] = challenge
    progress[chat_id] = {}


async def finish_challenge(message: Message, user, label: str) -> None:
    who = mention(user.id, user.full_name)
    chat_wins = win_counts.setdefault(message.chat.id, {})
    chat_wins[user.id] = chat_wins.get(user.id, 0) + 1
    await message.reply(
        f"🎉 {who}, поздравляю! Условие челленджа выполнено — {label}!"
    )
    active_challenge.pop(message.chat.id, None)
    progress.pop(message.chat.id, None)


def has_wins_left(chat_id: int, user_id: int) -> bool:
    return win_counts.get(chat_id, {}).get(user_id, 0) < MAX_WINS_PER_USER


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
    start_challenge(message.chat.id, {"kind": "slot", "combo": combo, "target": target}, "")
    await message.reply(
        f"Челлендж запущен!\nНужно выбить {COMBO_DISPLAY_NAMES[combo]} — {target} раз(а) "
        f"(суммарно, не обязательно подряд) на слот-машине 🎰"
    )


@dp.message(F.text.regexp(r"(?i)^/(Darts|Bowling)(?:@\S+)?(?:\s+(\d+))?"))
async def cmd_streak_challenge(message: Message):
    if not is_admin(message.from_user):
        await message.reply("Эта команда доступна только админам.")
        return

    match = re.match(r"(?i)^/(Darts|Bowling)(?:@\S+)?(?:\s+(\d+))?", message.text)
    command_name = match.group(1)
    canonical = next((c for c in STREAK_COMMAND_TO_EMOJI if c.lower() == command_name.lower()), None)
    emoji = STREAK_COMMAND_TO_EMOJI.get(canonical)
    args_str = match.group(2)

    # По умолчанию — 2 раза подряд, как ты и просил, но можно указать своё число.
    target = int(args_str) if args_str else 2
    if target <= 0:
        await message.reply(f"Пример: /{command_name} 2 — сколько раз ПОДРЯД нужно попасть.")
        return

    start_challenge(message.chat.id, {"kind": "streak", "emoji": emoji, "target": target}, "")
    await message.reply(
        f"Челлендж запущен!\nНужно попасть в {STREAK_EMOJI_NAMES[emoji]} — {target} раз(а) "
        f"ПОДРЯД. Один промах — и счётчик обнуляется!"
    )


@dp.message(F.text.regexp(r"(?i)^/StopChallenge(?:@\S+)?"))
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
# ОБРАБОТКА БРОСКОВ ДАЙСОВ
# ---------------------------------------------------------------------------

@dp.message(F.dice)
async def handle_dice(message: Message):
    challenge = active_challenge.get(message.chat.id)
    if challenge is None:
        return

    dice = message.dice
    user = message.from_user

    if not has_wins_left(message.chat.id, user.id):
        return  # уже выиграл максимум раз — броски больше не считаются

    chat_progress = progress.setdefault(message.chat.id, {})

    if challenge["kind"] == "slot" and dice.emoji == "🎰":
        symbols = decode_slot_symbols(dice.value)
        combo = get_slot_combo(symbols)
        if combo != challenge["combo"]:
            return  # эта комбинация не в счёт, но и не мешает — просто игнор

        count = chat_progress.get(user.id, 0) + 1
        chat_progress[user.id] = count
        target = challenge["target"]

        if count >= target:
            await finish_challenge(message, user, COMBO_DISPLAY_NAMES[combo])
        else:
            await message.reply(
                f"{mention(user.id, user.full_name)}, выбил {COMBO_DISPLAY_NAMES[combo]}! "
                f"Прогресс: {count}/{target}"
            )

    elif challenge["kind"] == "streak" and dice.emoji == challenge["emoji"]:
        hit = dice.value == STREAK_TARGET_VALUE
        target = challenge["target"]

        if hit:
            count = chat_progress.get(user.id, 0) + 1
            chat_progress[user.id] = count
            if count >= target:
                await finish_challenge(message, user, f"{STREAK_EMOJI_NAMES[challenge['emoji']]} {target} раз(а) подряд")
            else:
                await message.reply(
                    f"{mention(user.id, user.full_name)}, попал! 🎯 "
                    f"Серия подряд: {count}/{target}. Не промахнись!"
                )
        else:
            had_progress = chat_progress.get(user.id, 0) > 0
            chat_progress[user.id] = 0
            if had_progress:
                await message.reply(
                    f"{mention(user.id, user.full_name)}, промах! Серия сброшена на 0/{target}. "
                    f"Начинай заново."
                )
            # если серии и не было (0/N) — бот молчит, чтобы не спамить на каждый обычный бросок


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
