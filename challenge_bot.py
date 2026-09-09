"""
Отдельный бот-челлендж: команды на комбинации слот-машины (BAR, виноград,
лимоны, 777) и на дайсы (дартс, боулинг, баскетбол, футбол, кубик).

Для каждого типа есть ДВЕ версии команды:
  - обычная (СУММАРНЫЙ счёт, промахи не мешают): /Darts N
  - "подряд" (промах обнуляет счётчик до нуля): /DartsPodryad N

Челлендж не завершается после первой победы — работает, пока админ
не остановит его командой /stop (или /StopChallenge). Каждый участник
может побеждать до MAX_WINS_PER_USER раз, дальше его броски не считаются.

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
# этого бота есть Telegram Premium (или куплен username через Fragment) —
# так требует сам Telegram. Иначе виден обычный fallback-смайлик — это ок.
# ---------------------------------------------------------------------------
PREMIUM_EMOJI_IDS = {
    "party": "5208541126583136130",     # 🎉
    "check": "5465665580050717956",     # ✔️
    "blue_dot": "5465250866598547954",  # 🔵
    "pin": "5465298472016059249",       # 📌
    "seven": "4938373072185984758",     # 7️⃣
    "star": "5924870095925942277",      # ⭐
    "slot_spin": "5915833712368424979", # 🎰 (в тексте "крути ещё")
    "dart": "5350460637182993292",      # 🎯
    "bowling_six": "5891120371762990493",  # 6️⃣ (боулинг — кегли)
    "cube_six": "5891181334528789506",     # 6️⃣ (кубик)
    "gift_smile": "5240487046086169983",   # 😀
}


def tg(key: str, fallback: str) -> str:
    emoji_id = PREMIUM_EMOJI_IDS.get(key)
    if not emoji_id:
        return fallback
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


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

# Значения dice.value, которые считаются "попаданием" для каждого эмодзи:
#   🎯 дартс     — 6 = яблочко
#   🎳 боулинг   — 6 = страйк
#   🏀 баскетбол — 4 или 5 = мяч влетел в кольцо
#   ⚽ футбол    — 3, 4 или 5 = гол
#   🎲 кубик     — 6 = шестёрка
HIT_VALUES = {
    "🎯": {6},
    "🎳": {6},
    "🏀": {4, 5},
    "⚽": {3, 4, 5},
    "🎲": {6},
}
DICE_EMOJI_NAMES = {
    "🎯": "яблочко в дартс",
    "🎳": "страйк в боулинг",
    "🏀": "попадание в кольцо",
    "⚽": "гол",
    "🎲": "шестёрка на кубике",
}
DICE_COMMAND_TO_EMOJI = {
    "Darts": "🎯", "Bowling": "🎳", "Basketball": "🏀", "Football": "⚽", "Cube": "🎲",
}
# Отдельные команды для режима "подряд"
DICE_PODRYAD_COMMAND_TO_EMOJI = {
    "DartsPodryad": "🎯", "BowlingPodryad": "🎳", "BasketballPodryad": "🏀",
    "FootballPodryad": "⚽", "CubePodryad": "🎲",
}

# Активный челлендж в чате: chat_id -> {
#   "category": "slot" | "dice",
#   "key": "bar"/"seven"/"lemons"/"grapes"  ИЛИ  "🎯"/"🎳"/"🏀"/"⚽"/"🎲",
#   "mode": "cumulative" | "streak",
#   "target": int,
# }
active_challenge: dict[int, dict] = {}
# chat_id -> {user_id: текущее число}
# cumulative — сумма попаданий за всё время; streak — серия подряд (промах = 0)
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


async def announce_win(message: Message, user, category: str, key: str) -> None:
    who = mention(user.id, user.full_name)
    chat_wins = win_counts.setdefault(message.chat.id, {})
    chat_wins[user.id] = chat_wins.get(user.id, 0) + 1

    if category == "slot" and key == "seven":
        text = (
            f"{who}, {tg('party', '🎉')} Выигрыш! Слот выдал\n"
            f"{COMBO_DISPLAY_NAMES['seven']}!\n\n"
            f"На этом веселье не заканчивается! Крути {tg('slot_spin', '🎰')} и получай звезды "
            f"{tg('star', '⭐')}\n\n"
            f"За выдачей пишите: {ADMINS_LINE}"
        )
    elif category == "slot":
        text = (
            f"{who}, 🎉 Выигрыш! Слот выдал {COMBO_DISPLAY_NAMES[key]}!\n\n"
            f"На этом веселье не заканчивается! Крутите слот-машину и получайте звезды ⭐\n\n"
            f"За выдачей пишите: {ADMINS_LINE}"
        )
    elif key == "🎳":
        text = (
            f"{who}, {tg('party', '🎉')} Выигрыш! Вы сбили все кегли {tg('bowling_six', '6️⃣')}\n\n"
            f"На этом веселье не заканчивается! Сбивай все кегли {tg('bowling_six', '6️⃣')} и "
            f"получай звезды {tg('star', '⭐')}\n\nЗа выдачей пишите: {ADMINS_LINE}"
        )
    elif key == "🎯":
        text = (
            f"{who}, {tg('party', '🎉')} Выигрыш! Вы попали точно в цель {tg('dart', '🎯')}\n\n"
            f"На этом веселье не заканчивается! Попадай идеально в центр {tg('dart', '🎯')} и "
            f"получай звезды {tg('star', '⭐')}\n\nЗа выдачей пишите: {ADMINS_LINE}"
        )
    elif key == "🎲":
        text = (
            f"{who}, {tg('party', '🎉')} Выигрыш! Вы выбросили шестёрку {tg('cube_six', '6️⃣')}\n\n"
            f"На этом веселье не заканчивается! Выбрасывай шестёрку {tg('cube_six', '6️⃣')} и "
            f"получай звезды {tg('star', '⭐')}\n\nЗа выдачей пишите: {ADMINS_LINE}"
        )
    else:
        text = (
            f"{who}, 🎉 Выигрыш! Вы выбили {DICE_EMOJI_NAMES[key]} {key}\n\n"
            f"На этом веселье не заканчивается! Продолжайте и получайте звезды ⭐\n\n"
            f"За выдачей пишите: {ADMINS_LINE}"
        )

    await message.reply(text)
    # Челлендж НЕ завершается — сбрасываем прогресс только этому пользователю,
    # чтобы он (если не выбрал лимит побед) мог выиграть ещё раз.
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
    start_challenge(message.chat.id, {"category": "slot", "key": combo, "mode": "cumulative", "target": target})

    if combo == "seven":
        text = (
            f"{tg('blue_dot', '🔵')}Лудка запущена {tg('check', '✔️')}\n"
            f"{tg('pin', '📌')}Цель: {COMBO_DISPLAY_NAMES['seven']} - {target} раз"
        )
    else:
        text = (
            f"{tg('blue_dot', '🔵')}Лудка запущена {tg('check', '✔️')}\n"
            f"{tg('pin', '📌')}Цель: {COMBO_DISPLAY_NAMES[combo]} - {target} раз"
        )
    await message.reply(text)


@dp.message(F.text.regexp(r"(?i)^/(DartsPodryad|BowlingPodryad|BasketballPodryad|FootballPodryad|CubePodryad)(?:@\S+)?(?:\s+(\d+))?"))
async def cmd_dice_challenge_streak(message: Message):
    if not is_admin(message.from_user):
        await message.reply("Эта команда доступна только админам.")
        return

    match = re.match(
        r"(?i)^/(DartsPodryad|BowlingPodryad|BasketballPodryad|FootballPodryad|CubePodryad)(?:@\S+)?(?:\s+(\d+))?",
        message.text,
    )
    command_name = match.group(1)
    canonical = next((c for c in DICE_PODRYAD_COMMAND_TO_EMOJI if c.lower() == command_name.lower()), None)
    emoji = DICE_PODRYAD_COMMAND_TO_EMOJI.get(canonical)
    args_str = match.group(2)

    target = int(args_str) if args_str else 2
    if target <= 0:
        await message.reply(f"Пример: /{command_name} 2 — сколько раз ПОДРЯД нужно попасть.")
        return

    start_challenge(message.chat.id, {"category": "dice", "key": emoji, "mode": "streak", "target": target})
    await message.reply(
        f"{tg('blue_dot', '🔵')}Лудка запущена {tg('check', '✔️')}\n"
        f"{tg('pin', '📌')}Цель: {DICE_EMOJI_NAMES[emoji]} {emoji} - {target} раз ПОДРЯД. "
        f"Один промах — и счётчик обнуляется!"
    )


@dp.message(F.text.regexp(r"(?i)^/(Darts|Bowling|Basketball|Football|Cube)(?:@\S+)?(?:\s+(\d+))?"))
async def cmd_dice_challenge_cumulative(message: Message):
    if not is_admin(message.from_user):
        await message.reply("Эта команда доступна только админам.")
        return

    match = re.match(r"(?i)^/(Darts|Bowling|Basketball|Football|Cube)(?:@\S+)?(?:\s+(\d+))?", message.text)
    command_name = match.group(1)
    canonical = next((c for c in DICE_COMMAND_TO_EMOJI if c.lower() == command_name.lower()), None)
    emoji = DICE_COMMAND_TO_EMOJI.get(canonical)
    args_str = match.group(2)

    if emoji is None or not args_str or int(args_str) <= 0:
        await message.reply(f"Пример: /{command_name} 3 — сколько раз суммарно нужно попасть.")
        return

    target = int(args_str)
    start_challenge(message.chat.id, {"category": "dice", "key": emoji, "mode": "cumulative", "target": target})
    await message.reply(
        f"{tg('blue_dot', '🔵')}Лудка запущена {tg('check', '✔️')}\n"
        f"{tg('pin', '📌')}Цель: {DICE_EMOJI_NAMES[emoji]} {emoji} - {target} раз (суммарно)"
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
    # Пересланные сообщения игнорируем полностью — иначе можно абузить
    # челлендж, форвардя себе старый выигрышный бросок.
    if message.forward_origin is not None or message.forward_date is not None:
        return

    challenge = active_challenge.get(message.chat.id)
    if challenge is None:
        return

    dice = message.dice
    user = message.from_user

    if not has_wins_left(message.chat.id, user.id):
        return  # уже выиграл максимум раз — броски больше не считаются

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
            await announce_win(message, user, "slot", combo)
        else:
            label = COMBO_DISPLAY_NAMES[combo]
            gift = tg("gift_smile", "😀")
            if combo == "seven":
                await message.reply(
                    f"{mention(user.id, user.full_name)}, {tg('party', '🎉')} Отличный результат! "
                    f"Слот выдал\n{label}!\nОсталось еще выбить {label} {target - count} раз "
                    f"чтобы забрать приз {gift}"
                )
            else:
                await message.reply(
                    f"{mention(user.id, user.full_name)}, выбил {label}! Прогресс: {count}/{target}"
                )

    elif challenge["category"] == "dice" and dice.emoji == challenge["key"]:
        emoji = challenge["key"]
        hit = dice.value in HIT_VALUES[emoji]

        if challenge["mode"] == "cumulative":
            if not hit:
                return  # промах просто игнорируем, счётчик не трогаем
            count = chat_progress.get(user.id, 0) + 1
            chat_progress[user.id] = count
            if count >= target:
                await announce_win(message, user, "dice", emoji)
            else:
                remaining = target - count
                gift = tg("gift_smile", "😀")
                if emoji == "🎳":
                    await message.reply(
                        f"{mention(user.id, user.full_name)}, {tg('party', '🎉')} Отличный результат! "
                        f"Вы сбили все кегли {tg('bowling_six', '6️⃣')}\n\n"
                        f"Осталось еще выбить {tg('bowling_six', '6️⃣')} {remaining} раз чтобы забрать приз {gift}"
                    )
                elif emoji == "🎲":
                    await message.reply(
                        f"{mention(user.id, user.full_name)}, {tg('party', '🎉')} Отличный результат! "
                        f"Вы попали идеально в центр {tg('cube_six', '6️⃣')}\n\n"
                        f"Осталось еще выбить {tg('cube_six', '6️⃣')} {remaining} раз чтобы забрать приз {gift}"
                    )
                elif emoji == "🎯":
                    await message.reply(
                        f"{mention(user.id, user.full_name)}, попал в {tg('dart', '🎯')}! "
                        f"Прогресс: {count}/{target}"
                    )
                else:
                    await message.reply(
                        f"{mention(user.id, user.full_name)}, выбил {DICE_EMOJI_NAMES[emoji]}! "
                        f"Прогресс: {count}/{target}"
                    )
        else:  # streak — режим "подряд"
            if hit:
                count = chat_progress.get(user.id, 0) + 1
                chat_progress[user.id] = count
                if count >= target:
                    await announce_win(message, user, "dice", emoji)
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
