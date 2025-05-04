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
SESSION_TTL = 15  # seconds

# === Matching Logic ===
def prefix_match(name, city, data):
    name_lower = name.lower()
    city_lower = city.lower() if city else ""
    direct = []
    family = []

    for row in data:
        r_city = row.get('City', '').lower()
        r_fname = row.get('Registrant First Name', '').lower()
        r_lname = row.get('Registrant Last Name', '').lower()
        full_name = f"{r_fname} {r_lname}"

        if city_lower and not r_city.startswith(city_lower):
            continue

        # Direct match
        if r_fname.startswith(name_lower) or r_lname.startswith(name_lower) or full_name.startswith(name_lower):
            direct.append({'row': row, 'via_family': False, 'matched_family': None})
            continue

        # Family match
        matched_line = None
        for line in row.get('Additional Family Members', '').split('\n'):
            if line.strip().lower().startswith(name_lower):
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
    city = row.get('City', '')

    response = f"""✅ *{full_name}* is registered from *{city}*
👥 *Attendees:* {attendees}
👨‍👩‍👧 *Family Members:*
{family if family else 'None'}"""

    if entry['via_family']:
        response += f"\n🧑‍🤝‍🧑 *Matched via family member:* *{entry['matched_family']}*"
    return response

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Send a message like `b Kunj Addison` or `b Hem Mck` to search."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace('\n', ' ')
    chat_id = update.effective_chat.id
    now = time.time()

    print(f"[INFO] Received message: {text} from chat: {chat_id}")

    if chat_id in user_state:
        state = user_state[chat_id]
        if now - state.get('timestamp', 0) > SESSION_TTL:
            print(f"[INFO] Expiring session for chat: {chat_id}")
            del user_state[chat_id]
            state = {}
    else:
        state = {}

    # Check for "b format"
    if text.lower() == "b format":
        await update.message.reply_text(
            "🧾 *Supported Formats:*\n\n"
            "1️⃣ `b firstname city`\n"
            "2️⃣ `b firstname lastname city`\n"
            "3️⃣ `b lastname city`\n"
            "4️⃣ `b firstname`\n"
            "5️⃣ `b lastname`\n\n"
            "➕ Prefixes allowed: e.g., `b hem mck` will find `Hemalkumar McKinney`",
            parse_mode='Markdown'
        )
        return

    # Handle number reply
    if 'awaiting_choice' in state and text.isdigit():
        idx = int(text) - 1
        matches = state.get('matches', [])
        if 0 <= idx < len(matches):
            await update.message.reply_text(format_entry(matches[idx]), parse_mode='Markdown')
            del user_state[chat_id]
        else:
            await update.message.reply_text("❗ Invalid number. Please try again.")
        return

    if not text.lower().startswith("b "):
        return

    query = text[2:].strip()
    tokens = query.split()
    if not tokens:
        await update.message.reply_text("❗ Please type a name or name + city.")
        return

    name = " ".join(tokens[:-1]) if len(tokens) >= 2 else tokens[0]
    city = tokens[-1] if len(tokens) >= 2 else None
    matches = prefix_match(name, city, registration_data)

    if not matches:
        await update.message.reply_text(
            f"❌ No matches found for *{name}* in *{city or 'any city'}*.\n🔍 Try another query.",
            parse_mode='Markdown'
        )
        print(f"[WARN] No match for name: {name} city: {city}")
        return

    if len(matches) == 1:
        await update.message.reply_text(format_entry(matches[0]), parse_mode='Markdown')
    else:
        reply = f"🔎 *Found {len(matches)} possible matches:*\n\n"
        for i, m in enumerate(matches, 1):
            row = m['row']
            full = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
            attendees = row.get('Attendees', '?')
            city_display = row.get('City', '')
            note = f" _(via family: {m['matched_family']})_" if m['via_family'] else ""
            reply += f"*{i}. {full}* — {attendees} attendees from *{city_display}*{note}\n"
        reply += "\n✉️ *Please reply with the number to view full details.*"
        await update.message.reply_text(reply, parse_mode='Markdown')

        user_state[chat_id] = {
            'awaiting_choice': True,
            'matches': matches,
            'timestamp': now
        }

        async def timeout_warning():
            await asyncio.sleep(SESSION_TTL)
            state = user_state.get(chat_id)
            if state and state.get('timestamp') == now and state.get('awaiting_choice'):
                await context.bot.send_message(
                    chat_id,
                    "⏳ Waited 15 seconds… no reply received.\nSend a new query like `b Patel Frisco` anytime!"
                )
                del user_state[chat_id]

        asyncio.create_task(timeout_warning())

# === Init Bot ===
print("🔁 Starting Walkathon Bot")
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()
