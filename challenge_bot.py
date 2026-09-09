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

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message

# ==== НАСТРОЙКИ ====
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ОТ_BOTFATHER")

ADMIN_USERNAMES = {"nexoraizfuck", "Raivens1", "Mtl_sr"}
ADMINS_LINE = " ".join(f"@{u}" for u in ADMIN_USERNAMES)

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

# У баскетбола каждое значение — своя ситуация, а не просто "попал/не попал",
# поэтому у каждого значения своя фраза. 1 и 5 — по аналогии с тем, что дано
# (промах / попадание), поправь текст сам, если нужно другое слово.
BASKETBALL_PHRASES = {
    1: "Кинуть в молоко",
    2: "промахнуться",
    3: "Мячик застрял",
    4: "Попасть",
    5: "Попасть в кольцо идеально",
}


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
    if emoji == "🎯":
        return f"Попасть ровно в центр {digit}"
    if emoji == "🎳":
        return f"Выбить все кегли {digit}"
    if emoji == "🏀":
        phrase = BASKETBALL_PHRASES.get(value, "Попасть")
        return f"{phrase} {digit}"
    if emoji == "🎲":
        return f"{digit} {tg('cube_icon', '🎲')}"
    return digit


DICE_SHORT_NAME = {"🎯": "дартс", "🎳": "боулинг", "🏀": "баскетбол", "🎲": "кубик"}

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


def has_wins_left(chat_id: int, user_id: int) -> bool:
    return win_counts.get(chat_id, {}).get(user_id, 0) < MAX_WINS_PER_USER


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
# ОБРАБОТКА БРОСКОВ ДАЙСОВ
# ---------------------------------------------------------------------------

@dp.message(F.dice)
async def handle_dice(message: Message):
    if message.forward_origin is not None or message.forward_date is not None:
        return

    challenge = active_challenge.get(message.chat.id)
    if challenge is None:
        return

    dice = message.dice
    user = message.from_user

    if not has_wins_left(message.chat.id, user.id):
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
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
