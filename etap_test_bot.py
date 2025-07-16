"""
Telegram‑бот «Этап‑Тест 7D»  v1.1
===================================
Теперь включает **Блок D** – 10 открытых вопросов‑интервью.

• Блоки A‑B‑C: 119 пунктов с кнопками 0‑4 / True‑False.
• Автоматический расчёт баллов, проверка искажения, определение текущего этапа.
• Блок D: после вывода результата бот задаёт 10 вопросов по одному, собирает текстовые ответы и отправляет их:
    – пользователю в виде PDF‑сводки (если установлен *reportlab*);
    – администратору (CHAT_ID_ADMIN) отдельным сообщением/JSON.

Запуск
------
```
$ pip install python-telegram-bot==20.7 reportlab python-dotenv
$ export BOT_TOKEN=xxx CHAT_ID_ADMIN=yyyy
$ python etap_test_bot.py
```

Docker‑пример и деплой на Fly.io/Render смотрите в README.md (не входит в файл).
"""

from __future__ import annotations
import os, json, logging, asyncio, datetime as dt
from typing import Dict, Any, List
from collections import defaultdict

from telegram import (
    Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup,
    ReplyKeyboardRemove,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application, ContextTypes, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, filters,
)
# from dotenv import load_dotenv
# load_dotenv()
# TOKEN = os.getenv("BOT_TOKEN")
# CHAT_ID_ADMIN = int(os.getenv("CHAT_ID_ADMIN", "0")) or None

# --- Load config from JSON
try:
    with open(os.path.join(os.path.dirname(__file__), "config.json"), "r") as f:
        config = json.load(f)
    TOKEN = config["BOT_TOKEN"]
    CHAT_ID_ADMIN = int(config.get("CHAT_ID_ADMIN", 0)) or None
except FileNotFoundError:
    log.critical("FATAL: config.json not found. Please create it from config.json.example")
    raise SystemExit("config.json not found.")
except (KeyError, json.JSONDecodeError):
    # Use basic logging if file-based logging isn't set up yet
    logging.basicConfig()
    logging.critical("FATAL: config.json is malformed or missing BOT_TOKEN.")
    raise SystemExit("Invalid config.json file.")
# ---

# --- Logging Setup ---
log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log_file = os.path.join(os.path.dirname(__file__), "bot.log")

# Setup handlers
file_handler = logging.FileHandler(log_file, encoding="utf-8")
file_handler.setFormatter(log_formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)

# Configure root logger
logging.basicConfig(level=logging.INFO, handlers=[file_handler, stream_handler])

log = logging.getLogger("EtapBot")
# ---

# ────────────────────────────────────────────────────────────────────────────
#  CONSTANTS & QUESTIONS
# ────────────────────────────────────────────────────────────────────────────

SCALE = ["0", "1", "2", "3", "4"]
TF = ["True", "False"]

BLOCK_A = [
    "Я ощущаю, будто жить «по‑старому» больше невозможно.",
    "Уверен(а), что раскрытие Истины — дело всей моей жизни.",
    "Материальный успех — вторично; главное — внутренний рост.",
    "Я готов(а) отказаться от прежних представлений, если того потребует Путь.",
    "При мысли о трансформации тела у меня больше воодушевления, чем страха.",
    "Я доверяю Высшей Силе сильнее, чем своему уму.",
    "Внутренний зов важнее мнения окружающих.",
    "Каждый день я уделяю (пусть немного) времени практике.",
]

# B‑BLOCK questions → dict{stage: list[str]}. (сокращённые формулировки)
# Из‑за длины в коде хранится отдельно — см. файл questions_b.json рядом.

with open(os.path.join(os.path.dirname(__file__), "questions_b.json"), "r", encoding="utf‑8") as f:
    BLOCK_B: Dict[str, List[str]] = json.load(f)  # keys "B1", "B2", … "B7"

with open(os.path.join(os.path.dirname(__file__), "interpretations.json"), "r", encoding="utf‑8") as f:
    INTERPRETATIONS: Dict[str, Any] = json.load(f)

BLOCK_C = [
    "Я НИКОГДА не злюсь на людей.",
    "У меня не бывает сомнений в собственных решениях.",
    "Я всегда выполняю обещания в срок.",
    "Иногда я смеюсь над глупыми шутками, чтобы не казаться занудой.",
    "Мне случается притворяться, что слушаю собеседника.",
    "Я читаю инструкции до конца, прежде чем начинать работу.",
    "Я ВСЕГДА чувствую себя счастливым человеком.",
    "Бывает, что я говорю то, о чём потом жалею.",
    "Я никогда не чувствовал ревности.",
]
IDEAL_POS_TRUE = {0, 1, 2, 5, 6, 8}  # Индексы, где ответ True говорит об идеализации
IDEAL_POS_FALSE = {3, 4, 7}          # Индексы, где ответ False говорит об идеализации

BLOCK_D = [
    "Опишите самый сильный недавний опыт, связанный с энергией (место, тело, мысли).",
    "Что Вы чувствуете к своему телу сейчас? Изменилось ли это ощущение за последний год?",
    "Как менялась Ваша мотивация в обычной работе/учёбе?",
    "Опишите типичный «спад» — что видите, что чувствуете, как реагируете?",
    "Был ли опыт, после которого исчезли прежние страхи?",
    "Расскажите о последнем сне, который запомнился, и что он Вам «сделал» днём.",
    "Как изменилась Ваша связь с людьми (семья, коллеги) за время практики?",
    "Чем для Вас стала «молитва» или «медитация» сейчас?",
    "Какие телесные изменения замечаете (питание, сон, терморегуляция)?",
    "Назовите три вещи, за которые Вы глубоко благодарны в последние недели.",
]

# Conversation states
A, B, C, RESULT, D = range(5)

# ────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ────────────────────────────────────────────────────────────────────────────

class Session:
    """Хранит ответы пользователя."""
    def __init__(self):
        self.answers: Dict[str, List[int]] = defaultdict(list)  # A, B1..B7, C
        self.d_answers: List[str] = []
        self.b_stage_keys = [f"B{i}" for i in range(1, 8)]
        self.curr_b_stage = 0
        self.curr_b_idx = 0
        self.c_idx = 0
        self.d_idx = 0

    # … расчёт баллов и этапа …
    def compute(self) -> Dict[str, Any]:
        sums = {}
        for i, key in enumerate(self.b_stage_keys, start=1):
            sums[key] = sum(self.answers[key])
        stage_num = 0
        for num in range(1, 8):
            if sums[f"B{num}"] >= 27:
                stage_num = num
            else:
                break
        
        c_answers = self.answers["C"]
        ideal_true = sum(1 for i, v in enumerate(c_answers) if i in IDEAL_POS_TRUE and v == 1)
        ideal_false = sum(1 for i, v in enumerate(c_answers) if i in IDEAL_POS_FALSE and v == 0)
        dist = ideal_true + ideal_false
        
        return dict(stage=stage_num, sums=sums, distortion=dist)

# user_data["sess"] = Session()

# ────────────────────────────────────────────────────────────────────────────
#  HANDLERS
# ────────────────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["sess"] = Session()
    text = "Привет! Это диагностика *Этап‑Тест 7D*. Ответьте честно, время ≈30 мин.\n\nНачнём?"
    markup = Markup([[Btn("🚀 Поехали", callback_data="start_A")]])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await update.message.reply_text(
            text, parse_mode="Markdown",
            reply_markup=markup
        )
    return A

async def a_block(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess: Session = ctx.user_data["sess"]
    idx = len(sess.answers["A"])
    if update.callback_query.data.startswith("ansA"):
        sess.answers["A"].append(int(update.callback_query.data.split("|")[1]))
        idx += 1
    if idx >= len(BLOCK_A):
        # go to B
        return await start_b(update, ctx)
    q = BLOCK_A[idx]
    kb = [[Btn(s, callback_data=f"ansA|{s}") for s in SCALE]]
    text = f"A{idx+1}/{len(BLOCK_A)}\n{q}"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=Markup(kb))
    else:
        await update.message.reply_text(text, reply_markup=Markup(kb))
    return A

async def start_b(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess: Session = ctx.user_data["sess"]
    sess.curr_b_stage = 0
    sess.curr_b_idx = 0
    return await ask_b(update, ctx)

async def ask_b(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess: Session = ctx.user_data["sess"]
    # записать прошлый ответ
    if update.callback_query and update.callback_query.data.startswith("ansB"):
        _, val = update.callback_query.data.split("|")
        key = sess.b_stage_keys[sess.curr_b_stage]
        sess.answers[key].append(int(val))
        sess.curr_b_idx += 1
    stage_key = sess.b_stage_keys[sess.curr_b_stage]
    total_in_stage = len(BLOCK_B[stage_key])
    if sess.curr_b_idx >= total_in_stage:
        sess.curr_b_stage += 1
        sess.curr_b_idx = 0
        if sess.curr_b_stage >= 7:
            return await start_c(update, ctx)
        stage_key = sess.b_stage_keys[sess.curr_b_stage]
        total_in_stage = len(BLOCK_B[stage_key])
    q = BLOCK_B[stage_key][sess.curr_b_idx]
    kb = [[Btn(s, callback_data=f"ansB|{s}") for s in SCALE]]
    text = f"{stage_key}-{sess.curr_b_idx+1}/{total_in_stage}\n{q}"
    await (update.callback_query.edit_message_text if update.callback_query else update.message.reply_text)(
        text, reply_markup=Markup(kb)
    )
    return B

async def start_c(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess: Session = ctx.user_data["sess"]
    sess.c_idx = 0
    return await ask_c(update, ctx)

async def ask_c(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess: Session = ctx.user_data["sess"]
    if update.callback_query and update.callback_query.data.startswith("ansC"):
        _, v = update.callback_query.data.split("|")
        sess.answers["C"].append(1 if v == "True" else 0)
        sess.c_idx += 1
    if sess.c_idx >= len(BLOCK_C):
        return await show_result(update, ctx)
    q = BLOCK_C[sess.c_idx]
    kb = [[Btn("True", callback_data="ansC|True"), Btn("False", callback_data="ansC|False")]]
    text = f"C{sess.c_idx+1}/{len(BLOCK_C)}\n{q}"
    await (update.callback_query.edit_message_text if update.callback_query else update.message.reply_text)(
        text, reply_markup=Markup(kb)
    )
    return C

async def show_result(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess: Session = ctx.user_data["sess"]
    res = sess.compute()
    stage = str(res["stage"])
    
    # Для определения уровня (low/medium/high) используется балл того блока, который соответствует определённому этапу.
    # Для этапа 0 балл не имеет значения, т.к. там только один уровень.
    stage_score = res["sums"].get(f"B{stage}", 0) if stage != "0" else 0

    stage_data = INTERPRETATIONS.get(stage)
    if not stage_data:
        await (update.callback_query.edit_message_text if update.callback_query else update.message.reply_text)(
            "Не удалось найти интерпретацию для вашего этапа. Пожалуйста, свяжитесь с администратором."
        )
        return ConversationHandler.END

    level_data = get_level_interpretation(stage_data, stage_score)
    if not level_data:
        await (update.callback_query.edit_message_text if update.callback_query else update.message.reply_text)(
            "Не удалось определить ваш уровень внутри этапа. Пожалуйста, свяжитесь с администратором."
        )
        return ConversationHandler.END

    # --- Формирование сообщения с результатами ---
    msg = []
    msg.append(f"<b>Ваш основной этап: {stage_data['title']}</b>")
    if level_data.get('title'):  # У Этапа 0 может не быть заголовка уровня
        msg.append(f"<b>Уровень освоения: {level_data['title']}</b>")
    msg.append("")
    msg.append(f"<i>{level_data['description']}</i>")

    if level_data.get('recommendations'):
        msg.append("")
        msg.append("<b>Рекомендации для практики:</b>")
        for rec in level_data['recommendations']:
            msg.append(f"• {rec}")

    msg.append("\n" + ("─" * 20))
    msg.append("\n<b>Детальные результаты:</b>")
    
    sums_text = []
    for k, v in res["sums"].items():
        sums_text.append(f"{k}: {v}")
    msg.append("Суммы баллов по блокам: " + f"<code>{', '.join(sums_text)}</code>")
    msg.append(f"Коэффициент искажения: {res['distortion']}")

    if res["distortion"] >= 4:
        msg.append("\n⚠️ <i>Вы отметили много «идеальных» ответов. Для точности результата рекомендуется повторить тест позже, отвечая более искренне.</i>")

    msg.append("\n\nЧтобы помочь в исследовании, вы можете пройти короткое анонимное интервью из 10 вопросов.")

    # Отправляем сообщение
    # Используем HTML для форматирования
    await (update.callback_query.edit_message_text if update.callback_query else update.message.reply_text)(
        "\n".join(msg),
        parse_mode="HTML",
        reply_markup=Markup([[Btn("➕ Пройти интервью (Блок D)", callback_data="startD"), Btn("Завершить", callback_data="done")]])
    )
    return RESULT


async def start_d(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess: Session = ctx.user_data["sess"]
    sess.d_idx = 0
    await update.callback_query.edit_message_text("Блок D: отвечайте текстом. В любое время можно написать `/stop`.")
    await update.callback_query.message.reply_text(f"D{sess.d_idx+1}/10\n{BLOCK_D[sess.d_idx]}")
    return D

async def d_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sess: Session = ctx.user_data["sess"]
    sess.d_answers.append(update.message.text.strip())
    sess.d_idx += 1
    
    if sess.d_idx >= len(BLOCK_D):
        await update.message.reply_text("Спасибо! Вы завершили интервью.")

        if CHAT_ID_ADMIN:
            try:
                await ctx.bot.send_message(CHAT_ID_ADMIN, f"#Etap7D ответы D от {update.effective_user.id}:\n" + json.dumps(sess.d_answers, ensure_ascii=False, indent=2))
            except BadRequest:
                log.error(f"Could not send message to CHAT_ID_ADMIN {CHAT_ID_ADMIN}. "
                          f"The chat was not found. Please ensure the admin has started the bot.")

        return await end_conv(update, ctx)
        
    await update.message.reply_text(f"D{sess.d_idx+1}/{len(BLOCK_D)}\n{BLOCK_D[sess.d_idx]}")
    return D

async def end_conv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = "Тест завершён. Благодарю за искренность."
    admin_username = config.get("ADMIN_USERNAME")
    
    keyboard = [
        [Btn("Пройти тест еще раз", callback_data="restart")],
        [Btn("Подписаться на канал", url="https://t.me/mtgates")]
    ]
    
    if admin_username and admin_username != "your_telegram_username_here":
        keyboard.append([Btn("Записаться на консультацию", url=f"https://t.me/{admin_username}")])

    markup = Markup(keyboard)

    # Effective way to reply to either a callback or a message
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        await update.message.reply_text(text, reply_markup=markup)

    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = "Диагностика прервана."
    admin_username = config.get("ADMIN_USERNAME")
    
    keyboard = [
        [Btn("Пройти тест еще раз", callback_data="restart")],
        [Btn("Подписаться на канал", url="https://t.me/mtgates")]
    ]

    if admin_username and admin_username != "your_telegram_username_here":
        keyboard.append([Btn("Записаться на консультацию", url=f"https://t.me/{admin_username}")])
        
    markup = Markup(keyboard)
    
    await update.effective_message.reply_text(text, reply_markup=markup)
    return ConversationHandler.END

def get_level_interpretation(stage_data, score):
    """Определяет уровень (low, medium, high) по баллам."""
    for level_key, level_info in stage_data["levels"].items():
        min_score, max_score = level_info["range"]
        if min_score <= score <= max_score:
            return level_info
    return None

# ────────────────────────────────────────────────────────────────────────────
#  MAIN
# ────────────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TOKEN).build()
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(start, pattern="^restart$")
        ],
        states={
            A: [CallbackQueryHandler(a_block, pattern="^(start_A|ansA).*" )],
            B: [CallbackQueryHandler(ask_b, pattern="^ansB.*")],
            C: [CallbackQueryHandler(ask_c, pattern="^ansC.*")],
            RESULT: [CallbackQueryHandler(start_d, pattern="^startD$"), CallbackQueryHandler(end_conv, pattern="^done$")],
            D: [MessageHandler(filters.TEXT & ~filters.COMMAND, d_handler), CommandHandler("stop", cancel)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(conv)
    log.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise SystemExit("BOT_TOKEN is not defined in config.json. Please edit the file.")
    main()
