import os
import json
import time
import asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, ContextTypes,
    CommandHandler, MessageHandler, filters
)
from decrypt_utils import decrypt_file

# === Load env + decrypt data ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GPG_PASSPHRASE = os.getenv("GPG_PASSPHRASE")

decrypted_path = decrypt_file(GPG_PASSPHRASE)
with open(decrypted_path, 'r') as f:
    registration_data = json.load(f)

# === Session Cache ===
user_state = {}  # chat_id -> dict(state)
SESSION_TTL = 30  # seconds
MAX_MSG_LENGTH = 4000

# === Utilities ===
async def send_split_message(text, update):
    for i in range(0, len(text), MAX_MSG_LENGTH):
        await update.message.reply_text(text[i:i + MAX_MSG_LENGTH], parse_mode='Markdown')

def clean_input(text):
    return ' '.join(text.replace('\n', ' ').split()).strip()

# === Matching Logic ===
def prefix_match(name, city, data):
    name = name.lower()
    city = city.lower()
    direct = []
    family = []

    for row in data:
        r_city = row.get('City', '').lower()
        if city and not r_city.startswith(city):
            continue

        r_fname = row.get('Registrant First Name', '').lower()
        r_lname = row.get('Registrant Last Name', '').lower()
        full_name = f"{r_fname} {r_lname}"

        if r_fname.startswith(name) or r_lname.startswith(name) or full_name.startswith(name):
            direct.append({'row': row, 'via_family': False, 'matched_family': None})
            continue

        matched_line = None
        for line in row.get('Additional Family Members', '').split('\n'):
            if line.strip().lower().startswith(name):
                matched_line = line.strip()
                break
        if matched_line:
            family.append({'row': row, 'via_family': True, 'matched_family': matched_line})

    return direct if direct else family

# === Format Entry ===
def format_entry(entry):
    row = entry['row']
    full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
    attendees = row.get('Attendees', '?')
    family = row.get('Additional Family Members', 'None').strip()

    response = f"""✅ *{full_name}* is registered.
👥 *Attendees:* {attendees}
👨‍👩‍👧 *Family Members:*
{family if family else 'None'}"""

    if entry['via_family']:
        response += f"\n🧑‍🤝‍🧑 *Matched via family member:* *{entry['matched_family']}*"
    return response

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Send a message like `b Kunj Addison` or `b Kun Add` to search.\n"
        "To see all supported formats, type `b format`."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = clean_input(update.message.text)
    chat_id = update.effective_chat.id
    now = time.time()

    if chat_id in user_state and now - user_state[chat_id].get('timestamp', 0) > SESSION_TTL:
        del user_state[chat_id]

    # === Format help ===
    if text.lower() == "b format":
        await update.message.reply_text(
            "✅ *Supported Formats:*\n"
            "`b firstname city`\n"
            "`b firstname lastname city`\n"
            "`b lastname city`\n"
            "`b firstname`\n"
            "`b lastname`",
            parse_mode='Markdown'
        )
        return

    # === Handle selection
    if chat_id in user_state and user_state[chat_id].get('awaiting_choice') and text.isdigit():
        idx = int(text) - 1
        matches = user_state[chat_id]['matches']
        if 0 <= idx < len(matches):
            await update.message.reply_text(format_entry(matches[idx]), parse_mode='Markdown')
        else:
            await update.message.reply_text("❗ Invalid number.")
        del user_state[chat_id]
        return

    # Only handle messages starting with "b "
    if not text.lower().startswith("b "):
        return

    text = clean_input(text[2:])

    tokens = text.split()
    name = ""
    city = ""

    if len(tokens) >= 2:
        name = " ".join(tokens[:-1])
        city = tokens[-1]
    elif len(tokens) == 1:
        name = tokens[0]
        city = ""

    matches = prefix_match(name, city, registration_data)

    if not matches:
        await update.message.reply_text(
            f"❌ No matches found for *{name}* in *{city or 'any city'}*.\n"
            "🔍 Try another name or family member.",
            parse_mode='Markdown'
        )
        return

    if len(matches) == 1:
        await update.message.reply_text(format_entry(matches[0]), parse_mode='Markdown')
    else:
        reply = f"🔎 *Found {len(matches)} possible matches:*\n\n"
        for i, m in enumerate(matches, 1):
            row = m['row']
            full = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
            city = row.get('City', '?')
            attendees = row.get('Attendees', '?')
            note = f" _(via family: {m['matched_family']})_" if m['via_family'] else ""
            reply += f"*{i}. {full}* — {attendees} attendees – _{city}_{note}\n"

        reply += "\n✉️ *Please reply with the number to see full details.*"
        await send_split_message(reply, update)

        user_state[chat_id] = {
            'awaiting_choice': True,
            'matches': matches,
            'timestamp': now
        }

        async def timeout_warning():
            await asyncio.sleep(SESSION_TTL)
            if chat_id in user_state and user_state[chat_id].get('timestamp') == now:
                await context.bot.send_message(
                    chat_id,
                    "⏳ Waited 15 seconds… no reply received.\nSend a new query like `b Patel Frisco` if needed!"
                )
                del user_state[chat_id]

        asyncio.create_task(timeout_warning())

# === App Init ===
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()
